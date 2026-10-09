"""Bounded, inert RPG include expansion and original-artifact coordinates."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from laip.persistence import validate


@dataclass(frozen=True)
class SourceUnit:
    identity: dict[str, Any]
    artifact: dict[str, Any]
    decoded: bytes
    origin_map: dict[str, Any]


@dataclass(frozen=True)
class IncludeLimits:
    max_depth: int = 16
    max_includes: int = 256
    max_expanded_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 30


@dataclass(frozen=True)
class ExpandedSource:
    text: str
    segments: tuple[dict[str, Any], ...]
    diagnostics: tuple[str, ...]
    barriers: tuple[int, ...]


DEFAULT_INCLUDE_LIMITS = IncludeLimits()


def expand_source(
    root: SourceUnit,
    available: tuple[SourceUnit, ...],
    *,
    library_list: tuple[str, ...] = (),
    limits: IncludeLimits = DEFAULT_INCLUDE_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> ExpandedSource:
    if min(limits.max_depth, limits.max_includes) < 0 or limits.max_expanded_bytes < 1:
        raise ValueError("INVALID_INCLUDE_LIMITS")
    if not 0 < limits.timeout_seconds <= 3600:
        raise ValueError("INVALID_INCLUDE_LIMITS")
    deadline = time.monotonic() + limits.timeout_seconds
    chunks: list[str] = []
    segments: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    barriers: list[int] = []
    chars = size = count = 0

    def check() -> None:
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        if time.monotonic() >= deadline:
            raise ValueError("INCLUDE_TIMEOUT")

    def append(text: str, unit: SourceUnit, start: int, end: int) -> None:
        nonlocal chars, size
        check()
        size += len(text.encode("utf-8"))
        if size > limits.max_expanded_bytes:
            raise ValueError("EXPANDED_BYTE_LIMIT")
        if (
            segments
            and segments[-1]["unit"] is unit
            and segments[-1]["source_end"] == start
            and segments[-1]["expanded_end"] == chars
        ):
            segments[-1]["expanded_end"] += len(text)
            segments[-1]["source_end"] = end
        else:
            segments.append(
                dict(
                    expanded_start=chars,
                    expanded_end=chars + len(text),
                    unit=unit,
                    source_start=start,
                    source_end=end,
                )
            )
        chunks.append(text)
        chars += len(text)

    def key(unit: SourceUnit) -> tuple[str, ...]:
        return (
            str(unit.identity["system_namespace"]),
            *(str(p) for p in unit.identity["qualified_identity"]),
        )

    indexed: dict[tuple[str, ...], list[SourceUnit]] = {}
    if len(available) > 10000 or len(library_list) > 10000:
        raise ValueError("INCLUDE_INPUT_LIMIT")
    for unit in (root, *available):
        check()
        validate("Identity", unit.identity)
        if unit.identity["kind"] != "SourceMember" or len(unit.identity["qualified_identity"]) != 3:
            raise ValueError("INVALID_SOURCE_IDENTITY")
    for unit in available:
        check()
        indexed.setdefault(key(unit), []).append(unit)

    def walk(unit: SourceUnit, ancestors: tuple[tuple[str, ...], ...]) -> None:
        nonlocal count
        check()
        if len(unit.decoded) > limits.max_expanded_bytes:
            raise ValueError("EXPANDED_BYTE_LIMIT")
        text = unit.decoded.decode("utf-8", errors="strict")
        offset = 0
        quote = block = False
        for line in text.splitlines(keepends=True):
            check()
            match = (
                None
                if quote or block
                else re.fullmatch(
                    r"\s*/(?:copy|include)\s+([A-Za-z0-9_$#@]+(?:/[A-Za-z0-9_$#@]+)?"
                    r"(?:,[A-Za-z0-9_$#@]+)?)\s*(?://[^\r\n]*)?[\r\n]*",
                    line,
                    re.I,
                )
            )
            if match:
                libraries: tuple[str, ...]
                operand = match[1]
                ns, lib, srcfile, _ = key(unit)
                if "," in operand:
                    prefix, member = operand.split(",")
                    if "/" in prefix:
                        library, file = prefix.split("/")
                        libraries = (library,)
                    else:
                        file, libraries = prefix, library_list or (lib,)
                else:
                    member, file, libraries = operand, srcfile, library_list or (lib,)
                candidates = []
                for library in libraries:
                    check()
                    candidates.extend(indexed.get((ns, library, file, member), []))
                reason = None
                count += 1
                if count > limits.max_includes:
                    reason = "INCLUDE_COUNT_LIMIT"
                elif len(ancestors) > limits.max_depth:
                    reason = "INCLUDE_DEPTH_LIMIT"
                elif not candidates:
                    reason = "INCLUDE_MISSING"
                elif len(candidates) != 1:
                    reason = "INCLUDE_AMBIGUOUS"
                elif key(candidates[0]) in ancestors:
                    reason = "INCLUDE_CYCLE"
                if reason:
                    diagnostics.append(reason)
                    barriers.append(chars)
                    append(line, unit, offset, offset + len(line))
                else:
                    before = chars
                    walk(candidates[0], (*ancestors, key(candidates[0])))
                    if chars > before and not chunks[-1].endswith(("\r", "\n")):
                        # Directive newline is real provenance; never fabricate source bytes.
                        newline = (
                            "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                        )
                        if newline:
                            append(
                                newline, unit, offset + len(line) - len(newline), offset + len(line)
                            )
            else:
                append(line, unit, offset, offset + len(line))
                i = 0
                while i < len(line):
                    if block:
                        if line.startswith("*/", i):
                            block = False
                            i += 2
                            continue
                    elif quote:
                        if line[i] == "'":
                            if i + 1 < len(line) and line[i + 1] == "'":
                                i += 2
                                continue
                            quote = False
                    elif line.startswith("//", i):
                        break
                    elif line.startswith("/*", i):
                        block = True
                        i += 2
                        continue
                    elif line[i] == "'":
                        quote = True
                    i += 1
            offset += len(line)

    walk(root, (key(root),))
    check()
    return ExpandedSource("".join(chunks), tuple(segments), tuple(diagnostics), tuple(barriers))


def span_origins(source: ExpandedSource, start: int, end: int) -> tuple[dict[str, Any], ...]:
    if not 0 <= start <= end <= len(source.text):
        raise ValueError("INVALID_SOURCE_SPAN")
    locations: list[dict[str, Any]] = []
    for segment in source.segments:
        left, right = max(start, segment["expanded_start"]), min(end, segment["expanded_end"])
        if left >= right:
            continue
        unit = segment["unit"]
        text = unit.decoded.decode("utf-8")
        a = segment["source_start"] + left - segment["expanded_start"]
        b = a + right - left
        ds, de = len(text[:a].encode("utf-8")), len(text[:b].encode("utf-8"))
        covered = [
            s
            for s in unit.origin_map["segments"]
            if s["decoded_byte_start"] < de and s["decoded_byte_end"] > ds
        ]
        if not covered:
            raise ValueError("MISSING_ORIGIN_MAPPING")
        raw_start, raw_end = covered[0]["raw_byte_start"], covered[-1]["raw_byte_end"]
        for s, boundary, is_start in ((covered[0], ds, True), (covered[-1], de, False)):
            if (
                s["decoded_byte_end"] - s["decoded_byte_start"]
                == s["raw_byte_end"] - s["raw_byte_start"]
            ):
                value = s["raw_byte_start"] + boundary - s["decoded_byte_start"]
                if is_start:
                    raw_start = value
                else:
                    raw_end = value

        def position(offset: int, text: str = text) -> tuple[int, int]:
            endings = list(re.finditer(r"\r\n|\r|\n", text[:offset]))
            return len(endings) + 1, offset - (endings[-1].end() if endings else 0) + 1

        start_line, start_column = position(a)
        end_line, end_column = position(b)
        locations.append(
            dict(
                artifact_id=unit.artifact["artifact_id"],
                start_line=start_line,
                start_column=start_column,
                end_line=end_line,
                end_column=end_column,
                byte_start=raw_start,
                byte_end=raw_end,
                coordinate_space="decoded_unicode_codepoints",
                origin_map_id=unit.origin_map["origin_map_id"],
                decoded_byte_start=ds,
                decoded_byte_end=de,
            )
        )
    return tuple(locations)


def span_locations(source: ExpandedSource, start: int, end: int) -> tuple[dict[str, Any], ...]:
    return tuple(
        {k: v for k, v in loc.items() if not k.startswith("decoded_byte_")}
        for loc in span_origins(source, start, end)
    )
