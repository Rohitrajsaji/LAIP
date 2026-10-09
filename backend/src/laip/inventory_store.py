"""Database-only publication of bounded offline inventory and discovery reports."""

from typing import Any

from laip.canonical import digest
from laip.inventory import InventoryResult
from laip.persistence import Repository
from laip.source_import import PreparedImport


def _basis(
    result: InventoryResult, prepared: PreparedImport, repository: Repository
) -> dict[str, Any]:
    if prepared.manifest["system_namespace"] != repository.namespace:
        raise ValueError("INVENTORY_NAMESPACE_MISMATCH")
    if (
        result.import_id != prepared.context.import_id
        or result.system_namespace != repository.namespace
    ):
        raise ValueError("INVENTORY_IDENTITY_MISMATCH")
    return {
        "providers": [result.provider],
        "configuration_sha256": result.configuration_sha256,
        "input_configuration_sha256": prepared.configuration_sha256,
        "masking_policy_version": prepared.context.masking_policy_version,
        "stage": "offline_inventory",
    }


def register_inventory(
    result: InventoryResult, prepared: PreparedImport, repository: Repository
) -> None:
    """Freeze the analysis run basis before its durable analysis job is enqueued."""
    basis = _basis(result, prepared, repository)
    with repository.connection.transaction():
        row = repository.connection.execute(
            "SELECT state,payload FROM imports WHERE import_id=%s "
            "AND system_namespace=%s FOR SHARE",
            (prepared.context.import_id, repository.namespace),
        ).fetchone()
        if row is None or row[0] not in ("sealed", "partial"):
            raise ValueError("IMPORT_NOT_READY")
        plan = repository.connection.execute(
            "SELECT configuration_sha256 FROM import_plans WHERE run_id=%s AND import_id=%s "
            "AND system_namespace=%s",
            (prepared.context.run_id, prepared.context.import_id, repository.namespace),
        ).fetchone()
        if row[1] != prepared.manifest or plan is None or plan[0] != prepared.configuration_sha256:
            raise ValueError("INVENTORY_INPUT_BASIS_MISMATCH")
        old = repository.connection.execute(
            "SELECT payload,import_id,system_namespace FROM runs WHERE run_id=%s", (result.run_id,)
        ).fetchone()
        if old is None:
            repository.create_run(result.run_id, prepared.context.import_id, basis)
        elif tuple(old) != (basis, prepared.context.import_id, repository.namespace):
            raise ValueError("INVENTORY_BASIS_MISMATCH")
        repository.attach_artifact(result.run_id, prepared.manifest_artifact["artifact_id"])


def persist_inventory(
    result: InventoryResult, prepared: PreparedImport, repository: Repository
) -> dict[str, Any]:
    """Invoke inside an active lease's fenced checkpoint; no filesystem operations."""
    basis = _basis(result, prepared, repository)
    if repository._run(result.run_id) != basis:
        raise ValueError("INVENTORY_BASIS_MISMATCH")
    expected = {item.path: item for item in prepared.items}
    accounted = {record["locator"]: record for record in result.accounting}
    if len(accounted) != len(result.accounting) or set(accounted) != set(expected):
        raise ValueError("INVENTORY_ACCOUNTING_MISMATCH")
    for path, item in expected.items():
        artifact_id = item.artifact["artifact_id"] if item.artifact else None
        if (
            accounted[path]["artifact_id"] != artifact_id
            or accounted[path]["processing_status"] != item.status
        ):
            raise ValueError("INVENTORY_ACCOUNTING_MISMATCH")
    entities = {record["entity_id"]: record for record in result.entities}
    evidence = {record["evidence_id"]: record for record in result.evidence}
    if len(entities) != len(result.entities) or len(evidence) != len(result.evidence):
        raise ValueError("DUPLICATE_INVENTORY_RECORD")
    for entity in entities.values():
        if entity["parent_id"] is not None and entity["parent_id"] not in entities:
            raise ValueError("INVENTORY_PARENT_MISSING")
        if not set(entity["evidence_ids"]) <= evidence.keys():
            raise ValueError("INVENTORY_EVIDENCE_MISSING")
    for edge in result.dependencies:
        endpoints = {edge["from_entity_id"], *edge["candidate_entity_ids"]}
        if edge["to_entity_id"] is not None:
            endpoints.add(edge["to_entity_id"])
        if not endpoints <= entities.keys() or not set(edge["evidence_ids"]) <= evidence.keys():
            raise ValueError("INVENTORY_ENDPOINT_MISSING")
    with repository.connection.transaction():
        pending = dict(entities)
        while pending:
            ready = [record for record in pending.values() if record["parent_id"] not in pending]
            if not ready:
                raise ValueError("INVENTORY_PARENT_CYCLE")
            for record in ready:
                repository.put_entity(record, result.run_id)
                del pending[record["entity_id"]]
        artifacts = [prepared.manifest_artifact, prepared.plan_artifact]
        artifacts.extend(item.artifact for item in prepared.items if item.artifact is not None)
        for artifact in artifacts:
            repository.attach_artifact(result.run_id, artifact["artifact_id"])
        pending_evidence = dict(evidence)
        while pending_evidence:
            ready = [
                record
                for record in pending_evidence.values()
                if not (
                    set(record["supporting_evidence_ids"])
                    | set(record["contradiction_evidence_ids"])
                )
                & pending_evidence.keys()
            ]
            if not ready:
                raise ValueError("INVENTORY_EVIDENCE_CYCLE")
            for record in ready:
                if record["run_id"] != result.run_id:
                    raise ValueError("INVENTORY_RUN_MISMATCH")
                repository.put_evidence(record)
                del pending_evidence[record["evidence_id"]]
        for edge in result.dependencies:
            if edge["run_id"] != result.run_id:
                raise ValueError("INVENTORY_RUN_MISMATCH")
            repository.put_dependency(edge)
        payload = {
            "schema_version": "0.1.0",
            "accounting": list(result.accounting),
            "entity_ids": sorted(entities),
            "evidence_ids": sorted(evidence),
            "dependency_ids": sorted(edge["dependency_id"] for edge in result.dependencies),
            "completeness": "supplied_material_only",
        }
        repository._insert(
            "inventory_reports",
            "run_id",
            {
                "run_id": result.run_id,
                "import_id": prepared.context.import_id,
                "system_namespace": repository.namespace,
                "configuration_sha256": result.configuration_sha256,
                "payload": payload,
            },
        )
    return {
        "run_id": result.run_id,
        "report_sha256": digest(payload),
        "outcome": "partial"
        if prepared.partial or any(edge["resolution"] != "resolved" for edge in result.dependencies)
        else "succeeded",
        "artifact_count": len(result.accounting),
    }
