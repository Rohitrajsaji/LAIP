"""Pure offline inventory and conservative qualified dependency resolution."""

import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, cast

from laip.canonical import digest, identity_id, record_id
from laip.persistence import SCHEMA, validate
from laip.source_import import PreparedImport

INVENTORY_PROVIDER = {"id": "laip-offline-inventory", "version": "0.1.0", "source_revision": None}
LOOKUP_TAGS = {
    "Program": "*PGM",
    "Module": "*MODULE",
    "ServiceProgram": "*SRVPGM",
    "Table": "*FILE",
}
RELATIONSHIPS = frozenset(SCHEMA["$defs"]["Dependency"]["properties"]["relationship"]["enum"])


@dataclass(frozen=True)
class InventoryLimits:
    max_entities: int = 100000
    max_dependencies: int = 100000
    max_evidence: int = 200000
    max_records: int = 100000
    max_work: int = 1000000
    max_path_length: int = 128
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("INVALID_INVENTORY_LIMIT")


DEFAULT_INVENTORY_LIMITS = InventoryLimits()


@dataclass(frozen=True)
class InventoryResult:
    run_id: str
    import_id: str
    system_namespace: str
    provider: dict[str, Any]
    configuration_sha256: str
    entities: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    dependencies: tuple[dict[str, Any], ...]
    accounting: tuple[dict[str, Any], ...]


