"""Persistence regressions for the independently captured pinned CLI fixture."""

import hashlib
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.artifacts import LocalArtifactStore
from laip.migrations import migrate
from laip.persistence import Repository
from laip.rea_adapter import InputFile, ReferenceInventoryResult, validate_inventory
from laip.rea_import import REA_PROVIDER, persist_inventory, prepare_inventory

FIXTURE = Path(__file__).parent / "fixtures/rea"


def result():
    inputs = tuple(
        InputFile(
            p.relative_to(FIXTURE / "source").as_posix(),
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_size,
        )
        for p in sorted((FIXTURE / "source").rglob("*"))
        if p.is_file()
    )
    raw = (FIXTURE / "inventory.json").read_bytes()
    graph = validate_inventory(raw, inputs)
    return ReferenceInventoryResult(
        graph, raw, hashlib.sha256(raw).hexdigest(), graph["root_sha256"], inputs
    )


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Run make check for Docker-backed persistence tests")
    schema = "test_rea_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migrate(connection)
        try:
            yield connection
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_preserve_raw_graph_id_and_replay(db, tmp_path):
    repo = Repository(db, "synthetic:rea")
    repo.create_system()
    repo.create_import("import_rea", {})
    repo.create_run("run_rea", "import_rea", {"providers": [REA_PROVIDER]})
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    kwargs = dict(
        run_id="run_rea",
        import_id="import_rea",
        collected_at="2026-10-07T12:00:00Z",
        masking_policy_version="synthetic-v1",
        job_id="job_rea",
        fence=1,
    )
    prepared = prepare_inventory(result(), store, **kwargs)
    first = persist_inventory(prepared, repo)
    assert persist_inventory(prepared, repo) == first
    with store.open(first.blob) as stream:
        assert stream.read() == result().raw_output
    record = db.execute("SELECT payload FROM evidence").fetchone()[0]
    assert record["upstream_record"]["upstream_id"] == result().upstream_id
    assert record["upstream_record"]["payload_artifact_id"] == first.artifact_id
    assert record["authority"] == "historical_reference"
    assert record["classification"] == "observed"
    assert record["provider"] == REA_PROVIDER
    assert record["source_locations"] == []
    assert record["metadata"]["inventory_state"] == "partial"
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    # No invented upstream Evidence records; the exact returned graph remains the raw artifact.
    assert "evidence" not in json.loads(result().raw_output)


def test_invalid_result_and_wrong_provider_rollback(db, tmp_path):
    repo = Repository(db, "synthetic:rea")
    repo.create_system()
    repo.create_import("import_rea", {})
    repo.create_run("run_rea", "import_rea", {"providers": []})
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    kwargs = dict(
        run_id="run_rea",
        import_id="import_rea",
        collected_at="2026-10-07T12:00:00Z",
        masking_policy_version="synthetic-v1",
        job_id="job_rea",
        fence=1,
    )
    with pytest.raises(ValueError):
        prepare_inventory(
            replace(result(), output_sha256="0" * 64), LocalArtifactStore(root), **kwargs
        )
    with pytest.raises(ValueError, match="PROVIDER_NOT_IN_RUN"):
        persist_inventory(prepare_inventory(result(), LocalArtifactStore(root), **kwargs), repo)
    assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_fenced_publication_has_no_filesystem_work_under_database_locks(db, tmp_path, monkeypatch):
    from psycopg.pq import TransactionStatus

    from laip.canonical import digest
    from laip.jobs import JobRepository, PublicationKey

    repo = Repository(db, "synthetic:rea")
    repo.create_system()
    repo.create_import("import_rea", {})
    repo.create_run(
        "run_rea",
        "import_rea",
        {
            "providers": [REA_PROVIDER],
            "configuration_sha256": "a" * 64,
        },
    )
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    original_put = store.put

    def checked_put(*args, **kwargs):
        assert db.info.transaction_status == TransactionStatus.IDLE
        return original_put(*args, **kwargs)

    monkeypatch.setattr(store, "put", checked_put)
    prepared = prepare_inventory(
        result(),
        store,
        run_id="run_rea",
        import_id="import_rea",
        collected_at="2026-10-07T12:00:00Z",
        masking_policy_version="synthetic-v1",
        job_id="job_rea",
        fence=1,
    )
    # A preexisting source occurrence anchors the durable provider checkpoint.
    input_artifact = {**prepared.artifact, "locator": "sealed-import/source-inventory"}
    input_artifact["artifact_id"] = "art_" + digest(
        {key: input_artifact[key] for key in ("import_id", "locator", "sha256")}
    )
    repo.put_blob(prepared.blob.sha256, prepared.blob.byte_length)
    repo.put_artifact(input_artifact)
    repo.attach_artifact("run_rea", input_artifact["artifact_id"])
    jobs = JobRepository(db)
    jobs.enqueue("job_rea", "run_rea", "analysis")
    lease = jobs.claim("worker")
    assert lease is not None
    key = PublicationKey(
        input_artifact["artifact_id"],
        REA_PROVIDER["id"],
        REA_PROVIDER["version"],
        REA_PROVIDER["source_revision"],
        "a" * 64,
        "reference_inventory",
    )
    calls = []

    def publish(connection):
        calls.append(connection)
        assert connection.info.transaction_status == TransactionStatus.INTRANS
        published = persist_inventory(prepared, repo)
        return {"artifact_id": published.artifact_id, "evidence_id": published.evidence_id}

    first = jobs.publish_checkpoint(lease, key, result().output_sha256, publish)
    assert jobs.publish_checkpoint(lease, key, result().output_sha256, publish) == first
    assert len(calls) == 1
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifact_results").fetchone()[0] == 1
