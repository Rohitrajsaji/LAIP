import pytest

from laip.cl_parser import ParseError, ParserLimits, parse_statements


def test_quotes_continuation_and_facts():
    text = (
        "DCL VAR(&X) TYPE(*CHAR) LEN(20) VALUE('a''b')\nCALL PGM(LIB/PGM) +\n PARM(&X)\n"
        "CALL PGM(&X)\n"
    )
    facts = parse_statements(text)
    assert [s["kind"] for s in facts] == ["declaration", "call", "call"]
    assert facts[0]["defines"] == ["&X"]
    assert facts[1]["attributes"]["target"] == "LIB/PGM"
    assert facts[2]["attributes"]["dynamic"]
    assert text[facts[1]["start"] : facts[1]["end"]].startswith("CALL")


def test_unknown_and_bad_quote():
    assert parse_statements("RUNSQL SQL('select 1')")[0]["barrier"]
    assert parse_statements("SNDPGMMSG MSG('bad)")[0]["barrier"]


def test_limits():
    with pytest.raises(ParseError):
        parse_statements("PGM", limits=ParserLimits(max_source_bytes=1))
    with pytest.raises(ParseError):
        parse_statements("PGM", cancelled=lambda: True)


def test_quote_continuation_and_expression_validation():
    facts = parse_statements(
        "SNDPGMMSG MSG('ab+\n  cd')\nSNDPGMMSG MSG('ab-\n  cd')\n"
        "CHGVAR VAR(&X) VALUE(&X + 1)\nCHGVAR VAR(&X) VALUE(1 +)\n"
    )
    assert facts[0]["attributes"]["arguments"]["MSG"] == "'abcd'"
    assert facts[1]["attributes"]["arguments"]["MSG"] == "'ab  cd'"
    assert facts[2]["attributes"]["expression"]["kind"] == "binary"
    assert facts[3]["barrier"]


def test_real_fixture_nested_commands_and_references():
    from pathlib import Path

    source = (Path(__file__).parent / "fixtures/cl/MAIN.clle").read_text()
    facts = parse_statements(source)
    assert not any(s["barrier"] for s in facts)
    assert facts[3]["attributes"]["embedded"]["kind"] == "do"
    job = next(s for s in facts if s["kind"] == "submit_job")
    assert job["attributes"]["submitted"]["attributes"]["target"] == "LIB1/BATCH"
    monitor = next(s for s in facts if s["kind"] == "message_monitor")
    assert monitor["attributes"]["embedded"]["kind"] == "assignment"
    assert monitor["attributes"]["embedded"]["attributes"]["expression"]["kind"] == "unary"
    assert next(s for s in facts if s["kind"] == "override")["attributes"]["target"] == "LIB1/DATA"
    for fact in facts:
        assert source[fact["start"] : fact["end"]] == fact["text"]


@pytest.mark.parametrize(
    "source",
    [
        "CALL PGM(A) PGM(B)",
        "DCL VAR(&X) TYPE(*BAD)",
        "IF COND(1 +) THEN(DO)",
        "IF COND(1) THEN(RUNSQL SQL('x'))",
        "LEAVE LABEL(OUTER)",
        "CALL PGM(X) UNKNOWN(Y)",
        "CALL PGM('bad)",
        "X: CALL PGM(Y)",
    ],
)
def test_unsupported_shapes_explicit(source):
    assert parse_statements(source)[0]["barrier"]


@pytest.mark.parametrize(
    "source",
    [
        "/* ignored */ DCL VAR(&X) TYPE(*INT) VALUE(1)",
        "DCL VAR(&X) TYPE(*CHAR) VALUE('/* literal */')",
        "CHGVAR VAR(&X) VALUE((&A *GE 1) *AND (&B *NE 2))",
        "DOFOR VAR(&I) FROM(1) TO(3) BY(1)",
        "DOUNTIL COND(&I *GT 3)",
        "IF COND(&X *EQ 1) THEN(CHGVAR VAR(&X) VALUE(2))",
        "ELSE CMD(CALL PGM(LIB/NO))",
    ],
)
def test_supported_forms(source):
    assert not parse_statements(source)[0]["barrier"]


@pytest.mark.parametrize(
    "limits,source",
    [
        (ParserLimits(max_statements=1), "PGM\nENDPGM"),
        (ParserLimits(max_statement_chars=2), "PGM"),
        (ParserLimits(max_expression_depth=1), "CHGVAR VAR(&X) VALUE(((1)))"),
        (ParserLimits(max_tokens=1), "CALL PGM(X)"),
    ],
)
def test_resource_bounds(limits, source):
    with pytest.raises(ParseError):
        parse_statements(source, limits=limits)


@pytest.mark.parametrize(
    "source", ["CALL PGM(A B)", "DCL VAR(&X) TYPE(*CHAR) LEN(A)", "/* unclosed"]
)
def test_malformed_operands(source):
    assert parse_statements(source)[0]["barrier"]


@pytest.mark.parametrize(
    "limits", [ParserLimits(max_tokens=0), ParserLimits(timeout_seconds=float("nan"))]
)
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        parse_statements("PGM", limits=limits)


def test_quoted_target_and_expression_operators():
    facts = parse_statements(
        "CALL PGM('LIB/PROG')\nCHGVAR VAR(&X) VALUE(*NOT (&Y *EQ *TRUE) *OR &A / 2 *GT 1)\n"
    )
    assert facts[0]["attributes"]["target"] == "LIB/PROG"
    assert not facts[1]["barrier"]
    assert facts[1]["uses"] == ["&A", "&Y"]


def test_embedded_nesting_bound():
    with pytest.raises(ParseError):
        parse_statements(
            "IF COND(1) THEN(IF COND(1) THEN(IF COND(1) THEN(DO)))",
            limits=ParserLimits(max_expression_depth=2),
        )


def test_operator_chain_bound():
    with pytest.raises(ParseError):
        parse_statements(
            "CHGVAR VAR(&X) VALUE(" + "+".join(["1"] * 10) + ")",
            limits=ParserLimits(max_expression_depth=4),
        )


@pytest.mark.parametrize(
    "source,expected",
    [
        ("CHGVAR VAR(&X) VALUE(1)", []),
        ("CHGVAR VAR(&X) VALUE(&X + 1)", ["&X"]),
        ("DOFOR VAR(&I) FROM(1) TO(3) BY(1)", []),
        ("DOFOR VAR(&I) FROM(&A) TO(&B) BY(&STEP)", ["&A", "&B", "&STEP"]),
        ("DCL VAR(&X) TYPE(*INT) VALUE(&X)", ["&X"]),
    ],
)
def test_variable_reads_come_from_expressions(source, expected):
    assert parse_statements(source)[0]["uses"] == expected
