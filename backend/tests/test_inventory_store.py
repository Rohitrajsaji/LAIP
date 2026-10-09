import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.artifacts import LocalArtifactStore
from laip.inventory import build_inventory
from laip.inventory_store import persist_inventory, register_inventory
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from laip.source_import import ImportContext, persist_import, prepare_local, register_import

FIXTURE = Path(__file__).parent / "fixtures/import"


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Docker PostgreSQL required")
    schema = "inventory_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            migrate(connection)
            yield connection
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_inventory_fenced_replay_accounting_and_history(db, tmp_path):
    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    shutil.copytree(FIXTURE / "source", source)
    manifest = json.loads((FIXTURE / "manifest.json").read_bytes())

    def identity(kind, *parts):
        return {
            "system_namespace": "synthetic:import",
            "kind": kind,
            "qualified_identity": list(parts),
        }

    program = identity("Program", "LIB1", "*PGM", "ENTRY")
    manifest["artifacts"][0]["object_identity"] = program
    manifest["application_memberships"] = [
        {
            "application_identity": identity("Application", "ORDER_APP"),
            "member_identity": manifest["artifacts"][0]["source_member_identity"],
            "evidence_path": manifest["artifacts"][0]["relative_path"],
        }
    ]
    records = [
        {"record_type": "entity", "identity": identity("Program", lib, "*PGM", "TARGET")}
        for lib in ["LIB1", "LIB2"]
    ]
    records += [
        {
            "record_type": "reference",
            "from_identity": program,
            "relationship": "CALLS",
            "target": {"kind": "Program", "name": "TARGET", "library": None, "dynamic": False},
        },
        {
            "record_type": "reference",
            "from_identity": program,
            "relationship": "READS",
            "target": {"kind": "Table", "name": "dynamicFile", "library": None, "dynamic": True},
        },
    ]
    (source / "metadata/inventory.json").write_text(
        json.dumps({"schema_version": "0.1.0", "kind": "inventory", "records": records})
    )
    prepared = prepare_local(
        "fixture",
        {"fixture": source},
        json.dumps(manifest).encode(),
        system_namespace="synthetic:import",
        store=LocalArtifactStore(tmp_path),
        context=ImportContext(
            "imp", "import_run", "2026-10-07T12:00:00Z", "synthetic-v1", "import_job", 1
        ),
    )
    repo = Repository(db, "synthetic:import")
    register_import(prepared, repo)
    result = build_inventory(prepared, run_id="inventory_run")
    with pytest.raises(ValueError, match="IMPORT_NOT_READY"):
        register_inventory(result, prepared, repo)
    persist_import(prepared, repo)
    db.execute("UPDATE imports SET state='sealed' WHERE import_id='imp'")
    register_inventory(result, prepared, repo)
    jobs = JobRepository(db)
    jobs.enqueue("inventory_job", "inventory_run", "analysis")
    lease = jobs.claim("inventory_worker")
    assert lease
    key = PublicationKey(
        prepared.manifest_artifact["artifact_id"],
        result.provider["id"],
        result.provider["version"],
        None,
        result.configuration_sha256,
        "inventory",
    )
    calls = []

    def publish(_):
        calls.append(True)
        return persist_inventory(result, prepared, repo)

    first = jobs.publish_checkpoint(lease, key, result.configuration_sha256, publish)
    assert jobs.publish_checkpoint(lease, key, result.configuration_sha256, publish) == first
    assert len(calls) == 1
    assert {edge["resolution"] for edge in result.dependencies} >= {
        "ambiguous",
        "dynamic",
        "resolved",
    }
    memberships = [edge for edge in result.dependencies if edge["relationship"] == "MEMBER_OF"]
    assert {edge["classification"] for edge in memberships} >= {"observed", "inferred"}
    inferred = next(edge for edge in memberships if edge["classification"] == "inferred")
    review = repo.review(
        "dependency",
        inferred["dependency_id"],
        None,
        "rejected",
        "fixture",
        "outside application scope",
    )
    assert review
    jobs.finish(lease, first["outcome"])
    assert db.execute("SELECT count(*) FROM inventory_reports").fetchone()[0] == 1
    payload = db.execute("SELECT payload FROM inventory_reports").fetchone()[0]
    assert len(payload["accounting"]) == len(prepared.items)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE inventory_reports SET payload='{}'")
    with pytest.raises(ValueError, match="ACCOUNTING_MISMATCH"):
        persist_inventory(replace(result, accounting=result.accounting[:-1]), prepared, repo)
    bad_entity = dict(result.entities[0])
    bad_entity["parent_id"] = "ent_" + "f" * 64
    invalid = replace(result, entities=(bad_entity, *result.entities[1:]))
    with pytest.raises(ValueError, match="PARENT_MISSING"):
        persist_inventory(invalid, prepared, repo)
    with pytest.raises(ValueError, match="INPUT_BASIS"):
        register_inventory(result, replace(prepared, configuration_sha256="0" * 64), repo)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO inventory_reports VALUES("
            "'inventory_run','imp','synthetic:import',%s,'{}')",
            ("0" * 64,),
        )
    foreign = Repository(db, "other:system")
    foreign.create_system()
    foreign.create_import("foreign_import", {})
    foreign.create_run("foreign_inventory", "foreign_import", repo._run(result.run_id))
    with pytest.raises(ValueError, match="BASIS_MISMATCH"):
        register_inventory(replace(result, run_id="foreign_inventory"), prepared, repo)
    again = build_inventory(prepared, run_id="inventory_again")
    register_inventory(again, prepared, repo)
    persist_inventory(again, prepared, repo)
    assert db.execute("SELECT count(*) FROM inventory_reports").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM entities").fetchone()[0] == len(result.entities)
    with pytest.raises(ValueError, match="NAMESPACE"):
        register_inventory(result, prepared, Repository(db, "other:system"))
