import pytest

from laip.dds_ir import build_ir
from laip.rpgle_ir import IrError, IrLimits

IDENTITY = {
    "system_namespace": "synthetic:dds",
    "kind": "SourceMember",
    "qualified_identity": ["LIB1", "QDDSSRC", "SRC"],
}


def statement(kind, name=None, keywords=(), **attrs):
    return {
        "kind": kind,
        "text": "",
        "start": 0,
        "end": 1,
        "barrier": False,
        "diagnostics": [],
        "uses": [],
        "defines": [],
        "attributes": {"name": name, "keywords": list(keywords), **attrs},
    }


def kw(name, *arguments):
    return {"name": name, "arguments": list(arguments)}


def build(*statements, file_type="physical", **args):
    return build_ir(
        statements,
        file_type=file_type,
        source_identity=args.pop("source_identity", IDENTITY),
        **args,
    )


def test_record_fields_keys_and_local_reference():
    result = build(
        statement("record", "REC"),
        statement("field", "ID", length=5, datatype="S", decimals=0),
        statement("field", "ALIAS", [kw("REFFLD", "ID", "*SRC")], reference="R"),
        statement("key", "ID"),
    )
    assert not result["barriers"]
    assert len(result["definitions"]) == 4
    reference = next(r for r in result["relationships"] if r["kind"] == "field_reference")
    assert reference["resolution"] == "resolved"
    assert (
        next(d for d in result["definitions"] if d["name"] == "ALIAS")["attributes"]["length"] == 5
    )
    assert any(
        r["kind"] == "key" and r["resolution"] == "resolved" for r in result["relationships"]
    )


def test_same_named_fields_in_formats_stay_distinct():
    result = build(
        statement("record", "A"),
        statement("field", "ID", length=1),
        statement("record", "B"),
        statement("field", "ID", length=2),
    )
    fields = [d for d in result["definitions"] if d["kind"] == "field"]
    assert len({d["id"] for d in fields}) == 2
    other = build(
        statement("record", "A"),
        statement("field", "ID", length=1),
        source_identity={**IDENTITY, "system_namespace": "other"},
    )
    assert fields[0]["id"] != other["definitions"][1]["id"]


def test_logical_missing_description_remains_unresolved():
    result = build(
        statement("record", "REC", [kw("PFILE", "LIB1/PF")]),
        statement("field", "ID"),
        file_type="logical",
    )
    assert not result["conclusions_allowed"]
    assert any(
        r["resolution"] == "unresolved" and r["target"]["file"] == "PF"
        for r in result["relationships"]
    )
    assert not next(d for d in result["definitions"] if d["kind"] == "field")["complete"]


def test_supplied_qualified_physical_description_resolves_without_name_guessing():
    physical = build(statement("record", "PFREC"), statement("field", "ID", length=5, datatype="S"))
    description = {
        "identity": {
            "system_namespace": "synthetic:dds",
            "kind": "Table",
            "qualified_identity": ["LIB1", "*FILE", "PF"],
        },
        "artifact_id": "art_fixture",
        "definitions": physical["definitions"],
    }
    result = build(
        statement("record", "REC", [kw("PFILE", "LIB1/PF")]),
        statement("field", "ID"),
        file_type="logical",
        available_descriptions=(description,),
    )
    field = next(d for d in result["definitions"] if d["kind"] == "field")
    assert field["complete"] and field["attributes"]["length"] == 5
    assert all(r["resolution"] == "resolved" for r in result["relationships"])
    assert any("art_fixture" in r["supporting_artifact_ids"] for r in result["relationships"])
    wrong = {**description, "identity": {**description["identity"], "system_namespace": "other"}}
    assert not build(
        statement("record", "REC", [kw("PFILE", "LIB1/PF")]),
        statement("field", "ID"),
        file_type="logical",
        available_descriptions=(wrong,),
    )["conclusions_allowed"]


@pytest.mark.parametrize(
    "source",
    [
        (statement("field", "ORPHAN", length=1),),
        (statement("record", "REC"), statement("key", "MISSING")),
        (statement("record", "REC"), statement("field", "X", reference="R")),
        (
            statement("record", "REC"),
            statement("field", "X", length=1),
            statement("field", "X", length=2),
        ),
    ],
)
def test_incomplete_or_ambiguous_definitions_block(source):
    assert not build(*source)["conclusions_allowed"]


