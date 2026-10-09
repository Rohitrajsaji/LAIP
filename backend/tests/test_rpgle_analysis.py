import io
import json
import os
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.artifacts import LocalArtifactStore
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from laip.rpgle_analysis import RPGLE_PROVIDER, analyze_rpgle, persist_analysis, register_analysis
from laip.source_import import ImportContext, persist_import, prepare_zip, register_import

FIXTURE = Path(__file__).parent / "fixtures/rpgle"


def imported(tmp_path, name="MAIN"):
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    artifacts = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        for member in [name, "CONSTANTS"]:
            path = f"LIB1/QRPGLESRC/{member}.rpgle"
            zipped.writestr(path, (FIXTURE / f"{member}.rpgle").read_bytes())
            artifacts.append(
                {
                    "relative_path": path,
                    "language": "RPGLE",
                    "dialect": "fully_free",
                    "kind": "source",
                    "source_member_identity": {
                        "system_namespace": "synthetic:rpg",
                        "kind": "SourceMember",
                        "qualified_identity": ["LIB1", "QRPGLESRC", member],
                    },
                    "object_identity": None,
                    "encoding": "utf-8",
                    "collection_method": "fixture",
                    "collected_at": "2026-10-07T12:00:00Z",
                }
            )
    manifest = {
        "schema_version": "0.1.0",
        "system_namespace": "synthetic:rpg",
        "system_display_name": "Synthetic RPG",
        "library_list": ["LIB1"],
        "default_encoding": "utf-8",
        "artifacts": artifacts,
        "application_memberships": [],
        "exclusions": [],
        "limits": {"max_files": 100, "max_bytes": 1000000},
    }
    archive.seek(0)
    prepared = prepare_zip(
        archive,
        json.dumps(manifest).encode(),
        system_namespace="synthetic:rpg",
        store=store,
        context=ImportContext(
            "rpg_import", "import_run", "2026-10-07T12:00:00Z", "synthetic-v1", "import_job", 1
        ),
    )
    return prepared, store, artifacts[0]["relative_path"]


def test_fixture_analysis_include_origins_and_opaque_barrier(tmp_path):
    prepared, store, root = imported(tmp_path)
    result = analyze_rpgle(prepared, root, store, run_id="rpg_run")
    assert result.ir["schema_version"] == "0.1.0"
    assert result.ir["nodes"]
    kinds = {node["statement"]["kind"] for node in result.ir["nodes"]}
    assert kinds >= {
        "declaration",
        "procedure_start",
        "if",
        "dow",
        "assignment",
        "monitor",
        "chain",
        "update",
        "on_error",
        "call",
        "return",
        "procedure_end",
    }
    declarations = {
        node["statement"]["attributes"]["name"]
        for node in result.ir["nodes"]
        if node["statement"]["kind"] == "declaration"
    }
    assert declarations >= {"increment", "customerId", "balance", "CUSTOMER"}
    assert any(edge["kind"] == "repeat" for edge in result.ir["edges"])
    assert any(edge["kind"] == "exception" for edge in result.ir["edges"])
    paths = {
        span["artifact_id"] for evidence in result.evidence for span in evidence["source_locations"]
    }
    assert len(paths) == 2
    assert any(
        evidence["metadata"].get("statement_kind") == "chain" for evidence in result.evidence
    )
    with store.open(result.blob) as stream:
        assert json.loads(stream.read())["ir"] == result.ir
    other = tmp_path / "unsupported"
    other.mkdir(mode=0o700)
    prepared, store, root = imported(other, "UNSUPPORTED")
    result = analyze_rpgle(prepared, root, store, run_id="rpg_unknown")
    assert result.ir["conclusions_allowed"] is False
    assert result.ir["barriers"]


def test_analysis_durable_evidence_replay(db, tmp_path):
    prepared, store, root = imported(tmp_path)
    repo = Repository(db, "synthetic:rpg")
    register_import(prepared, repo)
    persist_import(prepared, repo)
    db.execute("UPDATE imports SET state='sealed'")
    result = analyze_rpgle(prepared, root, store, run_id="rpg_run")
    register_analysis(result, prepared, repo)
    jobs = JobRepository(db)
    jobs.enqueue("rpg_job", result.run_id, "analysis")
    lease = jobs.claim("rpg_worker")
    assert lease
    key = PublicationKey(
        prepared.manifest_artifact["artifact_id"],
        "laip-rpgle-lark",
        RPGLE_PROVIDER["version"],
        None,
        result.configuration_sha256,
        "rpgle_analysis",
    )
    calls = []

    def publish(_):
        calls.append(True)
        return persist_analysis(result, prepared, repo)

    first = jobs.publish_checkpoint(lease, key, result.configuration_sha256, publish)
    assert jobs.publish_checkpoint(lease, key, result.configuration_sha256, publish) == first
    assert calls == [True]
    jobs.finish(lease, first["outcome"])
    assert persist_analysis(result, prepared, repo) == first
    assert db.execute("SELECT count(*) FROM evidence WHERE run_id='rpg_run'").fetchone()[0] == len(
        result.evidence
    )


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Docker PostgreSQL required")
    schema = "rpg_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            migrate(connection)
            yield connection
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_analysis_limits_and_missing_include_are_explicit(tmp_path):
    prepared, store, root = imported(tmp_path)
    with pytest.raises(ValueError, match="CANCELLED"):
        analyze_rpgle(prepared, root, store, run_id="cancelled", cancelled=lambda: True)
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        analyze_rpgle(prepared, root, store, run_id="limited", max_input_bytes=1)
    from dataclasses import replace

    value = replace(prepared, items=tuple(item for item in prepared.items if item.path == root))
    result = analyze_rpgle(value, root, store, run_id="missing_include")
    assert "INCLUDE_MISSING" in result.ir["include_diagnostics"]
    assert not result.ir["conclusions_allowed"]
    assert any(record["metadata"]["barrier"] for record in result.evidence)
