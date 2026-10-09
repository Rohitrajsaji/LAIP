"""Bounded free-format RPGLE subset; unknown syntax remains an opaque barrier."""

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from lark import Lark, Token, Tree
from lark.exceptions import LarkError

GRAMMAR = r"""
?start: declaration | procedure_start | procedure_end | subroutine_start | subroutine_end
      | condition | simple | assignment | return_stmt | call_stmt | exsr_stmt
      | file_stmt | keyed_stmt | for_stmt | on_error | control_stmt | display_stmt
      | implicit_call | bare_parameter

declaration: "dcl-s"i NAME datatype ["inz"i "(" expr ")"] -> variable_decl
           | "dcl-c"i NAME expr -> constant_decl
           | "dcl-f"i NAME file_option* -> file_decl
           | "dcl-pr"i NAME [datatype] -> prototype_decl
           | "dcl-pi"i (NAME | ANONYMOUS) [datatype] -> interface_decl
           | "dcl-parm"i NAME datatype parameter_mode* -> parameter_decl
file_option: "keyed"i -> file_keyed
           | "usage"i "(" FILE_USAGE (":" FILE_USAGE)* ")" -> file_usage
bare_parameter: NAME datatype parameter_mode* -> bare_parameter_decl
parameter_mode: "const"i -> const_mode
              | "value"i -> value_mode
control_stmt: "ctl-opt"i control_option+
control_option: NAME "(" CONTROL_VALUE (":" CONTROL_VALUE)* ")"
display_stmt: "dsply"i expr
implicit_call: NAME "(" [args] ")"
datatype: TYPE ["(" INT [":" INT] ")"]
procedure_start: "dcl-proc"i NAME
procedure_end: "end-proc"i
subroutine_start: "begsr"i NAME
subroutine_end: "endsr"i
condition: "if"i expr -> if_stmt
         | "elseif"i expr -> elseif_stmt
         | "dow"i expr -> dow_stmt
         | "dou"i expr -> dou_stmt
         | "when"i expr -> when_stmt
simple: "else"i -> else_stmt
      | "endif"i -> endif_stmt
      | "enddo"i -> enddo_stmt
      | "endfor"i -> endfor_stmt
      | "select"i -> select_stmt
      | "other"i -> other_stmt
      | "endsl"i -> endsl_stmt
      | "leave"i -> leave_stmt
      | "iter"i -> iter_stmt
      | "monitor"i -> monitor_stmt
      | "endmon"i -> endmon_stmt
      | "end-pr"i -> prototype_end
      | "end-pi"i -> interface_end
assignment: ["eval"i] (NAME | INDICATOR) ASSIGN expr
return_stmt: "return"i [expr]
call_stmt: "callp"i NAME "(" [args] ")" -> callp_stmt
         | "call"i (NAME | STRING) -> call_stmt
exsr_stmt: "exsr"i NAME
file_stmt: FILEOP NAME
keyed_stmt: KEYOP expr NAME
for_stmt: "for"i NAME "=" expr DIRECTION expr ["by"i expr]
on_error: "on-error"i [INT]
?expr: or_expr
?or_expr: and_expr (OR and_expr)*
?and_expr: comparison (AND comparison)*
?comparison: sum (COMPARE sum)?
?sum: product (ADD product)*
?product: power (MUL power)*
?power: unary (POW power)?
?unary: UNARY unary -> unary_expr
      | atom
?atom: NUMBER -> number
     | STRING -> string
     | CONSTANT -> constant
     | NAME "(" [args] ")" -> invocation
     | NAME -> identifier
     | INDICATOR -> identifier
     | "(" expr ")"
args: expr (":" expr)*
ANONYMOUS.3: /\*(?i:n)(?![A-Za-z0-9_])/
INDICATOR.3: /\*(?i:inlr|in[0-9]{2})(?![A-Za-z0-9_])/
CONTROL_VALUE: /\*[A-Za-z0-9_]+/ | /'(?:''|[^'])*'/ | /[A-Za-z0-9_]+/
TYPE.2: /(?i:char|varchar|int|uns|packed|zoned|float|ind|date|time|timestamp)(?![A-Za-z0-9_])/
FILE_USAGE.2: /\*(?i:input|output|update|delete)(?![A-Za-z0-9_])/
FILEOP.2: /(?i:read|reade|readp|readpe|write|update|delete|exfmt)(?![A-Za-z0-9_])/
KEYOP.2: /(?i:chain|setll|setgt)(?![A-Za-z0-9_])/
DIRECTION.2: /(?i:to|downto)(?![A-Za-z0-9_])/
OR.2: /(?i:or)(?![A-Za-z0-9_])/
AND.2: /(?i:and)(?![A-Za-z0-9_])/
UNARY.2: /(?i:not)(?![A-Za-z0-9_])/ | "+" | "-"
ASSIGN: "+=" | "-=" | "*=" | "/=" | "="
COMPARE: "<>" | ">=" | "<=" | "=" | ">" | "<"
ADD: "+" | "-"
MUL: "*" | "/"
POW: "**"
CONSTANT: /\*(?i:on|off|zeros|zero|blanks|blank|loval|hival|null)(?![A-Za-z0-9_])/
INT: /[0-9]+/
NUMBER: /[0-9]+(?:\.[0-9]+)?/
NAME: /(?i:(?!dcl-|end-|on-error|ctl-opt)%?[a-z_$#@][a-z0-9_$#@]*(?:\.[a-z_$#@][a-z0-9_$#@]*)*)/
STRING: /'(?:''|[^'])*'/
%import common.WS
%ignore WS
"""
PARSER = Lark(GRAMMAR, parser="lalr", maybe_placeholders=False)


