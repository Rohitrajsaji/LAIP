"""Bounded positional DDS source inspection with original physical column spans."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lark import Lark, Tree
from lark.exceptions import LarkError


class ParseError(ValueError):
    """Parsing budget exhausted or cancelled."""


@dataclass(frozen=True)
class ParserLimits:
    max_source_bytes: int = 8 * 1024 * 1024
    max_statements: int = 20_000
    max_statement_chars: int = 5000
    max_lines: int = 100_000
    max_keywords: int = 256
    timeout_seconds: float = 30.0


DEFAULT_LIMITS = ParserLimits()
_GRAMMAR = r"""
start: entry*
?entry: NAME ["(" value+ ")"] -> keyword
      | STRING -> constant
?value: STRING | NUMBER | NAME
NAME: /[A-Za-z_$#@*][A-Za-z0-9_$#@*\/.-]*/
NUMBER: /[+-]?[0-9]+(?:\.[0-9]+)?/
STRING: /'(?:''|[^'])*'/
%import common.WS
%ignore WS
"""
_KEYWORDS = Lark(_GRAMMAR, parser="lalr", propagate_positions=True)
_COLUMNS = {
    "form": (5, 6),
    "indicators": (6, 16),
    "specification": (16, 17),
    "name": (18, 28),
    "reference": (28, 29),
    "length": (29, 34),
    "datatype": (34, 35),
    "decimals": (35, 37),
    "usage": (37, 38),
    "line": (38, 41),
    "column": (41, 44),
    "functions": (44, 80),
}
_ARITY = {
    "REF": (1, 2),
    "REFFLD": (1, 2),
    "PFILE": (1, 1),
    "JFILE": (2, 16),
    "RANGE": (2, 2),
    "VALUES": (1, 100),
    "COMP": (2, 2),
    "CHECK": (1, 16),
    "DFT": (1, 1),
    "DSPATR": (1, 16),
    "EDTCDE": (1, 2),
    "TEXT": (1, 1),
    "COLHDG": (1, 3),
    "ALIAS": (1, 1),
    "DESCEND": (0, 0),
    "UNIQUE": (0, 0),
    "SFL": (0, 0),
    "SFLCTL": (1, 1),
    "OVERLAY": (0, 0),
    "DSPSIZ": (1, 4),
    "CF03": (0, 2),
    "CA03": (0, 2),
    "COLOR": (1, 1),
    "SFLPAG": (1, 1),
    "SFLSIZ": (1, 1),
    "SFLDSP": (0, 0),
    "SFLDSPCTL": (0, 0),
    "SFLCLR": (0, 0),
    "SFLEND": (0, 1),
    "DATFMT": (1, 1),
    "TIMFMT": (1, 1),
}
for _key in range(1, 25):
    _ARITY[f"CA{_key:02}"] = (0, 2)
    _ARITY[f"CF{_key:02}"] = (0, 2)
_DB_ONLY = {"PFILE", "JFILE", "UNIQUE", "DESCEND", "COLHDG"}
_DISPLAY_ONLY = {
    "DSPATR",
    "EDTCDE",
    "SFL",
    "SFLCTL",
    "OVERLAY",
    "DSPSIZ",
    "COLOR",
    "SFLPAG",
    "SFLSIZ",
    "SFLDSP",
    "SFLDSPCTL",
    "SFLCLR",
    "SFLEND",
} | {f"{prefix}{key:02}" for prefix in ("CA", "CF") for key in range(1, 25)}


def _decode(value: str) -> str:
    return value[1:-1].replace("''", "'") if value.startswith("'") else value


def _keywords(
    text: str, file_type: str, limit: int, diagnostics: list[str]
) -> list[dict[str, Any]]:
    tree = _KEYWORDS.parse(text)
    if len(tree.children) > limit:
        raise ParseError("DDS keyword limit exceeded")
    result: list[dict[str, Any]] = []
    for item in tree.children:
        assert isinstance(item, Tree)
        if item.data == "constant":
            if file_type != "display":
                raise ValueError("constant requires display source")
            result.append(
                {"name": "DFT", "arguments": [_decode(str(item.children[0]))], "implicit": True}
            )
            continue
        try:
            name = str(item.children[0]).upper()
            values = [_decode(str(v)) for v in item.children[1:]]
            raw_values = [str(v) for v in item.children[1:]]
            if name not in _ARITY or not _ARITY[name][0] <= len(values) <= _ARITY[name][1]:
                raise ValueError("unsupported keyword or argument count")
            if (file_type == "display" and name in _DB_ONLY) or (
                file_type != "display" and name in _DISPLAY_ONLY
            ):
                raise ValueError("keyword invalid for source type")
            if name == "PFILE" and file_type != "logical":
                raise ValueError("PFILE requires logical source")
            if name == "JFILE" and file_type != "logical":
                raise ValueError("JFILE requires logical source")
            if name in {"REF", "REFFLD"} and file_type == "logical":
                raise ValueError("REF/REFFLD unsupported for logical source")
            identifier = r"[A-Za-z_$#@][A-Za-z0-9_$#@]{0,9}"
            qualified = identifier + r"(?:/" + identifier + r")?"
            if name in {"REF", "REFFLD", "PFILE", "JFILE"}:
                if not re.fullmatch(qualified, raw_values[0]):
                    raise ValueError("malformed DDS reference name")
                for index, value in enumerate(raw_values[1:], 1):
                    if name == "REFFLD" and index == 1 and value == "*SRC":
                        continue
                    pattern = identifier if name == "REF" else qualified
                    if not re.fullmatch(pattern, value):
                        raise ValueError("malformed DDS reference qualifier")
            if name == "SFLCTL" and not re.fullmatch(identifier, raw_values[0]):
                raise ValueError("invalid subfile record name")
            if name == "COMP" and values[0].upper() not in {"EQ", "NE", "LT", "GT", "LE", "GE"}:
                raise ValueError("unsupported comparison")
            if name == "CHECK" and any(
                v.upper()
                not in {
                    "AB",
                    "ME",
                    "MF",
                    "M10",
                    "M11",
                    "VN",
                    "VNE",
                    "RL",
                    "RB",
                    "RZ",
                    "LC",
                    "UC",
                    "ER",
                }
                for v in values
            ):
                raise ValueError("unsupported validation check")
            if name == "DSPATR" and any(
                v.upper() not in {"BL", "CS", "HI", "ND", "PC", "RI", "UL", "PR", "MDT", "SP"}
                for v in values
            ):
                raise ValueError("unsupported display attribute")
            if name == "EDTCDE" and values[0].upper() not in set("123456789ABCDJKLMNXYZ"):
                raise ValueError("unsupported edit code")
            if name.startswith(("CA", "CF")) and re.fullmatch(r"C[AF][0-9]{2}", name):
                if values and (
                    not re.fullmatch(r"[0-9]{2}", raw_values[0]) or not 1 <= int(values[0]) <= 99
                ):
                    raise ValueError("invalid function-key response indicator")
                if len(values) == 2 and not raw_values[1].startswith("'"):
                    raise ValueError("function-key description requires quoted text")
            if name in {"SFLPAG", "SFLSIZ"} and (
                not re.fullmatch(r"[0-9]+", raw_values[0]) or not 1 <= int(values[0]) <= 9999
            ):
                raise ValueError("invalid subfile size/page")
            if name == "SFLEND" and values and values[0].upper() != "*MORE":
                raise ValueError("unsupported subfile-end operand")
            if name == "COLOR" and values[0].upper() not in {
                "BLU",
                "GRN",
                "PNK",
                "RED",
                "TRQ",
                "WHT",
                "YLW",
            }:
                raise ValueError("unsupported display color")
            if name == "DATFMT" and values[0].upper() not in {
                "*ISO",
                "*USA",
                "*EUR",
                "*JIS",
                "*MDY",
                "*DMY",
                "*YMD",
                "*JUL",
            }:
                raise ValueError("unsupported date format")
            if name == "TIMFMT" and values[0].upper() not in {
                "*ISO",
                "*USA",
                "*EUR",
                "*JIS",
                "*HMS",
            }:
                raise ValueError("unsupported time format")
            result.append(
                {
                    "name": name,
                    "arguments": values,
                    "raw_arguments": [str(v) for v in item.children[1:]],
                    "implicit": False,
                }
            )
        except ValueError as error:
            diagnostics.append(f"DDS_KEYWORD_UNSUPPORTED: {name}: {error}")
    return result


def parse_statements(
    text: str,
    *,
    file_type: str,
    limits: ParserLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], ...]:
    if file_type not in {"physical", "logical", "display"}:
        raise ValueError("explicit physical/logical/display DDS type required")
    if (
        any(
            type(v) is not int or v < 1
            for v in (
                limits.max_source_bytes,
                limits.max_statements,
                limits.max_statement_chars,
                limits.max_lines,
                limits.max_keywords,
            )
        )
        or not math.isfinite(limits.timeout_seconds)
        or limits.timeout_seconds <= 0
    ):
        raise ValueError("positive DDS limits required")
    deadline = time.monotonic() + limits.timeout_seconds

    def check() -> None:
        if (cancelled and cancelled()) or time.monotonic() > deadline:
            raise ParseError("DDS parsing cancelled or timed out")

    check()
    if len(text.encode("utf-8")) > limits.max_source_bytes:
        raise ParseError("DDS source byte limit exceeded")
    lines = text.splitlines(keepends=True)
    if len(lines) > limits.max_lines:
        raise ParseError("DDS physical line limit exceeded")
    groups: list[dict[str, Any]] = []
    offset = 0
    pending: dict[str, Any] | None = None
    for number, raw in enumerate(lines, 1):
        check()
        body = raw.rstrip("\r\n")
        if len(body) > limits.max_statement_chars:
            raise ParseError("DDS statement length limit exceeded")
        end = offset + len(body)
        if not body.strip():
            offset += len(raw)
            continue
        padded = body.ljust(80)
        if padded[6] == "*":
            if pending is not None and pending["marker"]:
                pending["bad_continuation"] = True
            groups.append(
                {
                    "start": offset,
                    "end": end,
                    "body": body,
                    "physical": [body],
                    "functions": "",
                    "marker": None,
                    "segments": [],
                    "line_numbers": [number],
                    "comment": True,
                }
            )
            pending = None
            offset += len(raw)
            if len(groups) > limits.max_statements:
                raise ParseError("DDS statement limit exceeded")
            continue
        functions = padded[44:80]
        continuation = not padded[16:44].strip() and padded[6:16].strip() == ""
        if pending is not None and continuation and padded[5] == "A" and padded[6] != "*":
            marker = pending["marker"]
            pending["functions"] += functions.lstrip() if marker == "+" else functions
            pending["end"] = end
            pending["segments"].append(
                {"start": min(end, offset + 44), "end": min(end, offset + 80), "line": number}
            )
            pending["physical"].append(body)
            pending["line_numbers"].append(number)
            stripped = pending["functions"].rstrip()
            pending["marker"] = stripped[-1] if stripped.endswith(("+", "-")) else None
            if pending["marker"]:
                pending["functions"] = stripped[:-1]
            if (
                len(pending["functions"]) + 44 > limits.max_statement_chars
                or pending["end"] - pending["start"] > limits.max_statement_chars
            ):
                raise ParseError("DDS continued statement limit exceeded")
            offset += len(raw)
            continue
        if pending is not None and pending["marker"]:
            pending["bad_continuation"] = True
        stripped = functions.rstrip()
        marker = stripped[-1] if stripped.endswith(("+", "-")) else None
        pending = {
            "start": offset,
            "end": end,
            "body": body,
            "physical": [body],
            "line_numbers": [number],
            "functions": stripped[:-1] if marker else functions,
            "marker": marker,
            "segments": [
                {"start": min(end, offset + 44), "end": min(end, offset + 80), "line": number}
            ],
        }
        if 44 + len(pending["functions"]) > limits.max_statement_chars:
            raise ParseError("DDS statement length limit exceeded")
        groups.append(pending)
        offset += len(raw)
        if len(groups) > limits.max_statements:
            raise ParseError("DDS statement limit exceeded")
    result = []
    for group in groups:
        check()
        body = group["body"]
        padded = body.ljust(80)
        start = group["start"]
        positions = {
            key: {"start": start + min(a, len(body)), "end": start + min(b, len(body))}
            for key, (a, b) in _COLUMNS.items()
        }
        attrs: dict[str, Any] = {
            "file_type": file_type,
            "positions": positions,
            "column_spans": positions,
            "keyword_segments": group["segments"],
            "physical_lines": group["physical"],
            "indicators": [],
            "indicator_text": padded[6:16],
            "indicator_connector": padded[6].strip() or None,
            "name": padded[18:28].strip(),
            "specification": padded[16],
            "reference": padded[28] == "R",
            "datatype": padded[34].strip() or None,
            "usage": padded[37].strip() or None,
            "keywords": [],
            "overflow_lines": [
                {"line": number, "columns": len(line)}
                for number, line in zip(group["line_numbers"], group["physical"], strict=True)
                if len(line) > 80 and line[80:].strip()
            ],
        }
        statement: dict[str, Any] = {
            "kind": "opaque",
            "text": text[start : group["end"]],
            "start": start,
            "end": group["end"],
            "attributes": attrs,
            "uses": [],
            "defines": [],
            "barrier": True,
            "diagnostics": ["Unsupported or malformed positional DDS"],
        }
        if group.get("comment"):
            statement.update(kind="comment", barrier=False, diagnostics=[])
            result.append(statement)
            continue
        keyword_diagnostics = []
        try:
            if padded[5] != "A" or "\t" in body:
                raise ValueError("invalid fixed columns")
            if padded[6] == "*":
                statement.update(kind="comment", barrier=False, diagnostics=[])
                result.append(statement)
                continue
            if group.get("bad_continuation") or group["marker"]:
                raise ValueError("unfinished continuation")
            if padded[17] != " " or padded[28] not in {" ", "R"}:
                raise ValueError("unsupported positional indicator")
            if padded[6] not in {" ", "A", "O"}:
                raise ValueError("unsupported indicator connector")
            for begin in (7, 10, 13):
                slot = padded[begin : begin + 3]
                if not slot.strip():
                    continue
                if (
                    slot[0] not in {" ", "N"}
                    or not re.fullmatch(r"[0-9]{2}", slot[1:])
                    or slot[1:] == "00"
                ):
                    raise ValueError("unsupported indicator slot")
                attrs["indicators"].append(
                    {
                        "number": int(slot[1:]),
                        "negated": slot[0] == "N",
                        "positions": {"start": start + begin, "end": start + begin + 3},
                    }
                )
            if attrs["indicator_connector"] and not attrs["indicators"]:
                raise ValueError("indicator connector without indicators")
            if file_type != "display" and attrs["indicators"]:
                raise ValueError("device indicator condition in database source")
            for key, (a, b) in {
                "length": (29, 34),
                "decimals": (35, 37),
                "line": (38, 41),
                "column": (41, 44),
            }.items():
                value = padded[a:b].strip()
                if value and not value.isascii() or value and not value.isdigit():
                    raise ValueError("invalid numeric column")
                attrs[key] = int(value) if value else None
            if attrs["datatype"] not in {
                None,
                "A",
                "P",
                "S",
                "B",
                "F",
                "L",
                "T",
                "Z",
                "Y",
                "N",
                "I",
                "D",
            }:
                raise ValueError("unsupported datatype")
            if attrs["usage"] not in {None, "I", "O", "B", "H", "M", "P", "N"}:
                raise ValueError("unsupported usage")
            if file_type != "display" and (
                attrs["line"] is not None
                or attrs["column"] is not None
                or attrs["usage"] is not None
            ):
                raise ValueError("device columns in database DDS")
            if attrs["overflow_lines"]:
                keyword_diagnostics.append("DDS_COLUMN_OVERFLOW")
                # Recover independently framed continuation keywords, never the
                # truncated literal or any syntax from an overflowing line.
                for physical in group["physical"][1:]:
                    if len(physical) > 80 and physical[80:].strip():
                        continue
                    try:
                        recovered = _keywords(
                            physical.ljust(80)[44:80],
                            file_type,
                            limits.max_keywords,
                            keyword_diagnostics,
                        )
                    except LarkError:
                        keyword_diagnostics.append("DDS_KEYWORD_SYNTAX_UNSUPPORTED")
                    else:
                        attrs["keywords"].extend(recovered)
                        if len(attrs["keywords"]) > limits.max_keywords:
                            raise ParseError("DDS keyword limit exceeded")
            else:
                try:
                    attrs["keywords"] = _keywords(
                        group["functions"], file_type, limits.max_keywords, keyword_diagnostics
                    )
                except LarkError:
                    keyword_diagnostics.append("DDS_KEYWORD_SYNTAX_UNSUPPORTED")
            spec = padded[16]
            name = attrs["name"]
            if name and not re.fullmatch(r"[A-Za-z_$#@][A-Za-z0-9_$#@]{0,9}", name):
                raise ValueError("unsupported name")
            if spec == "R" and name:
                kind = "record"
            elif spec == "K" and name and file_type != "display":
                kind = "key"
            elif spec == " " and name:
                kind = "field"
            elif (
                spec == " "
                and (attrs["line"] is not None or attrs["column"] is not None)
                and file_type == "display"
            ):
                kind = "constant"
            elif spec == " " and (attrs["keywords"] or group["functions"].strip()):
                kind = "keyword"
            else:
                raise ValueError("unsupported specification")
            statement.update(
                kind=kind,
                barrier=bool(keyword_diagnostics),
                diagnostics=keyword_diagnostics,
                defines=[name] if kind in {"record", "field"} else [],
                uses=[name] if kind == "key" else [],
            )
        except ParseError:
            raise
        except (ValueError, LarkError):
            pass
        result.append(statement)
    check()
    return tuple(result)
