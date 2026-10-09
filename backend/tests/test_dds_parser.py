import pytest

from laip.dds_parser import ParseError, ParserLimits, parse_statements


def line(name="", spec="", length="", dtype="", keywords="", ref="", usage="", row="", col=""):
    cells = [" "] * 80
    cells[5] = "A"
    cells[16] = spec or " "
    cells[18:28] = list(name.ljust(10))
    cells[28] = ref or " "
    cells[29:34] = list(length.rjust(5))
    cells[34] = dtype or " "
    cells[37] = usage or " "
    cells[38:41] = list(row.rjust(3))
    cells[41:44] = list(col.rjust(3))
    cells[44:80] = list(keywords.ljust(36))
    return "".join(cells)


def test_physical_facts():
    facts = parse_statements(
        line("REC", "R")
        + "\n"
        + line("ID", length="5", dtype="P", keywords="RANGE(1 99)")
        + "\n"
        + line("ID", "K"),
        file_type="physical",
    )
    assert [x["kind"] for x in facts] == ["record", "field", "key"]
    assert facts[1]["attributes"]["length"] == 5
    assert facts[1]["attributes"]["keywords"][0]["name"] == "RANGE"
    assert facts[1]["attributes"]["column_spans"]["name"] == {"start": 99, "end": 109}


def test_unknown_is_barrier():
    assert parse_statements(
        line("ID", length="5", dtype="A", keywords="UNKNOWN(1)"), file_type="physical"
    )[0]["barrier"]


def test_logical_reference_targets_preserve_spelling():
    source = (
        line("REC", "R", keywords="PFILE(MyLib/MyFile)")
        + "\n"
        + line("ID")
        + "\n"
        + line("ID", "K", keywords="DESCEND")
    )
    facts = parse_statements(source, file_type="logical")
    assert not any(x["barrier"] for x in facts)
    assert facts[0]["attributes"]["keywords"][0]["arguments"] == ["MyLib/MyFile"]
    assert not facts[1]["attributes"]["reference"]


def test_display_constant_coordinates_and_quote_continuation():
    source = (
        line(row="2", col="3", keywords="'Customer''s +")
        + "\n"
        + line(keywords="    Name' DSPATR(HI)")
    )
    facts = parse_statements(source, file_type="display")
    assert len(facts) == 1 and facts[0]["kind"] == "constant"
    assert facts[0]["attributes"]["line"] == 2 and facts[0]["attributes"]["column"] == 3
    assert facts[0]["attributes"]["keywords"][0]["arguments"] == ["Customer's Name"]
    assert len(facts[0]["attributes"]["keyword_segments"]) == 2
    assert source[facts[0]["start"] : facts[0]["end"]] == facts[0]["text"]


def test_minus_continuation_retains_columns45_spaces():
    facts = parse_statements(
        line(row="1", col="1", keywords="'a -") + "\n" + line(keywords="  b'"), file_type="display"
    )
    assert facts[0]["attributes"]["keywords"][0]["arguments"] == ["a   b"]


def test_ordinary_keyword_continuations():
    facts = parse_statements(
        line("X", length="5", dtype="A", keywords="VALUES('A' 'B')")
        + "\n"
        + line(keywords="CHECK(LC)"),
        file_type="physical",
    )
    assert len(facts) == 1
    assert [k["name"] for k in facts[0]["attributes"]["keywords"]] == ["VALUES", "CHECK"]


@pytest.mark.parametrize(
    "keywords,file_type",
    [
        ("REF(LIB/FILE REC)", "physical"),
        ("REFFLD(ID LIB/FILE)", "physical"),
        ("JFILE(LIB/A LIB/B)", "logical"),
        ("COMP(GE 1)", "display"),
        ("RANGE(1 9)", "display"),
        ("CHECK(ME MF)", "display"),
        ("DFT(0)", "physical"),
        ("EDTCDE(J)", "display"),
        ("DSPATR(RI UL)", "display"),
        ("TEXT('a')", "physical"),
    ],
)
def test_supported_keyword_shapes(keywords, file_type):
    assert not parse_statements(line("X", ref="R", keywords=keywords), file_type=file_type)[0][
        "barrier"
    ]


@pytest.mark.parametrize(
    "keywords,file_type",
    [
        ("RANGE(1)", "physical"),
        ("COMP(BAD 1)", "display"),
        ("CHECK(BAD)", "display"),
        ("DSPATR(BAD)", "display"),
        ("EDTCDE(Q)", "display"),
        ("PFILE(A)", "physical"),
        ("JFILE(A B)", "display"),
        ("OVERLAY", "physical"),
        ("COLHDG('x')", "display"),
        ("PFILE(A B)", "logical"),
        ("UNKNOWN", "physical"),
        ("DFT('bad)", "display"),
    ],
)
def test_unknown_keywords_and_bad_shapes(keywords, file_type):
    assert parse_statements(line("X", keywords=keywords), file_type=file_type)[0]["barrier"]


