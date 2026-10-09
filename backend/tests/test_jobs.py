"""Real PostgreSQL lease, concurrency and publication regression tests."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from laip.jobs import JobRepository, LeaseLost, NondeterministicResult, PublicationKey
from laip.migrations import migrate


@pytest.fixture
def database():
    url = os.getenv("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL")
    schema = "jobs_test_" + uuid4().hex
    connection = psycopg.connect(url, autocommit=True)
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    migrate(connection)
    connection.execute("INSERT INTO systems VALUES('system',clock_timestamp())")
    connection.execute("INSERT INTO imports VALUES('imp','system',%s,'{}','sealed')", ("a" * 64,))
    connection.execute(
        "INSERT INTO runs(run_id,system_namespace,import_id,payload,state) "
        "VALUES('run','system','imp',%s,'queued')",
        (Jsonb({"configuration_sha256": "a" * 64}),),
    )
    for provider in ["parser", "p"]:
        connection.execute(
            "INSERT INTO "
            "run_providers(run_id,provider_id,provider_version,source_revision,payloa"
            "d) VALUES('run',%s,'1',NULL,'{}')",
            (provider,),
        )
    connection.execute("INSERT INTO blobs(sha256,byte_length) VALUES(%s,1)", ("a" * 64,))
    connection.execute(
        "INSERT INTO artifacts(artifact_id,system_namespace,import_id,locator,"
        "sha256,byte_length,payload) VALUES('art','system','imp','a',%s,1,'{}')",
        ("a" * 64,),
    )
    connection.execute("INSERT INTO run_artifacts VALUES('run','art','system','imported')")
    yield connection, url, schema
    connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    connection.close()


def expire(connection):
    connection.execute(
        "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE state='running'"
    )


def claim(repository):
    lease = repository.claim("worker")
    assert lease is not None
    return lease


def test_restart_checkpoint_fencing_and_exhaustion(database):
    connection, url, schema = database
    repo = JobRepository(connection)
    assert repo.claim("worker") is None
    repo.enqueue("job", "run", "analysis")
    lease = claim(repo)
    assert lease.lease_token.startswith(f"{lease.fence}:")
    assert repo.heartbeat(lease)
    with pytest.raises(ValueError):
        repo.heartbeat(lease, 0)
    with pytest.raises(ValueError):
        repo.finish(lease, "unknown")
    with pytest.raises(ValueError):
        repo.publish_export(lease, "a" * 64, lambda _: {})
    with pytest.raises(ValueError):
        repo.publish_checkpoint(
            lease, PublicationKey("missing", "p", "1", None, "a" * 64, "x"), "a" * 64, lambda _: {}
        )
    key = PublicationKey("art", "parser", "1", None, "a" * 64, "analysis")

    def publish(conn):
        conn.execute(
            "INSERT INTO audit_events(actor_id,action,resource_id,payload) "
            "VALUES('worker','publication','art','{}')"
        )
        return {"result": "record"}

    assert repo.publish_checkpoint(lease, key, "c" * 64, publish) == {"result": "record"}
    assert repo.publish_checkpoint(lease, key, "c" * 64, publish) == {"result": "record"}
    with pytest.raises(NondeterministicResult):
        repo.publish_checkpoint(lease, key, "d" * 64, publish)
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
    expire(connection)
    assert not repo.heartbeat(lease)
    assert repo.recover() == 1
    assert repo.recover() == 0
    assert repo.claim("worker") is None
    with pytest.raises(LeaseLost):
        repo.finish(lease, "succeeded")
    with pytest.raises(LeaseLost):
        repo.publish_checkpoint(lease, key, "c" * 64, publish)
    with psycopg.connect(url, autocommit=True) as restarted:
        restarted.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        new_repo = JobRepository(restarted)
        restarted.execute("UPDATE jobs SET available_at=clock_timestamp()")
        lease2 = claim(new_repo)
        assert lease2.fence > lease.fence
        assert new_repo.publish_checkpoint(lease2, key, "c" * 64, publish) == {"result": "record"}
        assert restarted.execute("SELECT count(*) FROM artifact_results").fetchone()[0] == 1
        expire(restarted)
        assert new_repo.recover() == 1
        restarted.execute("UPDATE jobs SET available_at=clock_timestamp()")
        lease3 = claim(new_repo)
        assert lease3.attempt == 3
        expire(restarted)
        assert new_repo.recover() == 1
        assert new_repo.claim("worker") is None
    assert connection.execute("SELECT state FROM runs WHERE run_id='run'").fetchone()[0] == "failed"
    assert connection.execute("SELECT count(*) FROM job_attempts").fetchone()[0] == 3


def test_concurrent_claim_and_cancel_publication(database):
    connection, url, schema = database
    repo = JobRepository(connection)
    repo.enqueue("job", "run", "analysis")

    def compete(worker):
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            return JobRepository(conn).claim(worker)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(compete, ["one", "two"]))
    lease = next(value for value in leases if value is not None)
    assert sum(value is not None for value in leases) == 1
    assert repo.cancel("job", "operator request") == "cancel_requested"
    assert not repo.heartbeat(lease)
    key = PublicationKey("art", "parser", "1", None, "a" * 64, "analysis")
    with pytest.raises(LeaseLost):
        repo.publish_checkpoint(lease, key, "b" * 64, lambda _: {})
    assert repo.finish(lease, "succeeded") == "cancelled"
    assert repo.cancel("job", "again") == "cancelled"
    with pytest.raises(LeaseLost):
        repo.finish(lease, "failed")


def test_cancel_expired_and_queued(database):
    connection, _, _ = database
    repo = JobRepository(connection)
    repo.enqueue("queued", "run", "analysis")
    assert repo.cancel("queued", "stop") == "cancelled"
    with pytest.raises(KeyError):
        repo.cancel("absent", "stop")
    connection.execute(
        "INSERT INTO runs(run_id,system_namespace,import_id,payload,state) "
        "VALUES('next_run','system','imp','{}','queued')"
    )
    repo.enqueue("running", "next_run", "analysis")
    lease = claim(repo)
    repo.cancel("running", "stop")
    expire(connection)
    assert repo.recover() == 1
    with pytest.raises(LeaseLost):
        repo.finish(lease, "cancelled")


def test_export_and_retention_do_not_mutate_completed_basis(database):
    connection, _, _ = database
    connection.execute("UPDATE runs SET state='succeeded',completed_at=clock_timestamp()")
    connection.execute("INSERT INTO snapshots VALUES('snap','system','run',%s,'{}',0)", ("a" * 64,))
    connection.execute(
        "INSERT INTO exports(export_id,run_id,snapshot_id,options_sha256,"
        "masking_policy_version) VALUES('exp','run','snap',%s,'1')",
        ("a" * 64,),
    )
    connection.execute("INSERT INTO retention_operations(retention_id,run_id) VALUES('ret','run')")
    repo = JobRepository(connection)
    repo.enqueue("export", "run", "export", "exp")
    export = claim(repo)
    assert repo.publish_export(export, "b" * 64, lambda _: {"manifest": "bundle"}) == {
        "manifest": "bundle"
    }
    assert repo.publish_export(export, "b" * 64, lambda _: {}) == {"manifest": "bundle"}
    with pytest.raises(NondeterministicResult):
        repo.publish_export(export, "c" * 64, lambda _: {})
    with pytest.raises(ValueError):
        repo.publish_checkpoint(
            export, PublicationKey("art", "p", "1", None, "a" * 64, "x"), "b" * 64, lambda _: {}
        )
    assert repo.finish(export, "partial") == "partial"
    with pytest.raises(ValueError, match="terminal"):
        repo.cancel("export", "stop")
    repo.enqueue("retention", "run", "retention", "ret")
    retention = claim(repo)
    expire(connection)
    repo.recover()
    connection.execute("UPDATE jobs SET available_at=clock_timestamp()")
    retention = claim(repo)
    assert repo.cancel("retention", "stop") == "cancel_requested"
    assert repo.finish(retention, "succeeded") == "cancelled"
    assert (
        connection.execute("SELECT state FROM runs WHERE run_id='run'").fetchone()[0] == "succeeded"
    )


def test_invalid_and_owner_terminal(database):
    connection, _, _ = database
    repo = JobRepository(connection)
    with pytest.raises(ValueError):
        repo.enqueue("x", "run", "unsupported")
    with pytest.raises(ValueError):
        repo.enqueue("x", "run", "analysis", "another")
    with pytest.raises(ValueError):
        repo.enqueue("x", "run", "export", "missing")
    with pytest.raises(ValueError):
        repo.claim("", 0)
    with pytest.raises(ValueError):
        repo.cancel("x", "")
    repo.enqueue("job", "run", "analysis")
    connection.execute("UPDATE runs SET state='failed',completed_at=clock_timestamp()")
    assert repo.claim("worker") is None
    assert connection.execute("SELECT state FROM jobs").fetchone()[0] == "cancelled"


def test_retry_releases_fence_and_cancel_wins(database):
    connection, _, _ = database
    repo = JobRepository(connection)
    repo.enqueue("job", "run", "analysis")
    original = claim(repo)
    assert repo.retry(original) == "retry_wait"
    assert not repo.heartbeat(original)
    connection.execute("UPDATE jobs SET available_at=clock_timestamp()")
    current = claim(repo)
    assert current.fence > original.fence
    repo.cancel("job", "operator")
    assert repo.retry(current) == "cancelled"


def test_three_explicit_failures_exhaust(database):
    connection, _, _ = database
    repo = JobRepository(connection)
    repo.enqueue("job", "run", "analysis")
    for attempt in range(1, 4):
        connection.execute("UPDATE jobs SET available_at=clock_timestamp()")
        lease = claim(repo)
        assert lease.attempt == attempt
        assert repo.retry(lease) == ("failed" if attempt == 3 else "retry_wait")


def test_checkpoint_survives_new_worker_process(database):
    import subprocess
    import sys

    connection, url, schema = database
    JobRepository(connection).enqueue("job", "run", "analysis")
    script = """import os
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from laip.migrations import migrate
from laip.jobs import JobRepository, PublicationKey
with psycopg.connect(os.environ['LAIP_TEST_DATABASE_URL'],autocommit=True) as conn:
 schema=sql.Identifier(os.environ['JOB_TEST_SCHEMA'])
 conn.execute(sql.SQL('SET search_path TO {}').format(schema))
 repo=JobRepository(conn)
 lease=repo.claim('fresh-process')
 assert lease is not None
 key=PublicationKey('art','p','1',None,'a'*64,'parse')
 repo.publish_checkpoint(lease,key,'b'*64,lambda _: {'record':'stable'})
 repo.retry(lease)
"""
    env = dict(os.environ, LAIP_TEST_DATABASE_URL=url, JOB_TEST_SCHEMA=schema)
    for _ in range(2):
        connection.execute("UPDATE jobs SET available_at=clock_timestamp()")
        subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
    assert connection.execute("SELECT count(*) FROM artifact_results").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM job_attempts").fetchone()[0] == 2


def test_owner_uniqueness_and_frozen_publication_profile(database):
    from dataclasses import replace

    connection, _, _ = database
    repo = JobRepository(connection)
    repo.enqueue("job", "run", "analysis")
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.enqueue("another", "run", "analysis")
    lease = claim(repo)
    key = PublicationKey("art", "parser", "1", None, "a" * 64, "analysis")
    for changed in [
        replace(key, provider_id="other"),
        replace(key, provider_version="2"),
        replace(key, source_revision="new"),
        replace(key, configuration_sha256="b" * 64),
    ]:
        with pytest.raises(ValueError, match="PROVENANCE"):
            repo.publish_checkpoint(lease, changed, "c" * 64, lambda _: {})
    assert connection.execute("SELECT count(*) FROM artifact_results").fetchone()[0] == 0
