"""Bounded inert CL analysis with private IR and source-backed evidence."""

import re
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

from laip.artifacts import LocalArtifactStore
from laip.canonical import canonical, digest, record_id
from laip.cl_ir import build_ir
from laip.cl_parser import parse_statements
from laip.persistence import Repository, validate
from laip.rpgle_analysis import PreparedAnalysis
from laip.rpgle_source import ExpandedSource, SourceUnit, span_locations, span_origins
from laip.source_import import PreparedImport

CL_PROVIDER = {"id": "laip-cl-lark", "version": "0.1.0", "source_revision": None}


def analyze_cl(
    prepared: PreparedImport,
    root_path: str,
    store: LocalArtifactStore,
    *,
    run_id: str,
    cancelled: Callable[[], bool] | None = None,
    max_input_bytes: int = 32 * 1024 * 1024,
    timeout_seconds: int = 120,
) -> PreparedAnalysis:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) or run_id == prepared.context.run_id:
        raise ValueError("INVALID_ANALYSIS_RUN")
    if (
        type(max_input_bytes) is not int
        or max_input_bytes < 1
        or type(timeout_seconds) is not int
        or timeout_seconds < 1
    ):
        raise ValueError("INVALID_ANALYSIS_LIMIT")
    deadline = time.monotonic() + timeout_seconds

    def check() -> bool:
        if time.monotonic() >= deadline:
            raise ValueError("ANALYSIS_TIMEOUT")
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        return False

    declared = {entry["relative_path"]: entry for entry in prepared.manifest["artifacts"]}
    inputs: list[dict[str, Any]] = []
    root = None
    total = 0
    artifacts = {prepared.manifest_artifact["artifact_id"]: prepared.manifest_artifact}
    for item in prepared.items:
        check()
        entry = declared.get(item.path)
        if item.path != root_path:
            continue
        if not entry or entry["kind"] != "source" or entry["source_member_identity"] is None:
            continue
        if item.decoded_blob is None or item.origin_map is None or item.artifact is None:
            continue
        total += item.decoded_blob.byte_length
        if total > max_input_bytes:
            raise ValueError("ANALYSIS_INPUT_LIMIT")
        with store.open(item.decoded_blob) as stream:
            decoded = stream.read(max_input_bytes + 1)
        unit = SourceUnit(entry["source_member_identity"], item.artifact, decoded, item.origin_map)
        inputs.append(
            {
                "artifact_id": item.artifact["artifact_id"],
                "decoded_sha256": item.decoded_blob.sha256,
            }
        )
        artifacts[item.artifact["artifact_id"]] = item.artifact
        if item.path == root_path:
            if entry["language"] is None or entry["language"].casefold() not in ("cl", "clle"):
                raise ValueError("UNSUPPORTED_ROOT_LANGUAGE")
            root = unit
    if root is None:
        raise ValueError("CL_SOURCE_UNAVAILABLE")
    text = root.decoded.decode("utf-8", errors="strict")
    expanded = ExpandedSource(
        text,
        (
            {
                "expanded_start": 0,
                "expanded_end": len(text),
                "unit": root,
                "source_start": 0,
                "source_end": len(text),
            },
        )
        if text
        else (),
        (),
        (),
    )
    statements = parse_statements(expanded.text, cancelled=check)
    ir = build_ir(statements, cancelled=check)
    for node in ir["nodes"]:
        check()
        statement = node["statement"]
        node["source_locations"] = list(
            span_locations(expanded, statement["start"], statement["end"])
        )
        node["source_origins"] = list(span_origins(expanded, statement["start"], statement["end"]))
        if "condition_source_stmt_index" in node:
            condition = statements[node["condition_source_stmt_index"]]
            node["condition_source_locations"] = list(
                span_locations(expanded, condition["start"], condition["end"])
            )
    if expanded.barriers:
        ir["conclusions_allowed"] = False
    ir["source_diagnostics"] = list(expanded.diagnostics)
    configuration = digest(
        {
            "provider": CL_PROVIDER,
            "lark_version": "1.3.1",
            "profile": "cl-subset-0.1.0",
            "input_configuration_sha256": prepared.configuration_sha256,
            "root_path": root_path,
            "max_input_bytes": max_input_bytes,
            "timeout_seconds": timeout_seconds,
        }
    )
    payload = canonical(
        {
            "schema_version": "0.1.0",
            "ir": ir,
            "root_path": root_path,
            "inputs": inputs,
            "configuration_sha256": configuration,
        }
    )
    if len(payload) > 32 * 1024 * 1024:
        raise ValueError("ANALYSIS_OUTPUT_LIMIT")
    sha = digest_bytes(payload)
    artifact: dict[str, Any] = {
        "schema_version": "0.1.0",
        "import_id": prepared.context.import_id,
        "locator": f"__laip__/analysis/{run_id}/cl-ir.json",
        "sha256": sha,
        "byte_length": len(payload),
        "media_type": "application/json",
        "encoding": "utf-8",
        "source_member_id": None,
        "collected_at": prepared.context.collected_at,
        "collection_method": "laip-cl-lark:analysis",
        "decoded_sha256": None,
        "origin_map_id": None,
        "processing_status": "partial" if not ir["conclusions_allowed"] else "imported",
        "masking_policy_version": prepared.context.masking_policy_version,
    }
    artifact["artifact_id"] = "art_" + digest(
        {key: artifact[key] for key in ("import_id", "locator", "sha256")}
    )
    validate("Artifact", artifact)
    evidence = []
    references_by_node: dict[int, list[dict[str, Any]]] = {}
    for reference in ir.get("references", []):
        check()
        references_by_node.setdefault(reference["node"], []).append(reference)
    for node in ir["nodes"]:
        check()
        statement = node["statement"]
        spans = node["source_locations"]
        source_artifact = (
            artifacts[spans[0]["artifact_id"]] if spans else prepared.manifest_artifact
        )
        record: dict[str, Any] = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "artifact_id": source_artifact["artifact_id"],
            "content_sha256": source_artifact["sha256"],
            "subject_entity_ids": sorted(
                {
                    artifacts[span["artifact_id"]]["source_member_id"]
                    for span in spans
                    if artifacts[span["artifact_id"]].get("source_member_id")
                }
            ),
            "source_locations": spans,
            "collection_method": "laip-cl-lark:declared-subset",
            "collected_at": prepared.context.collected_at,
            "provider": dict(CL_PROVIDER),
            "authority": "source",
            "classification": "observed",
            "excerpt": None,
            "metadata": {
                "statement_kind": statement["kind"],
                "statement_index": node["stmt_index"],
                "barrier": statement["barrier"],
                "diagnostics": statement["diagnostics"],
                "source_origins": node["source_origins"],
                "conclusions_allowed": ir["conclusions_allowed"],
                "references": references_by_node.get(node["id"], []),
            },
            "limitations": [
                "Declared CL/CLLE subset only; no runtime or complete compiler semantics.",
                "Unknown syntax and effects block unsupported conclusions.",
            ],
            "supporting_evidence_ids": [],
            "contradiction_evidence_ids": [],
            "upstream_record": None,
        }
        record["evidence_id"] = record_id("ev_", record, "evidence_id")
        validate("Evidence", record)
        evidence.append(record)
    blob = store.put(
        BytesIO(payload),
        max_bytes=32 * 1024 * 1024,
        job_id=prepared.context.job_id,
        fence=prepared.context.fence,
    )
    check()
    return PreparedAnalysis(
        run_id,
        prepared.context.import_id,
        prepared.manifest["system_namespace"],
        prepared.configuration_sha256,
        configuration,
        artifact,
        blob,
        ir,
        tuple(evidence),
    )