def test_comment_and_unfinished_continuation():
    comment = line()[:6] + "*" + "comment"
    facts = parse_statements(
        comment + "\n" + line("X", keywords="TEXT('oops+") + "\n" + line("REC", "R"),
        file_type="physical",
    )
    assert facts[0]["kind"] == "comment" and not facts[0]["barrier"]
    assert facts[1]["barrier"]
    assert parse_statements(line("X", keywords="TEXT('x')+"), file_type="physical")[0]["barrier"]


@pytest.mark.parametrize(
    "source",
    [
        line("BAD NAME"),
        line("X", length="bad"),
        line("X", dtype="?"),
        line("X", usage="?"),
        line("X", row="2"),
        line("X") + "EXTRA",
        line("X", "S"),
        line("X")[:7] + "01" + line("X")[9:],
        "\t" + line("X"),
    ],
)
def test_invalid_positions(source):
    assert parse_statements(source, file_type="physical")[0]["barrier"]


@pytest.mark.parametrize(
    "limits,source",
    [
        (ParserLimits(max_source_bytes=1), line("X")),
        (ParserLimits(max_statements=1), line("R", "R") + "\n" + line("X")),
        (ParserLimits(max_lines=1), line("R", "R") + "\n" + line("X")),
        (ParserLimits(max_keywords=1), line("X", keywords="DFT(1) TEXT('x')")),
        (
            ParserLimits(max_statement_chars=60),
            line("X", keywords="TEXT('long +") + "\n" + line(keywords="continued')"),
        ),
    ],
)
def test_resource_limits(limits, source):
    with pytest.raises(ParseError):
        parse_statements(source, file_type="physical", limits=limits)


def test_invalid_parameters_and_cancellation():
    with pytest.raises(ValueError):
        parse_statements("", file_type="unknown")
    with pytest.raises(ValueError):
        parse_statements("", file_type="physical", limits=ParserLimits(max_lines=0))
    with pytest.raises(ParseError):
        parse_statements(line("X"), file_type="physical", cancelled=lambda: True)
    assert parse_statements("\n  \n", file_type="physical") == ()


@pytest.mark.parametrize(
    "filename,file_type,kinds",
    [
        ("PHYSICAL.dds", "physical", ["keyword", "record", "field", "field", "key"]),
        ("LOGICAL.dds", "logical", ["record", "field", "key"]),
        ("DISPLAY.dds", "display", ["record", "field", "constant"]),
    ],
)
def test_fixture_facts(filename, file_type, kinds):
    from pathlib import Path

    source = (Path(__file__).parent / "fixtures/dds" / filename).read_text()
    facts = parse_statements(source, file_type=file_type)
    assert [fact["kind"] for fact in facts] == kinds
    assert not any(fact["barrier"] for fact in facts)
    for fact in facts:
        assert source[fact["start"] : fact["end"]] == fact["text"]
        for segment in fact["attributes"]["keyword_segments"]:
            assert segment["start"] <= segment["end"]


def test_simple_conditional_indicators():
    source = line("X", length="10", dtype="A", usage="O", row="2", col="3", keywords="DSPATR(HI)")
    source = source[:7] + "N01" + " 02" + "   " + source[16:]
    fact = parse_statements(source, file_type="display")[0]
    assert not fact["barrier"]
    assert [(i["number"], i["negated"]) for i in fact["attributes"]["indicators"]] == [
        (1, True),
        (2, False),
    ]
    assert fact["attributes"]["indicators"][0]["positions"] == {"start": 7, "end": 10}


def test_conditional_keyword_continuation_stays_separate():
    field = line("X", length="10", dtype="A", usage="O", row="2", col="3")
    conditional = line(keywords="DSPATR(HI)")
    conditional = conditional[:7] + " 01" + conditional[10:]
    facts = parse_statements(field + "\n" + conditional, file_type="display")
    assert len(facts) == 2
    assert not facts[0]["attributes"]["indicators"]
    assert facts[1]["attributes"]["indicators"][0]["number"] == 1


def test_reffld_extra_operand_is_opaque():
    assert parse_statements(
        line("COPY", ref="R", keywords="REFFLD(ID *SRC EXTRA)"), file_type="physical"
    )[0]["barrier"]


@pytest.mark.parametrize("keyword", ["REF(LIB/PF)", "REFFLD(ID LIB/PF)"])
def test_logical_reference_keywords_are_opaque(keyword):
    assert parse_statements(line("ID", keywords=keyword), file_type="logical")[0]["barrier"]


@pytest.mark.parametrize(
    "keyword",
    [
        "REFFLD(BAD/REC/ID *SRC)",
        "REFFLD('ID' *SRC)",
        "REFFLD(ID 'LIB/PF')",
        "REF(BAD/LIB/PF)",
    ],
)
def test_malformed_reference_names_block(keyword):
    assert parse_statements(line("COPY", ref="R", keywords=keyword), file_type="physical")[0][
        "barrier"
    ]
