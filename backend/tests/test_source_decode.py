import hashlib

import pytest

from laip.source_decode import decode_source

ART = "art_" + "a" * 64


def test_utf8_identity_preserves_newlines_and_empty():
    raw = "é\r\nx\n".encode()
    result = decode_source(raw, "UTF-8", ART)
    assert result.status == "accepted"
    assert result.decoded == raw
    assert result.origin_map["decoded_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result.origin_map["newline_policy"] == "preserved"
    assert len(result.origin_map["segments"]) == 1
    empty = decode_source(b"", "utf-8", ART)
    assert empty.origin_map["segments"][0]["raw_byte_end"] == 0


@pytest.mark.parametrize(
    "encoding,codec,bom",
    [
        ("UTF-16LE", "utf-16-le", b"\xff\xfe"),
        ("UTF-16BE", "utf-16-be", b"\xfe\xff"),
        ("CCSID:37", "cp037", b""),
        ("CCSID:500", "cp500", b""),
    ],
)
def test_exact_nonidentity_mapping(encoding, codec, bom):
    text = "Aé\r\n"
    raw = bom + text.encode(codec)
    result = decode_source(raw, encoding, ART)
    assert result.decoded == text.encode()
    segments = result.origin_map["segments"]
    for segment in segments:
        original = raw[segment["raw_byte_start"] : segment["raw_byte_end"]]
        decoded = result.decoded[segment["decoded_byte_start"] : segment["decoded_byte_end"]]
        assert original.decode(codec).encode() == decoded
    assert segments[0]["raw_byte_start"] == len(bom)


def test_utf16_surrogate_pair_and_limits():
    result = decode_source("😀".encode("utf-16-le"), "utf-16le", ART)
    assert result.origin_map["segments"][0]["raw_byte_end"] == 4
    assert decode_source(b"A", "ascii", ART, max_raw_bytes=0).status == "partial"
    assert decode_source(b"A", "ascii", ART, max_decoded_bytes=0).decoded is None
    result = decode_source("éé".encode("cp037"), "cp037", ART, max_segments=1)
    assert result.status == "partial" and result.origin_map is None


@pytest.mark.parametrize(
    "raw,encoding,diagnostic",
    [
        (b"\xff", "utf-8", "INVALID_ENCODING"),
        (b"A", "CCSID:65535", "UNSUPPORTED_ENCODING"),
        (b"\xfe\xff\x00A", "utf-16le", "BOM_MISMATCH"),
    ],
)
def test_decode_rejects_loss_or_unknown(raw, encoding, diagnostic):
    result = decode_source(raw, encoding, ART)
    assert result.decoded is None and result.origin_map is None
    assert diagnostic in result.diagnostics


def test_identity_merge_stays_byte_exact_and_map_is_valid():
    from laip.persistence import validate

    text = "ABCéDEF"
    result = decode_source(text.encode("cp037"), "cp037", ART)
    validate("OriginMap", result.origin_map)
    assert result.decoded == text.encode()
    for segment in result.origin_map["segments"]:
        left = text.encode("cp037")[segment["raw_byte_start"] : segment["raw_byte_end"]]
        right = result.decoded[segment["decoded_byte_start"] : segment["decoded_byte_end"]]
        assert left.decode("cp037").encode() == right
    with pytest.raises(ValueError, match="ARTIFACT"):
        decode_source(b"A", "utf-8", "unsafe")
    assert decode_source(b"", "utf-8", ART, max_segments=0).status == "partial"