@dataclass(frozen=True)
class ParserLimits:
    max_source_bytes: int = 16 * 1024 * 1024
    max_statements: int = 100000
    max_tokens: int = 1000000
    max_expression_depth: int = 64
    max_statement_chars: int = 65536
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("INVALID_PARSER_LIMIT")


DEFAULT_PARSER_LIMITS = ParserLimits()


def _tree(node: Tree[Token] | Token) -> Tree[Token]:
    if not isinstance(node, Tree):
        raise ValueError("UNSUPPORTED_TREE")
    return node


def _expression(node: Tree[Token] | Token, check: Callable[[], None]) -> dict[str, Any]:
    check()
    if isinstance(node, Token):
        raise ValueError("UNSUPPORTED_EXPRESSION")
    kind, children = str(node.data), node.children
    if kind == "identifier":
        return {"node": "reference", "name": str(children[0])}
    if kind == "number":
        # Decimal spelling is retained, avoiding lossy floats under the canonical integer profile.
        return {"node": "literal", "value": str(children[0]), "literal_kind": "number"}
    if kind == "string":
        return {"node": "literal", "value": str(children[0])[1:-1].replace("''", "'")}
    if kind == "constant":
        return {
            "node": "literal",
            "value": str(children[0]).lower(),
            "literal_kind": "rpg_constant",
        }
    if kind == "invocation":
        arguments = _tree(children[1]).children if len(children) > 1 else []
        return {
            "node": "invocation",
            "name": str(children[0]),
            "arguments": [_expression(c, check) for c in arguments],
        }
    if kind == "unary_expr":
        return {
            "node": "unary",
            "operator": str(children[0]).lower(),
            "operand": _expression(children[1], check),
        }
    if kind in {"or_expr", "and_expr", "comparison", "sum", "product", "power"}:
        result = _expression(children[0], check)
        for i in range(1, len(children), 2):
            result = {
                "node": "binary",
                "operator": str(children[i]).lower(),
                "left": result,
                "right": _expression(children[i + 1], check),
            }
        return result
    raise ValueError("UNSUPPORTED_EXPRESSION")


def _uses(value: dict[str, Any], check: Callable[[], None]) -> list[str]:
    names: set[str] = set()
    queue = [value]
    while queue:
        check()
        current = queue.pop()
        if current["node"] == "reference":
            names.add(current["name"])
        elif current["node"] == "binary":
            queue.extend([current["left"], current["right"]])
        elif current["node"] == "unary":
            queue.append(current["operand"])
        elif current["node"] == "invocation":
            queue.extend(current["arguments"])
    return sorted(names)


