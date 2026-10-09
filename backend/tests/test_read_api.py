from fastapi.testclient import TestClient
from test_persistence import db as db_fixture
from test_retrieval import published
from test_runtime import TOKEN, FakeProbe, settings

from laip.api import create_app
from laip.masking import POLICY
from laip.read_service import ReadService

db = db_fixture


def test_api_reads_match_shared_service_and_do_not_write(db, tmp_path):
    repo, bundle = published(db)
    eid = bundle["evidence"][0]["evidence_id"]
    entity_id = bundle["entities"][0]["entity_id"]
    repo.put_chunk("read", "snap_retrieval", "customer validation", [eid], POLICY, "v1", "none")
    read = ReadService(repo)
    app = create_app(settings(tmp_path), FakeProbe(), read_factory=lambda: read)
    args = {"schema_version": "0.1.0", "snapshot_id": "snap_retrieval"}
    headers = {"Authorization": "Bearer " + TOKEN}
    before = db.execute(
        "SELECT (SELECT count(*) FROM jobs),(SELECT count(*) FROM reviews)"
    ).fetchone()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        cases = [
            (
                "entity",
                {**args, "entity_id": entity_id},
                f"/api/v1/entities/{entity_id}?snapshot_id=snap_retrieval",
            ),
            (
                "evidence",
                {**args, "evidence_id": eid},
                f"/api/v1/evidence/{eid}?snapshot_id=snap_retrieval",
            ),
            ("search", {**args, "query": "customer"}, "/api/v1/retrieval/query"),
            (
                "graph",
                {**args, "root_entity_ids": [bundle["dependencies"][0]["from_entity_id"]]},
                "/api/v1/graph/query",
            ),
            (
                "context",
                {**args, "level": "system", "entity_ids": [], "max_context_tokens": 32768},
                "/api/v1/context/build",
            ),
        ]
        for operation, request, url in cases:
            response = (
                client.get(url, headers=headers)
                if operation in {"entity", "evidence"}
                else client.post(url, json=request, headers=headers)
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"] == read.call(operation, request)["data"]
        assert client.post("/api/v1/retrieval/query", json=cases[2][1]).status_code == 401
        assert (
            client.post(
                "/api/v1/retrieval/query",
                json={**cases[2][1], "command": "fake secret"},
                headers=headers,
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/retrieval/query",
                content='{"schema_version":"0.1.0","schema_version":"9"}',
                headers={**headers, "Content-Type": "application/json"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/retrieval/query",
                json=cases[2][1],
                headers={**headers, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        missing = client.get(
            "/api/v1/entities/ent_" + "f" * 64 + "?snapshot_id=snap_retrieval", headers=headers
        )
        assert missing.status_code == 404
        assert "fake secret" not in missing.text
    after = db.execute(
        "SELECT (SELECT count(*) FROM jobs),(SELECT count(*) FROM reviews)"
    ).fetchone()
    assert before == after


def test_default_namespace_disabled_and_body_bounded(tmp_path):
    with TestClient(create_app(settings(tmp_path), FakeProbe()), base_url="http://api") as client:
        headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
        assert (
            client.post(
                "/api/v1/retrieval/query",
                json={"schema_version": "0.1.0", "snapshot_id": "x", "query": "x"},
                headers=headers,
            ).status_code
            == 503
        )
        result = client.post(
            "/api/v1/retrieval/query", content="x" * (1024 * 1024 + 1), headers=headers
        )
        assert result.status_code == 413
        assert "x" * 100 not in result.text
