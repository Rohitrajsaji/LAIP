import copy
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb
from test_persistence import db as db_fixture
from test_retrieval import published

from laip.artifacts import LocalArtifactStore
from laip.canonical import canonical, loads, record_id
from laip.exports import (
    ExportLimits,
    archive_bytes,
    build_export,
    publish_export,
    validate_bundle,
)
from laip.masking import POLICY
from laip.persistence import validate
from laip.retrieval import RetrievalService

db = db_fixture
OPTIONS = {"export_id": "export_fixture", "created_at": "2026-10-08T10:00:00Z"}


def test_snapshot_export_deterministic_linked_and_source_absent(db):
    repo, original = published(db)
    first = build_export(repo, "snap_retrieval", **OPTIONS)
    second = build_export(repo, "snap_retrieval", **OPTIONS)
    assert first.files == second.files
    assert first.manifest == second.manifest
    validate("ExportManifest", first.manifest)
    validate_bundle(first)
    assert first.manifest["source_included"] is False
    assert not any(path.startswith("source/") for path in first.files)
    assert {
        "manifest.json",
        "knowledge.json",
        "evidence/index.jsonl",
        "context/chunks.jsonl",
        "graph.json",
        "graph.mmd",
        "workflow/index.md",
    } <= set(first.files)
    assert first.manifest["providers"] == original["runs"][0]["providers"]
    assert "review_sequence" in first.files["knowledge.json"].decode()
    for item in first.manifest["files"]:
        value = first.files[item["relative_path"]]
        assert item["sha256"] == hashlib.sha256(value).hexdigest()
        assert item["byte_length"] == len(value)
    assert archive_bytes(first) == archive_bytes(second)


def test_export_hashes_links_and_paths_fail_closed(db):
    repo, _ = published(db)
    bundle = build_export(repo, "snap_retrieval", **OPTIONS)
    for path, value in [("graph.json", b"tampered"), ("../escape", b"bad")]:
        corrupt = copy.deepcopy(bundle)
        corrupt.files[path] = value
        with pytest.raises(ValueError):
            validate_bundle(corrupt)


def test_export_limits_cancellation_and_source_policy(db):
    repo, _ = published(db)
    with pytest.raises(ValueError, match="SOURCE_ACCESS_DENIED"):
        build_export(repo, "snap_retrieval", source_included=True, **OPTIONS)
    with pytest.raises(ValueError, match="CANCELLED"):
        build_export(repo, "snap_retrieval", cancelled=lambda: True, **OPTIONS)
    with pytest.raises(ValueError, match="EXPORT_LIMIT"):
        build_export(repo, "snap_retrieval", limits=ExportLimits(max_bytes=10), **OPTIONS)


def test_archive_publication_is_private_verified_blob(db, tmp_path):
    repo, _ = published(db)
    bundle = build_export(repo, "snap_retrieval", **OPTIONS)
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    ref = publish_export(bundle, store, job_id="export_fixture", fence=1)
    value = archive_bytes(bundle)
    assert ref.sha256 == hashlib.sha256(value).hexdigest()
    with zipfile.ZipFile(io.BytesIO(value)) as archive:
        assert set(archive.namelist()) == set(bundle.files)
        assert all(info.external_attr >> 16 & 0o777 == 0o600 for info in archive.infolist())


def test_full_source_requires_capability_and_retains_exact_raw_bytes(db):
    repo, _ = published(db)
    root = Path(__file__).resolve().parents[2] / "docs/contracts/0.1.0"

    def reader(record):
        return (
            root / ("VALIDATE.rpgle" if record["source_member_id"] else "relations.example.json")
        ).read_bytes()

    source = build_export(
        repo, "snap_retrieval", source_included=True, source_reader=reader, **OPTIONS
    )
    assert source.manifest["source_included"]
    index = loads(source.files["source/index.json"].decode())
    assert len(index["artifacts"]) == 2
    for artifact in index["artifacts"]:
        assert (
            hashlib.sha256(source.files[artifact["relative_path"]]).hexdigest()
            == artifact["sha256"]
        )
    with pytest.raises(ValueError, match="INVALID_DIGEST"):
        build_export(
            repo, "snap_retrieval", source_included=True, source_reader=lambda _: b"bad", **OPTIONS
        )
    # Even an authorized reader is not called without the explicit source option.
    build_export(
        repo, "snap_retrieval", source_reader=lambda _: pytest.fail("source read"), **OPTIONS
    )


def test_masking_escaping_excerpt_bounds_and_context_links(db, monkeypatch):
    repo, original = published(db)
    eid = original["evidence"][0]["evidence_id"]
    repo.put_chunk(
        "chunk_sensitive",
        "snap_retrieval",
        "password=hidden Bearer hidden-token " + "a" * 100,
        [eid],
        POLICY,
        "v1",
        "none",
    )
    collect = RetrievalService.collect

    def hostile(self, **kwargs):
        result = collect(self, **kwargs)
        for record in result["records"]:
            if record["type"] == "entity":
                record["payload"]["display_name"] = (
                    '<script>%%{init}%% " ] --> [x](https://bad) password=hidden'
                )
            if record["type"] == "evidence":
                record["payload"]["excerpt"] = "password=hidden " + "a" * 100
        return result

    monkeypatch.setattr(RetrievalService, "collect", hostile)
    export = build_export(repo, "snap_retrieval", limits=ExportLimits(excerpt_chars=30), **OPTIONS)
    all_text = b"".join(export.files.values()).decode()
    assert "hidden" not in all_text
    assert "<script>" not in export.files["graph.mmd"].decode()
    assert "%%{init}" not in export.files["graph.mmd"].decode()
    assert (
        "[x](https://bad)"
        not in b"".join(v for k, v in export.files.items() if k.endswith(".md")).decode()
    )
    contexts = [loads(line.decode()) for line in export.files["context/chunks.jsonl"].splitlines()]
    assert contexts and all(c["links"] for c in contexts)
    assert all(len(c["view"]["text"]) <= 30 and c["limitations"] for c in contexts)
    evidence = [
        loads(value.decode())["view"]
        for path, value in export.files.items()
        if path.startswith("evidence/") and path.endswith(".json")
    ]
    assert evidence and all(len(e["display_excerpt"]) <= 30 for e in evidence)


