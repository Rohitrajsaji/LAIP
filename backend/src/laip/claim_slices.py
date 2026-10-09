"""Bounded structured control dependence and persisted-fact support slices.

Structured IF arms are lexical regions, not dominator approximations. Joins lose
all guards of the joined block; ELSEIF carries every prior arm's false guard.
"""

from typing import Any

from laip.claim_support import validate_claim_support


def structured_guards(ir: dict[str, Any]) -> dict[int, list[tuple[int, str]]]:
    nodes = ir["nodes"]
    if len(nodes) > 2000:
        raise ValueError("CONTROL_STRUCTURE_LIMIT")
    stack: list[dict[str, Any]] = []
    result = {}
    for node in nodes:
        kind = node["statement"]["kind"]
        if kind in {"procedure_start", "procedure_end", "subroutine_start", "subroutine_end"}:
            if stack:
                raise ValueError("CONTROL_STRUCTURE_SCOPE")
        if kind in {"elseif", "else"}:
            if not stack or stack[-1]["kind"] != "if" or stack[-1]["else"]:
                raise ValueError("CONTROL_STRUCTURE_ARM")
            current = stack[-1]
            current["terms"] = [(head, "false") for head in current["heads"]]
            if kind == "elseif":
                current["terms"].append((node["id"], "true"))
                current["heads"].append(node["id"])
            else:
                current["else"] = True
        elif kind in {"endif", "enddo", "endfor", "endsl", "endmon"}:
            expected = {
                "endif": "if",
                "enddo": "loop",
                "endfor": "loop",
                "endsl": "select",
                "endmon": "monitor",
            }[kind]
            if not stack or stack[-1]["kind"] != expected:
                raise ValueError("CONTROL_STRUCTURE_END")
            stack.pop()
        result[node["id"]] = [term for frame in stack for term in frame["terms"]]
        if kind == "if":
            stack.append(
                {
                    "kind": "if",
                    "heads": [node["id"]],
                    "terms": [(node["id"], "true")],
                    "else": False,
                }
            )
        elif kind in {"dow", "dou", "for"}:
            stack.append({"kind": "loop", "terms": [(node["id"], "limited")]})
        elif kind in {"select", "monitor"}:
            stack.append({"kind": kind, "terms": [(node["id"], "limited")]})
        if len(stack) > 128:
            raise ValueError("CONTROL_STRUCTURE_LIMIT")
    if stack:
        raise ValueError("CONTROL_STRUCTURE_UNCLOSED")
    return result