def _statement(tree: Tree[Token], check: Callable[[], None]) -> dict[str, Any]:
    kind = str(tree.data)
    children = tree.children
    attributes: dict[str, Any] = {}
    uses: set[str] = set()
    defines: list[str] = []

    def expression(node: Any) -> dict[str, Any]:
        result = _expression(node, check)
        uses.update(_uses(result, check))
        return result

    if kind.endswith("_decl"):
        attributes = {"declaration_kind": kind.removesuffix("_decl"), "name": str(children[0])}
        if kind in {
            "variable_decl",
            "parameter_decl",
            "bare_parameter_decl",
            "prototype_decl",
            "interface_decl",
        }:
            if len(children) > 1:
                datatype = _tree(children[1])
                attributes["datatype"] = str(datatype.children[0]).lower()
                attributes["dimensions"] = [str(c) for c in datatype.children[1:]]
            if kind in {"parameter_decl", "bare_parameter_decl"}:
                modes = [str(_tree(c).data) for c in children[2:]]
                if len(set(modes)) != len(modes) or len(modes) > 1:
                    raise ValueError("INVALID_PARAMETER_MODE")
                attributes["parameter_mode"] = (
                    "value"
                    if "value_mode" in modes
                    else "const_reference"
                    if "const_mode" in modes
                    else "reference"
                )
            elif len(children) > 2:
                attributes["initializer"] = expression(children[2])
        elif kind == "constant_decl":
            attributes["initializer"] = expression(children[1])
        elif kind == "file_decl":
            seen_options = set()
            for child in children[1:]:
                check()
                option = _tree(child)
                option_kind = str(option.data)
                if option_kind in seen_options:
                    raise ValueError("DUPLICATE_FILE_OPTION")
                seen_options.add(option_kind)
                if option_kind == "file_keyed":
                    attributes["keyed"] = True
                else:
                    attributes["usage"] = [
                        str(token).lower().removeprefix("*") for token in option.children
                    ]
                    if len(set(attributes["usage"])) != len(attributes["usage"]):
                        raise ValueError("DUPLICATE_FILE_USAGE")
        defines = [str(children[0])]
        kind = "declaration"
    elif kind == "control_stmt":
        options = {}
        supported = {
            "dftactgrp": {"*no", "*yes"},
            "actgrp": {"*new", "*caller"},
            "option": {"*nodebugio", "*srcstmt", "*nosrcstmt"},
        }
        for child in children:
            option = _tree(child)
            name = str(option.children[0]).casefold()
            values = [str(v).casefold() for v in option.children[1:]]
            if (
                name in options
                or name not in supported
                or not set(values).issubset(supported[name])
            ):
                raise ValueError("UNSUPPORTED_CONTROL_OPTION")
            if name != "option" and len(values) != 1:
                raise ValueError("INVALID_CONTROL_OPTION_ARITY")
            options[name] = values
        attributes["options"] = options
        kind = "control_options"
    elif kind == "display_stmt":
        attributes["expression"] = expression(children[0])
        kind = "display"
    elif kind in {"procedure_start", "subroutine_start"}:
        attributes["name"] = str(children[0])
    elif kind in {"if_stmt", "elseif_stmt", "dow_stmt", "dou_stmt", "when_stmt"}:
        attributes["expression"] = expression(children[0])
        kind = kind.removesuffix("_stmt")
    elif kind == "assignment":
        name, operator = str(children[0]), str(children[1])
        attributes = {"target": name, "operator": operator, "expression": expression(children[2])}
        defines = [name]
        if operator != "=":
            uses.add(name)
    elif kind == "return_stmt":
        if children:
            attributes["expression"] = expression(children[0])
        kind = "return"
    elif kind in {"callp_stmt", "call_stmt", "exsr_stmt", "implicit_call"}:
        target = str(children[0])
        attributes["target"] = target[1:-1].replace("''", "'") if target.startswith("'") else target
        attributes["call_kind"] = (
            "implicit" if kind == "implicit_call" else kind.removesuffix("_stmt")
        )
        if len(children) > 1:
            attributes["arguments"] = [expression(c) for c in _tree(children[1]).children]
        kind = "exsr" if kind == "exsr_stmt" else "call"
    elif kind == "file_stmt":
        kind = str(children[0]).lower()
        attributes["target"] = str(children[1])
    elif kind == "keyed_stmt":
        kind = str(children[0]).lower()
        attributes["key"] = expression(children[1])
        attributes["target"] = str(children[2])
    elif kind == "for_stmt":
        defines = [str(children[0])]
        attributes = {
            "target": defines[0],
            "start": expression(children[1]),
            "direction": str(children[2]).lower(),
            "end": expression(children[3]),
        }
        if len(children) > 4:
            attributes["step"] = expression(children[4])
        kind = "for"
    elif kind == "on_error":
        if children:
            attributes["status_code"] = str(children[0])
    else:
        kind = kind.removesuffix("_stmt")
    return {
        "kind": kind,
        "attributes": attributes,
        "uses": sorted(uses),
        "defines": defines,
        "barrier": False,
        "diagnostics": [],
    }


