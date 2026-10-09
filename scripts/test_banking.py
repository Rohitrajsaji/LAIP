"""Private corpus through durable jobs in disposable PostgreSQL/blob scopes."""

import argparse
import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path

import psycopg
from laip.analyst_jobs import AnalystJobs
from laip.analyst_service import AnalystService
from laip.banking_corpus import load_corpus, upload_payload
from laip.config import Settings
from laip.exports import ExportBundle, validate_bundle
from laip.migrations import migrate
from laip.source_import import restore_import
from psycopg import sql


def check(database_url: str, directory: Path) -> dict:
    corpus = load_corpus(directory)
    schema = "banking_" + uuid.uuid4().hex
    namespace = "test:banking:" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="laip-private-banking-") as temporary:
        root = Path(temporary).resolve()
        root.chmod(0o700)
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            try:
                migrate(conn)
                settings = Settings(
                    database_url=database_url,
                    database_url_file=None,
                    service_token=uuid.uuid4().hex,
                    service_token_file=None,
                    artifact_root=root,
                    analyst_namespace=namespace,
                    ai_enabled=False,
                    embeddings_enabled=False,
                    ai_operator_approved=False,
                    private_ai_token_file=None,
                )
                jobs = AnalystJobs(conn, settings)
                accepted = jobs.accept_import(
                    *upload_payload(corpus, namespace=namespace)
                )
                assert jobs.execute_one()
                prepared = restore_import(
                    accepted["run_id"], jobs.repository, jobs.store
                )
                assert len(prepared.items) == 4
                for item in prepared.items:
                    with jobs.store.open(item.raw_blob) as stream:
                        assert stream.read() == corpus[item.path]
                    assert (
                        item.origin_map
                        and item.artifact["sha256"]
                        == hashlib.sha256(corpus[item.path]).hexdigest()
                    )
                    with jobs.store.open(item.decoded_blob) as stream:
                        decoded = stream.read()
                    assert decoded == corpus[item.path]
                    for segment in item.origin_map["segments"]:
                        assert (
                            decoded[
                                segment["decoded_byte_start"] : segment[
                                    "decoded_byte_end"
                                ]
                            ]
                            == corpus[item.path][
                                segment["raw_byte_start"] : segment["raw_byte_end"]
                            ]
                        )
                queued = jobs.queue_analysis(accepted["run_id"])
                jobs = AnalystJobs(conn, settings)
                assert jobs.execute_one()
                row = conn.execute(
                    "SELECT state FROM runs WHERE run_id=%s", (queued["run_id"],)
                ).fetchone()
                assert row[0] == "partial"
                snapshot = conn.execute(
                    "SELECT snapshot_id FROM snapshots WHERE run_id=%s",
                    (queued["run_id"],),
                ).fetchone()[0]
                overflow_artifact = next(
                    item.artifact["artifact_id"]
                    for item in prepared.items
                    if item.path == "ACCT_DSPF.dspf"
                )
                evidence = [
                    r[0]
                    for r in conn.execute(
                        "SELECT payload FROM evidence WHERE run_id=%s",
                        (queued["run_id"],),
                    )
                ]
                raw_by_artifact = {
                    item.artifact["artifact_id"]: corpus[item.path]
                    for item in prepared.items
                }
                for ev in evidence:
                    for loc in ev["source_locations"]:
                        raw = raw_by_artifact[loc["artifact_id"]]
                        assert 0 <= loc["byte_start"] <= loc["byte_end"] <= len(raw)
                        if "decoded_byte_start" not in loc:
                            continue
                        assert (
                            raw[loc["byte_start"] : loc["byte_end"]]
                            == raw[loc["decoded_byte_start"] : loc["decoded_byte_end"]]
                        )
                        start = loc["decoded_byte_start"]
                        prefix = raw[:start].decode("utf-8")
                        assert prefix.count("\n") + 1 == loc["start_line"]
                assert any(
                    ev["metadata"].get("barrier")
                    and ev["metadata"].get("diagnostics")
                    and any(
                        loc["artifact_id"] == overflow_artifact
                        and loc["start_line"] <= 26 <= loc["end_line"]
                        for loc in ev["source_locations"]
                    )
                    for ev in evidence
                )
                service = AnalystService(conn, settings)
                exported = jobs.queue_export(
                    snapshot, source_included=False, schema_version="0.2.0"
                )
                assert jobs.execute_one()
                stream, _ = service.download(exported["export_id"])
                with stream:
                    package = stream.read()
                with zipfile.ZipFile(io.BytesIO(package)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    validate_bundle(
                        ExportBundle(
                            manifest,
                            {name: archive.read(name) for name in archive.namelist()},
                        )
                    )
                    assert manifest["source_included"] is False
                    assert manifest["schema_version"] == "0.2.0"
                    assert not any(
                        name.startswith("source/") for name in archive.namelist()
                    )
                    for entry in manifest["files"]:
                        assert (
                            hashlib.sha256(
                                archive.read(entry["relative_path"])
                            ).hexdigest()
                            == entry["sha256"]
                        )
                counts = {
                    table: conn.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE run_id=%s").format(
                            sql.Identifier(table)
                        ),
                        (queued["run_id"],),
                    ).fetchone()[0]
                    for table in (
                        "evidence",
                        "rule_revisions",
                        "dependencies",
                        "workflow_revisions",
                    )
                }
                return {
                    "schema_disposed": schema,
                    "namespace": namespace,
                    "run_id": queued["run_id"],
                    "snapshot_id": snapshot,
                    "state": row[0],
                    "supplied_artifacts": 4,
                    "counts": counts,
                    "source_included": False,
                    "ai_enabled": False,
                    "dependency_relationships": [
                        r[0]
                        for r in conn.execute(
                            "SELECT DISTINCT relationship FROM dependencies WHERE run_id=%s ORDER BY relationship",
                            (queued["run_id"],),
                        ).fetchall()
                    ],
                    "conditional_rule_revisions": conn.execute(
                        "SELECT count(*) FROM rule_revisions WHERE run_id=%s AND jsonb_array_length(payload->'conditions') > 0",
                        (queued["run_id"],),
                    ).fetchone()[0],
                }
            finally:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir", type=Path, default=os.environ.get("LAIP_BANKING_FIXTURE_DIR")
    )
    args = parser.parse_args()
    if args.fixture_dir is None or not os.environ.get("LAIP_TEST_DATABASE_URL"):
        raise SystemExit(
            "Set LAIP_TEST_DATABASE_URL to isolated test PostgreSQL and supply --fixture-dir"
        )
    print(
        json.dumps(
            check(os.environ["LAIP_TEST_DATABASE_URL"], args.fixture_dir),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
