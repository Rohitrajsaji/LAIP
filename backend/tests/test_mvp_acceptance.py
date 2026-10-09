"""Synthetic offline acceptance through real HTTP handlers and durable workers.

Recovery/cancellation adversarial checkpoints are separately exercised by
 test_analyst_jobs; archive and AI citation abuse by source_import/context tests.
"""

import hashlib
import io
import json
import zipfile

from fastapi.testclient import TestClient
from test_analyst_jobs import pipeline as pipeline_fixture
from test_runtime import FakeProbe

from laip.analyst_jobs import AnalystJobs
from laip.analyst_service import AnalystService
from laip.api import create_app
from laip.masking import POLICY
from laip.read_service import ReadService

pipeline = pipeline_fixture


def test_customer_validation_http_to_durable_export_retains_partial_truth(pipeline):
    jobs, conn = pipeline
    service = AnalystService(conn, jobs.settings)
    headers = {"Authorization": "Bearer " + jobs.settings.service_token.get_secret_value()}
    with TestClient(
        create_app(
            jobs.settings,
            FakeProbe(),
            analyst_factory=lambda: service,
            read_factory=lambda: ReadService(jobs.repository),
        ),
        base_url="http://127.0.0.1",
    ) as client:
        fixture = client.get("/api/v1/fixtures/analyst", headers=headers).json()["data"]
        imported = client.post(
            "/api/v1/imports",
            headers=headers,
            json={
                "schema_version": "0.1.0",
                "archive_base64": fixture["archive_base64"],
                "manifest": fixture["manifest"],
            },
        )
        assert imported.status_code == 200
        accepted = imported.json()["data"]
        # A new worker instance must recover durable input without frontend state.
        jobs = AnalystJobs(conn, jobs.settings)
        assert jobs.execute_one()
        queued = client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "schema_version": "0.1.0",
                "import_id": accepted["import_id"],
            },
        )
        assert queued.status_code == 200
        run_id = queued.json()["data"]["run_id"]
        assert jobs.execute_one()
        workspace = client.get("/api/v1/workspace", headers=headers).json()["data"]
        assert workspace["ai_enabled"] is False
        snapshot = workspace["snapshot_id"]
        assert snapshot
        run_summary = next(r for r in workspace["runs"] if r["run_id"] == run_id)
        assert run_summary["snapshot_id"] == snapshot
        assert any(d["code"] == "INCLUDE_MISSING" for d in run_summary["diagnostics"])
        assert {
            d["resolution"]
            for d in run_summary["diagnostics"]
            if d["code"] == "UNRESOLVED_DEPENDENCY"
        } >= {"dynamic", "ambiguous"}
        assert any("supplied" in limitation.lower() for limitation in run_summary["limitations"])
        assert (
            conn.execute("SELECT state FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0]
            == "partial"
        )
        inventory = service.inventory(snapshot, "0.2.0")
        orders = [
            r
            for r in inventory["records"]
            if r["type"] == "entity"
            and r["payload"]["identity"]["kind"] == "Program"
            and r["payload"]["identity"]["qualified_identity"][-1] == "ORDER"
        ]
        assert len(orders) == 2 and len({r["id"] for r in orders}) == 2
        assert all(r["payload"]["analysis_status"] == "analyzed" for r in orders)
        customer_observation = next(
            r["payload"]
            for r in inventory["records"]
            if r["type"] == "entity"
            and r["payload"]["identity"]["kind"] == "Program"
            and r["payload"]["identity"]["qualified_identity"][-1] == "CUSTOMER"
        )
        assert customer_observation["analysis_status"] == "partial"
        dependencies = service.dependencies(snapshot, "0.2.0")
        unresolved = [
            r
            for r in dependencies["records"]
            if r["type"] == "dependency" and r["payload"]["resolution"] in {"dynamic", "ambiguous"}
        ]
        assert {r["payload"]["resolution"] for r in unresolved} == {"dynamic", "ambiguous"}
        assert all(
            r["payload"]["to_entity_id"] is None
            and r["evidence_ids"]
            and r["review_status"] != "verified"
            for r in unresolved
        )
        evidence = conn.execute(
            "SELECT payload FROM evidence WHERE run_id=%s", (run_id,)
        ).fetchall()
        barriers = [r[0] for r in evidence if r[0]["metadata"].get("barrier")]
        assert barriers and all(r["source_locations"] for r in barriers)
        rules = service.rules(snapshot, "0.2.0")["rules"]
        assert rules and all(r["review_status"] != "verified" for r in rules)
        assert all(r["revision"]["evidence_ids"] for r in rules)
        customer = next(
            r["payload"]["entity_id"]
            for r in inventory["records"]
            if r["type"] == "entity"
            and r["payload"]["identity"]["kind"] == "Program"
            and r["payload"]["identity"]["qualified_identity"][-1] == "CUSTOMER"
        )
        customer_rules = [
            r["revision"] for r in rules if customer in r["revision"]["program_entity_ids"]
        ]
        assert customer_rules and all(r["classification"] == "inferred" for r in customer_rules)
        # Syntactic guards can survive, but the missing include blocks dependent
        # conclusions through cited claim-local support, without automatic review.
        assert all(r["claim_support"]["state"] == "blocked" for r in customer_rules)
        assert all(
            r["claim_support"]["barriers"]
            and all(b["evidence_ids"] for b in r["claim_support"]["barriers"])
            for r in customer_rules
        )
        retrieved = client.post(
            "/api/v1/retrieval/query",
            headers=headers,
            json={
                "schema_version": "0.2.0",
                "snapshot_id": snapshot,
                "mode": "keyword",
                "query": "balance",
                "limit": 20,
            },
        )
        assert retrieved.status_code == 200 and retrieved.json()["data"]["records"]
        assert service._snapshot(snapshot).search("balance", policy_version=POLICY)["records"]
        exported = client.post(
            "/api/v1/exports",
            headers=headers,
            json={
                "schema_version": "0.2.0",
                "snapshot_id": snapshot,
                "source_included": False,
            },
        )
        assert exported.status_code == 200
        export_id = exported.json()["data"]["export_id"]
        assert jobs.execute_one()
        download = client.get(f"/api/v1/exports/{export_id}/download", headers=headers)
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["source_included"] is False
            assert manifest["limitations"]
            assert not any(p.startswith("source/") for p in archive.namelist())
            for item in manifest["files"]:
                assert (
                    hashlib.sha256(archive.read(item["relative_path"])).hexdigest()
                    == item["sha256"]
                )
        assert (
            client.post(
                "/api/v1/exports",
                headers=headers,
                json={
                    "schema_version": "0.2.0",
                    "snapshot_id": snapshot,
                    "source_included": True,
                },
            ).status_code
            == 400
        )
        second = jobs.queue_analysis(accepted["run_id"])
        assert jobs.execute_one()
        assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT count(*) FROM rule_revisions WHERE run_id=%s "
                "AND previous_revision_id IS NOT NULL",
                (second["run_id"],),
            ).fetchone()[0]
            > 0
        )
        assert service.rules(snapshot, "0.2.0")["rules"] == rules
        latest_inventory = service.inventory(service.workspace()["snapshot_id"], "0.2.0")
        assert {
            r["payload"]["entity_id"] for r in latest_inventory["records"] if r["type"] == "entity"
        } == {r["payload"]["entity_id"] for r in inventory["records"] if r["type"] == "entity"}
