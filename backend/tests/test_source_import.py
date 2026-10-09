import hashlib
import io
import os
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.artifacts import LocalArtifactStore
from laip.migrations import migrate
from laip.persistence import Repository
from laip.source_import import (
    ImportContext,
    persist_import,
    prepare_local,
    prepare_zip,
    register_import,
    restore_import,
)

FIXTURE = Path(__file__).parent / "fixtures/import"


def archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted((FIXTURE / "source").rglob("*")):
            if path.is_file():
                output.writestr(path.relative_to(FIXTURE / "source").as_posix(), path.read_bytes())
    buffer.seek(0)
    return buffer


def context():
    return ImportContext(
        "import_fixture", "run_fixture", "2026-10-07T12:00:00Z", "synthetic-v1", "job_fixture", 1
    )


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Run make check for isolated Docker database")
    schema = "import_test_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migrate(connection)
        try:
            yield connection
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_zip_local_fixture_bytes_maps_statuses_and_replay(db, tmp_path):
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    options = dict(system_namespace="synthetic:import", store=store, context=context())
    zipped = prepare_zip(archive(), (FIXTURE / "manifest.json").read_bytes(), **options)
    local = prepare_local(
        "fixture",
        {"fixture": FIXTURE / "source"},
        (FIXTURE / "manifest.json").read_bytes(),
        **options,
    )
    assert len(zipped.items) == len(local.items) == 7
    assert [item.path for item in zipped.items] == [item.path for item in local.items]
    repo = Repository(db, "synthetic:import")
    register_import(zipped, repo)
    published = persist_import(zipped, repo)
    assert persist_import(zipped, repo) == published
    assert db.execute("SELECT count(*) FROM import_entries").fetchone()[0] == 7
    assert db.execute("SELECT count(*) FROM entities").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM origin_maps").fetchone()[0] == 7
    for item in zipped.items:
        assert item.artifact["processing_status"] in {"decoded", "imported"}
        raw = (FIXTURE / "source" / item.path).read_bytes()
        assert item.raw_blob.sha256 == hashlib.sha256(raw).hexdigest()
        with store.open(item.raw_blob) as stream:
            assert stream.read() == raw
        assert item.origin_map["lossy"] is False
    member = next(item for item in zipped.items if "LIB1" in item.path)
    with store.open(member.decoded_blob) as stream:
        assert (
            stream.read()
            == (FIXTURE / "source" / member.path).read_bytes().decode("cp037").encode()
        )
    assert member.origin_map["newline_policy"] == "preserved"


def test_missing_unsupported_excluded_and_unmanifested_have_status(tmp_path):
    import json
    import shutil

    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    shutil.copytree(FIXTURE / "source", source)
    (source / "unlisted.bin").write_bytes(b"\xff\x00")
    (source / "password.secret").write_bytes(b"private synthetic secret")
    (source / "sources/LIB2/QRPGLESRC/ORDER.rpgle").unlink()
    manifest = json.loads((FIXTURE / "manifest.json").read_bytes())
    manifest["artifacts"][0]["encoding"] = "unknown-ccsid"
    storage = tmp_path / "storage"
    storage.mkdir(mode=0o700)
    prepared = prepare_local(
        "fixture",
        {"fixture": source},
        json.dumps(manifest).encode(),
        system_namespace="synthetic:import",
        store=LocalArtifactStore(storage),
        context=context(),
    )
    items = {item.path: item for item in prepared.items}
    assert items["unlisted.bin"].status == "unsupported"
    assert items["password.secret"].status == "excluded"
    assert items["sources/LIB1/QRPGLESRC/ORDER.rpgle"].status == "unsupported"
    assert items["sources/LIB2/QRPGLESRC/ORDER.rpgle"].status == "failed"
    assert items["sources/LIB2/QRPGLESRC/ORDER.rpgle"].scope_state == "missing"
    assert items["password.secret"].raw_blob is None
    assert prepared.partial