def build_inventory(
    prepared: PreparedImport,
    *,
    run_id: str,
    cancelled: Callable[[], bool] | None = None,
    limits: InventoryLimits = DEFAULT_INVENTORY_LIMITS,
) -> InventoryResult:
    if not run_id or run_id == prepared.context.run_id:
        raise ValueError("INVALID_ANALYSIS_RUN")
    namespace = prepared.manifest["system_namespace"]
    deadline, work = time.monotonic() + limits.timeout_seconds, 0
    entities: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, dict[str, Any]] = {}
    accounting: list[dict[str, Any]] = []
    references: list[tuple[dict[str, Any], str]] = []
    availability_claims: dict[str, set[str]] = {}

    def check() -> None:
        nonlocal work
        work += 1
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        if time.monotonic() >= deadline or work > limits.max_work:
            raise ValueError("INVENTORY_WORK_LIMIT")

    def checked_identity(value: Any) -> dict[str, Any]:
        check()
        try:
            validate("Identity", value)
        except Exception as exc:
            raise ValueError("INVALID_METADATA_IDENTITY") from exc
        if value["system_namespace"] != namespace:
            raise ValueError("IDENTITY_NAMESPACE_MISMATCH")
        if len(value["qualified_identity"]) > 64 or any(
            len(p) > 256 or p != unicodedata.normalize("NFC", p) or any(ord(c) < 32 for c in p)
            for p in value["qualified_identity"]
        ):
            raise ValueError("INVALID_METADATA_IDENTITY")
        parts = value["qualified_identity"]
        if (
            value["kind"] in LOOKUP_TAGS
            and (len(parts) != 3 or parts[1] != LOOKUP_TAGS[value["kind"]])
        ) or (value["kind"] == "SourceMember" and len(parts) != 3):
            raise ValueError("INVALID_METADATA_IDENTITY")
        return cast(dict[str, Any], value)

    def add_entity(
        identity: dict[str, Any],
        eid: str,
        availability: str = "unknown",
        status: str = "not_analyzed",
        attributes: dict[str, Any] | None = None,
    ) -> str:
        checked_identity(identity)
        key = identity_id(identity)
        if key not in entities:
            if len(entities) >= limits.max_entities:
                raise ValueError("INVENTORY_ENTITY_LIMIT")
            entities[key] = {
                "schema_version": "0.1.0",
                "entity_id": key,
                "identity": identity,
                "display_name": identity["qualified_identity"][-1],
                "original_names": [identity["qualified_identity"][-1]],
                "aliases": [],
                "parent_id": None if identity["kind"] in {"System", "SourceMember"} else system_id,
                "source_availability": availability,
                "analysis_status": status,
                "attributes": attributes or {},
                "evidence_ids": [],
            }
        entity = entities[key]
        if attributes and entity["attributes"] and entity["attributes"] != attributes:
            raise ValueError("CONFLICTING_METADATA_ATTRIBUTES")
        if attributes:
            entity["attributes"] = attributes
        availability_claims.setdefault(key, set()).add(availability)
        concrete = availability_claims[key] - {"unknown"}
        entity["source_availability"] = next(iter(concrete)) if len(concrete) == 1 else "unknown"
        if eid and eid not in entity["evidence_ids"]:
            entity["evidence_ids"].append(eid)
        return key

    def new_evidence(
        artifact: dict[str, Any],
        metadata: dict[str, Any],
        subjects: list[str],
        classification: str = "observed",
        support: list[str] | None = None,
    ) -> str:
        check()
        record = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "artifact_id": artifact["artifact_id"],
            "content_sha256": artifact["sha256"],
            "subject_entity_ids": sorted(set(subjects)),
            "source_locations": [],
            "collection_method": "offline_inventory",
            "collected_at": prepared.context.collected_at,
            "provider": INVENTORY_PROVIDER,
            "authority": "imported_metadata",
            "classification": classification,
            "excerpt": None,
            "metadata": metadata,
            "limitations": ["Supplied offline material; no live IBM i or runtime validation."],
            "supporting_evidence_ids": sorted(set(support or [])),
            "contradiction_evidence_ids": [],
            "upstream_record": None,
        }
        eid = record_id("ev_", record, "evidence_id")
        record["evidence_id"] = eid
        if eid not in evidence and len(evidence) >= limits.max_evidence:
            raise ValueError("INVENTORY_EVIDENCE_LIMIT")
        evidence[eid] = record
        return eid

    def edge(
        source: str,
        target: str | None,
        relationship: str,
        expression: str,
        resolution: str,
        candidates: list[str],
        eids: list[str],
        classification: str,
        libraries: list[str],
        method: str,
    ) -> None:
        check()
        record = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "from_entity_id": source,
            "relationship": relationship,
            "to_entity_id": target,
            "target_expression": expression,
            "resolution": resolution,
            "candidate_entity_ids": sorted(candidates),
            "resolution_context": {
                "library_list": libraries,
                "namespace": namespace,
                "method": method,
            },
            "classification": classification,
            "evidence_ids": sorted(set(eids)),
            "limitations": ["Static supplied relation; runtime execution is not established."],
        }
        did = record_id("dep_", record, "dependency_id")
        record["dependency_id"] = did
        if did not in dependencies and len(dependencies) >= limits.max_dependencies:
            raise ValueError("INVENTORY_DEPENDENCY_LIMIT")
        dependencies[did] = record

    system_identity = {
        "system_namespace": namespace,
        "kind": "System",
        "qualified_identity": [namespace],
    }
    system_id = identity_id(system_identity)
    basis = new_evidence(prepared.manifest_artifact, {"kind": "inventory_basis"}, [system_id])
    add_entity(system_identity, basis, "not_applicable")
    declared = {a["relative_path"]: a for a in prepared.manifest["artifacts"]}
    item_evidence: dict[str, str] = {}
    record_count = 0
    for item in prepared.items:
        check()
        entry = declared.get(item.path)
        artifact = item.artifact or prepared.manifest_artifact
        subject_ids = [
            identity_id(entry[k])
            for k in ("source_member_identity", "object_identity")
            if entry and entry[k] is not None
        ]
        eid = new_evidence(
            artifact,
            {
                "kind": "input_accounting",
                "locator": item.path,
                "processing_status": item.status,
                "diagnostics": list(item.diagnostics),
            },
            subject_ids,
        )
        item_evidence[item.path] = eid
        accounting.append(
            {
                "locator": item.path,
                "artifact_id": item.artifact["artifact_id"] if item.artifact else None,
                "processing_status": item.status,
                "diagnostics": list(item.diagnostics),
                "evidence_ids": [eid],
                "scope_state": item.scope_state,
            }
        )
        if entry:
            member, obj = entry["source_member_identity"], entry["object_identity"]
            available = (
                "missing"
                if item.scope_state == "missing"
                else "available"
                if item.artifact
                else "unknown"
            )
            if member:
                add_entity(
                    member,
                    eid,
                    available,
                    "unsupported" if item.status == "unsupported" else "not_analyzed",
                )
            if obj:
                add_entity(obj, eid, available if member else "unknown")
            if member and obj:
                edge(
                    identity_id(member),
                    identity_id(obj),
                    "SOURCE_OF",
                    "/".join(obj["qualified_identity"]),
                    "resolved",
                    [],
                    [eid],
                    "observed",
                    prepared.manifest["library_list"],
                    "explicit_manifest_association",
                )
        if item.metadata is None or item.status not in {"imported", "decoded"}:
            continue
        for index, record in enumerate(item.metadata["records"]):
            check()
            record_count += 1
            if record_count > limits.max_records:
                raise ValueError("INVENTORY_RECORD_LIMIT")
            record_type = record.get("record_type")
            if record_type is None:
                continue  # Opaque step-6 exports remain accounted without interpretation.
            if record_type == "entity":
                if set(record) - {"record_type", "identity", "source_availability", "attributes"}:
                    raise ValueError("INVALID_METADATA_ENTITY")
                identity = checked_identity(record.get("identity"))
                availability = record.get("source_availability", "unknown")
                attributes = record.get("attributes", {})
                if availability not in {"available", "missing", "unknown", "not_applicable"}:
                    raise ValueError("INVALID_METADATA_ENTITY")
                if not isinstance(attributes, dict):
                    raise ValueError("INVALID_METADATA_ENTITY")
                ref_eid = new_evidence(
                    artifact,
                    {
                        "kind": "entity_record",
                        "record_index": index,
                        "metadata_kind": item.metadata["kind"],
                    },
                    [identity_id(identity)],
                )
                add_entity(identity, ref_eid, availability, attributes=attributes)
            elif record_type == "reference":
                if (
                    set(record)
                    - {
                        "record_type",
                        "from_identity",
                        "relationship",
                        "target",
                        "library_list",
                        "ordered_library_list",
                    }
                    or record.get("relationship") not in RELATIONSHIPS
                ):
                    raise ValueError("INVALID_METADATA_REFERENCE")
                source_identity = checked_identity(record.get("from_identity"))
                target = record.get("target")
                if (
                    not isinstance(target, dict)
                    or set(target) - {"kind", "name", "library", "dynamic", "qualified_identity"}
                    or not {"kind", "name", "library", "dynamic"} <= set(target)
                    or type(target["dynamic"]) is not bool
                    or target["kind"]
                    not in SCHEMA["$defs"]["Identity"]["properties"]["kind"]["enum"]
                    or not isinstance(target["name"], str)
                    or not target["name"]
                    or len(target["name"]) > 256
                    or (
                        target["library"] is not None
                        and (not isinstance(target["library"], str) or not target["library"])
                    )
                ):
                    raise ValueError("INVALID_METADATA_REFERENCE")
                if "qualified_identity" in target:
                    exact = checked_identity(
                        {
                            "system_namespace": namespace,
                            "kind": target["kind"],
                            "qualified_identity": target["qualified_identity"],
                        }
                    )
                    parts = exact["qualified_identity"]
                    if parts[-1] != target["name"] or (
                        target["library"] is not None
                        and (len(parts) < 2 or parts[0] != target["library"])
                    ):
                        raise ValueError("INVALID_METADATA_REFERENCE")
                if any(
                    value != unicodedata.normalize("NFC", value) or any(ord(c) < 32 for c in value)
                    for value in (target["name"], target["library"] or "")
                ):
                    raise ValueError("INVALID_METADATA_REFERENCE")
                for field in ("library_list", "ordered_library_list"):
                    if field in record and (
                        not isinstance(record[field], list)
                        or len(record[field]) > 1024
                        or any(
                            not isinstance(s, str)
                            or not s
                            or len(s) > 256
                            or s != unicodedata.normalize("NFC", s)
                            or any(ord(c) < 32 for c in s)
                            for s in record[field]
                        )
                        or len(set(record[field])) != len(record[field])
                    ):
                        raise ValueError("INVALID_METADATA_REFERENCE")
                ref_eid = new_evidence(
                    artifact,
                    {
                        "kind": "reference_record",
                        "record_index": index,
                        "metadata_kind": item.metadata["kind"],
                    },
                    [identity_id(source_identity)],
                )
                add_entity(source_identity, ref_eid)
                references.append((record, ref_eid))
            else:
                raise ValueError("INVALID_METADATA_RECORD_TYPE")
    target_index: dict[tuple[str, str], list[str]] = {}
    for key, entity in entities.items():
        check()
        ident = entity["identity"]
        target_index.setdefault((ident["kind"], ident["qualified_identity"][-1]), []).append(key)
    for record, eid in references:
        check()
        target = record["target"]
        libraries = record.get(
            "ordered_library_list", record.get("library_list", prepared.manifest["library_list"])
        )
        matches = []
        if not target["dynamic"]:
            for key in target_index.get((target["kind"], target["name"]), []):
                check()
                parts = entities[key]["identity"]["qualified_identity"]
                if "qualified_identity" in target:
                    eligible = parts == target["qualified_identity"]
                elif target["kind"] in {"Application", "System"} and target["library"] is None:
                    eligible = True
                elif target["kind"] not in LOOKUP_TAGS:
                    eligible = False
                elif target["library"] is not None:
                    eligible = len(parts) >= 2 and parts[0] == target["library"]
                else:
                    eligible = (not libraries and "ordered_library_list" not in record) or (
                        len(parts) >= 2 and parts[0] in libraries
                    )
                if eligible:
                    matches.append(key)
            if (
                "ordered_library_list" in record
                and target["library"] is None
                and matches
                and "qualified_identity" not in target
                and target["kind"] not in {"Application", "System"}
            ):
                first = min(
                    libraries.index(entities[k]["identity"]["qualified_identity"][0])
                    for k in matches
                )
                matches = [
                    k
                    for k in matches
                    if entities[k]["identity"]["qualified_identity"][0] == libraries[first]
                ]
        resolution = (
            "dynamic"
            if target["dynamic"]
            else "missing"
            if not matches
            else "resolved"
            if len(matches) == 1
            else "ambiguous"
        )
        expression = (target["library"] + "/" if target["library"] else "") + target["name"]
        method = (
            "explicit_library_order"
            if "ordered_library_list" in record
            else "qualified_offline_scope"
        )
        edge(
            identity_id(record["from_identity"]),
            matches[0] if resolution == "resolved" else None,
            record["relationship"],
            expression,
            resolution,
            matches if resolution == "ambiguous" else [],
            [eid],
            "observed" if resolution == "resolved" else "unresolved",
            libraries,
            method,
        )
    confirmed: dict[str, set[str]] = {}
    for membership in prepared.manifest["application_memberships"]:
        check()
        app = checked_identity(membership["application_identity"])
        member = checked_identity(membership["member_identity"])
        app_id, member_id = identity_id(app), identity_id(member)
        if member_id not in entities:
            raise ValueError("MISSING_MEMBERSHIP_ENTITY")
        eid = new_evidence(
            prepared.manifest_artifact,
            {
                "kind": "application_membership",
                "membership_status": "confirmed",
                "evidence_path": membership["evidence_path"],
            },
            [app_id, member_id],
            support=[item_evidence[membership["evidence_path"]]],
        )
        add_entity(app, eid, "not_applicable")
        edge(
            member_id,
            app_id,
            "MEMBER_OF",
            "/".join(app["qualified_identity"]),
            "resolved",
            [],
            [eid],
            "observed",
            prepared.manifest["library_list"],
            "explicit_manifest_membership",
        )
        confirmed.setdefault(app_id, set()).add(member_id)
    # Index membership support once rather than rescanning every edge for every seed.
    seed_evidence: dict[tuple[str, str], set[str]] = {}
    for dep in dependencies.values():
        check()
        if (
            dep["relationship"] == "MEMBER_OF"
            and dep["resolution"] == "resolved"
            and entities[dep["to_entity_id"]]["identity"]["kind"] == "Application"
        ):
            confirmed.setdefault(dep["to_entity_id"], set()).add(dep["from_entity_id"])
            seed_evidence.setdefault((dep["to_entity_id"], dep["from_entity_id"]), set()).update(
                dep["evidence_ids"]
            )
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for dep in dependencies.values():
        check()
        if dep["resolution"] == "resolved" and dep["relationship"] not in {"MEMBER_OF", "CONTAINS"}:
            outgoing.setdefault(dep["from_entity_id"], []).append(dep)
    for app_id, seeds in sorted(confirmed.items()):
        visited = set(seeds)
        queue: deque[tuple[str, list[str]]] = deque()
        for seed in sorted(seeds):
            check()
            queue.append((seed, sorted(seed_evidence[(app_id, seed)])))
        while queue:
            source, support = queue.popleft()
            for dep in sorted(outgoing.get(source, []), key=lambda d: d["dependency_id"]):
                check()
                target_id = dep["to_entity_id"]
                if entities[target_id]["identity"]["kind"] in {"Application", "System"}:
                    continue
                if target_id in visited:
                    continue
                visited.add(target_id)
                path_support = sorted(set(support + dep["evidence_ids"]))
                if len(path_support) > limits.max_path_length:
                    raise ValueError("INVENTORY_PATH_LIMIT")
                eid = new_evidence(
                    prepared.manifest_artifact,
                    {"kind": "reachability_membership", "membership_status": "inferred"},
                    [target_id, app_id],
                    "inferred",
                    path_support,
                )
                edge(
                    target_id,
                    app_id,
                    "MEMBER_OF",
                    "/".join(entities[app_id]["identity"]["qualified_identity"]),
                    "resolved",
                    [],
                    [eid],
                    "inferred",
                    prepared.manifest["library_list"],
                    "static_reachability",
                )
                queue.append((target_id, path_support))
    for entity in entities.values():
        check()
        entity["evidence_ids"].sort()
        validate("Entity", entity)
    for kind, records in (("Evidence", evidence), ("Dependency", dependencies)):
        for record in records.values():
            check()
            validate(kind, record)
    configuration = digest(
        {
            "provider": INVENTORY_PROVIDER,
            "input_configuration_sha256": prepared.configuration_sha256,
            "manifest": prepared.manifest,
            "metadata": [
                {"path": item.path, "payload": item.metadata}
                for item in prepared.items
                if item.metadata is not None
            ],
            "limits": asdict(limits),
            "resolution_profile": "offline-scope-0.1.0",
        }
    )
    check()
    return InventoryResult(
        run_id,
        prepared.context.import_id,
        namespace,
        dict(INVENTORY_PROVIDER),
        configuration,
        tuple(entities[k] for k in sorted(entities)),
        tuple(evidence[k] for k in sorted(evidence)),
        tuple(dependencies[k] for k in sorted(dependencies)),
        tuple(accounting),
    )