def parse_statements(
    text: str,
    *,
    limits: ParserLimits = DEFAULT_PARSER_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], ...]:
    deadline = time.monotonic() + limits.timeout_seconds

    def check() -> None:
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        if time.monotonic() >= deadline:
            raise ValueError("PARSER_TIME_LIMIT")

    check()
    if len(text.encode("utf-8")) > limits.max_source_bytes:
        raise ValueError("PARSER_SOURCE_LIMIT")
    result: list[dict[str, Any]] = []
    cleaned: list[str] = []
    start: int | None = None
    i, depth, tokens, iterations = 0, 0, 0, 0
    in_string = False
    line_start = True
    interface: tuple[str, int] | None = None

    def emit(end: int, terminated: bool) -> None:
        nonlocal cleaned, start, depth, interface
        check()
        if start is None:
            cleaned = []
            return
        if len(result) >= limits.max_statements:
            raise ValueError("PARSER_STATEMENT_LIMIT")
        if end - start > limits.max_statement_chars:
            raise ValueError("PARSER_STATEMENT_SIZE_LIMIT")
        raw = text[start:end]
        statement_text = "".join(cleaned).strip()
        try:
            if not terminated or in_string or depth != 0:
                raise ValueError("MALFORMED_STATEMENT")
            parsed = PARSER.parse(statement_text)
            check()
            pending: list[tuple[Tree[Token] | Token, int]] = [(parsed, 0)]
            while pending:
                check()
                node, nesting = pending.pop()
                if isinstance(node, Tree):
                    semantic = str(node.data) in {"unary_expr", "invocation"} or (
                        str(node.data) == "power" and len(node.children) > 1
                    )
                    if semantic:
                        nesting += 1
                    if nesting > limits.max_expression_depth:
                        raise ValueError("PARSER_DEPTH_LIMIT")
                    if str(node.data) in {"sum", "product", "or_expr", "and_expr", "power"}:
                        if len(node.children) // 2 > limits.max_expression_depth:
                            raise ValueError("PARSER_DEPTH_LIMIT")
                    pending.extend((child, nesting) for child in node.children)
            record = _statement(parsed, check)
            declaration = record["attributes"].get("declaration_kind")
            if declaration in {"interface", "prototype"}:
                if interface is not None:
                    raise ValueError("NESTED_INTERFACE_DECLARATION")
                interface = (declaration, len(result))
            elif declaration == "bare_parameter":
                if interface is None:
                    raise ValueError("PARAMETER_OUTSIDE_INTERFACE")
                record["attributes"]["declaration_kind"] = "parameter"
            elif record["kind"] in {"interface_end", "prototype_end"}:
                expected = "interface" if record["kind"] == "interface_end" else "prototype"
                if interface is None or interface[0] != expected:
                    raise ValueError("UNMATCHED_INTERFACE_END")
                interface = None
            elif interface is not None and declaration != "parameter":
                raise ValueError("NON_PARAMETER_IN_INTERFACE")
            # Bound the final expression shape before consumers serialize or traverse it.
            objects: list[tuple[Any, int]] = [(record["attributes"], 0)]
            while objects:
                check()
                value, nesting = objects.pop()
                if nesting > limits.max_expression_depth * 2 + 8:
                    raise ValueError("PARSER_DEPTH_LIMIT")
                if isinstance(value, dict):
                    objects.extend((child, nesting + 1) for child in value.values())
                elif isinstance(value, list):
                    objects.extend((child, nesting + 1) for child in value)

        except (LarkError, RecursionError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc) in {
                "CANCELLED",
                "PARSER_TIME_LIMIT",
                "PARSER_DEPTH_LIMIT",
            }:
                raise
            record = {
                "kind": "opaque",
                "attributes": {},
                "uses": [],
                "defines": [],
                "barrier": True,
                "diagnostics": [
                    str(exc)
                    if isinstance(exc, ValueError)
                    and str(exc)
                    in {
                        "NESTED_INTERFACE_DECLARATION",
                        "PARAMETER_OUTSIDE_INTERFACE",
                        "UNMATCHED_INTERFACE_END",
                        "NON_PARAMETER_IN_INTERFACE",
                        "UNSUPPORTED_CONTROL_OPTION",
                        "INVALID_CONTROL_OPTION_ARITY",
                        "INVALID_PARAMETER_MODE",
                        "DUPLICATE_FILE_OPTION",
                        "DUPLICATE_FILE_USAGE",
                    }
                    else "UNSUPPORTED_OR_MALFORMED_STATEMENT"
                ],
            }
        record.update({"text": raw, "start": start, "end": end})
        result.append(record)
        cleaned, start, depth = [], None, 0

    while i < len(text):
        iterations += 1
        if iterations % 256 == 1:
            check()
        char = text[i]
        if not in_string and line_start and re.match(r"/[A-Za-z]", text[i : i + 2]):
            line_end = text.find("\n", i)
            end = len(text) if line_end < 0 else line_end
            if start is not None:
                emit(i, False)
            if len(result) >= limits.max_statements or end - i > limits.max_statement_chars:
                raise ValueError("PARSER_STATEMENT_LIMIT")
            result.append(
                {
                    "kind": "opaque",
                    "text": text[i:end],
                    "start": i,
                    "end": end,
                    "attributes": {},
                    "uses": [],
                    "defines": [],
                    "barrier": True,
                    "diagnostics": ["UNRESOLVED_PREPROCESSOR_DIRECTIVE"],
                }
            )
            i = end
            continue
        if not in_string and text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            if start is not None:
                cleaned.append(" ")
            continue
        if not in_string and line_start and text[i : i + 6].lower() == "**free":
            line_end = text.find("\n", i)
            if text[i + 6 : line_end if line_end >= 0 else len(text)].strip() == "":
                i = len(text) if line_end < 0 else line_end + 1
                line_start = True
                continue
        if start is None and char.isspace():
            line_start = char in "\r\n" or line_start
            i += 1
            continue
        if start is None:
            start = i
        if i - start + 1 > limits.max_statement_chars:
            raise ValueError("PARSER_STATEMENT_SIZE_LIMIT")
        if char == "'":
            if in_string and i + 1 < len(text) and text[i + 1] == "'":
                if i - start + 2 > limits.max_statement_chars:
                    raise ValueError("PARSER_STATEMENT_SIZE_LIMIT")
                cleaned.extend([char, char])
                i += 2
                continue
            in_string = not in_string
            tokens += 1
        elif not in_string:
            if char == "(":
                depth += 1
                if depth > limits.max_expression_depth:
                    raise ValueError("PARSER_DEPTH_LIMIT")
            elif char == ")":
                depth -= 1
            if char == ";":
                emit(i + 1, True)
                i += 1
                line_start = False
                continue
            if not char.isspace() and (
                i == start or not text[i - 1].isalnum() or not char.isalnum()
            ):
                tokens += 1
        if tokens > limits.max_tokens:
            raise ValueError("PARSER_TOKEN_LIMIT")
        cleaned.append(char)
        line_start = char in "\r\n"
        i += 1
    emit(len(text), False)
    if interface is not None:
        result[interface[1]]["barrier"] = True
        result[interface[1]]["diagnostics"].append("UNCLOSED_INTERFACE")
    check()
    return tuple(result)
