import io
import json
import os
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.analyst_jobs import AnalystJobs
from laip.config import Settings
from laip.migrations import migrate


@pytest.fixture
def pipeline(tmp_path):
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Docker PostgreSQL required")
    schema = "analyst_" + uuid.uuid4().hex
    tmp_path.chmod(0o700)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            migrate(conn)
            settings = Settings(database_url=url, service_token="a" * 32, artifact_root=tmp_path)
            yield AnalystJobs(conn, settings), conn
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def upload(namespace="local:workspace"):
    manifest = json.loads((Path(__file__).parent / "fixtures/import/manifest.json").read_bytes())
    manifest["system_namespace"] = namespace
    manifest["artifacts"] = [manifest["artifacts"][1]]
    entry = manifest["artifacts"][0]
    entry["source_member_identity"]["system_namespace"] = namespace
    entry["object_identity"] = {
        "system_namespace": namespace,
        "kind": "Program",
        "qualified_identity": ["LIB2", "*PGM", "ORDER"],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(
            entry["relative_path"],
            "**free\ndcl-s amount packed(9:2);\nif amount > 0;\namount = amount + 1;\nendif;\n",
        )
    return stream.getvalue(), manifest


def test_import_analysis_snapshot_export_and_restart(pipeline):
    jobs, conn = pipeline
    accepted = jobs.accept_import(*upload())
    assert accepted["job_id"] and accepted["import_id"]
    assert jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM jobs WHERE job_id=%s", (accepted["job_id"],)).fetchone()[0]
        == "succeeded"
    )
    queued = jobs.queue_analysis(accepted["run_id"])
    # Recreate the handler to prove accepted inputs and options are durable.
    jobs = AnalystJobs(conn, jobs.settings)
    assert jobs.execute_one()
    state = conn.execute("SELECT state FROM jobs WHERE job_id=%s", (queued["job_id"],)).fetchone()[
        0
    ]
    assert state in ("succeeded", "partial")
    snapshot = conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE run_id=%s", (queued["run_id"],)
    ).fetchone()[0]
    assert (
        conn.execute(
            "SELECT count(*) FROM evidence WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        > 3
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM rule_revisions WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        >= 1
    )
    assert (
        conn.execute("SELECT count(*) FROM chunks WHERE snapshot_id=%s", (snapshot,)).fetchone()[0]
        > 0
    )
    exported = jobs.queue_export(snapshot, schema_version="0.2.0")
    assert jobs.execute_one()
    result = conn.execute(
        "SELECT payload FROM export_results WHERE export_id=%s", (exported["export_id"],)
    ).fetchone()[0]
    assert result["sha256"] and result["byte_length"] > 100
    assert not jobs.execute_one()


def test_cancel_and_namespace_and_source_policy(pipeline):
    jobs, conn = pipeline
    with pytest.raises(ValueError):
        jobs.accept_import(*upload("other:workspace"))
    accepted = jobs.accept_import(*upload())
    jobs.jobs.cancel(accepted["job_id"], "operator requested")
    assert not jobs.execute_one()
    assert conn.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2
    with pytest.raises(ValueError):
        jobs.queue_analysis(accepted["run_id"])
    with pytest.raises(ValueError, match="SOURCE_ACCESS_DENIED"):
        jobs.queue_export("missing", source_included=True)


def test_malformed_zip_rejected_before_enqueue(pipeline):
    jobs, conn = pipeline
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        jobs.accept_import(b"not zip", upload()[1])
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_all_languages_dynamic_paths_and_repeat_history(pipeline):
    import base64

    from laip.analyst_fixture import fixture_payload

    jobs, conn = pipeline
    fixture = fixture_payload(jobs.repository.namespace)
    accepted = jobs.accept_import(base64.b64decode(fixture["archive_base64"]), fixture["manifest"])
    jobs.execute_one()
    first = jobs.queue_analysis(accepted["run_id"])
    jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM runs WHERE run_id=%s", (first["run_id"],)).fetchone()[0]
        == "partial"
    )
    assert {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT provider_id FROM evidence WHERE run_id=%s", (first["run_id"],)
        ).fetchall()
    } >= {"laip-rpgle-lark", "laip-cl-lark", "laip-dds-lark"}
    assert (
        conn.execute(
            "SELECT count(*) FROM dependencies WHERE run_id=%s "
            "AND payload->>'resolution'='dynamic'",
            (first["run_id"],),
        ).fetchone()[0]
        > 0
    )
    second = jobs.queue_analysis(accepted["run_id"])
    jobs.execute_one()
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
    assert (
        conn.execute(
            "SELECT count(*) FROM rule_revisions WHERE run_id=%s "
            "AND previous_revision_id IS NOT NULL",
            (second["run_id"],),
        ).fetchone()[0]
        > 0
    )


