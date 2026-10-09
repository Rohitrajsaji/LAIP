"""Real ZIP/local fixture acceptance against an isolated Docker PostgreSQL schema.

Reads inert fixture bytes only. Never executes source, checkout, setup, or hooks.
"""

import hashlib
import io
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import psycopg
from laip.artifacts import LocalArtifactStore
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from laip.source_import import (
    IMPORT_PROVIDER,
    ImportContext,
    persist_import,
    prepare_local,
    prepare_zip,
    register_import,
    restore_import,
)
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend/tests/fixtures/import"


def check_connector(database_url: str, connector: str) -> None:
    schema = "import_acceptance_" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="laip-import-acceptance-") as temporary:
        staging = Path(temporary).resolve(strict=True)
        staging.chmod(0o700)
        source = staging / "source"
        shutil.copytree(FIXTURE / "source", source)
        expected = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        }
        artifact_root = staging / "artifacts"
        artifact_root.mkdir(mode=0o700)
        store = LocalArtifactStore(artifact_root)
        context = ImportContext(
            "import_fixture",
            "run_fixture",
            "2026-10-07T12:00:00Z",
            "synthetic-v1",
            "job_fixture",
            1,
        )
        options = {
            "system_namespace": "synthetic:import",
            "store": store,
            "context": context,
        }
        manifest = (FIXTURE / "manifest.json").read_bytes()
        if connector == "zip":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, raw in sorted(expected.items()):
                    archive.writestr(path, raw)
            buffer.seek(0)
            prepared = prepare_zip(buffer, manifest, **options)
        else:
            prepared = prepare_local(
                "fixture", {"fixture": source}, manifest, **options
            )
        if len(prepared.items) != len(expected) or prepared.partial:
            raise SystemExit("Fixture scope or import outcome is incomplete")
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
            )
            try:
                connection.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
                )
                migrate(connection)
                register_import(prepared, Repository(connection, "synthetic:import"))
                original_configuration = prepared.configuration_sha256
                del prepared
                shutil.rmtree(source)
                # New connection restores the durable plan without the original source tree.
                with psycopg.connect(database_url, autocommit=True) as restarted:
                    restarted.execute(
                        sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
                    )
                    repository = Repository(restarted, "synthetic:import")
                    restored = restore_import("run_fixture", repository, store)
                    if restored.configuration_sha256 != original_configuration:
                        raise SystemExit(
                            "Restart changed the frozen import configuration"
                        )
                    for item in restored.items:
                        if item.raw_blob is None or item.status not in {
                            "imported",
                            "decoded",
                        }:
                            raise SystemExit(
                                "Fixture artifact lacks bytes or explicit usable status"
                            )
                        raw = expected[item.path]
                        if item.raw_blob.sha256 != hashlib.sha256(raw).hexdigest():
                            raise SystemExit("Fixture raw digest changed")
                        with store.open(item.raw_blob) as stream:
                            if stream.read() != raw:
                                raise SystemExit("Fixture raw bytes changed")
                        if item.origin_map is None or item.origin_map["lossy"]:
                            raise SystemExit("Fixture source mapping missing or lossy")
                    jobs = JobRepository(restarted)
                    jobs.enqueue("job_fixture", "run_fixture", "import")
                    lease = jobs.claim("acceptance_worker")
                    if lease is None:
                        raise SystemExit("Durable import job could not be claimed")
                    key = PublicationKey(
                        restored.manifest_artifact["artifact_id"],
                        IMPORT_PROVIDER["id"],
                        IMPORT_PROVIDER["version"],
                        None,
                        restored.configuration_sha256,
                        "seal_import",
                    )
                    publications = []

                    def publish(_):
                        publications.append(True)
                        return persist_import(restored, repository)

                    first = jobs.publish_checkpoint(
                        lease, key, restored.configuration_sha256, publish
                    )
                    replay = jobs.publish_checkpoint(
                        lease, key, restored.configuration_sha256, publish
                    )
                    if replay != first or len(publications) != 1:
                        raise SystemExit(
                            "Fenced publication replay changed or duplicated records"
                        )
                    jobs.finish(lease, first["outcome"])
                    if persist_import(restored, repository) != first:
                        raise SystemExit("Repository replay changed the sealed import")
                    counts = restarted.execute(
                        "SELECT (SELECT count(*) FROM import_entries),"
                        "(SELECT count(*) FROM evidence), (SELECT count(*) FROM origin_maps),"
                        "(SELECT count(*) FROM artifact_results), (SELECT state FROM imports)"
                    ).fetchone()
                    if counts != (
                        len(expected),
                        len(expected),
                        len(expected),
                        1,
                        "sealed",
                    ):
                        raise SystemExit(
                            "Fixture history duplicated or import did not seal"
                        )
            finally:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )


def main() -> None:
    database_url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Use make import-check to provide the isolated Docker test database"
        )
    check_connector(database_url, "zip")
    check_connector(database_url, "local")
    print(
        "ZIP and configured local fixtures passed: raw bytes and source maps preserved; "
        "durable plans restored without original sources; fenced PostgreSQL replay stayed unique; "
        "no imported code, setup, hooks, or checkout commands executed."
    )


if __name__ == "__main__":
    main()
