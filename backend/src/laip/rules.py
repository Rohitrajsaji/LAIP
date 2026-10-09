"""Deterministic, source-backed rule candidates; business interpretation requires review."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from laip.canonical import digest, identity_id, record_id
from laip.persistence import validate
from laip.rpgle_analysis import PreparedAnalysis


@dataclass(frozen=True)
class RuleLimits:
    max_nodes: int = 2000
    max_candidates: int = 2000
    max_evidence: int = 20_000
    max_work: int = 2_000_000
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("INVALID_RULE_LIMITS")


DEFAULT_LIMITS = RuleLimits()
_ACTIONS = frozenset(
    {
        "assignment",
        "call",
        "callp",
        "exsr",
        "chain",
        "read",
        "reade",
        "readp",
        "readpe",
        "write",
        "update",
        "delete",
        "exfmt",
        "override",
        "delete_override",
        "submit_job",
        "message_send",
        "message_receive",
        "file_read",
    }
)
_VALIDATIONS = frozenset({"RANGE", "VALUES", "COMP", "CHECK"})


def extract_rules(
    analysis: PreparedAnalysis,
    *,
    run_id: str,
    system_namespace: str,
    created_at: str,
    program_entity_ids: Sequence[str] = (),
    procedure_entity_ids: Sequence[str] = (),
    application_entity_ids: Sequence[str] = (),
    previous_revisions: Sequence[dict[str, Any]] = (),
    limits: RuleLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Extract local candidates with verified source facts, never approved interpretations."""
    if run_id != analysis.run_id or system_namespace != analysis.system_namespace:
        raise ValueError("RULE_ANALYSIS_BASIS_MISMATCH")
    deadline = time.monotonic() + limits.timeout_seconds
    work = 0

    def check(amount: int = 1) -> None:
        nonlocal work
        work += amount
        if work > limits.max_work or time.monotonic() > deadline or (cancelled and cancelled()):
            raise ValueError("RULE_RESOURCE_LIMIT_OR_CANCELLED")

    check()
    nodes = analysis.ir["nodes"]
    if (
        len(nodes) > limits.max_nodes
        or len(analysis.evidence) > limits.max_evidence
        or len(previous_revisions) > limits.max_candidates
    ):
        raise ValueError("RULE_INPUT_LIMIT")
    for collection in (program_entity_ids, procedure_entity_ids, application_entity_ids):
        if len(collection) > limits.max_candidates or any(
            not re.fullmatch(r"ent_[a-f0-9]{64}", value) for value in collection
        ):
            raise ValueError("INVALID_RULE_ASSOCIATION")
    by_id = {node["id"]: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("DUPLICATE_IR_NODE")
    adjacency: dict[int, list[tuple[int, str]]] = {i: [] for i in by_id}
    pred: dict[int, set[int]] = {i: set() for i in by_id}
    for edge in analysis.ir.get("edges", []):
        check()
        if edge["from"] not in by_id or edge["to"] not in by_id:
            raise ValueError("INVALID_IR_EDGE")
        adjacency[edge["from"]].append((edge["to"], edge["kind"]))
        pred[edge["to"]].add(edge["from"])
    entries = {
        scope["entry"] for scope in analysis.ir.get("scopes", []) if scope.get("entry") in by_id
    }
    if not analysis.ir.get("scopes"):
        entries.update(i for i in by_id if not pred[i])
    if nodes and not entries:
        entries.add(nodes[0]["id"])
    reachable = set(entries)
    frontier = list(entries)
    while frontier:
        check()
        for target, _ in adjacency[frontier.pop()]:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    check(len(reachable) * len(reachable))
    dom = {i: ({i} if i in entries else set(reachable)) for i in reachable}
    changed = True
    while changed:
        changed = False
        for i in sorted(reachable - entries):
            check(len(reachable))
            parents = [dom[p] for p in pred[i] if p in reachable]
            dominator_value = {i} | (set.intersection(*parents) if parents else set())
            if dom[i] != dominator_value:
                dom[i] = dominator_value
                changed = True

    def branch_reaches(head: int, target: int, wanted: int) -> bool:
        visited = {head}
        todo = [target]
        while todo:
            check()
            current = todo.pop()
            if current == wanted:
                return True
            if current in visited:
                continue
            visited.add(current)
            todo.extend(n for n, _ in adjacency[current])
        return False

    evidence_by_index: dict[int, list[dict[str, Any]]] = {}
    providers: dict[str, dict[str, Any]] = {}
    for ev in analysis.evidence:
        check()
        validate("Evidence", ev)
        providers[digest(ev["provider"])] = ev["provider"]
        if ev["run_id"] != run_id:
            raise ValueError("RULE_EVIDENCE_RUN_MISMATCH")
        evidence_by_index.setdefault(ev["metadata"]["statement_index"], []).append(ev)
    prior_by_entity: dict[str, list[dict[str, Any]]] = {}
    for revision in previous_revisions:
        check()
        validate("RuleRevision", revision)
        if revision["revision_id"] != record_id("rev_", revision, "revision_id"):
            raise ValueError("INVALID_PREVIOUS_RULE_REVISION")
        prior_by_entity.setdefault(revision["rule_entity_id"], []).append(revision)
    entities = []
    revisions = []
    diagnostics = []
    anchors: dict[str, int] = {}
    source_members = sorted({eid for ev in analysis.evidence for eid in ev["subject_entity_ids"]})
    for node in nodes:
        check()
        statement = node["statement"]
        kind = statement["kind"]
        attrs = statement["attributes"]
        validations = [k for k in attrs.get("keywords", []) if k["name"] in _VALIDATIONS]
        if kind not in _ACTIONS and not validations:
            continue
        conditional_validation = any(
            group.get("indicators")
            and any(k["name"] in _VALIDATIONS for k in group.get("keywords", []))
            for group in attrs.get("conditional_keywords", [])
        )
        if validations and (attrs.get("indicators") or conditional_validation):
            diagnostics.append(
                {"node": node["id"], "code": "CONDITIONAL_DDS_VALIDATION_REQUIRES_REVIEW"}
            )
            continue
        if statement.get("barrier"):
            diagnostics.append({"node": node["id"], "code": "OPAQUE_ACTION_NOT_INTERPRETED"})
            continue
        if not source_members and not program_entity_ids:
            diagnostics.append({"node": node["id"], "code": "STABLE_SOURCE_IDENTITY_REQUIRED"})
            continue
        spans = node.get("source_locations", [])
        support = list(evidence_by_index.get(node["stmt_index"], []))
        if not spans or not support:
            diagnostics.append({"node": node["id"], "code": "SOURCE_EVIDENCE_REQUIRED"})
            continue
        scope = "main"
        for current in analysis.ir.get("scopes", []):
            check()
            if (
                current.get("entry") is not None
                and current.get("end") is not None
                and current["entry"] <= node["id"] <= current["end"]
            ):
                entry = by_id[current["entry"]]["statement"]
                scope = entry["attributes"].get("name", current["kind"])
        target = attrs.get("target", attrs.get("name", attrs.get("file", "")))
        anchor = digest(
            {
                "source_members": source_members,
                "programs": [] if source_members else sorted(set(program_entity_ids)),
                "scope": scope,
                "kind": kind,
                "target": target,
                "validation_names": [v["name"] for v in validations],
            }
        )
        ordinal = anchors.get(anchor, 0)
        anchors[anchor] = ordinal + 1
        identity = {
            "system_namespace": system_namespace,
            "kind": "BusinessRule",
            "qualified_identity": ["source-candidate", anchor, str(ordinal)],
        }
        rule_id = identity_id(identity)
        conditions = []
        control_heads = dom.get(node["id"], set()) - {node["id"]}
        if not analysis.ir.get("conclusions_allowed", False):
            control_heads = set()
        for head in sorted(control_heads):
            check()
            branch = by_id[head]
            branch_attrs = branch["statement"]["attributes"]
            expression = branch_attrs.get("expression")
            if branch["statement"]["kind"] == "for" and expression is None:
                expression = {
                    "operation": "loop_range",
                    "target": branch_attrs.get("target", branch_attrs.get("name")),
                    "start": branch_attrs.get("start", branch_attrs.get("from")),
                    "end": branch_attrs.get("end", branch_attrs.get("to")),
                    "step": branch_attrs.get("step", branch_attrs.get("by")),
                    "direction": branch_attrs.get("direction"),
                }
            if expression is None or branch["statement"]["kind"] not in {
                "if",
                "elseif",
                "dow",
                "for",
                "when",
            }:
                continue
            links = adjacency[head]
            positive = [n for n, label in links if label in {"true", "body"}]
            negative = [n for n, label in links if label in {"false", "exit"}]
            if not positive or not negative:
                continue
            yes = any(branch_reaches(head, n, node["id"]) for n in positive)
            no = any(branch_reaches(head, n, node["id"]) for n in negative)
            if yes == no or (branch["statement"]["kind"] in {"dow", "for"} and not yes):
                continue
            locations = branch.get("source_locations", [])
            branch_support = evidence_by_index.get(branch["stmt_index"], [])
            if not locations or not branch_support:
                continue
            conditions.append(
                {
                    "node": "operator",
                    "value": {
                        "operation": "condition",
                        "polarity": "true" if yes else "false",
                        "expression": expression,
                    },
                    "source_locations": locations,
                }
            )
            support.extend(branch_support)
        value = {
            "operation": "validation" if validations else kind,
            "target": target,
            "expression": attrs.get("expression"),
            "operator": attrs.get("operator"),
            "validations": validations,
            "arguments": attrs.get("arguments"),
            "uses": statement.get("uses", []),
            "defines": statement.get("defines", []),
        }
        actions = [{"node": "operator", "value": value, "source_locations": spans}]
        check(len(support))
        support_by_id = {ev["evidence_id"]: ev for ev in support}
        evidence_ids = sorted(support_by_id)
        fingerprint = digest(
            {
                "evidence": sorted(
                    [
                        {"evidence_id": ev["evidence_id"], "content_sha256": ev["content_sha256"]}
                        for ev in support_by_id.values()
                    ],
                    key=lambda ev: ev["evidence_id"],
                ),
                "providers": sorted(providers.values(), key=lambda p: (p["id"], p["version"])),
                "resolution_decisions": [],
                "configuration_sha256": analysis.configuration_sha256,
            }
        )
        limitations = [
            "Syntactic rule candidate; business meaning and applicability require analyst review.",
            "Associations are supplied; filenames never imply a program or application.",
        ]
        if not analysis.ir.get("conclusions_allowed", False):
            limitations.append(
                "Unknown constructs or effects prevent complete business conclusions."
            )
        name = f"Candidate {'validation' if validations else kind} {target or scope}"
        entity = {
            "schema_version": "0.1.0",
            "entity_id": rule_id,
            "identity": identity,
            "display_name": name,
            "original_names": [name],
            "aliases": [],
            "parent_id": None,
            "source_availability": "available",
            "analysis_status": "partial",
            "attributes": {
                "review_status": "pending",
                "extractor": {"id": "laip-deterministic-rules", "version": "0.1.0"},
                "classification": "inferred",
                "candidate_kind": "validation"
                if validations
                else "calculation"
                if kind == "assignment"
                else "action",
                "scope": scope,
                "source_statement_index": node["stmt_index"],
                "source_locations": spans,
                "conditions": conditions,
                "actions": actions,
                "limitations": limitations,
            },
            "evidence_ids": evidence_ids,
        }
        validate("Entity", entity)
        entities.append(entity)
        if len(entities) > limits.max_candidates:
            raise ValueError("RULE_CANDIDATE_LIMIT")
        if not program_entity_ids:
            diagnostics.append(
                {
                    "node": node["id"],
                    "code": "VERIFIED_PROGRAM_ASSOCIATION_REQUIRED",
                    "rule_entity_id": rule_id,
                }
            )
            continue
        previous = prior_by_entity.get(rule_id, [])
        referenced = {r["previous_revision_id"] for r in previous}
        heads = list(
            {r["revision_id"]: r for r in previous if r["revision_id"] not in referenced}.values()
        )
        if previous and not heads:
            raise ValueError("INVALID_PREVIOUS_RULE_CHAIN")
        if len(heads) > 1:
            raise ValueError("AMBIGUOUS_PREVIOUS_RULE_HEAD")
        revision = {
            "schema_version": "0.1.0",
            "rule_entity_id": rule_id,
            "previous_revision_id": heads[0]["revision_id"] if heads else None,
            "run_id": run_id,
            "application_entity_ids": sorted(set(application_entity_ids)),
            "program_entity_ids": sorted(set(program_entity_ids)),
            "procedure_entity_ids": sorted(set(procedure_entity_ids)),
            "name": name,
            "description": f"Source-backed {kind} candidate in {scope}; pending review.",
            "conditions": conditions,
            "actions": actions,
            "classification": "inferred",
            "interpretation_method": "deterministic",
            "evidence_ids": evidence_ids,
            "support_fingerprint": fingerprint,
            "limitations": limitations,
            "created_at": created_at,
        }

        def comparable(r: dict[str, Any]) -> dict[str, Any]:
            return {
                k: v
                for k, v in r.items()
                if k not in {"revision_id", "previous_revision_id", "created_at"}
            }

        if heads and comparable(heads[0]) == comparable(revision):
            revision = dict(heads[0])
        revision["revision_id"] = record_id("rev_", revision, "revision_id")
        validate("RuleRevision", revision)
        revisions.append(revision)
    check()
    return {"entities": entities, "rule_revisions": revisions, "diagnostics": diagnostics}


def extract_rules_v2(
    analysis: PreparedAnalysis,
    publication: Any,
    *,
    run_id: str,
    system_namespace: str,
    created_at: str,
    program_entity_ids: Sequence[str] = (),
    previous_revisions: Sequence[dict[str, Any]] = (),
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Versioned fact-closed rules with structured claim-local control dependence.

    The caller must persist fact bundles and replace the provisional support
    fingerprint with Repository.rule_support_fingerprint before publication.
    Legacy extract_rules remains byte-for-byte compatible.
    """
    from laip.claim_slices import FactSlice
    from laip.claim_support import support_fingerprint

    if analysis.run_id != run_id or analysis.system_namespace != system_namespace:
        raise ValueError("RULE_EVIDENCE_RUN_MISMATCH")
    slices = FactSlice(analysis, publication)
    entities: list[dict[str, Any]] = []
    revisions = []
    diagnostics = []
    ordinal: dict[str, int] = {}
    for node in analysis.ir["nodes"]:
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        kind, attrs = node["statement"]["kind"], node["statement"]["attributes"]
        validations = [v for v in attrs.get("keywords", []) if v["name"] in _VALIDATIONS]
        if kind not in _ACTIONS | {"display"} and not validations:
            continue
        if node["statement"].get("barrier"):
            diagnostics.append({"node": node["id"], "code": "OPAQUE_ACTION_NOT_INTERPRETED"})
            continue
        evs = [
            ev
            for ev in analysis.evidence
            if ev["metadata"].get("statement_index") == node["stmt_index"]
        ]
        if not node.get("source_locations") or not evs:
            diagnostics.append({"node": node["id"], "code": "SOURCE_EVIDENCE_REQUIRED"})
            continue
        target = attrs.get("target", attrs.get("name", attrs.get("file", "")))
        subjects = list(program_entity_ids)
        if analysis.ir.get("language") == "DDS":
            subjects = [
                entity["entity_id"]
                for entity in publication.entities
                if entity["identity"]["kind"] == "Field"
                and entity["display_name"].casefold() == str(target).casefold()
                and set(entity["evidence_ids"]) & {ev["evidence_id"] for ev in evs}
            ]
        if not subjects:
            subjects = sorted({eid for ev in evs for eid in ev["subject_entity_ids"]})
        if not subjects:
            raise ValueError("STABLE_SOURCE_IDENTITY_REQUIRED")
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
        support = slices.support(node["id"], subjects, conditions)
        if validations and (
            attrs.get("indicators")
            or any(group.get("indicators") for group in attrs.get("conditional_keywords", []))
        ):
            support["state"] = "conditional"
            support["limitations"].append(
                "DDS indicator-controlled validation requires device/state review."
            )
        if validations:
            # DDS field declaration facts are source observations; no invented Program.
            for fid in slices.node_facts(node["id"], "symbol"):
                support["symbol_dependencies"].append(fid["fact_id"])
                support["evidence_ids"] = sorted(
                    set(support["evidence_ids"]) | set(fid["evidence_ids"])
                )
        value = {
            "operation": "validation" if validations else kind,
            "target": target,
            "expression": attrs.get("expression"),
            "operator": attrs.get("operator"),
            "validations": validations,
            "arguments": attrs.get("arguments"),
            "uses": node["statement"].get("uses", []),
            "defines": node["statement"].get("defines", []),
        }
        actions = [
            {"node": "operator", "value": value, "source_locations": node["source_locations"]}
        ]
        scope = node.get("lexical_scope", "main")
        anchor = digest(
            {
                "subjects": subjects,
                "scope": scope,
                "kind": kind,
                "target": target,
                "source_members": sorted(
                    {eid for ev in analysis.evidence for eid in ev["subject_entity_ids"]}
                ),
            }
        )
        count = ordinal.get(anchor, 0)
        ordinal[anchor] = count + 1
        identity = {
            "system_namespace": system_namespace,
            "kind": "BusinessRule",
            "qualified_identity": ["fact-source-candidate", anchor, str(count)],
        }
        rule_id = identity_id(identity)
        previous = [r for r in previous_revisions if r["rule_entity_id"] == rule_id]
        referenced = {r["previous_revision_id"] for r in previous}
        heads = {r["revision_id"]: r for r in previous if r["revision_id"] not in referenced}
        if len(heads) > 1:
            raise ValueError("AMBIGUOUS_PREVIOUS_RULE_HEAD")
        name = f"Candidate {'validation' if validations else kind} {target or scope}"
        limitations = [
            *support["limitations"],
            "Source interpretation requires analyst review; observed statements "
            "do not prove business intent.",
        ]
        if support["state"] != "supported_within_profile":
            limitations.append(
                "Claim-dependent effects, bindings or control paths remain limited or unresolved."
            )
        revision = {
            "schema_version": "0.2.0",
            "rule_entity_id": rule_id,
            "previous_revision_id": next(iter(heads), None),
            "run_id": run_id,
            "application_entity_ids": [],
            "program_entity_ids": list(program_entity_ids),
            "procedure_entity_ids": [],
            "subject_entity_ids": support["subject_entity_ids"],
            "name": name,
            "description": f"Source-backed {kind} candidate in {scope}; pending review.",
            "conditions": conditions,
            "actions": actions,
            "classification": "inferred",
            "interpretation_method": "deterministic",
            "evidence_ids": support["evidence_ids"],
            "support_fingerprint": support_fingerprint(support),
            "claim_support": support,
            "limitations": limitations,
            "created_at": created_at,
        }
        revision["revision_id"] = record_id("rev_", revision, "revision_id")
        entity = {
            "schema_version": "0.2.0",
            "entity_id": rule_id,
            "identity": identity,
            "display_name": name,
            "original_names": [name],
            "aliases": [],
            "parent_id": None,
            "source_availability": "available",
            "analysis_status": "analyzed"
            if support["state"] == "supported_within_profile"
            else "partial",
            "attributes": {
                "review_status": "pending",
                "conditions": conditions,
                "actions": actions,
                "claim_support": support,
                "source_statement_index": node["stmt_index"],
            },
            "evidence_ids": support["evidence_ids"],
        }
        validate("Entity", entity)
        validate("RuleRevision", revision)
        entities.append(entity)
        revisions.append(revision)
    return {"entities": entities, "rule_revisions": revisions, "diagnostics": diagnostics}
