import io
import json
import os
import uuid
import zipfile
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql

from laip.artifacts import LocalArtifactStore
from laip.cl_analysis import analyze_cl, persist_analysis, register_analysis
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from laip.source_import import ImportContext, persist_import, prepare_zip, register_import

SOURCE = """PGM
DCL VAR(&COUNT) TYPE(*DEC) LEN(5 0)
DCL VAR(&TARGET) TYPE(*CHAR) LEN(10)
CHGVAR VAR(&COUNT) VALUE(1)
IF COND(&COUNT *GT 0) THEN(DO)
CALL PGM(LIB1/STATIC) +
 PARM('a''b')
ENDDO
CALL PGM(&TARGET)
UNKNOWNCMD VALUE('never run')
ENDPGM
"""


def imported(tmp_path, source=SOURCE, language="CLLE", encoding="utf-8"):
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    path = "LIB1/QCLSRC/MAIN.clle"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(path, source.encode(encoding))
    manifest = {
        "schema_version": "0.1.0",
        "system_namespace": "synthetic:rpg",
        "system_display_name": "Synthetic CL",
        "library_list": ["LIB1"],
        "default_encoding": encoding,
        "artifacts": [
            {
                "relative_path": path,
                "language": language,
                "dialect": "clle",
                "kind": "source",
                "source_member_identity": {
                    "system_namespace": "synthetic:rpg",
                    "kind": "SourceMember",
                    "qualified_identity": ["LIB1", "QCLSRC", "MAIN"],
                },
                "object_identity": None,
                "encoding": encoding,
                "collection_method": "fixture",
                "collected_at": "2026-10-07T12:00:00Z",
            }
        ],
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
    return prepared, store, path


def test_analysis_raw_continuation_spans_and_unknown_barrier(tmp_path):
    prepared, store, root = imported(tmp_path)
    result = analyze_cl(prepared, root, store, run_id="rpg_run")
    assert result.ir["schema_version"] == "0.1.0"
    assert not result.ir["conclusions_allowed"]
    assert result.ir["references"]
    calls = [record for record in result.evidence if record["metadata"]["statement_kind"] == "call"]
    assert len(calls) == 2
    assert (
        calls[0]["source_locations"][0]["end_line"] > calls[0]["source_locations"][0]["start_line"]
    )
    assert calls[1]["metadata"]["references"]
    assert all(
        record["classification"] == "observed" and record["excerpt"] is None
        for record in result.evidence
    )
    with store.open(result.blob) as stream:
        assert json.loads(stream.read())["ir"] == result.ir


def test_analysis_durable_evidence_replay(db, tmp_path):
    prepared, store, root = imported(tmp_path)
    repo = Repository(db, "synthetic:rpg")
    register_import(prepared, repo)
    persist_import(prepared, repo)
    db.execute("UPDATE imports SET state='sealed'")
    result = analyze_cl(prepared, root, store, run_id="rpg_run")
    register_analysis(result, prepared, repo)
    with pytest.raises(ValueError, match="ANALYSIS_BASIS_MISMATCH"):
        register_analysis(replace(result, configuration_sha256="f" * 64), prepared, repo)
    with pytest.raises(ValueError, match="ANALYSIS_INPUT_MISMATCH"):
        register_analysis(replace(result, input_configuration_sha256="f" * 64), prepared, repo)
    with pytest.raises(ValueError, match="ANALYSIS_NAMESPACE_MISMATCH"):
        register_analysis(replace(result, system_namespace="other"), prepared, repo)
    with pytest.raises(ValueError, match="ANALYSIS_OUTPUT_MISMATCH"):
        persist_analysis(
            replace(result, artifact={**result.artifact, "byte_length": 0}), prepared, repo
        )
    assert (
        db.execute("SELECT payload FROM runs WHERE run_id='rpg_run'").fetchone()[0]["providers"][0][
            "id"
        ]
        == "laip-cl-lark"
    )
    jobs = JobRepository(db)
    jobs.enqueue("rpg_job", result.run_id, "analysis")
    lease = jobs.claim("rpg_worker")
    assert lease
    key = PublicationKey(
        prepared.manifest_artifact["artifact_id"],
        "laip-cl-lark",
        "0.1.0",
        None,
        result.configuration_sha256,
        "cl_analysis",
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


def test_analysis_limits_languages_and_original_encoding(tmp_path):
    prepared, store, root = imported(tmp_path)
    with pytest.raises(ValueError, match="CANCELLED"):
        analyze_cl(prepared, root, store, run_id="cancelled", cancelled=lambda: True)
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        analyze_cl(prepared, root, store, run_id="limited", max_input_bytes=1)
    with pytest.raises(ValueError, match="INVALID_ANALYSIS_RUN"):
        analyze_cl(prepared, root, store, run_id="import_run")
    with pytest.raises(ValueError, match="INVALID_ANALYSIS_LIMIT"):
        analyze_cl(prepared, root, store, run_id="bad_limit", timeout_seconds=0)
    other = tmp_path / "encoded"
    other.mkdir(mode=0o700)
    source = "PGM\nSNDPGMMSG MSG('£')\nENDPGM\n"
    prepared, store, root = imported(other, source, "CL", "cp037")
    result = analyze_cl(prepared, root, store, run_id="encoded")
    message = next(
        record
        for record in result.evidence
        if record["metadata"]["statement_kind"] == "message_send"
    )
    span = message["source_locations"][0]
    assert span["byte_end"] - span["byte_start"] == len("SNDPGMMSG MSG('£')".encode("cp037"))
    third = tmp_path / "wrong"
    third.mkdir(mode=0o700)
    prepared, store, root = imported(third, language="RPGLE")
    with pytest.raises(ValueError, match="UNSUPPORTED_ROOT_LANGUAGE"):
        analyze_cl(prepared, root, store, run_id="wrong_language")
