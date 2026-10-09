"""One bounded, inert bridge from analyzer IR to qualified canonical facts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from laip.analysis_facts import validate_fact_bundle
from laip.canonical import canonical, identity_id, record_id
from laip.persistence import validate


@dataclass(frozen=True)
class Publication:
    entities: tuple[dict[str, Any], ...]
    dependencies: tuple[dict[str, Any], ...]
    bundles: tuple[dict[str, Any], ...]


def semantic(value: Any) -> Any:
    """Semantic values only; physical source rows stay in private IR."""
    if isinstance(value, dict):
        return {
            key: semantic(item)
            for key, item in value.items()
            if key not in {"physical_lines", "text", "body", "source_origins"}
        }
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def build_publication(
    prepared: Any,
    inventory: Any,
    analyses: list[Any],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Publication:
    work = 0

    def check() -> None:
        nonlocal work
        work += 1
        if work > 1000000:
            raise ValueError("PUBLICATION_WORK_LIMIT")
        if cancelled and cancelled():
            raise ValueError("CANCELLED")

    check()
    evidence_indexes = []
    for analysis in analyses:
        evidence_index: dict[Any, list[dict[str, Any]]] = {}
        for ev in analysis.evidence:
            check()
            evidence_index.setdefault(ev["metadata"].get("statement_index"), []).append(ev)
        evidence_indexes.append(evidence_index)
    analysis_indexes = {id(analysis): index for index, analysis in enumerate(analyses)}
    namespace = prepared.manifest["system_namespace"]
    if namespace != inventory.system_namespace:
        raise ValueError("PUBLICATION_NAMESPACE_MISMATCH")
    entities: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, dict[str, Any]] = {}
    groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
    entries = prepared.manifest["artifacts"]
    by_member = {
        identity_id(entry["source_member_identity"]): entry
        for entry in entries
        if entry.get("source_member_identity")
    }
    existing = {entity["entity_id"]: entity for entity in inventory.entities}
    dds_ids: dict[str, str] = {}
    procedure_ids: dict[tuple[int, int], str] = {}
    symbol_ids: dict[tuple[int, str], str] = {}
    global_symbols: dict[str, str] = {}
    effect_ids: dict[tuple[int, int], str] = {}

    def add_fact(
        index: int,
        analysis: Any,
        kind: str,
        scope: str,
        data: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> str:
        check()
        ids = sorted({ev["evidence_id"] for ev in evidence})
        spans = [span for ev in evidence for span in ev["source_locations"]]
        artifacts = sorted({span["artifact_id"] for span in spans})
        if not ids or not artifacts:
            raise ValueError("UNCITED_ANALYZER_FACT")
        result = ""
        for artifact in artifacts:
            locations = sorted(
                {
                    canonical(span): span for span in spans if span["artifact_id"] == artifact
                }.values(),
                key=lambda span: (span["byte_start"], span["byte_end"]),
            )
            fact: dict[str, Any] = {
                "kind": kind,
                "scope_id": scope,
                "evidence_ids": ids,
                "source_locations": locations,
                "data": data,
            }
            fact["fact_id"] = record_id("fact_", fact, "fact_id")
            groups.setdefault((index, artifact), []).append(fact)
            result = result or fact["fact_id"]
        return result

    def evidence_at(analysis: Any, node: int) -> list[dict[str, Any]]:
        check()
        return evidence_indexes[analysis_indexes[id(analysis)]].get(node, [])

    def entity(
        kind: str,
        parts: list[str],
        name: str,
        parent: str,
        attrs: dict[str, Any],
        complete: bool,
        evidence: list[dict[str, Any]],
    ) -> str:
        check()
        identity = {"system_namespace": namespace, "kind": kind, "qualified_identity": parts}
        eid = identity_id(identity)
        value = {
            "schema_version": "0.2.0",
            "entity_id": eid,
            "identity": identity,
            "display_name": name,
            "original_names": [name],
            "aliases": [],
            "parent_id": parent,
            "source_availability": "available",
            "analysis_status": "analyzed" if complete else "partial",
            "attributes": semantic(attrs),
            "evidence_ids": sorted({ev["evidence_id"] for ev in evidence}),
        }
        validate("Entity", value)
        if eid in entities and entities[eid] != value:
            raise ValueError("AMBIGUOUS_CANONICAL_DECLARATION")
        entities[eid] = value
        return eid

    def reference(
        index: int,
        analysis: Any,
        source: str,
        relationship: str,
        name: str,
        candidates: list[str],
        evidence: list[dict[str, Any]],
        dynamic: bool = False,
        source_resolution: str | None = None,
    ) -> str:
        candidates = sorted(set(candidates))
        if source_resolution in {"missing", "unresolved", "dynamic"}:
            candidates = []
        if source_resolution == "ambiguous" and len(candidates) < 2:
            raise ValueError("AMBIGUOUS_REFERENCE_COLLAPSED")
        resolution = (
            "dynamic"
            if dynamic
            else "resolved"
            if len(candidates) == 1
            else "ambiguous"
            if candidates
            else "missing"
        )
        target = candidates[0] if resolution == "resolved" else None
        choices = candidates if resolution == "ambiguous" else []
        fact = add_fact(
            index,
            analysis,
            "reference",
            source,
            {
                "name": name,
                "relationship": relationship,
                "resolution": resolution,
                "target_entity_id": target,
                "candidate_entity_ids": choices,
            },
            evidence,
        )
        edge = {
            "schema_version": "0.2.0",
            "run_id": inventory.run_id,
            "from_entity_id": source,
            "relationship": relationship,
            "to_entity_id": target,
            "target_expression": name,
            "resolution": resolution,
            "candidate_entity_ids": choices,
            "resolution_context": {
                "library_list": prepared.manifest["library_list"],
                "namespace": namespace,
                "method": "analyzer-fact:" + fact,
            },
            "classification": "observed" if target else "inferred",
            "evidence_ids": sorted({ev["evidence_id"] for ev in evidence}),
            "limitations": [
                "Supplied source identity/binding only; compiled/live identity "
                "and execution not established."
            ],
        }
        edge["dependency_id"] = record_id("dep_", edge, "dependency_id")
        validate("Dependency", edge)
        dependencies[edge["dependency_id"]] = edge
        return fact

    def owner(analysis: Any) -> tuple[str, list[str]]:
        subjects = {subject for ev in analysis.evidence for subject in ev["subject_entity_ids"]}
        roots = [by_member[subject] for subject in subjects if subject in by_member]
        # Includes may add subjects: DDS source_identity or the root artifact resolves the root.
        if analysis.ir.get("source_identity"):
            roots = [by_member[identity_id(analysis.ir["source_identity"])]]
        else:
            root = next(
                (node for node in analysis.ir["nodes"] if node.get("source_locations")), None
            )
            if root:
                artifact = root["source_locations"][0]["artifact_id"]
                member = next(
                    (
                        item.artifact["source_member_id"]
                        for item in prepared.items
                        if item.artifact and item.artifact["artifact_id"] == artifact
                    ),
                    None,
                )
                roots = [by_member[member]] if member in by_member else roots
        if len(roots) != 1:
            raise ValueError("AMBIGUOUS_ANALYZER_ROOT")
        entry = roots[0]
        identity = entry.get("object_identity") or entry["source_member_identity"]
        parent = identity_id(identity)
        if parent not in existing:
            raise ValueError("MISSING_ANALYZER_OWNER")
        return parent, list(identity["qualified_identity"])

    # DDS declarations establish formats/fields before RPG bindings are published.
    for index, analysis in enumerate(analyses):
        if analysis.ir.get("language") != "DDS":
            continue
        parent, parts = owner(analysis)
        records: dict[str, str] = {}
        for definition in analysis.ir["definitions"]:
            if definition["kind"] not in {"record", "field"}:
                continue
            name = definition["name"]
            if definition["kind"] == "record":
                kind, qualified, owner_id = "RecordFormat", parts + [name.upper()], parent
            else:
                format_name = definition.get("record")
                if not format_name or format_name.casefold() not in records:
                    continue
                kind, qualified, owner_id = (
                    "Field",
                    parts + [format_name.upper(), name.upper()],
                    records[format_name.casefold()],
                )
            support = evidence_at(analysis, definition["node"])
            eid = entity(
                kind,
                qualified,
                name,
                owner_id,
                definition["attributes"],
                definition["complete"],
                support,
            )
            dds_ids[definition["id"]] = eid
            if kind == "RecordFormat":
                records[name.casefold()] = eid
            declaration = add_fact(
                index,
                analysis,
                "declaration",
                owner_id,
                {
                    "name": name,
                    "entity_kind": kind,
                    "qualified_identity": qualified,
                    "owner_reference": owner_id,
                    "declared_attributes": [
                        {"name": key, "value": semantic(value)}
                        for key, value in definition["attributes"].items()
                        if key not in {"physical_lines", "text", "body", "source_origins"}
                    ],
                },
                support,
            )
            if kind == "Field":
                global_symbols[eid] = add_fact(
                    index,
                    analysis,
                    "symbol",
                    owner_id,
                    {
                        "name": name,
                        "data_type": str(definition["attributes"].get("datatype") or "unknown"),
                        "declaration_fact_id": declaration,
                    },
                    support,
                )
            reference(index, analysis, owner_id, "CONTAINS", name, [eid], support)
        for file_keyword in analysis.ir.get("file_keywords", []):
            support = evidence_at(analysis, file_keyword["node"])
            add_fact(
                index,
                analysis,
                "declaration",
                parent,
                {
                    "name": "file-options",
                    "owner_reference": parent,
                    "declared_attributes": [
                        {"name": "keywords", "value": semantic(file_keyword["keywords"])}
                    ],
                },
                support,
            )
        for node in analysis.ir["nodes"]:
            if node["statement"]["barrier"]:
                diagnostic = (
                    "DDS_COLUMN_OVERFLOW"
                    if "DDS_COLUMN_OVERFLOW" in node["statement"]["diagnostics"]
                    else "DDS_UNSUPPORTED_CONSTRUCT"
                )
                add_fact(
                    index,
                    analysis,
                    "diagnostic",
                    parent,
                    {
                        "code": diagnostic,
                        "message": "Source outside the declared DDS profile; "
                        "affected conclusions remain blocked.",
                        "severity": "warning",
                        "blocks_claim": True,
                    },
                    evidence_at(analysis, node["stmt_index"]),
                )
        for relationship in analysis.ir["relationships"]:
            source = dds_ids.get(relationship.get("source_id"))
            target = dds_ids.get(relationship.get("target_id"))
            if relationship["kind"] == "key" and target:
                key_evidence = evidence_at(analysis, relationship["node"])
                entities[target]["attributes"]["key"] = True
                entities[target]["evidence_ids"] = sorted(
                    set(entities[target]["evidence_ids"])
                    | {ev["evidence_id"] for ev in key_evidence}
                )
                add_fact(
                    index,
                    analysis,
                    "declaration",
                    entities[target]["parent_id"],
                    {
                        "name": entities[target]["display_name"],
                        "entity_kind": "Field",
                        "qualified_identity": entities[target]["identity"]["qualified_identity"],
                        "owner_reference": entities[target]["parent_id"],
                        "declared_attributes": [{"name": "key", "value": True}],
                    },
                    key_evidence,
                )
            if source and relationship["kind"] == "subfile_control":
                reference(
                    index,
                    analysis,
                    source,
                    "DEPENDS_ON",
                    relationship["target"]["record"],
                    [target] if target else [],
                    evidence_at(analysis, relationship["node"]),
                )

    for index, analysis in enumerate(analyses):
        if not analysis.evidence or analysis.evidence[0]["provider"]["id"] != "laip-rpgle-lark":
            continue
        parent, parts = owner(analysis)
        for node in analysis.ir["nodes"]:
            if node["statement"]["kind"] != "procedure_start":
                continue
            name = node["statement"]["attributes"]["name"]
            support = evidence_at(analysis, node["stmt_index"])
            qualified = parts + ["procedure", name.casefold()]
            eid = entity(
                "Procedure",
                qualified,
                name,
                parent,
                {"lexical_scope": node.get("lexical_scope")},
                not node["statement"]["barrier"],
                support,
            )
            procedure_ids[(index, node["id"])] = eid
            add_fact(
                index,
                analysis,
                "declaration",
                parent,
                {
                    "name": name,
                    "entity_kind": "Procedure",
                    "qualified_identity": qualified,
                    "owner_reference": parent,
                },
                support,
            )
            reference(index, analysis, parent, "CONTAINS", name, [eid], support)
        for symbol in analysis.ir.get("symbols", []):
            declaration = symbol.get("declaration_node")
            if declaration is None:
                continue
            support = evidence_at(analysis, declaration)
            if not support:
                continue
            data = {"name": symbol["name"], "data_type": str(symbol.get("datatype", "unknown"))}
            if symbol.get("parameter_mode"):
                data["parameter_mode"] = symbol["parameter_mode"]
            symbol_ids[(index, symbol["symbol_id"])] = add_fact(
                index, analysis, "symbol", parent + ":" + symbol.get("scope", "main"), data, support
            )
        for node in analysis.ir["nodes"]:
            check()
            support = evidence_at(analysis, node["stmt_index"])
            if not support:
                continue
            scope = parent + ":" + node.get("lexical_scope", "main")
            scoped_name = node.get("lexical_scope", "main").removeprefix("procedure:")
            source_entity = next(
                (
                    eid
                    for (owner_index, _), eid in procedure_ids.items()
                    if owner_index == index
                    and entities[eid]["display_name"].casefold() == scoped_name
                ),
                parent,
            )

            def symbol_reference(binding: dict[str, Any], index: int = index) -> str:
                sid = str(binding.get("symbol_id") or "")
                return symbol_ids.get(
                    (index, sid), global_symbols.get(sid, sid or "unresolved:" + binding["name"])
                )

            bindings = node.get("symbol_bindings", {})
            reads = [symbol_reference(binding) for binding in bindings.get("uses", [])]
            writes = [symbol_reference(binding) for binding in bindings.get("defines", [])]
            statement_kind = node["statement"]["kind"]
            unknown = node["statement"]["barrier"] or any(
                value.startswith("unresolved:") for value in reads + writes
            )
            if statement_kind not in {
                "comment",
                "declaration",
                "procedure_start",
                "procedure_end",
                "interface_start",
                "interface_end",
                "control_options",
                "free_marker",
            }:
                effect_ids[(index, node["id"])] = add_fact(
                    index,
                    analysis,
                    "effect",
                    scope,
                    {
                        "operation": statement_kind,
                        "reads": sorted(set(reads)),
                        "writes": sorted(set(writes)),
                        "nondeterministic": False,
                        "may_raise": False,
                        "unknown": unknown,
                    },
                    support,
                )
            if node["statement"]["barrier"] or any(
                barrier["node"] == node["id"] for barrier in analysis.ir.get("barriers", [])
            ):
                add_fact(
                    index,
                    analysis,
                    "diagnostic",
                    scope,
                    {
                        "code": "ANALYZER_BARRIER",
                        "message": "Unsupported or unresolved construct/effects "
                        "within the declared profile.",
                        "severity": "warning",
                        "blocks_claim": True,
                    },
                    support,
                )
            for effect in node.get("effects", []):
                data = {
                    "operation": effect.get("operation")
                    or effect.get("name")
                    or effect.get("kind", "unknown"),
                    "reads": sorted(
                        set(
                            reads
                            + [
                                global_symbols.get(sid, sid)
                                for sid in effect.get("reads_record_globals", [])
                            ]
                        )
                    ),
                    "writes": sorted(
                        set(
                            writes
                            + [
                                global_symbols.get(sid, sid)
                                for sid in effect.get("may_write_record_globals", [])
                            ]
                        )
                    ),
                    "nondeterministic": bool(effect.get("nondeterministic")),
                    "may_raise": bool(effect.get("may_raise")),
                    "unknown": bool(effect.get("unknown")),
                }
                producers = effect.get("status_producer_nodes", [])
                if len(producers) == 1:
                    data["status_producer_id"] = effect_ids.get((index, producers[0]))
                add_fact(index, analysis, "effect", scope, data, support)
            binding = node.get("record_binding")
            if binding:
                relationship = {
                    "chain": "READS",
                    "read": "READS",
                    "update": "UPDATES",
                    "write": "WRITES",
                    "delete": "DELETES",
                }.get(binding["operation"])
                if relationship:
                    target = (
                        identity_id(binding["file_identity"])
                        if binding.get("file_identity")
                        else None
                    )
                    candidates = [target] if target and target in existing else []
                    if binding["resolution"] == "ambiguous":
                        candidate_artifacts = set(binding.get("supporting_artifact_ids", []))
                        candidates = sorted(
                            {
                                identity_id(entry["object_identity"])
                                for entry in entries
                                if entry.get("object_identity")
                                and any(
                                    item.artifact
                                    and item.artifact["artifact_id"] in candidate_artifacts
                                    and item.path == entry["relative_path"]
                                    for item in prepared.items
                                )
                            }
                        )
                    reference(
                        index,
                        analysis,
                        source_entity,
                        relationship,
                        binding["target"],
                        candidates,
                        support,
                        binding["resolution"] == "dynamic",
                        source_resolution=binding["resolution"],
                    )
            call = node.get("call_resolution")
            if call:
                candidates = (
                    [procedure_ids[(index, call["target_node"])]]
                    if (index, call.get("target_node")) in procedure_ids
                    else []
                )
                if call["resolution"] == "ambiguous":
                    candidates = [
                        eid
                        for (owner_index, _), eid in procedure_ids.items()
                        if owner_index == index
                        and entities[eid]["display_name"].casefold() == call["target"].casefold()
                    ]
                ref = reference(
                    index,
                    analysis,
                    parent,
                    "CALLS",
                    call["target"],
                    candidates,
                    support,
                    call["resolution"] == "dynamic",
                    source_resolution=call["resolution"],
                )
                add_fact(
                    index,
                    analysis,
                    "callsite",
                    scope,
                    {
                        "name": call["target"],
                        "argument_expressions": [
                            canonical(argument).decode()
                            for argument in call.get("argument_order", [])
                        ],
                        "target_reference": ref,
                    },
                    support,
                )

    bundles = []
    for (index, artifact), facts in sorted(groups.items()):
        analysis = analyses[index]
        provider = analysis.evidence[0]["provider"]
        bundle = {
            "fact_schema_version": "0.2.0",
            "system_namespace": namespace,
            "run_id": inventory.run_id,
            "artifact_id": artifact,
            "provider": provider,
            "profile_id": analysis.ir.get("profile")
            or analysis.ir.get("language", "rpgle-banking"),
            "profile_version": provider["version"],
            "facts": facts,
            "accounting": {
                "supplied": len(analysis.ir["nodes"]),
                "analyzed": len(analysis.ir["nodes"]),
                "supported": sum(
                    not node.get("statement", {}).get("barrier", node.get("barrier", False))
                    for node in analysis.ir["nodes"]
                ),
                "unsupported": sum(
                    node.get("statement", {}).get("barrier", node.get("barrier", False))
                    for node in analysis.ir["nodes"]
                ),
                "unresolved": sum(
                    fact["kind"] == "reference" and fact["data"]["resolution"] != "resolved"
                    for fact in facts
                ),
                "failed": 0,
            },
        }
        validate_fact_bundle(bundle)
        bundles.append(bundle)
    if (
        len(entities) > 10000
        or len(dependencies) > 20000
        or sum(len(bundle["facts"]) for bundle in bundles) > 20000
    ):
        raise ValueError("PUBLICATION_OUTPUT_LIMIT")
    return Publication(tuple(entities.values()), tuple(dependencies.values()), tuple(bundles))