def test_display_coordinates_indicators_validation_are_descriptive():
    result = build(
        statement("record", "SCREEN"),
        statement(
            "field",
            "AMOUNT",
            [kw("RANGE", "0", "100"), kw("CHECK", "RB")],
            length=5,
            datatype="S",
            decimals=0,
            usage="B",
            line=3,
            column=10,
            indicators=[{"number": "01", "negated": True}],
        ),
        file_type="display",
    )
    definition = result["definitions"][1]
    assert definition["attributes"]["line"] == 3
    assert definition["attributes"]["indicators"][0]["negated"]
    assert definition["attributes"]["keywords"][0]["name"] == "RANGE"
    assert not result["barriers"]


def test_limits_and_unknown_constructs():
    with pytest.raises(IrError):
        build(statement("record", "REC"), statement("field", "X"), limits=IrLimits(max_nodes=1))
    with pytest.raises(IrError):
        build(statement("record", "REC"), cancelled=lambda: True)
    unknown = {**statement("opaque"), "barrier": True, "diagnostics": ["unknown keyword"]}
    assert not build(unknown)["conclusions_allowed"]


def test_real_parser_boolean_reference_and_condition_scope():
    from test_dds_parser import line

    from laip.dds_parser import parse_statements

    source = line(spec="R", name="REC") + "\n" + line("ID", length="5", dtype="S") + "\n"
    source += line("ALIAS", ref="R", keywords="REFFLD(ID *SRC)")
    parsed = parse_statements(source, file_type="physical")
    result = build_ir(parsed, file_type="physical", source_identity=IDENTITY)
    assert not result["barriers"]
    assert result["relationships"][0]["resolution"] == "resolved"


def test_multiple_record_physical_file_is_one_based_on_target():
    physical = build(
        statement("record", "A"),
        statement("field", "ID", length=5),
        statement("record", "B"),
        statement("field", "ID", length=6),
    )
    description = {
        "identity": {
            "system_namespace": "synthetic:dds",
            "kind": "Table",
            "qualified_identity": ["LIB1", "*FILE", "PF"],
        },
        "artifact_id": "art_fixture",
        "definitions": physical["definitions"],
    }
    result = build(
        statement("record", "REC", [kw("PFILE", "LIB1/PF")]),
        file_type="logical",
        available_descriptions=(description,),
    )
    assert result["relationships"][0]["resolution"] == "resolved"


def test_mixed_logical_source_modes_block():
    assert not build(
        statement("record", "REC", [kw("PFILE", "LIB1/PF"), kw("JFILE", "LIB1/A", "LIB1/B")]),
        file_type="logical",
    )["conclusions_allowed"]


def test_multiple_unnamed_display_constants_are_distinct():
    result = build(
        statement("record", "SCREEN"),
        statement("constant", line=1, column=1, keywords=[kw("DFT", "First")]),
        statement("constant", line=2, column=1, keywords=[kw("DFT", "Second")]),
        file_type="display",
    )
    assert not result["barriers"]
    constants = [d for d in result["definitions"] if d["kind"] == "constant"]
    assert len({d["id"] for d in constants}) == 2


def test_opaque_record_boundary_does_not_export_fields_under_previous_record():
    from test_dds_parser import line

    from laip.dds_parser import parse_statements

    source = "\n".join(
        [
            line("GOOD", "R"),
            line("ID", length="5"),
            line("BAD", "R", keywords="UNKNOWN"),
            line("OTHER", length="3"),
        ]
    )
    physical = build_ir(
        parse_statements(source, file_type="physical"),
        file_type="physical",
        source_identity=IDENTITY,
    )
    other = next(d for d in physical["definitions"] if d["name"] == "OTHER")
    assert other["record"] != "GOOD"
    assert not other["complete"]
    description = {
        "identity": {
            "system_namespace": "synthetic:dds",
            "kind": "Table",
            "qualified_identity": ["LIB1", "*FILE", "PF"],
        },
        "artifact_id": "art_fixture",
        "definitions": physical["definitions"],
    }
    referencing = "\n".join(
        [
            line(keywords="REF(LIB1/PF GOOD)"),
            line("OUT", "R"),
            line("COPY", ref="R", keywords="REFFLD(OTHER)"),
        ]
    )
    result = build_ir(
        parse_statements(referencing, file_type="physical"),
        file_type="physical",
        source_identity=IDENTITY,
        available_descriptions=(description,),
    )
    assert not result["conclusions_allowed"]
    assert result["relationships"][0]["resolution"] == "unresolved"
