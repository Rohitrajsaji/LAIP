import pytest

from laip.rpgle_parser import ParserLimits, parse_statements


def test_strings_comments_and_offsets():
    text = "**free\n// ignored ;\ndcl-s msg char;\nmsg = 'it''s; // text'; // trailing\nreturn;"
    statements = parse_statements(text)
    assert [s["kind"] for s in statements] == ["declaration", "assignment", "return"]
    assigned = statements[1]
    assert text[assigned["start"] : assigned["end"]] == assigned["text"]
    assert assigned["attributes"]["expression"] == {"node": "literal", "value": "it's; // text"}
    assert assigned["uses"] == [] and assigned["defines"] == ["msg"]
    assert all(not s["barrier"] for s in statements)


def test_nested_expression_and_conditions():
    statements = parse_statements(
        "total = (price + tax) * qty; if total >= 10 and ready = *on; endif;"
    )
    assignment, condition, end = statements
    assert assignment["kind"] == "assignment"
    assert assignment["uses"] == ["price", "qty", "tax"]
    assert assignment["attributes"]["expression"]["operator"] == "*"
    assert condition["attributes"]["expression"]["operator"] == "and"
    assert condition["uses"] == ["ready", "total"]
    assert end["kind"] == "endif"


@pytest.mark.parametrize(
    "text,kinds",
    [
        ("dcl-c MAX 10; dcl-f ORDERS keyed; dcl-s x int;", ["declaration"] * 3),
        (
            "dcl-pr calc; dcl-parm x int; end-pr; dcl-pi calc; end-pi;",
            ["declaration", "declaration", "prototype_end", "declaration", "interface_end"],
        ),
        (
            "dcl-proc calc; return x; end-proc; begsr sub; endsr;",
            ["procedure_start", "return", "procedure_end", "subroutine_start", "subroutine_end"],
        ),
        (
            "dow x < 10; x += 1; enddo; dou done; enddo; for i = 1 to 9 by 2; endfor;",
            ["dow", "assignment", "enddo", "dou", "enddo", "for", "endfor"],
        ),
        (
            "select; when x = 1; other; endsl; monitor; on-error; endmon;",
            ["select", "when", "other", "endsl", "monitor", "on_error", "endmon"],
        ),
        (
            "callp calc(x: y); call calc; exsr sub; read ORDERS; chain key ORDERS; "
            "setll key ORDERS;"
            "write REC; update REC; delete REC; exfmt SCREEN; leave; iter;",
            [
                "call",
                "call",
                "exsr",
                "read",
                "chain",
                "setll",
                "write",
                "update",
                "delete",
                "exfmt",
                "leave",
                "iter",
            ],
        ),
    ],
)
def test_supported_statement_families(text, kinds):
    statements = parse_statements(text)
    assert [s["kind"] for s in statements] == kinds
    assert all(not s["barrier"] for s in statements)


@pytest.mark.parametrize(
    "text",
    [
        "mystery x;",
        "read(e) ORDERS;",
        "x = (a + );",
        "dcl-s x likeds(Unknown);",
        "x = 'unterminated;",
        "x = 2",
    ],
)
def test_unknown_or_malformed_is_opaque_barrier(text):
    statements = parse_statements(text)
    assert statements[-1]["kind"] == "opaque"
    assert statements[-1]["barrier"] and statements[-1]["diagnostics"]
    assert statements[-1]["uses"] == statements[-1]["defines"] == []


@pytest.mark.parametrize(
    "limits",
    [
        ParserLimits(max_source_bytes=3),
        ParserLimits(max_tokens=2),
        ParserLimits(max_statement_chars=3),
        ParserLimits(max_expression_depth=1),
        ParserLimits(max_statements=1),
    ],
)
def test_resource_limits_reject(limits):
    with pytest.raises(ValueError, match="LIMIT"):
        parse_statements("x = ((a + b)); y = 2;", limits=limits)