def digest_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _basis(
    result: PreparedAnalysis, prepared: PreparedImport, repository: Repository
) -> dict[str, Any]:
    if (
        result.system_namespace != repository.namespace
        or prepared.manifest["system_namespace"] != repository.namespace
    ):
        raise ValueError("ANALYSIS_NAMESPACE_MISMATCH")
    if (
        result.import_id != prepared.context.import_id
        or result.input_configuration_sha256 != prepared.configuration_sha256
    ):
        raise ValueError("ANALYSIS_INPUT_MISMATCH")
    return {
        "providers": [dict(CL_PROVIDER)],
        "configuration_sha256": result.configuration_sha256,
        "input_configuration_sha256": prepared.configuration_sha256,
        "masking_policy_version": prepared.context.masking_policy_version,
        "stage": "cl_analysis",
    }


def register_analysis(
    result: PreparedAnalysis, prepared: PreparedImport, repository: Repository
) -> None:
    basis = _basis(result, prepared, repository)
    with repository.connection.transaction():
        row = repository.connection.execute(
            "SELECT state,payload FROM imports WHERE import_id=%s "
            "AND system_namespace=%s FOR SHARE",
            (result.import_id, repository.namespace),
        ).fetchone()
        plan = repository.connection.execute(
            "SELECT configuration_sha256 FROM import_plans WHERE run_id=%s AND import_id=%s "
            "AND system_namespace=%s",
            (prepared.context.run_id, result.import_id, repository.namespace),
        ).fetchone()
        if row is None or row[0] not in ("sealed", "partial"):
            raise ValueError("IMPORT_NOT_READY")
        if row[1] != prepared.manifest or plan is None or plan[0] != prepared.configuration_sha256:
            raise ValueError("ANALYSIS_INPUT_MISMATCH")
        old = repository.connection.execute(
            "SELECT payload,import_id,system_namespace FROM runs WHERE run_id=%s", (result.run_id,)
        ).fetchone()
        if old is None:
            repository.create_run(result.run_id, result.import_id, basis)
        elif tuple(old) != (basis, result.import_id, repository.namespace):
            raise ValueError("ANALYSIS_BASIS_MISMATCH")
        repository.attach_artifact(result.run_id, prepared.manifest_artifact["artifact_id"])


def persist_analysis(
    result: PreparedAnalysis, prepared: PreparedImport, repository: Repository
) -> dict[str, Any]:
    """Database-only; publish through a durable analysis job's fenced checkpoint."""
    if repository._run(result.run_id) != _basis(result, prepared, repository):
        raise ValueError("ANALYSIS_BASIS_MISMATCH")
    if (
        result.artifact["sha256"] != result.blob.sha256
        or result.artifact["byte_length"] != result.blob.byte_length
    ):
        raise ValueError("ANALYSIS_OUTPUT_MISMATCH")
    with repository.connection.transaction():
        for item in prepared.items:
            if item.artifact is not None:
                repository.attach_artifact(result.run_id, item.artifact["artifact_id"])
        repository.put_blob(result.blob.sha256, result.blob.byte_length)
        repository.put_artifact(result.artifact)
        repository.attach_artifact(result.run_id, result.artifact["artifact_id"])
        for record in result.evidence:
            if record["run_id"] != result.run_id:
                raise ValueError("ANALYSIS_RUN_MISMATCH")
            repository.put_evidence(record)
    return {
        "run_id": result.run_id,
        "artifact_id": result.artifact["artifact_id"],
        "outcome": "succeeded" if result.ir["conclusions_allowed"] else "partial",
    }
