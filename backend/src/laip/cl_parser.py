"""Bounded CL command syntax. Commands are inspected and never executed."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from lark import Lark, Tree
from lark.exceptions import LarkError


class ParseError(ValueError):
    """Resource or cancellation failure."""


@dataclass(frozen=True)
class ParserLimits:
    max_source_bytes: int = 8 * 1024 * 1024
    max_statements: int = 20_000
    max_statement_chars: int = 100_000
    max_expression_depth: int = 64
    max_tokens: int = 100_000
    timeout_seconds: float = 30.0


_GRAMMAR = r"""
start: WORD argument*
argument: WORD "(" value* ")"
?value: STRING | VARIABLE | WORD | OP | "(" value* ")" -> grouped
STRING: /'(?:''|[^'])*'/
VARIABLE: /&[A-Za-z0-9_$#@]+/
WORD: /[A-Za-z0-9_*.$#@\/:-]+/
OP: /[+=<>|%,]/
%import common.WS
%ignore WS
"""
_PARSER = Lark(_GRAMMAR, parser="lalr", propagate_positions=True)
_KINDS = {
    "PGM": "program_start",
    "ENDPGM": "program_end",
    "DCL": "declaration",
    "CHGVAR": "assignment",
    "IF": "if",
    "ELSE": "else",
    "DO": "do",
    "DOWHILE": "dow",
    "DOUNTIL": "dou",
    "DOFOR": "for",
    "ENDDO": "enddo",
    "LEAVE": "leave",
    "ITERATE": "iter",
    "RETURN": "return",
    "CALL": "call",
    "CALLPRC": "call",
    "SBMJOB": "submit_job",
    "OVRDBF": "override",
    "DLTOVR": "delete_override",
    "MONMSG": "message_monitor",
    "SNDPGMMSG": "message_send",
    "SNDUSRMSG": "message_send",
    "RCVMSG": "message_receive",
}
_ALLOWED = {
    "PGM": {"PARM"},
    "ENDPGM": set(),
    "DCL": {"VAR", "TYPE", "LEN", "VALUE"},
    "CHGVAR": {"VAR", "VALUE"},
    "IF": {"COND", "THEN"},
    "ELSE": {"CMD"},
    "DO": set(),
    "DOWHILE": {"COND"},
    "DOUNTIL": {"COND"},
    "DOFOR": {"VAR", "FROM", "TO", "BY"},
    "ENDDO": set(),
    "LEAVE": set(),
    "ITERATE": set(),
    "RETURN": set(),
    "CALL": {"PGM", "PARM"},
    "CALLPRC": {"PRC", "PARM", "RTNVAL"},
    "SBMJOB": {"CMD", "JOB", "JOBD", "JOBQ"},
    "OVRDBF": {"FILE", "TOFILE", "MBR", "OVRSCOPE"},
    "DLTOVR": {"FILE", "LVL"},
    "MONMSG": {"MSGID", "CMPDTA", "EXEC"},
    "SNDPGMMSG": {"MSG", "MSGID", "MSGF", "MSGDTA", "TOPGMQ", "MSGTYPE"},
    "SNDUSRMSG": {"MSG", "MSGID", "MSGF", "MSGDTA", "TOUSR", "MSGRPY"},
    "RCVMSG": {"PGMQ", "MSGTYPE", "WAIT", "MSG", "MSGID", "MSGDTA", "RMV", "MSGLEN", "SENDER"},
}
_REQUIRED = {
    "DCL": {"VAR", "TYPE"},
    "CHGVAR": {"VAR", "VALUE"},
    "IF": {"COND", "THEN"},
    "DOWHILE": {"COND"},
    "DOUNTIL": {"COND"},
    "DOFOR": {"VAR", "FROM", "TO"},
    "CALL": {"PGM"},
    "CALLPRC": {"PRC"},
    "SBMJOB": {"CMD"},
    "OVRDBF": {"FILE"},
    "DLTOVR": {"FILE"},
    "MONMSG": {"MSGID"},
}


_EXPR_GRAMMAR = r"""
?start: or_expr
?or_expr: and_expr (OR and_expr)*
?and_expr: compare (AND compare)*
?compare: sum (CMP sum)?
?sum: product (ADD product)*
?product: unary (MUL unary)*
?unary: UNARY unary -> unary
      | atom
?atom: VARIABLE -> variable
     | STRING -> literal
     | NUMBER -> literal
     | CONSTANT -> literal
     | "(" or_expr ")"
OR: /(?i:\*OR|\*XOR)/
AND: /(?i:\*AND)/
CMP: /(?i:\*EQ|\*NE|\*LT|\*GT|\*LE|\*GE)/ | "=" | "<>" | "<=" | ">=" | "<" | ">"
ADD: "+" | "-" | /(?i:\*CAT|\*BCAT|\*TCAT)/
MUL: "*" | "/"
UNARY: "+" | "-" | /(?i:\*NOT)/
VARIABLE: /&[A-Za-z0-9_$#@]+/
STRING: /'(?:''|[^'])*'/
NUMBER: /[0-9]+(?:\.[0-9]+)?/
CONSTANT: /(?i:\*TRUE|\*FALSE|\*ON|\*OFF)/
%import common.WS
%ignore WS
"""
_EXPR = Lark(_EXPR_GRAMMAR, parser="lalr")


def _expression(text: str, depth_limit: int) -> dict[str, Any]:
    # Flat operator chains produce deeply nested binary ASTs; bound them before building.
    operators = re.findall(r"'(?:''|[^'])*'|\*[A-Za-z]+|[+*/<>=-]", text)
    if sum(not item.startswith("'") for item in operators) > depth_limit:
        raise ParseError("CL expression operator limit exceeded")

    def convert(node: Any) -> dict[str, Any]:
        if not isinstance(node, Tree):
            return {"kind": "literal", "value": str(node)}
        if node.data in {"variable", "literal"}:
            return {"kind": str(node.data), "value": str(node.children[0])}
        if node.data == "unary":
            return {
                "kind": "unary",
                "operator": str(node.children[0]),
                "operand": convert(node.children[1]),
            }
        result = convert(node.children[0])
        for index in range(1, len(node.children), 2):
            result = {
                "kind": "binary",
                "operator": str(node.children[index]),
                "left": result,
                "right": convert(node.children[index + 1]),
            }
        return result

    return convert(_EXPR.parse(text))


DEFAULT_LIMITS = ParserLimits()


def parse_statements(
    text: str,
    *,
    limits: ParserLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
    _embedded_depth: int = 0,
) -> tuple[dict[str, Any], ...]:
    deadline = time.monotonic() + limits.timeout_seconds
    if (
        any(
            type(v) is not int or v < 1
            for v in (
                limits.max_source_bytes,
                limits.max_statements,
                limits.max_statement_chars,
                limits.max_expression_depth,
                limits.max_tokens,
            )
        )
        or not math.isfinite(limits.timeout_seconds)
        or limits.timeout_seconds <= 0
    ):
        raise ValueError("positive parser limits required")

    def check() -> None:
        if (cancelled and cancelled()) or time.monotonic() > deadline:
            raise ParseError("CL parsing cancelled or timed out")

    check()
    if _embedded_depth > limits.max_expression_depth:
        raise ParseError("CL embedded command nesting limit exceeded")
    if len(text.encode("utf-8")) > limits.max_source_bytes:
        raise ParseError("CL source limit exceeded")
    pieces: list[tuple[str, int, int]] = []
    buf: list[str] = []
    start = 0
    i = 0
    quote = False
    comment = False
    while i < len(text):
        check()
        c = text[i]
        if comment:
            if text[i : i + 2] == "*/":
                comment = False
                buf.extend("  ")
                i += 2
                continue
            buf.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if not quote and text[i : i + 2] == "/*":
            comment = True
            buf.extend("  ")
            i += 2
            continue
        if c == "'":
            if quote and text[i : i + 2] == "''":
                buf.extend("''")
                i += 2
                continue
            quote = not quote
        if c == "\n":
            value = "".join(buf)
            stripped = value.rstrip()
            if stripped.endswith(("+", "-")):
                # + removes leading spaces; - retains them (lexically insignificant outside quotes).
                marker = stripped[-1]
                buf = list(value[: len(stripped) - 1])
                if marker == "+":
                    while i + 1 < len(text) and text[i + 1] in " \t":
                        i += 1
                i += 1
                continue
            if value.strip():
                pieces.append((value, start, i))
            start = i + 1
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if comment:
        buf.append(" !unterminated-comment")
    if "".join(buf).strip():
        pieces.append(("".join(buf), start, len(text)))
    if len(pieces) > limits.max_statements:
        raise ParseError("CL statement limit exceeded")
    result = []
    for normalized, begin, end in pieces:
        check()
        if len(normalized) > limits.max_statement_chars:
            raise ParseError("CL statement length exceeded")
        depth = 0
        peak = 0
        for token in re.findall(r"'(?:''|[^'])*'|[()]|[^\s()]+", normalized):
            if token == "(":
                depth += 1
                peak = max(peak, depth)
            elif token == ")":
                depth -= 1
        if peak > limits.max_expression_depth:
            raise ParseError("CL nesting limit exceeded")
        if len(re.findall(r"\S+", normalized)) > limits.max_tokens:
            raise ParseError("CL token limit exceeded")
        statement: dict[str, Any] = {
            "kind": "opaque",
            "text": text[begin:end],
            "start": begin,
            "end": end,
            "attributes": {},
            "uses": [],
            "defines": [],
            "barrier": True,
            "diagnostics": ["Unsupported or malformed CL command"],
        }
        try:
            tree = _PARSER.parse(normalized)
            command = str(tree.children[0]).upper()
            args: dict[str, str] = {}
            for argument in tree.children[1:]:
                assert isinstance(argument, Tree)
                key = str(argument.children[0]).upper()
                if key in args:
                    raise ValueError("duplicate keyword")
                raw_argument = normalized[argument.meta.start_pos : argument.meta.end_pos]
                args[key] = raw_argument[raw_argument.index("(") + 1 : -1].strip()

            if (
                command not in _KINDS
                or set(args) - _ALLOWED[command]
                or not _REQUIRED.get(command, set()) <= set(args)
            ):
                raise ValueError("unsupported command shape")
            if (
                command == "DCL"
                and "LEN" in args
                and not re.fullmatch(r"[0-9]+(?:\s+[0-9]+)?", args["LEN"])
            ):
                raise ValueError("invalid declaration length")
            attrs: dict[str, Any] = {"command": command, "arguments": args}
            for key in ("COND", "VALUE", "FROM", "TO", "BY"):
                if key in args:
                    attrs["expression" if key in {"COND", "VALUE"} else key.lower()] = _expression(
                        args[key], limits.max_expression_depth
                    )
            if command == "DCL" and args["TYPE"].upper() not in {
                "*CHAR",
                "*DEC",
                "*LGL",
                "*INT",
                "*UINT",
            }:
                raise ValueError("unsupported declared type")
            if command in {"IF", "ELSE"}:
                attrs["embedded_command"] = args.get("THEN", args.get("CMD", ""))
            for key in ("THEN", "CMD", "EXEC"):
                if key in args:
                    check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ParseError("CL parsing timed out")
                    nested = parse_statements(
                        args[key],
                        limits=replace(limits, timeout_seconds=remaining),
                        cancelled=cancelled,
                        _embedded_depth=_embedded_depth + 1,
                    )
                    if len(nested) != 1:
                        raise ValueError("one embedded command required")
                    attrs["embedded"] = nested[0]
                    attrs["embedded_span_policy"] = "parent-original-command"
                    if command == "SBMJOB":
                        attrs["submitted"] = nested[0]
                    attrs["embedded_keyword"] = key
                    if nested[0]["barrier"]:
                        raise ValueError("unsupported embedded command")
            uses = sorted(
                set(re.findall(r"&[A-Za-z0-9_$#@]+", re.sub(r"'(?:''|[^'])*'", "", normalized)))
            )
            defines = []
            if command in {"DCL", "CHGVAR", "DOFOR"}:
                target = args.get("VAR", "")
                if not re.fullmatch(r"&[A-Za-z0-9_$#@]+", target):
                    raise ValueError("invalid variable")
                defines = [target]
                pending = [attrs.get(key) for key in ("expression", "from", "to", "by")]
                reads = set()
                while pending:
                    check()
                    expression = pending.pop()
                    if isinstance(expression, dict):
                        if expression.get("kind") == "variable":
                            reads.add(expression["value"])
                        pending.extend(expression.values())
                uses = sorted(reads)
                attrs["name"] = target
            if command in {"CALL", "CALLPRC"}:
                target = args["PGM" if command == "CALL" else "PRC"]
                if not re.fullmatch(
                    r"(?:&[A-Za-z0-9_$#@]+|[A-Za-z0-9_*.$#@]+(?:/[A-Za-z0-9_*.$#@]+)?|'(?:''|[^'])*')",
                    target,
                ):
                    raise ValueError("unsupported program target expression")
                attrs["raw_target"] = target
                if target.startswith("'"):
                    target = target[1:-1].replace("''", "'")
                attrs.update(target=target, dynamic="&" in target or target.startswith("*"))
            if command == "SBMJOB":
                attrs["submitted_command"] = args["CMD"]
                attrs["dynamic"] = "&" in args["CMD"]
            if command in {"OVRDBF", "DLTOVR"}:
                attrs["file"] = args["FILE"]
                attrs["target"] = args.get("TOFILE")
                attrs["dynamic"] = any("&" in v or "*LIBL" in v.upper() for v in args.values())
            statement.update(
                kind=_KINDS[command],
                attributes=attrs,
                uses=uses,
                defines=defines,
                barrier=False,
                diagnostics=[],
            )
        except ParseError:
            raise
        except (LarkError, ValueError):
            pass
        result.append(statement)
    check()
    return tuple(result)