def test_semantically_broken_link_rejected_even_with_recomputed_checksum(db):
    repo, _ = published(db)
    bundle = build_export(repo, "snap_retrieval", **OPTIONS)
    record = loads(bundle.files["graph.json"].decode())
    record["links"].append("nonexistent.json")
    bundle.files["graph.json"] = canonical(record)
    for item in bundle.manifest["files"]:
        if item["relative_path"] == "graph.json":
            item["sha256"] = hashlib.sha256(bundle.files["graph.json"]).hexdigest()
            item["byte_length"] = len(bundle.files["graph.json"])
    bundle.files["manifest.json"] = canonical(bundle.manifest)
    with pytest.raises(ValueError, match="INVALID_LINK"):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "path", ["knowledge.json", "graph.json", "evidence/index.jsonl", "context/chunks.jsonl"]
)
def test_export_structure_rejected_even_with_recomputed_hashes(db, path):
    repo, original = published(db)
    repo.put_chunk(
        "chunk_fixture",
        "snap_retrieval",
        "fixture chunk",
        [original["evidence"][0]["evidence_id"]],
        POLICY,
        "v1",
        "none",
    )
    bundle = build_export(repo, "snap_retrieval", **OPTIONS)
    bundle.files[path] = canonical({"schema_version": "0.1.0", "unexpected": "invalid"}) + (
        b"\n" if path.endswith("jsonl") else b""
    )
    for item in bundle.manifest["files"]:
        if item["relative_path"] == path:
            item["sha256"] = hashlib.sha256(bundle.files[path]).hexdigest()
            item["byte_length"] = len(bundle.files[path])
    bundle.files["manifest.json"] = canonical(bundle.manifest)
    with pytest.raises(ValueError, match="INVALID_EXPORT_SCHEMA"):
        validate_bundle(bundle)


def test_knowledge_nested_link_and_graph_consistency_rejected(db):
    repo, _ = published(db)
    for path in ("knowledge.json", "graph.json"):
        bundle = build_export(repo, "snap_retrieval", **OPTIONS)
        record = loads(bundle.files[path].decode())
        if path == "knowledge.json":
            record["records"][0]["relative_path"] = "missing.json"
        else:
            record["nodes"][0]["display_name"] = "unsupported changed name"
        bundle.files[path] = canonical(record)
        for item in bundle.manifest["files"]:
            if item["relative_path"] == path:
                item["sha256"] = hashlib.sha256(bundle.files[path]).hexdigest()
                item["byte_length"] = len(bundle.files[path])
        bundle.files["manifest.json"] = canonical(bundle.manifest)
        with pytest.raises(ValueError, match="INVALID_LINK"):
            validate_bundle(bundle)


def test_snapshot_workflow_is_exported_with_static_pending_certainty(db):
    repo, original = published(db)
    entity = next(e for e in original["entities"] if e["identity"]["kind"] == "Workflow")
    program = next(e for e in original["entities"] if e["identity"]["kind"] == "Program")
    workflow = {
        "schema_version": "0.1.0",
        "workflow_entity_id": entity["entity_id"],
        "run_id": original["runs"][0]["run_id"],
        "program_entity_ids": [program["entity_id"]],
        "dependency_ids": [],
        "evidence_ids": entity["evidence_ids"],
        "classification": "inferred",
        "review_status": "pending_review",
        "limitations": ["Possible static path only."],
        "created_at": OPTIONS["created_at"],
    }
    workflow["workflow_id"] = record_id("wf_", workflow, "workflow_id")
    db.execute(
        "INSERT INTO workflow_revisions VALUES(%s,%s,%s,%s,%s)",
        (
            workflow["workflow_id"],
            workflow["run_id"],
            entity["entity_id"],
            repo.namespace,
            Jsonb(workflow),
        ),
    )
    export = build_export(repo, "snap_retrieval", **OPTIONS)
    paths = [
        path for path in export.files if path.startswith("workflow/") and path.endswith(".json")
    ]
    assert paths
    record = next(
        loads(export.files[path].decode())
        for path in paths
        if loads(export.files[path].decode())["type"] == "workflow"
    )
    assert record["view"] == workflow
    assert record["classification"] == "inferred" and record["review_status"] == "pending_review"
    assert record["links"]


def test_invalid_export_options_and_limits(db):
    repo, _ = published(db)
    with pytest.raises(ValueError, match="INVALID_EXPORT_LIMIT"):
        ExportLimits(max_files=True)
    with pytest.raises(ValueError, match="INVALID_EXPORT_REQUEST"):
        build_export(repo, "snap_retrieval", export_id="", created_at=OPTIONS["created_at"])
    with pytest.raises(ValueError, match="EXPORT_LIMIT"):
        build_export(repo, "snap_retrieval", limits=ExportLimits(max_records=1), **OPTIONS)
    with pytest.raises(ValueError, match="EXPORT_LIMIT"):
        build_export(repo, "snap_retrieval", limits=ExportLimits(max_files=1), **OPTIONS)