def test_fenced_import_publication_replays_without_filesystem_under_locks(
    db, tmp_path, monkeypatch
):
    from psycopg.pq import TransactionStatus

    from laip.jobs import JobRepository, PublicationKey
    from laip.source_import import IMPORT_PROVIDER

    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    original = store.put

    def checked_put(*args, **kwargs):
        assert db.info.transaction_status == TransactionStatus.IDLE
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "put", checked_put)
    prepared = prepare_zip(
        archive(),
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=store,
        context=context(),
    )
    repo = Repository(db, "synthetic:import")
    register_import(prepared, repo)
    jobs = JobRepository(db)
    assert db.execute("SELECT state FROM imports").fetchone()[0] == "staging"
    jobs.enqueue("job_fixture", "run_fixture", "import")
    assert db.execute("SELECT state FROM imports").fetchone()[0] == "queued"
    lease = jobs.claim("worker")
    assert lease is not None
    assert db.execute("SELECT state FROM imports").fetchone()[0] == "running"
    assert register_import(prepared, repo)["run_id"] == "run_fixture"
    key = PublicationKey(
        prepared.manifest_artifact["artifact_id"],
        IMPORT_PROVIDER["id"],
        IMPORT_PROVIDER["version"],
        None,
        prepared.configuration_sha256,
        "seal_import",
    )
    callbacks = []

    def publish(connection):
        callbacks.append(connection)
        assert connection.info.transaction_status == TransactionStatus.INTRANS
        return persist_import(prepared, repo)

    first = jobs.publish_checkpoint(lease, key, prepared.configuration_sha256, publish)
    assert jobs.publish_checkpoint(lease, key, prepared.configuration_sha256, publish) == first
    assert len(callbacks) == 1
    assert db.execute("SELECT state FROM imports").fetchone()[0] == "running"
    jobs.finish(lease, "succeeded")
    assert db.execute("SELECT state FROM imports").fetchone()[0] == "sealed"
    assert db.execute("SELECT count(*) FROM import_entries").fetchone()[0] == 7
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 7


def test_namespace_and_import_history_are_enforced(db, tmp_path):
    tmp_path.chmod(0o700)
    prepared = prepare_zip(
        archive(),
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=LocalArtifactStore(tmp_path),
        context=context(),
    )
    repo = Repository(db, "synthetic:import")
    with pytest.raises(ValueError, match="NOT_REGISTERED"):
        persist_import(prepared, repo)
    with pytest.raises(ValueError, match="NAMESPACE"):
        register_import(prepared, Repository(db, "another:namespace"))
    register_import(prepared, repo)
    persist_import(prepared, repo)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE import_entries SET scope_state='excluded'")
    other = Repository(db, "another:namespace")
    other.create_system()
    other.create_import("foreign_import", {})
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO import_entries(import_id,locator,artifact_id,scope_state,payload,"
            "system_namespace) "
            "VALUES('foreign_import','foreign',%s,'supplied','{}','another:namespace')",
            (prepared.items[0].artifact["artifact_id"],),
        )


def test_invalid_metadata_retains_exact_raw_bytes_and_failed_status(tmp_path):
    import json

    tmp_path.chmod(0o700)
    manifest = json.loads((FIXTURE / "manifest.json").read_bytes())
    manifest["artifacts"] = [manifest["artifacts"][2]]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr(
            "metadata/inventory.json", b'{"schema_version":"0.1.0","kind":"job","records":[]}'
        )
    buffer.seek(0)
    store = LocalArtifactStore(tmp_path)
    prepared = prepare_zip(
        buffer,
        json.dumps(manifest).encode(),
        system_namespace="synthetic:import",
        store=store,
        context=context(),
    )
    item = prepared.items[0]
    assert item.status == "failed"
    assert item.metadata is None
    with store.open(item.raw_blob) as stream:
        assert b'"kind":"job"' in stream.read()
    assert prepared.partial


def test_cancel_before_publication(tmp_path):
    tmp_path.chmod(0o700)
    with pytest.raises(ValueError, match="(?i)cancel"):
        prepare_zip(
            archive(),
            (FIXTURE / "manifest.json").read_bytes(),
            system_namespace="synthetic:import",
            store=LocalArtifactStore(tmp_path),
            context=context(),
            cancelled=lambda: True,
        )


