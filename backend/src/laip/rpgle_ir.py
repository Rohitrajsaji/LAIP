"""Bounded, conservative intra-scope control and reaching-definition facts."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from laip.canonical import identity_id
from laip.intrinsic_registry import intrinsic_effect
from laip.persistence import validate


class IrError(ValueError):
    """IR construction cannot complete within its resource budget."""


@dataclass(frozen=True)
class IrLimits:
    max_nodes: int = 20_000
    max_depth: int = 128
    max_work: int = 2_000_000
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int or value < 1
                for value in (self.max_nodes, self.max_depth, self.max_work)
            )
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("positive IR limits required")


DEFAULT_LIMITS = IrLimits()

_ALIASES = {
    "dcl_proc": "procedure_start",
    "proc": "procedure_start",
    "end_proc": "procedure_end",
    "begsr": "subroutine_start",
    "endsr": "subroutine_end",
    "on-error": "on_error",
}
_START = {
    "if": "endif",
    "dow": "enddo",
    "dou": "enddo",
    "for": "endfor",
    "select": "endsl",
    "monitor": "endmon",
    "procedure_start": "procedure_end",
    "subroutine_start": "subroutine_end",
}
_ARMS = {"elseif": "if", "else": "if", "when": "select", "other": "select", "on_error": "monitor"}
_EFFECTS = {
    "call",
    "callp",
    "exsr",
    "exfmt",
    "file",
    "read",
    "readp",
    "reade",
    "readpe",
    "chain",
    "write",
    "update",
    "delete",
    "open",
    "close",
    "error",
    "unknown",
    "on_error",
}


def build_ir(
    statements: Sequence[dict[str, Any]],
    *,
    limits: IrLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
    available_descriptions: Sequence[dict[str, Any]] = (),
    system_namespace: str | None = None,
    library_list: Sequence[str] = (),
) -> dict[str, Any]:
    deadline = time.monotonic() + limits.timeout_seconds
    work = 0

    def check() -> None:
        nonlocal work
        work += 1
        if cancelled is not None and cancelled():
            raise IrError("IR cancelled")
        if work > limits.max_work or time.monotonic() > deadline:
            raise IrError("IR work/time limit exceeded")

    check()
    if len(statements) > limits.max_nodes:
        raise IrError("IR node limit exceeded")
    nodes: list[dict[str, Any]] = [
        {"id": i, "stmt_index": i, "statement": dict(s)} for i, s in enumerate(statements)
    ]
    kinds = [_ALIASES.get(str(s["kind"]).lower(), str(s["kind"]).lower()) for s in statements]
    lexical_scopes: list[str] = []
    current_scope = "main"
    symbols: dict[str, dict[str, list[dict[str, Any]]]] = {"main": {}}
    procedures: dict[str, list[int]] = {}
    for i, statement in enumerate(statements):
        check()
        attributes = statement.get("attributes", {})
        if kinds[i] == "procedure_start":
            name = attributes.get("name", f"unnamed:{i}").casefold()
            procedures.setdefault(name, []).append(i)
            current_scope = "procedure:" + name
            symbols.setdefault(current_scope, {})
        lexical_scopes.append(current_scope)
        nodes[i]["lexical_scope"] = current_scope
        nodes[i]["effects"] = []
        if kinds[i] == "declaration" and attributes.get("declaration_kind") in {
            "variable",
            "constant",
            "parameter",
            "file",
        }:
            name = attributes["name"].casefold()
            symbol = {
                "symbol_id": f"symbol:{current_scope}:{name}",
                "name": attributes["name"],
                "scope": current_scope,
                "declaration_node": i,
                "datatype": attributes.get(
                    "datatype", "file" if attributes["declaration_kind"] == "file" else "unknown"
                ),
                "dimensions": attributes.get("dimensions", []),
                "parameter_mode": attributes.get("parameter_mode"),
                "origin": "source_declaration",
            }
            symbols[current_scope].setdefault(name, []).append(symbol)
        if kinds[i] == "procedure_end":
            current_scope = "main"

    if len(available_descriptions) > limits.max_nodes:
        raise IrError("IR description limit exceeded")
    if available_descriptions and not system_namespace:
        raise ValueError("MISSING_ANALYSIS_NAMESPACE")
    descriptions: dict[str, list[dict[str, Any]]] = {}
    for description in available_descriptions:
        check()
        identity = description["identity"]
        validate("Identity", identity)
        parts = identity["qualified_identity"]
        if identity["system_namespace"] != system_namespace:
            continue
        if identity["kind"] not in {"Table", "Screen"} or len(parts) != 3 or parts[1] != "*FILE":
            raise ValueError("INVALID_EXTERNAL_DESCRIPTION")
        if len(description["definitions"]) > limits.max_nodes:
            raise IrError("IR description definition limit exceeded")
        descriptions.setdefault(parts[2].casefold(), []).append(description)
    file_bindings: dict[str, dict[str, Any]] = {}
    selected_descriptions: dict[str, dict[str, Any]] = {}
    for name, declared in list(symbols["main"].items()):
        if not any(symbol["datatype"] == "file" for symbol in declared):
            continue
        candidates = descriptions.get(name, [])
        if library_list:
            candidates = next(
                (
                    [
                        item
                        for item in candidates
                        if item["identity"]["qualified_identity"][0].casefold()
                        == library.casefold()
                    ]
                    for library in library_list
                    if any(
                        item["identity"]["qualified_identity"][0].casefold() == library.casefold()
                        for item in candidates
                    )
                ),
                [],
            )
        state = "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "missing"
        file_bindings[name] = {
            "name": name,
            "resolution": state,
            "identity": candidates[0]["identity"] if state == "resolved" else None,
            "supporting_artifact_ids": sorted({item["artifact_id"] for item in candidates}),
            "diagnostics": [] if state == "resolved" else ["EXTERNAL_DESCRIPTION_" + state.upper()],
        }
        if state != "resolved":
            continue
        description = candidates[0]
        selected_descriptions[name] = description
        for definition in description["definitions"]:
            check()
            if definition["kind"] != "field" or not definition["complete"]:
                continue
            field_name = definition["name"].casefold()
            parts = description["identity"]["qualified_identity"]
            field_identity = {
                "system_namespace": system_namespace,
                "kind": "Field",
                "qualified_identity": [*parts, definition["record"], definition["name"]],
            }
            attributes = definition["attributes"]
            datatype = {
                "A": "char",
                "P": "packed",
                "S": "zoned",
                "L": "date",
                "T": "time",
                "Z": "timestamp",
            }.get(attributes.get("datatype"), "unknown")
            symbol = {
                "symbol_id": identity_id(field_identity),
                "name": definition["name"],
                "scope": "main",
                "declaration_node": None,
                "datatype": datatype,
                "parameter_mode": None,
                "dimensions": (
                    [str(attributes["length"]), str(attributes.get("decimals") or "0")]
                    if datatype in {"packed", "zoned"} and attributes.get("length") is not None
                    else [str(attributes["length"])]
                    if datatype in {"char", "varchar"} and attributes.get("length") is not None
                    else []
                ),
                "origin": "external_record_field",
                "identity": field_identity,
                "file_identity": description["identity"],
                "record": definition["record"],
                "artifact_id": description["artifact_id"],
                "source_locations": definition.get("source_locations", []),
                "declared_length": attributes.get("declared_length", attributes.get("length")),
                "derived_length": attributes.get("derived_length"),
            }
            symbols["main"].setdefault(field_name, []).append(symbol)

    def bindings(name: str, scope: str) -> list[dict[str, Any]]:
        folded = name.casefold()
        if folded.startswith("*in"):
            return [
                {
                    "symbol_id": "indicator:" + folded,
                    "name": name,
                    "scope": "indicator",
                    "datatype": "ind",
                    "origin": "rpg_indicator",
                }
            ]
        return symbols.get(scope, {}).get(folded) or symbols["main"].get(folded, [])

    for i, statement in enumerate(statements):
        check()
        scope = lexical_scopes[i]
        node_bindings: dict[str, list[dict[str, Any]]] = {"uses": [], "defines": []}
        for direction in node_bindings:
            for name in statement.get(direction, []):
                candidates = bindings(name, scope)
                node_bindings[direction].append(
                    dict(candidates[0])
                    if len(candidates) == 1
                    else {
                        "name": name,
                        "scope": scope,
                        "symbol_id": None,
                        "resolution": "ambiguous" if candidates else "missing",
                    }
                )
        nodes[i]["symbol_bindings"] = node_bindings
        expression = statement.get("attributes", {}).get("expression", {})
        target = statement.get("attributes", {}).get("target")
        if kinds[i] == "assignment" and expression.get("node") == "reference" and target:
            left, right = bindings(target, scope), bindings(expression["name"], scope)
            nodes[i]["self_assignment"] = (
                len(left) == len(right) == 1 and left[0]["symbol_id"] == right[0]["symbol_id"]
            )
        if kinds[i] == "call":
            name = statement.get("attributes", {}).get("target", "").casefold()
            call_candidates = procedures.get(name, [])
            nodes[i]["call_resolution"] = {
                "target": name,
                "resolution": "resolved"
                if len(call_candidates) == 1
                else "ambiguous"
                if call_candidates
                else "missing",
                "target_node": call_candidates[0] if len(call_candidates) == 1 else None,
                "argument_order": statement.get("attributes", {}).get("arguments", []),
                "limitations": [
                    "Local declaration resolution; callee effect summary is a later claim gate."
                ],
            }
            nodes[i]["effects"].append({"kind": "call", "unknown": True})
        if kinds[i] == "call" and nodes[i]["call_resolution"]["resolution"] == "resolved":
            attributes = statement.get("attributes", {})
            callee_scope = "procedure:" + attributes["target"].casefold()
            parameters = [
                symbol
                for candidates in symbols[callee_scope].values()
                for symbol in candidates
                if symbol["parameter_mode"] is not None
            ]
            arguments = attributes.get("arguments", [])
            signature = "supported" if len(parameters) == len(arguments) else "invalid"
            aliases = []
            signature_diagnostics = []
            for parameter, argument in zip(parameters, arguments, strict=False):
                check()
                actual = (
                    bindings(argument.get("name", ""), scope)
                    if argument.get("node") == "reference"
                    else []
                )
                compatible = (
                    len(actual) == 1
                    and actual[0]["datatype"] == parameter["datatype"]
                    and actual[0].get("dimensions", []) == parameter.get("dimensions", [])
                )
                if not compatible:
                    signature = "invalid"
                    signature_diagnostics.append("PARAMETER_REPRESENTATION_OR_LAYOUT_MISMATCH")
                aliases.append(
                    {
                        "parameter_symbol_id": parameter["symbol_id"],
                        "argument_symbol_id": actual[0]["symbol_id"] if len(actual) == 1 else None,
                        "parameter_mode": parameter["parameter_mode"],
                        "aliases_caller": parameter["parameter_mode"] != "value",
                        "caller_write_supported": parameter["parameter_mode"] == "reference",
                    }
                )
            nodes[i]["call_resolution"].update(
                signature_status=signature,
                parameter_aliases=aliases,
                diagnostics=sorted(set(signature_diagnostics)),
                conversion_profile=(
                    "exact declared representation/layout; implicit conversions unsupported"
                ),
            )
        if kinds[i] in {
            "chain",
            "setll",
            "setgt",
            "read",
            "reade",
            "readp",
            "readpe",
            "write",
            "update",
            "delete",
            "exfmt",
        }:
            target = statement.get("attributes", {}).get("target", "").casefold()
            record_candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            for file_name, description in selected_descriptions.items():
                check()
                if kinds[i] in {"chain", "setll", "setgt", "read", "reade", "readp", "readpe"}:
                    if file_name == target:
                        record_candidates.append((description, None))
                else:
                    for definition in description["definitions"]:
                        check()
                        if (
                            definition["kind"] == "record"
                            and definition["name"].casefold() == target
                            and definition["complete"]
                        ):
                            record_candidates.append((description, definition))
            state = (
                "resolved"
                if len(record_candidates) == 1
                else "ambiguous"
                if record_candidates
                else "missing"
            )
            bound_description, bound_definition = (
                record_candidates[0] if state == "resolved" else (None, None)
            )
            nodes[i]["record_binding"] = {
                "operation": kinds[i],
                "target": target,
                "resolution": state,
                "file_identity": bound_description["identity"] if bound_description else None,
                "record": bound_definition["name"] if bound_definition else None,
                "supporting_artifact_ids": sorted(
                    {item[0]["artifact_id"] for item in record_candidates}
                ),
                "source_locations": bound_definition.get("source_locations", [])
                if bound_definition
                else [],
                "field_symbol_ids": [
                    symbol["symbol_id"]
                    for items in symbols["main"].values()
                    for symbol in items
                    if bound_description
                    and symbol.get("file_identity") == bound_description["identity"]
                    and (
                        bound_definition is None or symbol.get("record") == bound_definition["name"]
                    )
                ],
                "limitations": [
                    "Global record I/O binding does not establish local assignment lineage "
                    "or runtime execution."
                ],
            }
        if "record_binding" in nodes[i]:
            binding = nodes[i]["record_binding"]
            read_globals = kinds[i] in {"write", "update", "delete", "exfmt"}
            nodes[i]["effects"].append(
                {
                    "kind": "file_operation",
                    "operation": kinds[i],
                    "resolution": binding["resolution"],
                    "file_identity": binding["file_identity"],
                    "record": binding["record"],
                    "may_raise": True,
                    "reads_record_globals": binding["field_symbol_ids"] if read_globals else [],
                    "may_write_record_globals": [] if read_globals else binding["field_symbol_ids"],
                    "runtime_effects_established": False,
                }
            )
        if kinds[i] == "display":
            nodes[i]["effects"].append({"kind": "display", "external_io": True, "may_raise": True})
    stack: list[int] = []
    ends: dict[int, int] = {}
    arms: dict[int, list[int]] = {}
    parents: dict[int, list[int]] = {}
    barriers: dict[int, list[str]] = {}
    for i, kind in enumerate(kinds):
        check()
        parents[i] = list(stack)
        if statements[i].get("barrier"):
            barriers[i] = ["unknown construct or parser barrier"]
        call = nodes[i].get("call_resolution")
        if call and (
            call.get("signature_status") == "invalid"
            or (
                statements[i].get("attributes", {}).get("call_kind") == "implicit"
                and call["resolution"] != "resolved"
            )
        ):
            barriers.setdefault(i, []).append("local call target/signature unresolved")
        expressions: list[Any] = [statements[i].get("attributes", {})]
        while expressions:
            check()
            expression = expressions.pop()
            if isinstance(expression, dict):
                if expression.get("node") == "invocation":
                    type_basis = {
                        name: candidates[0]["datatype"]
                        for name, candidates in {
                            **symbols["main"],
                            **symbols[lexical_scopes[i]],
                        }.items()
                        if len(candidates) == 1
                    }
                    effect = intrinsic_effect(expression, type_basis)
                    nodes[i]["effects"].append(effect)
                    if not effect["recognized"]:
                        barriers.setdefault(i, []).append(
                            "expression invocation has unknown effects"
                        )
                expressions.extend(expression.values())
            elif isinstance(expression, list):
                expressions.extend(expression)
        if kind in _START:
            stack.append(i)
            arms[i] = []
            if len(stack) > limits.max_depth:
                raise IrError("IR structural depth limit exceeded")
        elif kind in _ARMS:
            if not stack or kinds[stack[-1]] != _ARMS[kind]:
                barriers.setdefault(i, []).append("unmatched branch")
            else:
                prior_arm_kinds = [kinds[arm] for arm in arms[stack[-1]]]
                if (kinds[stack[-1]] == "if" and "else" in prior_arm_kinds) or (
                    kinds[stack[-1]] == "select" and "other" in prior_arm_kinds
                ):
                    barriers.setdefault(i, []).append("branch after terminal arm")
                arms[stack[-1]].append(i)
        elif kind in _START.values():
            if not stack or _START[kinds[stack[-1]]] != kind:
                barriers.setdefault(i, []).append("unmatched structural end")
            else:
                ends[stack.pop()] = i
    for i in stack:
        barriers.setdefault(i, []).append("unclosed structure")
    for starts in procedures.values():
        if len(starts) > 1:
            for start in starts:
                barriers.setdefault(start, []).append("duplicate procedure declaration")
    for scope_symbols in symbols.values():
        for candidates in scope_symbols.values():
            if len(candidates) > 1:
                for symbol in candidates:
                    if symbol["declaration_node"] is not None:
                        barriers.setdefault(symbol["declaration_node"], []).append(
                            "duplicate scoped declaration"
                        )
    count = len(nodes)
    adjacency: dict[int, set[tuple[int, str]]] = {i: set() for i in range(count)}

    def link(source: int, target: int, kind: str) -> None:
        check()
        if target < count:
            adjacency[source].add((target, kind))

    for i in range(count - 1):
        link(i, i + 1, "next")
    scopes: list[dict[str, Any]] = [{"id": "main", "kind": "main", "entry": 0 if count else None}]
    entries = {0} if count else set()
    flow_uses = [list(statement.get("uses", [])) for statement in statements]
    boundary_redirects: dict[int, tuple[int, int, int]] = {}
    for start, end in ends.items():
        check()
        kind = kinds[start]
        branch = arms[start]
        if kind in {"procedure_start", "subroutine_start"}:
            scopes.append(
                {
                    "id": lexical_scopes[start]
                    if kind == "procedure_start"
                    else "subroutine:"
                    + statements[start].get("attributes", {}).get("name", "").casefold(),
                    "name": statements[start].get("attributes", {}).get("name"),
                    "kind": kind.removesuffix("_start"),
                    "entry": start,
                    "end": end,
                }
            )
            entries.add(start)
            adjacency[end].clear()
            if start > 0:
                adjacency[start - 1].clear()
                link(start - 1, end + 1, "next")
        elif kind == "if":
            tests = [start] + [arm for arm in branch if kinds[arm] == "elseif"]
            for test in tests:
                adjacency[test].clear()
                following = next((arm for arm in branch if arm > test), end)
                link(test, test + 1 if test + 1 < following else end, "true")
                link(test, following + 1 if kinds[following] == "else" else following, "false")
            previous = start
            for arm in branch:
                boundary_redirects[arm] = (previous + 1, arm, end)
                for source in range(previous + 1, arm):
                    check()
                    joins = {
                        edge
                        for edge in adjacency[source]
                        if edge[0] == arm and edge[1] != "exception"
                    }
                    if joins:
                        adjacency[source].difference_update(joins)
                        link(source, end, "join")
                previous = arm
        elif kind in {"dow", "dou", "for"}:
            adjacency[start].clear()
            link(start, start + 1, "body")
            if kind != "dou":
                link(start, end + 1, "exit")
            adjacency[end].clear()
            link(end, start + 1 if kind == "dou" else start, "repeat")
            if kind == "dou":
                flow_uses[end] = flow_uses[start] + flow_uses[end]
                flow_uses[start] = []
                nodes[end]["condition_source_stmt_index"] = start
                if start in barriers:
                    barriers.setdefault(end, []).extend(barriers[start])
                link(end, end + 1, "exit")
        elif kind == "select":
            adjacency[start].clear()
            for position, arm in enumerate(branch):
                link(start, arm, "case")
                previous_arm = branch[position - 1] if position else None
                if previous_arm is not None:
                    boundary_redirects[arm] = (previous_arm, arm, end)
                    for source in range(previous_arm, arm):
                        check()
                        joins = {
                            edge
                            for edge in adjacency[source]
                            if edge[0] == arm and edge[1] != "exception"
                        }
                        if joins:
                            adjacency[source].difference_update(joins)
                            link(source, end, "join")
            if not any(kinds[arm] == "other" for arm in branch):
                link(start, end, "unmatched")
        elif kind == "monitor":
            first_handler = branch[0] if branch else end
            for body in range(start + 1, first_handler):
                check()
                for handler in branch:
                    link(body, handler, "exception")
            previous = start
            for arm in branch:
                boundary_redirects[arm] = (previous, arm, end)
                for source in range(previous, arm):
                    check()
                    joins = {
                        edge
                        for edge in adjacency[source]
                        if edge[0] == arm and edge[1] != "exception"
                    }
                    if joins:
                        adjacency[source].difference_update(joins)
                        link(source, end, "normal")
                previous = arm
    for i, kind in enumerate(kinds):
        check()
        if kind == "return":
            adjacency[i].clear()
        elif kind in {"leave", "iter"}:
            adjacency[i].clear()
            loop = next(
                (
                    p
                    for p in reversed(parents[i])
                    if kinds[p] in {"dow", "dou", "for"} and p in ends
                ),
                None,
            )
            if loop is None:
                barriers.setdefault(i, []).append("loop transfer outside a matched loop")
            else:
                target = ends[loop] + 1 if kind == "leave" else ends[loop]
                while target in boundary_redirects:
                    check()
                    lower, upper, join_target = boundary_redirects[target]
                    if not lower <= i < upper:
                        break
                    target = join_target
                link(i, target, "leave" if kind == "leave" else "iterate")
    predecessors: dict[int, set[int]] = {i: set() for i in range(count)}
    for source, links in adjacency.items():
        for target, _ in links:
            predecessors[target].add(source)
    status_in: list[dict[str, set[int]]] = [{} for _ in nodes]
    status_out: list[dict[str, set[int]]] = [{} for _ in nodes]
    status_changed = True
    while status_changed:
        status_changed = False
        for i in range(count):
            check()
            status_predecessors = [p for p in predecessors[i]]
            status_merged: dict[str, set[int]] = {}
            for p in status_predecessors:
                for file_name, producers in status_out[p].items():
                    status_merged.setdefault(file_name, set()).update(producers)
            for name in status_merged:
                if any(name not in status_out[p] for p in status_predecessors):
                    status_merged[name].add(-1)
            if i in entries:
                status_merged = {}
            after = {name: set(values) for name, values in status_merged.items()}
            if i in barriers or kinds[i] in {"call", "callp", "exsr", "unknown", "opaque"}:
                after = {name: {-1} for name in status_merged}
            elif kinds[i] in {"chain", "setll", "setgt"}:
                file_name = statements[i].get("attributes", {}).get("target", "").casefold()
                after[file_name] = {i}
            elif kinds[i] in {"read", "reade", "readp", "readpe", "delete", "write", "update"}:
                after = {name: {-1} for name in status_merged}
            if after != status_out[i]:
                status_changed = True
                status_out[i] = after
            status_in[i] = status_merged
    for i, node in enumerate(nodes):
        for effect in node["effects"]:
            if effect.get("name") == "%found" and effect["recognized"]:
                values = status_in[i].get(effect["file"], set())
                if not values or -1 in values or len(values) != 1:
                    effect["diagnostics"].append("FILE_STATUS_UNRESOLVED")
                    barriers.setdefault(i, []).append(
                        "file status has no unambiguous supported producer"
                    )
                else:
                    effect["status_producer_nodes"] = sorted(values)

    reachable = set(entries)
    frontier = list(entries)
    while frontier:
        check()
        for target, _ in adjacency[frontier.pop()]:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    incoming: list[dict[str, set[int]]] = [{} for _ in nodes]
    outgoing: list[dict[str, set[int]]] = [{} for _ in nodes]
    definite_in: list[set[str]] = [set() for _ in nodes]
    definite_out: list[set[str]] = [set() for _ in nodes]
    changed = True
    while changed:
        changed = False
        for i in sorted(reachable):
            check()
            pred = [p for p in predecessors[i] if p in reachable]
            merged: dict[str, set[int]] = {}
            for p in pred:
                for name, definitions in outgoing[p].items():
                    work += len(definitions)
                    check()
                    merged.setdefault(name, set()).update(definitions)
            definite = set.intersection(*(definite_out[p] for p in pred)) if pred else set()
            if i in entries:
                merged = {}
                definite = set()
            after = {name: set(definitions) for name, definitions in merged.items()}
            known = set(definite)
            if (
                i in barriers
                or kinds[i] in _EFFECTS
                or statements[i].get("attributes", {}).get("unknown_effects")
            ):
                after.clear()
                known.clear()
            else:
                for variable in statements[i].get("defines", []):
                    after[variable.casefold()] = {i}
                    known.add(variable.casefold())
            if after != outgoing[i] or known != definite_out[i]:
                changed = True
                outgoing[i], definite_out[i] = after, known
            incoming[i], definite_in[i] = merged, definite
    uses: list[dict[str, Any]] = []
    for i in sorted(reachable):
        for variable in flow_uses[i]:
            check()
            name = variable.casefold()
            uses.append(
                {
                    "node": i,
                    "variable": name,
                    "symbol_id": bindings(variable, lexical_scopes[i])[0]["symbol_id"]
                    if len(bindings(variable, lexical_scopes[i])) == 1
                    else None,
                    "definitions": sorted(incoming[i].get(name, set())),
                    "definitely_defined": name in definite_in[i],
                }
            )
    check()
    return {
        "schema_version": "0.1.0",
        "nodes": nodes,
        "edges": [
            {"from": source, "to": target, "kind": kind}
            for source in sorted(adjacency)
            for target, kind in sorted(adjacency[source])
        ],
        "scopes": scopes,
        "file_bindings": list(file_bindings.values()),
        "symbols": [
            symbol
            for scope in symbols.values()
            for candidates in scope.values()
            for symbol in candidates
        ],
        "data_flow": {
            "uses": uses,
            "interpretation": "intra-scope may reaching definitions; no runtime analysis",
        },
        "barriers": [{"node": i, "diagnostics": barriers[i]} for i in sorted(barriers)],
        "conclusions_allowed": not barriers,
        "limitations": [
            "Calls and file operations invalidate variable facts; no callee or runtime analysis."
        ]
        + (["Unknown or malformed constructs block complete conclusions."] if barriers else []),
    }
