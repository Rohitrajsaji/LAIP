"""Bounded possible call paths over explicit Programs and closed static evidence.

Paths describe possible relationships, never execution order or runtime proof.
This pure stage neither resolves names nor upgrades analyst review status.
"""

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from laip.canonical import identity_id, record_id
from laip.persistence import validate

STATIC_LIMITATION = "Possible static call path; runtime execution and ordering are not established."


class _BudgetExceeded(Exception):
    pass


class _SupportError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def compose_workflows(
    dependencies: Iterable[dict[str, Any]],
    *,
    run_id: str,
    system_namespace: str,
    created_at: str,
    program_entities: Iterable[dict[str, Any]],
    evidence: Iterable[dict[str, Any]],
    max_depth: int = 32,
    max_paths: int = 1000,
    max_records: int = 20000,
    max_support: int = 20000,
    max_steps: int = 100000,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Return canonical Workflow entities, immutable path payloads and diagnostics.

    Each input collection is bounded by ``max_records``. ``max_depth`` counts
    Programs, not edges, and cannot exceed the 128-Program persistence profile.
    Truncated valid prefixes retain explicit limitations.
    A global work budget bounds support expansion as well as graph traversal.
    """
    if (
        any(
            type(value) is not int or value < 1
            for value in (
                max_depth,
                max_paths,
                max_records,
                max_support,
                max_steps,
            )
        )
        or max_depth > 128
    ):
        raise ValueError("INVALID_WORKFLOW_LIMIT")
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(system_namespace, str)
        or not system_namespace
    ):
        raise ValueError("INVALID_WORKFLOW_SCOPE")
    try:
        stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None or "T" not in created_at:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("INVALID_WORKFLOW_TIMESTAMP") from exc
    diagnostics: list[dict[str, Any]] = []
    diagnostic_keys: set[tuple[str, str, str | None]] = set()
    workflows: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    emitted: set[str] = set()
    steps = 0

    def check() -> None:
        if cancelled and cancelled():
            raise ValueError("CANCELLED")

    def tick() -> None:
        nonlocal steps
        check()
        steps += 1
        if steps > max_steps:
            raise _BudgetExceeded

    def diagnostic(code: str, message: str, subject: str | None = None) -> None:
        key = (code, message, subject)
        if key not in diagnostic_keys:
            diagnostic_keys.add(key)
            diagnostics.append(
                {
                    "code": code,
                    "message": message,
                    "artifact_id": None,
                    "location": None,
                    "retryable": False,
                    "provider_code": subject,
                }
            )

    def index(records: Iterable[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for count, record in enumerate(records, 1):
            check()
            if count > max_records:
                raise ValueError("WORKFLOW_INPUT_LIMIT")
            if not isinstance(record, dict) or not isinstance(record.get(field), str):
                raise ValueError("INVALID_WORKFLOW_INPUT")
            key = record[field]
            if key in indexed and indexed[key] != record:
                raise ValueError("WORKFLOW_INPUT_COLLISION")
            indexed[key] = record
        return indexed

    supplied_programs = index(program_entities, "entity_id")
    supplied_evidence = index(evidence, "evidence_id")
    supplied_dependencies = index(dependencies, "dependency_id")
    programs: dict[str, dict[str, Any]] = {}
    for key, entity in sorted(supplied_programs.items()):
        check()
        try:
            validate("Entity", entity)
            identity = entity["identity"]
            if (
                key != identity_id(identity)
                or identity["kind"] != "Program"
                or identity["system_namespace"] != system_namespace
                or len(identity["qualified_identity"]) != 3
                or identity["qualified_identity"][1] != "*PGM"
            ):
                continue
            programs[key] = entity
        except Exception:
            diagnostic("invalid_input", "Invalid canonical Program input.", key)

    def support_closure(ids: list[str]) -> tuple[set[str], set[str]]:
        closed: set[str] = set()
        active: set[str] = set()
        limitations: set[str] = set()
        pending = [(key, False) for key in reversed(sorted(set(ids)))]
        while pending:
            tick()
            key, exiting = pending.pop()
            if exiting:
                active.remove(key)
                closed.add(key)
                continue
            if key in closed:
                continue
            if key in active:
                raise _SupportError("analysis_barrier", "Evidence support contains a cycle.")
            if len(closed) + len(active) >= max_support:
                raise _SupportError("limit_exceeded", "Evidence support closure limit reached.")
            record = supplied_evidence.get(key)
            if record is None:
                raise _SupportError("missing_evidence", "Call support evidence is unavailable.")
            try:
                validate("Evidence", record)
                if key != record_id("ev_", record, "evidence_id"):
                    raise ValueError
            except Exception as exc:
                raise _SupportError("invalid_digest", "Call support evidence is invalid.") from exc
            if record["run_id"] != run_id:
                raise _SupportError("invalid_scope", "Call support belongs to another run.")
            metadata = record["metadata"]
            if (
                record["classification"] == "unresolved"
                or record["contradiction_evidence_ids"]
                or metadata.get("barrier")
                or metadata.get("conclusions_allowed") is False
            ):
                raise _SupportError("analysis_barrier", "Call support is unresolved or blocked.")
            for field in ("references", "relationships", "calls"):
                refs = metadata.get(field, [])
                if not isinstance(refs, list) or any(
                    not isinstance(ref, dict)
                    or ref.get("dynamic")
                    or ref.get("resolution") in ("unresolved", "ambiguous", "dynamic", "missing")
                    for ref in refs
                ):
                    raise _SupportError(
                        "unresolved_reference", "Call evidence has unresolved targets."
                    )
            supports = record["supporting_evidence_ids"]
            if not supports and (
                record["classification"] != "observed"
                or record["authority"]
                not in (
                    "source",
                    "imported_metadata",
                    "historical_reference",
                    "system_service",
                )
            ):
                raise _SupportError("missing_evidence", "Call has no observed static support.")
            limitations.update(record["limitations"])
            active.add(key)
            pending.append((key, True))
            pending.extend((support, False) for support in reversed(sorted(set(supports))))
        return closed, limitations

    adjacency: dict[str, list[dict[str, Any]]] = {}
    support_by_edge: dict[str, tuple[set[str], set[str]]] = {}
    indegree: dict[str, int] = {}
    reached: set[str] = set()

    def emit(path: list[str], edges: list[dict[str, Any]], boundary: str | None = None) -> None:
        if not edges:
            return
        ids: set[str] = set()
        limitations = {STATIC_LIMITATION}
        for edge in edges:
            closure, support_limits = support_by_edge[edge["dependency_id"]]
            ids.update(closure)
            limitations.update(support_limits)
            limitations.update(edge["limitations"])
        if boundary:
            limitations.add(boundary)
        identity = {
            "system_namespace": system_namespace,
            "kind": "Workflow",
            "qualified_identity": ["possible-call-path", *path],
        }
        eid = identity_id(identity)
        payload: dict[str, Any] = {
            "schema_version": "0.1.0",
            "workflow_entity_id": eid,
            "run_id": run_id,
            "program_entity_ids": list(path),
            "dependency_ids": [edge["dependency_id"] for edge in edges],
            "evidence_ids": sorted(ids),
            "classification": "inferred",
            "review_status": "pending_review",
            "limitations": sorted(limitations),
            "created_at": created_at,
        }
        payload["workflow_id"] = record_id("wf_", payload, "workflow_id")
        if payload["workflow_id"] in emitted:
            return
        if len(workflows) >= max_paths:
            diagnostic("limit_exceeded", "Possible call path count limit reached.")
            return
        emitted.add(payload["workflow_id"])
        name = "Possible call path: " + " → ".join(programs[key]["display_name"] for key in path)
        entity = {
            "schema_version": "0.1.0",
            "entity_id": eid,
            "identity": identity,
            "display_name": name,
            "original_names": [name],
            "aliases": [],
            "parent_id": None,
            "source_availability": "not_applicable",
            "analysis_status": "partial" if boundary else "analyzed",
            "attributes": {
                "classification": "inferred",
                "review_status": "pending_review",
            },
            "evidence_ids": sorted(ids),
        }
        validate("Entity", entity)
        workflows.append(payload)
        entities.append(entity)

    try:
        for key, edge in sorted(supplied_dependencies.items()):
            tick()
            if edge.get("relationship") != "CALLS":
                continue
            try:
                validate("Dependency", edge)
                if key != record_id("dep_", edge, "dependency_id"):
                    raise ValueError
            except Exception:
                diagnostic("invalid_input", "Invalid canonical call dependency.", key)
                continue
            if (
                edge["run_id"] != run_id
                or edge["resolution_context"]["namespace"] != system_namespace
            ):
                diagnostic(
                    "invalid_scope", "Call dependency has a different run or namespace.", key
                )
                continue
            if edge["resolution"] != "resolved" or edge["classification"] == "unresolved":
                code = (
                    "ambiguous_reference"
                    if edge["resolution"] == "ambiguous"
                    else "unresolved_reference"
                )
                diagnostic(code, "Unresolved call cannot establish a possible Program path.", key)
                continue
            source, target = edge["from_entity_id"], edge["to_entity_id"]
            if source not in programs or target not in programs:
                diagnostic(
                    "invalid_scope", "Call endpoints must be explicit qualified Programs.", key
                )
                continue
            try:
                support_by_edge[key] = support_closure(edge["evidence_ids"])
            except _SupportError as exc:
                diagnostic(exc.code, exc.message, key)
                continue
            adjacency.setdefault(source, []).append(edge)
            indegree[target] = indegree.get(target, 0) + 1
            indegree.setdefault(source, 0)

        roots = sorted(key for key in adjacency if not indegree[key])
        roots.extend(key for key in sorted(adjacency) if key not in roots)
        stop = False
        for root in roots:
            if stop:
                break
            if root in reached:
                continue
            pending_paths: list[tuple[list[str], list[dict[str, Any]]]] = [([root], [])]
            while pending_paths:
                tick()
                path, edges = pending_paths.pop()
                reached.update(path)
                next_edges = adjacency.get(path[-1], [])
                if next_edges and len(path) >= max_depth:
                    diagnostic("path_limit_reached", "Possible call path depth limit reached.")
                    emit(path, edges, "Path truncated at the configured depth limit.")
                elif not next_edges:
                    emit(path, edges)
                else:
                    for edge in reversed(next_edges):
                        tick()
                        target = edge["to_entity_id"]
                        if target in path:
                            diagnostic(
                                "analysis_barrier",
                                "Call cycle stops possible path traversal.",
                                edge["dependency_id"],
                            )
                            emit(path, edges, "Call cycle cut; repeated execution is unresolved.")
                        else:
                            if len(pending_paths) >= max_paths:
                                diagnostic("limit_exceeded", "Pending call path limit reached.")
                            else:
                                pending_paths.append(([*path, target], [*edges, edge]))
                if len(workflows) >= max_paths and (
                    pending_paths or any(other not in reached for other in roots)
                ):
                    diagnostic("limit_exceeded", "Possible call path count limit reached.")
                    stop = True
                    break
    except _BudgetExceeded:
        diagnostic("limit_exceeded", "Workflow composition work limit reached.")

    # Multiple cycle exits or duplicate logical references can yield the same path.
    by_id = {workflow["workflow_id"]: workflow for workflow in workflows[:max_paths]}
    entity_by_id: dict[str, dict[str, Any]] = {}
    for entity in entities:
        key = entity["entity_id"]
        if key in entity_by_id:
            previous = entity_by_id[key]
            previous["evidence_ids"] = sorted(
                set(previous["evidence_ids"]) | set(entity["evidence_ids"])
            )
            if entity["analysis_status"] == "partial":
                previous["analysis_status"] = "partial"
        else:
            entity_by_id[key] = entity
    return {
        "entities": [entity_by_id[key] for key in sorted(entity_by_id)],
        "workflows": [by_id[key] for key in sorted(by_id)],
        "diagnostics": sorted(
            diagnostics, key=lambda d: (d["code"], d["provider_code"] or "", d["message"])
        ),
    }


def compose_local_workflows(
    analyses: list[Any],
    publication: Any,
    *,
    run_id: str,
    system_namespace: str,
    created_at: str,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Resolved local normal-flow update→call→write source paths, never assurance."""
    from laip.canonical import digest
    from laip.claim_slices import FactSlice
    from laip.claim_support import support_fingerprint

    entities = []
    workflows = []
    diagnostics = []
    for analysis in analyses:
        if analysis.ir.get("language") == "DDS":
            continue
        slices = FactSlice(analysis, publication)
        for node in analysis.ir["nodes"]:
            if cancelled and cancelled():
                raise ValueError("CANCELLED")
            call = node.get("call_resolution")
            if (
                not call
                or call["resolution"] != "resolved"
                or call.get("signature_status") != "supported"
            ):
                continue
            prior = [
                n
                for n in analysis.ir["nodes"]
                if n["id"] < node["id"]
                and n.get("lexical_scope") == node.get("lexical_scope")
                and slices.guards[n["id"]] == slices.guards[node["id"]]
            ]
            if not prior or prior[-1]["statement"]["kind"] != "update":
                continue
            update = prior[-1]
            if update.get("record_binding", {}).get("resolution") != "resolved":
                continue
            callee_scope = slices.nodes[call["target_node"]].get("lexical_scope")
            callee = [n for n in analysis.ir["nodes"] if n.get("lexical_scope") == callee_scope]
            writes = [
                n
                for n in callee
                if n["statement"]["kind"] == "write"
                and n.get("record_binding", {}).get("resolution") == "resolved"
            ]
            for write in writes:
                # Only an unconditional callee normal-flow WRITE is summarized.
                if slices.guards[write["id"]] or any(n["statement"]["barrier"] for n in callee):
                    continue
                callsites = slices.node_facts(node["id"], "callsite")
                if len(callsites) != 1:
                    continue
                reference = slices.facts[callsites[0]["data"]["target_reference"]]
                procedure = reference["data"]["target_entity_id"]
                if not procedure:
                    continue
                program = reference["scope_id"]
                if any(
                    n["statement"]["kind"]
                    in {"return", "leave", "iter", "goto", "monitor", "on_error"}
                    for n in callee
                    if n["id"] < write["id"]
                ):
                    continue
                if any(
                    f["data"]["unknown"]
                    for n in callee
                    for f in slices.node_facts(n["id"], "effect")
                ):
                    continue
                conditions = []
                for head, polarity in slices.guards[node["id"]]:
                    branch = slices.nodes[head]
                    expression = branch["statement"]["attributes"].get("expression")
                    if expression is not None and polarity != "limited":
                        conditions.append(
                            {
                                "node": "operator",
                                "value": {
                                    "operation": "condition",
                                    "polarity": polarity,
                                    "expression": expression,
                                },
                                "source_locations": branch["source_locations"],
                            }
                        )
                support = slices.support(node["id"], [program, procedure], conditions)
                # IR conservatively marks every invocation unknown. A resolved,
                # representation-checked local call gets a bounded normal-flow
                # source summary only after its entire callee is effect-closed.
                summarized = {
                    f["fact_id"]
                    for f in slices.node_facts(node["id"], "effect")
                    if f["data"]["unknown"] and f["data"]["operation"] == "call"
                }
                summarized_evidence = {
                    eid for fid in summarized for eid in slices.facts[fid]["evidence_ids"]
                }
                support["effect_dependencies"] = [
                    fid for fid in support["effect_dependencies"] if fid not in summarized
                ]
                support["barriers"] = [
                    b
                    for b in support["barriers"]
                    if not (
                        b["code"] == "UNKNOWN_EFFECT"
                        and set(b["evidence_ids"]).issubset(summarized_evidence)
                    )
                ]
                for current in (update, write):
                    additional = slices.support(current["id"], [program, procedure], conditions)
                    for key in ("evidence_ids", "symbol_dependencies", "effect_dependencies"):
                        support[key] = sorted(set(support[key]) | set(additional[key]))
                    support["resolution_decisions"] += additional["resolution_decisions"]
                    support["barriers"] += additional["barriers"]
                support["resolution_decisions"] = list(
                    {d["reference"]: d for d in support["resolution_decisions"]}.values()
                )
                support["evidence_ids"] = sorted(
                    set(support["evidence_ids"]) | set(callsites[0]["evidence_ids"])
                )
                if support["barriers"] or any(
                    d["resolution"] != "resolved" for d in support["resolution_decisions"]
                ):
                    diagnostics.append(
                        {"node": node["id"], "code": "LOCAL_WORKFLOW_SUPPORT_BLOCKED"}
                    )
                    continue
                support["state"] = "conditional"
                limitations = [
                    "Bounded normal-flow source sequence; exceptions may interrupt it.",
                    "Local parameter shadowing does not prove global log-field population.",
                    "Account update and log write are not proven atomic or committed.",
                    "Clock expressions are nondeterministic; TXNID uniqueness is not proven.",
                    "Supplied identity resolution is not compiled-object or live IBM i validation.",
                ]
                steps = []
                for operation, current, subject in [
                    ("update", update, program),
                    ("call", node, procedure),
                    ("write", write, procedure),
                ]:
                    facts = [
                        f
                        for f in slices.node_facts(current["id"], "effect")
                        if f["fact_id"] not in summarized
                    ]
                    eids = sorted({eid for f in facts for eid in f["evidence_ids"]})
                    support["effect_dependencies"] = sorted(
                        set(support["effect_dependencies"]) | {f["fact_id"] for f in facts}
                    )
                    support["evidence_ids"] = sorted(set(support["evidence_ids"]) | set(eids))
                    steps.append(
                        {
                            "operation": operation,
                            "entity_id": subject,
                            "evidence_ids": eids,
                            "source_locations": current["source_locations"],
                            "fact_ids": sorted(f["fact_id"] for f in facts),
                        }
                    )
                identity = {
                    "system_namespace": system_namespace,
                    "kind": "Workflow",
                    "qualified_identity": [
                        "bounded-normal-flow",
                        program,
                        procedure,
                        "callsite:"
                        + str(node["source_locations"][0]["start_line"])
                        + ":"
                        + str(node["source_locations"][0]["start_column"]),
                    ],
                }
                entity_id = identity_id(identity)
                workflow = {
                    "schema_version": "0.2.0",
                    "workflow_entity_id": entity_id,
                    "run_id": run_id,
                    "program_entity_ids": [program],
                    "procedure_entity_ids": [procedure],
                    "dependency_ids": [],
                    "evidence_ids": support["evidence_ids"],
                    "classification": "inferred",
                    "review_status": "pending_review",
                    "limitations": limitations,
                    "created_at": created_at,
                    "steps": steps,
                    "claim_support": support,
                    "support_fingerprint": support_fingerprint(support),
                }
                workflow["workflow_id"] = "wf_" + digest(workflow)
                entities.append(
                    {
                        "schema_version": "0.2.0",
                        "entity_id": entity_id,
                        "identity": identity,
                        "display_name": "Possible account update and local log write",
                        "original_names": ["Possible account update and local log write"],
                        "aliases": [],
                        "parent_id": None,
                        "source_availability": "available",
                        "analysis_status": "partial",
                        "attributes": {
                            "review_status": "pending_review",
                            "normal_flow_only": True,
                            "steps": steps,
                        },
                        "evidence_ids": support["evidence_ids"],
                    }
                )
                workflows.append(workflow)
    return {"entities": entities, "workflows": workflows, "diagnostics": diagnostics}
