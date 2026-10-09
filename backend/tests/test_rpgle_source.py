import pytest

from laip.rpgle_source import IncludeLimits, SourceUnit, expand_source, span_locations, span_origins
from laip.source_decode import decode_source


def unit(member, text, library="LIB", encoding="utf-8"):
    aid = "art_" + (str(len(member)) * 64)
    raw = text.encode(encoding)
    decoded = decode_source(raw, encoding, aid)
    return SourceUnit(
        {
            "system_namespace": "sys",
            "kind": "SourceMember",
            "qualified_identity": [library, "SRC", member],
        },
        {"artifact_id": aid, "byte_length": len(raw)},
        decoded.decoded,
        decoded.origin_map,
    )


def test_nested_repeat_and_coordinates():
    root = unit("ROOT", "/copy SRC,A\n/copy SRC,A\n")
    a = unit("A", "/include SRC,B\n")
    b = unit("BB", "x = '£';\r\n", encoding="cp037")
    b = SourceUnit(
        {**b.identity, "qualified_identity": ["LIB", "SRC", "B"]},
        b.artifact,
        b.decoded,
        b.origin_map,
    )
    expanded = expand_source(root, (a, b))
    assert expanded.text == "x = '£';\r\nx = '£';\r\n"
    assert not expanded.barriers
    pos = expanded.text.index("£")
    loc = span_locations(expanded, pos, pos + 1)[0]
    assert (loc["start_line"], loc["start_column"], loc["byte_start"], loc["byte_end"]) == (
        1,
        6,
        5,
        6,
    )
    assert span_origins(expanded, pos, pos + 1)[0]["decoded_byte_end"] == 7


@pytest.mark.parametrize(
    "directive,units,diagnostic",
    [
        ("/copy SRC,NO\n", (), "INCLUDE_MISSING"),
        ("/copy SRC,A\n", (unit("A", "/copy SRC,A\n"),), "INCLUDE_CYCLE"),
        (
            "/copy SRC,A\n",
            (unit("A", "x;\n", "ONE"), unit("A", "y;\n", "TWO")),
            "INCLUDE_AMBIGUOUS",
        ),
    ],
)
def test_unresolved_has_evidence_barrier(directive, units, diagnostic):
    result = expand_source(unit("ROOT", directive), units, library_list=("LIB", "ONE", "TWO"))
    assert diagnostic in result.diagnostics
    assert result.barriers
    assert span_locations(result, result.barriers[0], result.barriers[0] + 1)


def test_qualified_and_comment_string_lookalikes():
    text = "/*\n/copy SRC,A\n*/\nx = '\n/copy SRC,A\n';\n// /copy SRC,A\n/copy OTHER/SRC,A\n"
    result = expand_source(unit("ROOT", text), (unit("A", "ok;\n", "OTHER"),))
    assert result.text == text.replace("/copy OTHER/SRC,A\n", "ok;\n")


def test_missing_newline_does_not_fuse_statements():
    result = expand_source(unit("ROOT", "/copy SRC,A\nx;\n"), (unit("A", "a;"),))
    assert result.text == "a;\nx;\n"
    assert span_locations(result, 0, 2)[0]["byte_end"] == 2


def test_limits_and_cancellation():
    root = unit("ROOT", "/copy SRC,A\n")
    a = unit("A", "abc;\n")
    with pytest.raises(ValueError, match="EXPANDED_BYTE_LIMIT"):
        expand_source(root, (a,), limits=IncludeLimits(max_expanded_bytes=2))
    with pytest.raises(ValueError, match="CANCELLED"):
        expand_source(root, (a,), cancelled=lambda: True)
    result = expand_source(root, (a,), limits=IncludeLimits(max_includes=0))
    assert "INCLUDE_COUNT_LIMIT" in result.diagnostics


def test_span_crosses_include_sources():
    result = expand_source(unit("ROOT", "a;\n/copy SRC,A\nb;\n"), (unit("A", "c;\n"),))
    assert len(span_locations(result, 0, len(result.text))) == 3
    with pytest.raises(ValueError):
        span_locations(result, -1, 2)


def test_depth_limit_and_bare_member():
    root = unit("ROOT", "/copy A\n")
    a = unit("A", "/copy B\n")
    b = unit("B", "x;\n")
    result = expand_source(root, (a, b), limits=IncludeLimits(max_depth=1))
    assert result.text == "/copy B\n"
    assert result.diagnostics == ("INCLUDE_DEPTH_LIMIT",)
    assert (
        span_locations(result, 0, len(result.text))[0]["artifact_id"] == a.artifact["artifact_id"]
    )


def test_scope_is_not_cross_namespace():
    a = unit("A", "x;\n")
    a = SourceUnit(
        {**a.identity, "system_namespace": "different"}, a.artifact, a.decoded, a.origin_map
    )
    result = expand_source(unit("ROOT", "/copy A\n"), (a,))
    assert result.diagnostics == ("INCLUDE_MISSING",)


def test_utf16_bom_and_escaped_quote_span():
    root = unit("ROOT", "x='it''s £';\n", encoding="utf-16-le")
    result = expand_source(root, ())
    pos = result.text.index("£")
    span = span_locations(result, pos, pos + 1)[0]
    assert span["byte_start"] == pos * 2
    assert span["byte_end"] == pos * 2 + 2
    assert len(result.segments) == 1


def test_invalid_limits_and_timeout(monkeypatch):
    root = unit("ROOT", "x;\n")
    with pytest.raises(ValueError, match="INVALID_INCLUDE_LIMITS"):
        expand_source(root, (), limits=IncludeLimits(max_depth=-1))
    with pytest.raises(ValueError, match="INVALID_INCLUDE_LIMITS"):
        expand_source(root, (), limits=IncludeLimits(timeout_seconds=0))
    clock = iter((1, 50))
    monkeypatch.setattr("laip.rpgle_source.time.monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="INCLUDE_TIMEOUT"):
        expand_source(root, ())


def test_canonical_identity_and_exact_case_do_not_alias():
    root = unit("ROOT", "/copy SRC,A\n")
    other = unit("a", "x = 1;\n")
    result = expand_source(root, (other,))
    assert "INCLUDE_MISSING" in result.diagnostics
    assert result.barriers
