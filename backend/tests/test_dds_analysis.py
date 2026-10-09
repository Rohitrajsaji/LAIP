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
from laip.dds_analysis import DDS_PROVIDER, analyze_dds, persist_analysis, register_analysis
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from laip.source_import import ImportContext, persist_import, prepare_zip, register_import


def line(
    *, level="", name="", length="", dtype="", decimals="", usage="", row="", column="", keywords=""
):
    return (
        "     A"
        + " " * 10
        + level.ljust(1)
        + " "
        + name.ljust(10)
        + " "
        + length.rjust(5)
        + dtype.ljust(1)
        + decimals.rjust(2)
        + usage.ljust(1)
        + row.rjust(3)
        + column.rjust(3)
        + keywords
        + "\n"
    )


SOURCE = (
    line(level="R", name="CUSTOMER")
    + line(name="ID", length="10", dtype="A")
    + line(level="K", name="ID")
)


def imported(tmp_path, source=SOURCE, language="DDS", encoding="utf-8", extra_sources=()):
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    path = "LIB1/QDDSSRC/MAIN.dds"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(path, source.encode(encoding))
    manifest = {
        "schema_version": "0.1.0",
        "system_namespace": "synthetic:rpg",
        "system_display_name": "Synthetic DDS",
        "library_list": ["LIB1"],
        "default_encoding": encoding,
        "artifacts": [
            {
                "relative_path": path,
                "language": language,
                "dialect": "PF",
                "kind": "source",
                "source_member_identity": {
                    "system_namespace": "synthetic:rpg",
                    "kind": "SourceMember",
                    "qualified_identity": ["LIB1", "QDDSSRC", "MAIN"],
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
    if extra_sources:
        with zipfile.ZipFile(archive, "a") as zipped:
            for member, text in extra_sources:
                additional = {
                    **manifest["artifacts"][0],
                    "relative_path": f"LIB1/QDDSSRC/{member}.dds",
                    "dialect": "PF",
                    "language": "DDS-PF",
                    "source_member_identity": {
                        "system_namespace": "synthetic:rpg",
                        "kind": "SourceMember",
                        "qualified_identity": ["LIB1", "QDDSSRC", member],
                    },
                    "object_identity": {
                        "system_namespace": "synthetic:rpg",
                        "kind": "Table",
                        "qualified_identity": ["LIB1", "*FILE", member],
                    },
                }
                manifest["artifacts"].append(additional)
                zipped.writestr(additional["relative_path"], text.encode(encoding))
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


def test_analysis_original_column_spans_and_private_ir(tmp_path):
    prepared, store, root = imported(tmp_path)
    result = analyze_dds(prepared, root, store, run_id="rpg_run")
    assert result.ir["schema_version"] == "0.1.0"
    assert result.ir["definitions"]
    fields = [
        record for record in result.evidence if record["metadata"]["statement_kind"] == "field"
    ]
    assert len(fields) == 1
    column = fields[0]["metadata"]["column_source_locations"]["name"][0]
    assert column["start_column"] == 19
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
    result = analyze_dds(prepared, root, store, run_id="rpg_run")
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
        == "laip-dds-lark"
    )
    jobs = JobRepository(db)
    jobs.enqueue("rpg_job", result.run_id, "analysis")
    lease = jobs.claim("rpg_worker")
    assert lease
    key = PublicationKey(
        prepared.manifest_artifact["artifact_id"],
        DDS_PROVIDER["id"],
        DDS_PROVIDER["version"],
        None,
        result.configuration_sha256,
        "dds_analysis",
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
        analyze_dds(prepared, root, store, run_id="cancelled", cancelled=lambda: True)
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        analyze_dds(prepared, root, store, run_id="limited", max_input_bytes=1)
    with pytest.raises(ValueError, match="INVALID_ANALYSIS_RUN"):
        analyze_dds(prepared, root, store, run_id="import_run")
    with pytest.raises(ValueError, match="INVALID_ANALYSIS_LIMIT"):
        analyze_dds(prepared, root, store, run_id="bad_limit", timeout_seconds=0)
    other = tmp_path / "encoded"
    other.mkdir(mode=0o700)
    source = line(level="R", name="SCREEN") + line(
        name="LABEL", length="10", dtype="A", usage="O", row="1", column="2", keywords="TEXT('£')"
    )
    prepared, store, root = imported(other, source, "DDS-DSPF", "cp037")
    result = analyze_dds(prepared, root, store, run_id="encoded", file_type="display")
    field = next(
        record for record in result.evidence if record["metadata"]["statement_kind"] == "field"
    )
    column = field["metadata"]["column_source_locations"]["name"][0]
    assert column["byte_end"] - column["byte_start"] == 10
    assert field["source_locations"][0]["byte_end"] <= len(source.encode("cp037"))
    third = tmp_path / "wrong"
    third.mkdir(mode=0o700)
    prepared, store, root = imported(third, language="RPGLE")
    with pytest.raises(ValueError, match="UNSUPPORTED_ROOT_LANGUAGE"):
        analyze_dds(prepared, root, store, run_id="wrong_language")


def test_analysis_supplied_description_relationships(tmp_path):
    logical = line(level="R", name="VIEW", keywords="PFILE(LIB1/BASE)") + line(name="ID", usage="I")
    physical = line(level="R", name="BASE") + line(name="ID", length="10", dtype="A")
    prepared, store, root = imported(
        tmp_path, logical, "DDS-LF", extra_sources=(("BASE", physical),)
    )
    result = analyze_dds(prepared, root, store, run_id="logical", file_type="logical")
    assert result.ir["relationships"]
    assert any(relation["resolution"] == "resolved" for relation in result.ir["relationships"])
    artifact_ids = {item.artifact["artifact_id"] for item in prepared.items if item.artifact}
    assert all(
        set(relation["supporting_artifact_ids"]) <= artifact_ids
        for relation in result.ir["relationships"]
    )
    other = tmp_path / "missing"
    other.mkdir(mode=0o700)
    prepared, store, root = imported(other, logical, "DDS-LF")
    unresolved = analyze_dds(prepared, root, store, run_id="missing", file_type="logical")
    assert any(
        relation["resolution"] == "unresolved" for relation in unresolved.ir["relationships"]
    )


def test_analysis_continued_keywords_and_unknown_barrier(tmp_path):
    source = (
        line(level="R", name="REC")
        + line(name="LABEL", length="10", dtype="A", keywords="TEXT('a +")
        + line(keywords="b')")
    )
    prepared, store, root = imported(tmp_path, source)
    result = analyze_dds(prepared, root, store, run_id="continued")
    field = next(
        record for record in result.evidence if record["metadata"]["statement_kind"] == "field"
    )
    assert len(field["metadata"]["keyword_source_locations"]) == 2
    assert field["source_locations"][0]["end_line"] > field["source_locations"][0]["start_line"]
    other = tmp_path / "unsupported"
    other.mkdir(mode=0o700)
    prepared, store, root = imported(
        other, line(level="R", name="REC", keywords="UNKNOWN('not executed')")
    )
    unknown = analyze_dds(prepared, root, store, run_id="unknown")
    assert not unknown.ir["conclusions_allowed"]
    assert any(record["metadata"]["barrier"] for record in unknown.evidence)
