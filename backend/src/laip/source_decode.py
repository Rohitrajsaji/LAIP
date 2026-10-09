"""Strict bounded decoding with exact UTF-8-byte to original-byte provenance."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from laip.canonical import digest

ENCODINGS = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "ccsid:1208": "utf-8",
    "ascii": "ascii",
    "us-ascii": "ascii",
    "ccsid:367": "ascii",
    "utf16le": "utf-16-le",
    "utf-16le": "utf-16-le",
    "utf-16-le": "utf-16-le",
    "utf16be": "utf-16-be",
    "utf-16be": "utf-16-be",
    "utf-16-be": "utf-16-be",
    "ccsid:1200": "utf-16-be",
    "ccsid:37": "cp037",
    "cp037": "cp037",
    "ccsid:500": "cp500",
    "cp500": "cp500",
}


@dataclass(frozen=True)
class DecodeResult:
    decoded: bytes | None
    origin_map: dict[str, Any] | None
    status: str
    diagnostics: tuple[str, ...]


def decode_source(
    raw: bytes,
    encoding: str,
    artifact_id: str,
    *,
    max_raw_bytes: int = 16 * 1024 * 1024,
    max_decoded_bytes: int = 32 * 1024 * 1024,
    max_segments: int = 100000,
) -> DecodeResult:
    if not re.fullmatch(r"art_[a-f0-9]{64}", artifact_id):
        raise ValueError("INVALID_ARTIFACT_ID")
    codec = ENCODINGS.get(encoding.lower())
    if codec is None:
        return DecodeResult(None, None, "unsupported", ("UNSUPPORTED_ENCODING",))
    if len(raw) > max_raw_bytes:
        return DecodeResult(None, None, "partial", ("RAW_BYTE_LIMIT",))
    offset = 0
    if codec.startswith("utf-16"):
        bom = b"\xff\xfe" if codec == "utf-16-le" else b"\xfe\xff"
        opposite = b"\xfe\xff" if codec == "utf-16-le" else b"\xff\xfe"
        if raw.startswith(opposite):
            return DecodeResult(None, None, "unsupported", ("BOM_MISMATCH",))
        if raw.startswith(bom):
            offset = 2
    try:
        text = raw[offset:].decode(codec, errors="strict")
        decoded = text.encode("utf-8")
    except UnicodeError:
        return DecodeResult(None, None, "unsupported", ("INVALID_ENCODING",))
    if len(decoded) > max_decoded_bytes:
        return DecodeResult(None, None, "partial", ("DECODED_BYTE_LIMIT",))
    segments: list[dict[str, Any]] = []

    def segment(ds: int, de: int, rs: int, re_: int) -> dict[str, Any]:
        return {
            "decoded_byte_start": ds,
            "decoded_byte_end": de,
            "original_artifact_id": artifact_id,
            "raw_byte_start": rs,
            "raw_byte_end": re_,
        }

    if codec in {"utf-8", "ascii"} or not text:
        segments.append(segment(0, len(decoded), offset, len(raw)))
    else:
        dpos, rpos = 0, offset
        previous_identity = False
        for char in text:
            original = char.encode(codec)
            output = char.encode("utf-8")
            identity = original == output
            if identity and previous_identity:
                segments[-1]["decoded_byte_end"] += len(output)
                segments[-1]["raw_byte_end"] += len(original)
            else:
                if len(segments) >= max_segments:
                    return DecodeResult(None, None, "partial", ("ORIGIN_SEGMENT_LIMIT",))
                segments.append(segment(dpos, dpos + len(output), rpos, rpos + len(original)))
            previous_identity = identity
            dpos += len(output)
            rpos += len(original)
    if len(segments) > max_segments:
        return DecodeResult(None, None, "partial", ("ORIGIN_SEGMENT_LIMIT",))
    mapping = {
        "schema_version": "0.1.0",
        "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
        "encoding": codec,
        "lossy": False,
        "newline_policy": "preserved",
        "segments": segments,
    }
    mapping["origin_map_id"] = "map_" + digest(mapping)
    return DecodeResult(decoded, mapping, "accepted", ())