def test_restart_uses_durable_input_plan_not_changing_original_source(db, tmp_path):
    import shutil

    from laip.jobs import JobRepository, PublicationKey
    from laip.source_import import IMPORT_PROVIDER

    source = tmp_path / "source"
    shutil.copytree(FIXTURE / "source", source)
    storage = tmp_path / "storage"
    storage.mkdir(mode=0o700)
    store = LocalArtifactStore(storage)
    prepared = prepare_local(
        "fixture",
        {"fixture": source},
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=store,
        context=context(),
    )
    repo = Repository(db, "synthetic:import")
    register_import(prepared, repo)
    original_sha = prepared.items[-1].raw_blob.sha256
    del prepared
    shutil.rmtree(source)
    # A new real database connection represents a restarted worker process.
    search_path = db.execute("SHOW search_path").fetchone()[0]
    with psycopg.connect(os.environ["LAIP_TEST_DATABASE_URL"], autocommit=True) as restarted:
        restarted.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(search_path)))
        repository = Repository(restarted, "synthetic:import")
        restored = restore_import("run_fixture", repository, store)
        assert restored.items[-1].raw_blob.sha256 == original_sha
        jobs = JobRepository(restarted)
        jobs.enqueue("job_fixture", "run_fixture", "import")
        lease = jobs.claim("worker_restarted")
        key = PublicationKey(
            restored.manifest_artifact["artifact_id"],
            IMPORT_PROVIDER["id"],
            IMPORT_PROVIDER["version"],
            None,
            restored.configuration_sha256,
            "seal_import",
        )
        result = jobs.publish_checkpoint(
            lease,
            key,
            restored.configuration_sha256,
            lambda _: persist_import(restored, repository),
        )
        jobs.finish(lease, result["outcome"])
        assert restarted.execute("SELECT state FROM imports").fetchone()[0] == "sealed"
        assert restarted.execute("SELECT count(*) FROM import_entries").fetchone()[0] == 7
        assert restarted.execute("SELECT count(*) FROM import_input_blobs").fetchone()[0] >= 8


@pytest.mark.parametrize("outcome", ["cancelled", "failed", "partial"])
def test_import_lifecycle_failure_cancellation_and_partial(db, tmp_path, outcome):
    from laip.jobs import JobRepository

    tmp_path.chmod(0o700)
    prepared = prepare_zip(
        archive(),
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=LocalArtifactStore(tmp_path),
        context=context(),
    )
    repo = Repository(db, "synthetic:import")
    register_import(prepared, repo)
    jobs = JobRepository(db)
    jobs.enqueue("job_fixture", "run_fixture", "import")
    if outcome == "cancelled":
        jobs.cancel("job_fixture", "operator")
    else:
        lease = jobs.claim("worker")
        jobs.finish(lease, outcome)
    assert db.execute("SELECT state FROM imports").fetchone()[0] == outcome
    assert db.execute("SELECT count(*) FROM import_entries").fetchone()[0] == 0


def test_corrupt_durable_input_blob_is_rejected(db, tmp_path):
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    prepared = prepare_zip(
        archive(),
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=store,
        context=context(),
    )
    repo = Repository(db, "synthetic:import")
    register_import(prepared, repo)
    path = store.root / prepared.items[0].raw_blob.storage_key
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="digest|length"):
        restore_import("run_fixture", repo, store)


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
def test_final_plan_write_cannot_accept_expired_or_cancelled_input(
    tmp_path, monkeypatch, interrupt
):
    from laip import source_import
    from laip.import_readers import ImportLimits

    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    put = store.put
    current = [0.0]
    cancelled = [False]
    monkeypatch.setattr(source_import.time, "monotonic", lambda: current[0])

    def delayed_put(stream, **kwargs):
        result = put(stream, **kwargs)
        if kwargs["max_bytes"] == 32 * 1024 * 1024:
            current[0] = 10.0
            cancelled[0] = True
        return result

    monkeypatch.setattr(store, "put", delayed_put)
    with pytest.raises(
        ValueError, match="PREPARATION_TIMEOUT" if interrupt == "timeout" else "CANCELLED"
    ):
        prepare_zip(
            archive(),
            (FIXTURE / "manifest.json").read_bytes(),
            system_namespace="synthetic:import",
            store=store,
            context=context(),
            limits=ImportLimits(timeout_seconds=1 if interrupt == "timeout" else 120),
            cancelled=lambda: cancelled[0],
        )