class FactSlice:
    def __init__(self, analysis: Any, publication: Any):
        self.analysis = analysis
        self.nodes = {node["id"]: node for node in analysis.ir["nodes"]}
        self.evidence = {ev["evidence_id"]: ev for ev in analysis.evidence}
        self.facts = {
            fact["fact_id"]: fact for bundle in publication.bundles for fact in bundle["facts"]
        }
        self.by_node: dict[int, list[dict[str, Any]]] = {}
        for fact in self.facts.values():
            for eid in fact["evidence_ids"]:
                if eid in self.evidence:
                    index = self.evidence[eid]["metadata"].get("statement_index")
                    self.by_node.setdefault(index, []).append(fact)
        self.guards = (
            structured_guards(analysis.ir)
            if analysis.ir.get("language") != "DDS"
            else {node: [] for node in self.nodes}
        )
        self.pred: dict[int, set[int]] = {node: set() for node in self.nodes}
        for edge in analysis.ir.get("edges", []):
            if edge["from"] not in self.nodes or edge["to"] not in self.nodes:
                raise ValueError("INVALID_IR_EDGE")
            self.pred[edge["to"]].add(edge["from"])

    def node_facts(self, node: int, kind: str) -> list[dict[str, Any]]:
        index = self.nodes[node]["stmt_index"]
        return list(
            {f["fact_id"]: f for f in self.by_node.get(index, []) if f["kind"] == kind}.values()
        )

    def compatible(self, prior: int, current: int) -> bool:
        current_guards = dict(self.guards[current])
        return all(
            current_guards.get(head, polarity) == polarity for head, polarity in self.guards[prior]
        )

    def support(
        self, node_id: int, subjects: list[str], conditions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        node = self.nodes[node_id]
        selected = {node_id, *(head for head, _ in self.guards[node_id])}
        barriers = []
        # Reachable predecessors, restricted to compatible branch arms and lexical scope.
        reachable = set()
        todo = list(selected)
        while todo:
            current = todo.pop()
            if current in reachable:
                continue
            reachable.add(current)
            if len(reachable) > 2000:
                raise ValueError("CLAIM_SLICE_LIMIT")
            todo.extend(self.pred.get(current, set()))
        scope = node.get("lexical_scope", "main")
        # Lexical joins alone cannot prove fallthrough after a terminating arm.
        # Keep those candidates inspectable, but block conclusions until their
        # exit-dependent control predicates are modeled within this profile.
        for prior in self.nodes.values():
            if (
                prior["id"] < node_id
                and prior.get("lexical_scope", "main") == scope
                and prior["statement"]["kind"] in {"return", "leave", "iter", "goto"}
            ):
                prior_evidence = [
                    ev["evidence_id"]
                    for ev in self.analysis.evidence
                    if ev["metadata"].get("statement_index") == prior["stmt_index"]
                ]
                barriers.append(
                    {
                        "code": "CONTROL_FLOW_TERMINATION_UNMODELED",
                        "evidence_ids": prior_evidence,
                    }
                )
        needed = set()
        effect_ids = set()
        symbol_ids = set()
        decisions = {}

        def include(current: int) -> None:
            selected.add(current)
            for fact in self.node_facts(current, "effect"):
                effect_ids.add(fact["fact_id"])
                needed.update(fact["data"]["reads"])
                if fact["data"]["unknown"]:
                    barriers.append(
                        {"code": "UNKNOWN_EFFECT", "evidence_ids": fact["evidence_ids"]}
                    )
                producer = fact["data"].get("status_producer_id")
                if producer:
                    effect_ids.add(producer)
            for fact in self.node_facts(current, "reference"):
                decisions[fact["fact_id"]] = {
                    "reference": fact["fact_id"],
                    "resolution": fact["data"]["resolution"],
                    "target_entity_id": fact["data"]["target_entity_id"],
                }
            for fact in self.node_facts(current, "diagnostic"):
                if fact["data"]["blocks_claim"]:
                    barriers.append(
                        {"code": fact["data"]["code"], "evidence_ids": fact["evidence_ids"]}
                    )

        for current in sorted(selected):
            include(current)
        for head, polarity in self.guards[node_id]:
            if polarity == "limited":
                eids = [
                    ev["evidence_id"]
                    for ev in self.analysis.evidence
                    if ev["metadata"].get("statement_index") == self.nodes[head]["stmt_index"]
                ]
                barriers.append({"code": "LIMITED_CONTROL_REGION", "evidence_ids": eids})
        for current in sorted(reachable, reverse=True):
            candidate = self.nodes[current]
            if (
                current in selected
                or candidate.get("lexical_scope", "main") != scope
                or not self.compatible(current, node_id)
            ):
                continue
            facts = self.node_facts(current, "effect")
            # Unknown effects before a value/status-dependent claim cannot be treated as no-ops.
            if any(f["data"]["unknown"] for f in facts) and needed:
                include(current)
            elif any(set(f["data"]["writes"]) & needed for f in facts):
                include(current)
        # Effects depend on resolved symbols/declarations and explicit status producers.
        pending = list(needed)
        while pending:
            fid = pending.pop()
            fact = self.facts.get(fid)
            if fact and fact["kind"] == "symbol":
                symbol_ids.add(fid)
            elif fid.startswith("unresolved:"):
                support = [
                    ev["evidence_id"]
                    for ev in self.analysis.evidence
                    if ev["metadata"].get("statement_index") == node["stmt_index"]
                ]
                barriers.append({"code": "UNRESOLVED_SYMBOL", "evidence_ids": support})
        cited: set[str] = set()
        for current in selected:
            cited.update(
                ev["evidence_id"]
                for ev in self.analysis.evidence
                if ev["metadata"].get("statement_index") == self.nodes[current]["stmt_index"]
            )
        for fid in effect_ids | symbol_ids | decisions.keys():
            cited.update(self.facts[fid]["evidence_ids"])
        for barrier in barriers:
            cited.update(barrier["evidence_ids"])
        state = (
            "blocked"
            if barriers
            else "unresolved"
            if any(d["resolution"] != "resolved" for d in decisions.values())
            else "supported_within_profile"
        )
        if (
            any(
                polarity == "limited"
                or self.nodes[head]["statement"]["kind"] in {"for", "dow", "dou"}
                for head, polarity in self.guards[node_id]
            )
            and state == "supported_within_profile"
        ):
            state = "conditional"
        result = {
            "support_schema_version": "0.2.0",
            "state": state,
            "profile_id": "bounded-structured-source",
            "profile_version": "0.2.0",
            "subject_entity_ids": sorted(set(subjects)),
            "evidence_ids": sorted(cited),
            "controlling_predicates": conditions,
            "symbol_dependencies": sorted(symbol_ids),
            "effect_dependencies": sorted(effect_ids),
            "resolution_decisions": list(decisions.values()),
            "barriers": list({str(b): b for b in barriers}.values()),
            "limitations": [
                "Declared source profile and normal flow only; no runtime execution "
                "or automatic verification."
            ],
        }
        validate_claim_support(result)
        return result