def test_expired_claim_recovery_and_active_cancel(pipeline, monkeypatch):
    from laip import analyst_jobs

    jobs, conn = pipeline
    accepted = jobs.accept_import(*upload())
    lease = jobs.jobs.claim("crashed-worker", authorized_job_ids=[accepted["job_id"]])
    assert lease
    conn.execute(
        "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
        (lease.job_id,),
    )
    jobs.jobs.recover()
    conn.execute("UPDATE jobs SET available_at=clock_timestamp() WHERE job_id=%s", (lease.job_id,))
    assert jobs.execute_one()
    assert (
        conn.execute("SELECT attempt FROM jobs WHERE job_id=%s", (lease.job_id,)).fetchone()[0] == 2
    )
    queued = jobs.queue_analysis(accepted["run_id"])
    original = analyst_jobs.analyze_rpgle

    def cancelling(*args, **kwargs):
        jobs.jobs.cancel(queued["job_id"], "Cancel active analysis")
        return original(*args, **kwargs)

    monkeypatch.setattr(analyst_jobs, "analyze_rpgle", cancelling)
    assert jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM jobs WHERE job_id=%s", (queued["job_id"],)).fetchone()[0]
        == "cancelled"
    )
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT count(*) FROM evidence WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        == 0
    )


def test_snapshot_failure_rolls_back_publication(pipeline, monkeypatch):
    jobs, conn = pipeline
    accepted = jobs.accept_import(*upload())
    jobs.execute_one()
    queued = jobs.queue_analysis(accepted["run_id"])

    def fail_snapshot(run_id):
        raise ValueError("Invalid selected snapshot")

    monkeypatch.setattr(jobs, "_snapshot", fail_snapshot)
    assert jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM jobs WHERE job_id=%s", (queued["job_id"],)).fetchone()[0]
        == "failed"
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM artifact_results WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM rule_revisions WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        == 0
    )


def test_cancel_racing_fenced_checkpoint_finishes_immediately(pipeline, monkeypatch):
    jobs, conn = pipeline
    accepted = jobs.accept_import(*upload())
    jobs.execute_one()
    queued = jobs.queue_analysis(accepted["run_id"])
    original = jobs.jobs.publish_checkpoint

    def race(lease, *args, **kwargs):
        jobs.jobs.cancel(lease.job_id, "Cancel at publication boundary")
        return original(lease, *args, **kwargs)

    monkeypatch.setattr(jobs.jobs, "publish_checkpoint", race)
    assert jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM jobs WHERE job_id=%s", (queued["job_id"],)).fetchone()[0]
        == "cancelled"
    )
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT count(*) FROM evidence WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        == 0
    )


def test_input_limits_unknown_snapshot_and_unhandled_language(pipeline):
    jobs, conn = pipeline
    with pytest.raises(ValueError, match="IMPORT_ARCHIVE_LIMIT"):
        jobs.accept_import(b"", upload()[1])
    oversized = upload()[1]
    oversized["system_display_name"] = "x" * (1024 * 1024)
    with pytest.raises(ValueError, match="MANIFEST_LIMIT"):
        jobs.accept_import(upload()[0], oversized)
    with pytest.raises(ValueError, match="SNAPSHOT_NOT_FOUND"):
        jobs.queue_export("missing")
    archive, manifest = upload()
    manifest["artifacts"][0]["language"] = "COBOL"
    accepted = jobs.accept_import(archive, manifest)
    jobs.execute_one()
    queued = jobs.queue_analysis(accepted["run_id"])
    jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM runs WHERE run_id=%s", (queued["run_id"],)).fetchone()[0]
        == "partial"
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM snapshots WHERE run_id=%s", (queued["run_id"],)
        ).fetchone()[0]
        == 1
    )


def test_worker_does_not_claim_another_operator_namespace(pipeline):
    jobs, conn = pipeline
    settings = jobs.settings.model_copy(update={"analyst_namespace": "other:workspace"})
    other = AnalystJobs(conn, settings)
    accepted = other.accept_import(*upload("other:workspace"))
    assert not jobs.execute_one()
    assert (
        conn.execute("SELECT state FROM jobs WHERE job_id=%s", (accepted["job_id"],)).fetchone()[0]
        == "queued"
    )
    assert other.execute_one()
