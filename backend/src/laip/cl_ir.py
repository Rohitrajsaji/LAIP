"""Bounded CL lowering to conservative control-flow and data-flow facts.

Embedded source commands become distinct nodes; submitted commands are only
references. The shared flow engine receives CL-specific structural lowering.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from laip.rpgle_ir import IrError, IrLimits
from laip.rpgle_ir import build_ir as build_flow

DEFAULT_LIMITS = IrLimits()


def build_ir(
    statements: Sequence[dict[str, Any]],
    *,
    limits: IrLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + limits.timeout_seconds
    work = 0

    def check() -> None:
        nonlocal work
        work += 1
        if work > limits.max_work or time.monotonic() > deadline or (cancelled and cancelled()):
            raise IrError("CL IR resource limit or cancellation")

    check()
    if len(statements) > limits.max_nodes:
        raise IrError("CL IR node limit")
    # AST tuples keep original statement indexes and original end commands.
    position = 0

    def embedded(parent: dict[str, Any]) -> dict[str, Any] | None:
        child = parent["attributes"].get("embedded")
        if child is None:
            return None
        # Source mappings conservatively cover the enclosing source command.
        return {**child, "start": parent["start"], "end": parent["end"], "text": parent["text"]}

    def sequence(depth: int = 0, grouped: bool = False) -> tuple[list[dict[str, Any]], int | None]:
        nonlocal position
        if depth > limits.max_depth:
            raise IrError("CL block depth limit")
        result: list[dict[str, Any]] = []
        while position < len(statements):
            check()
            index = position
            statement = statements[index]
            position += 1
            kind = statement["kind"]
            if kind == "enddo" and grouped:
                return result, index
            item: dict[str, Any] = {"index": index, "statement": statement}
            child = embedded(statement)
            if kind in {"do", "dow", "dou", "for"} or (
                kind in {"if", "else", "message_monitor"}
                and child is not None
                and child["kind"] == "do"
            ):
                item["body"], item["end"] = sequence(depth + 1, True)
                if item["end"] is None:
                    item["statement"] = {
                        **statement,
                        "barrier": True,
                        "diagnostics": ["Unclosed CL group"],
                    }
            elif child is not None and kind in {"if", "else", "message_monitor"}:
                item["body"] = [{"index": index, "statement": child}]
            if (
                kind == "if"
                and position < len(statements)
                and statements[position]["kind"] == "else"
            ):
                else_index = position
                else_statement = statements[else_index]
                position += 1
                else_item: dict[str, Any] = {"index": else_index, "statement": else_statement}
                else_child = embedded(else_statement)
                if else_child is not None and else_child["kind"] == "do":
                    else_item["body"], else_item["end"] = sequence(depth + 1, True)
                    if else_item["end"] is None:
                        else_item["statement"] = {
                            **else_statement,
                            "barrier": True,
                            "diagnostics": ["Unclosed ELSE group"],
                        }
                elif else_child is not None:
                    else_item["body"] = [{"index": else_index, "statement": else_child}]
                item["else"] = else_item
            result.append(item)
        return result, None

    tree, _ = sequence()
    lowered: list[dict[str, Any]] = []
    originals: list[tuple[int, dict[str, Any]]] = []

    def emit(
        index: int, statement: dict[str, Any], kind: str | None = None, *, synthetic: bool = False
    ) -> None:
        check()
        if len(lowered) >= limits.max_nodes:
            raise IrError("CL lowered node limit")
        actual = {**statement}
        if synthetic:
            actual = {
                **statement,
                "kind": "structural_join",
                "uses": [],
                "defines": [],
                "attributes": {},
                "barrier": False,
                "diagnostics": [],
            }
        flow = {**actual, "kind": kind or actual["kind"]}
        attrs = dict(flow["attributes"])
        # External operations invalidate facts without asserting actual effects.
        if actual["kind"] in {
            "override",
            "delete_override",
            "submit_job",
            "message_send",
            "message_receive",
            "message_monitor",
            "file_read",
        }:
            attrs["unknown_effects"] = True
        if attrs.get("dynamic"):
            flow["barrier"] = True
            actual = {
                **actual,
                "barrier": True,
                "diagnostics": actual["diagnostics"] + ["Dynamic target remains unresolved"],
            }
        flow["attributes"] = attrs
        lowered.append(flow)
        originals.append((index, actual))

    def end_statement(item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        end = item.get("end")
        return (end, statements[end]) if end is not None else (item["index"], item["statement"])

    def lower(items: list[dict[str, Any]], depth: int = 0) -> None:
        if depth > limits.max_depth:
            raise IrError("CL lowering depth limit")
        cursor = 0
        while cursor < len(items):
            check()
            item = items[cursor]
            index, statement = item["index"], item["statement"]
            kind = statement["kind"]
            child = embedded(statement)
            if "body" not in item and child is not None and kind in {"if", "else"}:
                item = {**item, "body": [{"index": index, "statement": child}]}
            if kind in {"do", "dow", "dou", "for"} and "body" not in item:
                statement = {
                    **statement,
                    "barrier": True,
                    "diagnostics": ["Embedded group has no matched source body"],
                }
            # Command-level handlers apply to a single preceding simple command.
            handlers: list[dict[str, Any]] = []
            following = cursor + 1
            while (
                following < len(items)
                and items[following]["statement"]["kind"] == "message_monitor"
            ):
                check()
                handlers.append(items[following])
                following += 1
            monitorable = kind not in {
                "program_start",
                "declaration",
                "if",
                "else",
                "do",
                "dow",
                "dou",
                "for",
                "message_monitor",
                "program_end",
            }
            if handlers and monitorable:
                emit(index, statement, "monitor", synthetic=True)
                lower([item], depth + 1)
                for handler in handlers:
                    emit(handler["index"], handler["statement"], "on_error")
                    lower(handler.get("body", []), depth + 1)
                    if handler.get("end") is not None:
                        emit(handler["end"], statements[handler["end"]], "declaration")
                emit(index, statement, "endmon", synthetic=True)
                cursor = following
                continue
            if kind == "if":
                # Embedded commands' uses belong to their own conditional node.
                from_condition = _variables(statement["attributes"].get("expression"), check)
                emit(index, {**statement, "uses": from_condition}, "if")
                lower(item.get("body", []), depth + 1)
                alternative = item.get("else")
                if alternative is not None:
                    if item.get("end") is not None:
                        emit(item["end"], statements[item["end"]], "declaration")
                    emit(alternative["index"], alternative["statement"], "else")
                    lower(alternative.get("body", []), depth + 1)
                    end_index, ending = end_statement(alternative)
                    emit(end_index, ending, "endif", synthetic=alternative.get("end") is None)
                else:
                    end_index, ending = end_statement(item)
                    emit(end_index, ending, "endif", synthetic=item.get("end") is None)
            elif kind in {"dow", "dou", "for", "do"}:
                emit(index, statement, "declaration" if kind == "do" else kind)
                lower(item.get("body", []), depth + 1)
                end_index, ending = end_statement(item)
                emit(
                    end_index,
                    ending,
                    {"do": "declaration", "for": "endfor"}.get(kind, "enddo"),
                    synthetic=item.get("end") is None,
                )
            elif kind in {"else", "enddo", "message_monitor"}:
                emit(
                    index,
                    {
                        **statement,
                        "barrier": True,
                        "diagnostics": statement["diagnostics"]
                        + ["Unmatched group or unsupported program-level monitor scope"],
                    },
                    "opaque",
                )
                # Retain handler body facts, but block any complete flow conclusion.
                lower(item.get("body", []), depth + 1)
            else:
                emit(
                    index,
                    statement,
                    {"program_start": "declaration", "program_end": "return"}.get(kind),
                )
            cursor += 1

    lower(tree)
    check()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IrError("CL IR time limit")
    flow_limits = IrLimits(limits.max_nodes, limits.max_depth, limits.max_work, remaining)
    result = build_flow(lowered, limits=flow_limits, cancelled=cancelled)
    references: list[dict[str, Any]] = []
    for node in result["nodes"]:
        check()
        flat_index = node["stmt_index"]
        original_index, original = originals[flat_index]
        node["stmt_index"] = original_index
        node["statement"] = original
        if "condition_source_stmt_index" in node:
            node["condition_source_stmt_index"] = originals[node["condition_source_stmt_index"]][0]
        pending = [(original, "current_job")]
        while pending:
            check()
            source, context = pending.pop()
            attrs = source["attributes"]
            kind = source["kind"]
            args = attrs.get("arguments", {})
            if kind in {
                "call",
                "override",
                "delete_override",
                "submit_job",
                "file_read",
                "file_declaration",
            }:
                target = attrs.get("target")
                if kind == "delete_override":
                    target = attrs.get("file")
                if kind == "submit_job":
                    target = args.get("JOB")
                references.append(
                    {
                        "node": node["id"],
                        "kind": kind,
                        "target": target,
                        "dynamic": attrs.get("dynamic", False),
                        "context": context,
                        "resolution": "unresolved",
                        "file": attrs.get("file"),
                        "member": args.get("MBR"),
                        "scope": args.get("OVRSCOPE", args.get("LVL")),
                    }
                )
            if kind == "submit_job":
                child = attrs.get("submitted", attrs.get("embedded"))
                if child is not None:
                    pending.append((child, "submitted_job"))
    result["references"] = references
    result["language"] = "CL/CLLE"
    result["limitations"] = [
        "Declared CL subset; no command execution, call effects or runtime object resolution.",
        "Embedded node spans conservatively cover the enclosing command.",
        "Program-level MONMSG scope is an explicit barrier; "
        "command-level exception edges are possible paths.",
        "Overrides retain supplied scope and targets; opened file identity is not established.",
    ]
    return result


def _variables(expression: Any, check: Callable[[], None]) -> list[str]:
    pending = [expression]
    result = set()
    while pending:
        check()
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("kind") == "variable":
                result.add(value["value"])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return sorted(result)
