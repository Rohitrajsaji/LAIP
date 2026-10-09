"""Persist validated REA graph bytes without manufacturing upstream Evidence.

Prepare outside the durable job's fenced publication callback; persist inside it.
Filesystem publication precedes DB locks; a failed transaction may leave an orphan.
The caller freezes collection time and masking policy for idempotent retries.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from laip.artifacts import BlobRef, LocalArtifactStore
from laip.canonical import digest, record_id
from laip.persistence import Repository, validate
from laip.rea_adapter import PROVIDER_REF, ReferenceInventoryResult, validate_inventory

REA_PROVIDER = PROVIDER_REF


@dataclass(frozen=True)
class ImportedInventory:
    artifact_id: str
    evidence_id: str
    blob: BlobRef


@dataclass(frozen=True)
class PreparedInventory:
    artifact: dict[str, Any]
    evidence: dict[str, Any]
    blob: BlobRef


def prepare_inventory(
    result: ReferenceInventoryResult,
    store: LocalArtifactStore,
    *,
    run_id: str,
    import_id: str,
    collected_at: str,
    masking_policy_version: str,
    job_id: str,
    fence: int,
) -> PreparedInventory:
    graph = validate_inventory(result.raw_output, result.input_files)
    sha = hashlib.sha256(result.raw_output).hexdigest()
    if (
        sha != result.output_sha256
        or graph != result.graph
        or graph["root_sha256"] != result.upstream_id
    ):
        raise ValueError("INVALID_PROVIDER_OUTPUT")
    locator = f"providers/rea/{digest(run_id)}/{sha}.json"
    artifact: dict[str, Any] = {
        "schema_version": "0.1.0",
        "import_id": import_id,
        "locator": locator,
        "sha256": sha,
        "byte_length": len(result.raw_output),
        "media_type": "application/json",
        "encoding": "utf-8",
        "source_member_id": None,
        "collected_at": collected_at,
        "collection_method": "rea-cli:import-reference-source",
        "decoded_sha256": None,
        "origin_map_id": None,
        "processing_status": "imported" if graph["inventory_state"] == "complete" else "partial",
        "masking_policy_version": masking_policy_version,
    }
    artifact["artifact_id"] = "art_" + digest(
        {key: artifact[key] for key in ("import_id", "locator", "sha256")}
    )
    evidence: dict[str, Any] = {
        "schema_version": "0.1.0",
        "run_id": run_id,
        "artifact_id": artifact["artifact_id"],
        "content_sha256": sha,
        "subject_entity_ids": [],
        "source_locations": [],
        "collection_method": artifact["collection_method"],
        "collected_at": collected_at,
        "provider": dict(REA_PROVIDER),
        "authority": "historical_reference",
        "classification": "observed",
        "excerpt": None,
        "metadata": {
            "upstream_schema": graph["schema"],
            "inventory_state": graph["inventory_state"],
            "root_sha256": graph["root_sha256"],
            "adapter_version": "0.1.0",
        },
        "limitations": [
            *graph["limitations"],
            "Historical inventory and static references do not prove runtime execution.",
            "Source bytes and IBM i/RPG semantics are not supplied by this REA operation.",
        ],
        "supporting_evidence_ids": [],
        "contradiction_evidence_ids": [],
        "upstream_record": {
            "provider": "rea-cli",
            "upstream_id": result.upstream_id,
            "payload_artifact_id": artifact["artifact_id"],
        },
    }
    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
    validate("Artifact", artifact)
    validate("Evidence", evidence)
    blob = store.put(
        BytesIO(result.raw_output), max_bytes=16 * 1024 * 1024, job_id=job_id, fence=fence
    )
    return PreparedInventory(artifact, evidence, blob)


def persist_inventory(prepared: PreparedInventory, repository: Repository) -> ImportedInventory:
    """DB-only publication; invoke from the active lease's fenced checkpoint callback."""
    artifact = validate("Artifact", prepared.artifact)
    evidence = validate("Evidence", prepared.evidence)
    blob = prepared.blob
    if artifact["sha256"] != blob.sha256 or artifact["byte_length"] != blob.byte_length:
        raise ValueError("INVALID_PROVIDER_OUTPUT")
    with repository.connection.transaction():
        repository.put_blob(blob.sha256, blob.byte_length)
        repository.put_artifact(artifact)
        repository.attach_artifact(evidence["run_id"], artifact["artifact_id"])
        repository.put_evidence(evidence)
    return ImportedInventory(artifact["artifact_id"], evidence["evidence_id"], blob)