def test_cancel_empty_and_case_insensitive_keywords():
    assert parse_statements("// comment\n**free\n") == ()
    assert parse_statements("IF A = 1; ELSE; ENDIF;")[0]["uses"] == ["A"]
    with pytest.raises(ValueError, match="CANCELLED"):
        parse_statements("return;", cancelled=lambda: True)
    with pytest.raises(ValueError):
        ParserLimits(max_tokens=0)


def test_unresolved_copy_is_standalone_and_strings_are_inert():
    text = "/copy LIB/FILE,MEMBER\nx = 1; // /copy ignored\nx = '/include text';"
    statements = parse_statements(text)
    assert [s["kind"] for s in statements] == ["opaque", "assignment", "assignment"]
    assert statements[0]["diagnostics"] == ["UNRESOLVED_PREPROCESSOR_DIRECTIVE"]
    assert statements[1]["defines"] == ["x"]


def test_initializers_types_and_expression_calls():
    statements = parse_statements(
        "dcl-s amount packed(9:2) inz(0); "
        "amount = -base + %len(name) ** 2; on-error 100; "
        "call 'PGM'; return %trim(name);"
    )
    declaration, assignment, handler, call, returned = statements
    assert declaration["attributes"]["dimensions"] == ["9", "2"]
    assert declaration["attributes"]["initializer"]["value"] == "0"
    assert assignment["uses"] == ["base", "name"]
    assert handler["attributes"]["status_code"] == "100"
    assert call["attributes"]["target"] == "PGM"
    assert returned["attributes"]["expression"]["node"] == "invocation"


@pytest.mark.parametrize(
    "source", ["x = " + "not " * 20 + "flag;", "x = " + " + ".join(["1"] * 20) + ";"]
)
def test_operator_depth_is_bounded_without_parentheses(source):
    with pytest.raises(ValueError, match="DEPTH_LIMIT"):
        parse_statements(source, limits=ParserLimits(max_expression_depth=4))


def test_deadline_and_post_parse_cancellation(monkeypatch):
    import laip.rpgle_parser as module

    times = iter([0, 121])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="TIME_LIMIT"):
        parse_statements("return;")
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)
    cancelled = False
    original_parse = module.PARSER.parse

    def parse_then_cancel(text):
        nonlocal cancelled
        result = original_parse(text)
        cancelled = True
        return result

    monkeypatch.setattr(module.PARSER, "parse", parse_then_cancel)
    with pytest.raises(ValueError, match="CANCELLED"):
        parse_statements("x = 1;", cancelled=lambda: cancelled)


def test_statement_size_counts_terminator_comments_and_doubled_quotes():
    for text, maximum in [("x=1;", 3), ("x='''''';", 8), ("x=1 // " + "a" * 100, 20)]:
        with pytest.raises(ValueError, match="STATEMENT_SIZE_LIMIT"):
            parse_statements(text, limits=ParserLimits(max_statement_chars=maximum))


def test_cancel_during_doubled_quote_scan():
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls == 3

    with pytest.raises(ValueError, match="CANCELLED"):
        parse_statements("x='" + "''" * 5000 + "';", cancelled=cancelled)


def test_right_associative_power_depth_limit():
    with pytest.raises(ValueError, match="DEPTH_LIMIT"):
        parse_statements(
            "x=" + "**".join(["1"] * 20) + ";", limits=ParserLimits(max_expression_depth=4)
        )


def test_file_declaration_usage_is_explicit_and_unknown_options_barrier():
    declared = parse_statements("dcl-f CUSTOMER usage(*input:*output) keyed;")[0]
    assert declared["kind"] == "declaration"
    assert declared["attributes"]["usage"] == ["input", "output"]
    assert declared["attributes"]["keyed"] is True
    assert parse_statements("dcl-f CUSTOMER usage(*unknown) keyed;")[0]["barrier"]
