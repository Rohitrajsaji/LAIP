"""Real HTTP-to-durable-worker offline import/review/export acceptance."""

import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient
from test_analyst_jobs import pipeline as pipeline_fixture
from test_runtime import FakeProbe

from laip.analyst_service import AnalystService
from laip.api import create_app
from laip.persistence import Repository, validate
from laip.read_service import ReadService

pipeline = pipeline_fixture


def test_http_fixture_import_analysis_revision_retrieval_and_download(pipeline):
    jobs, connection = pipeline
    configured = jobs.settings
    analyst = AnalystService(connection, configured)
    read = ReadService(Repository(connection, configured.analyst_namespace))
    application = create_app(
        configured, FakeProbe(), analyst_factory=lambda: analyst, read_factory=lambda: read
    )
    headers = {"Authorization": "Bearer " + configured.service_token.get_secret_value()}
    with TestClient(application, base_url="http://127.0.0.1") as client:

        def get(path):
            if "snapshot_id=" in path:
                path += "&schema_version=0.2.0"
            response = client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()["data"]

        def post(path, arguments):
            response = client.post(
                path, headers=headers, json={"schema_version": "0.1.0", **arguments}
            )
            assert response.status_code == 200, response.text
            return response.json()["data"]

        assert get("/api/v1/workspace")["snapshot_id"] is None
        fixture = get("/api/v1/fixtures/analyst")
        accepted = post(
            "/api/v1/imports",
            {"archive_base64": fixture["archive_base64"], "manifest": fixture["manifest"]},
        )
        assert jobs.execute_one()
        queued = post("/api/v1/runs", {"import_id": accepted["import_id"]})
        assert jobs.execute_one()
        workspace = get("/api/v1/workspace")
        assert workspace["ai_enabled"] is False
        run = next(r for r in workspace["runs"] if r["run_id"] == queued["run_id"])
        assert run["state"] == "partial"
        snapshot = workspace["snapshot_id"]
        inventory = get(f"/api/v1/inventory?snapshot_id={snapshot}")
        assert inventory["records"]
        dependencies = get(f"/api/v1/dependencies?snapshot_id={snapshot}")
        assert any(r["payload"]["resolution"] == "dynamic" for r in dependencies["records"])
        rules = get(f"/api/v1/rules?snapshot_id={snapshot}")["rules"]
        assert rules
        original = next(
            rule["revision"] for rule in rules if rule["revision"]["program_entity_ids"]
        )
        evidence_id = original["evidence_ids"][0]
        evidence = get(f"/api/v1/evidence/{evidence_id}?snapshot_id={snapshot}")
        assert evidence
        entity_id = original["program_entity_ids"][0]
        assert get(f"/api/v1/entities/{entity_id}?snapshot_id={snapshot}")
        artifact = connection.execute(
            "SELECT a.artifact_id FROM artifacts a JOIN run_artifacts r USING (artifact_id) "
            "WHERE r.run_id=%s AND a.payload->>'locator' LIKE '%%ORDER.rpgle' LIMIT 1",
            (queued["run_id"],),
        ).fetchone()[0]
        source = get(
            f"/api/v1/artifacts/{artifact}/source?snapshot_id={snapshot}&start_line=1&end_line=20"
        )
        assert "balance" in source["text"]
        correction = post(
            f"/api/v1/rules/{original['rule_entity_id']}/revisions",
            {
                "snapshot_id": snapshot,
                "expected_revision_id": original["revision_id"],
                "name": "Reviewed synthetic balance cap",
                "description": "Synthetic condition and actions retained.",
                "conditions": original["conditions"],
                "actions": original["actions"],
                "evidence_ids": original["evidence_ids"],
                "reason": "Acceptance journey correction",
            },
        )
        revised_snapshot = correction["snapshot_id"]
        revised = next(
            r
            for r in get(f"/api/v1/rules?snapshot_id={revised_snapshot}")["rules"]
            if r["revision"]["rule_entity_id"] == original["rule_entity_id"]
        )
        assert len(revised["revisions"]) == 2
        assert revised["revisions"][1]["revision_id"] == original["revision_id"]
        assert revised["review_status"] != "approved"
        assert get("/api/v1/workspace")["snapshot_id"] == revised_snapshot
        retrieved = post(
            "/api/v1/retrieval/query",
            {"schema_version": "0.2.0", "snapshot_id": revised_snapshot, "query": "balance"},
        )
        assert retrieved["records"]
        exported = post(
            "/api/v1/exports",
            {"schema_version": "0.2.0", "snapshot_id": revised_snapshot, "source_included": False},
        )
        assert jobs.execute_one()
        ready = get(f"/api/v1/exports/{exported['export_id']}")
        assert ready["download_url"]
        response = client.get(ready["download_url"], headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            validate("ExportManifest", manifest)
            assert manifest["source_included"] is False
            for item in manifest["files"]:
                raw = archive.read(item["relative_path"])
                assert len(raw) == item["byte_length"]
                assert hashlib.sha256(raw).hexdigest() == item["sha256"]
