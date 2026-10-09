import json

import pytest

from laip.import_manifest import normalized_relative_path, parse_manifest, parse_metadata


def manifest():
    return {
        "schema_version": "0.1.0",
        "system_namespace": "sys",
        "system_display_name": "System",
        "library_list": ["LIB"],
        "default_encoding": "CCSID:37",
        "artifacts": [
            {
                "relative_path": "src/main.rpgle",
                "language": "RPG",
                "dialect": None,
                "source_member_identity": {
                    "system_namespace": "sys",
                    "kind": "SourceMember",
                    "qualified_identity": ["LIB", "QRPGLESRC", "MAIN"],
                },
                "object_identity": None,
                "encoding": None,
                "kind": "source",
                "collection_method": "operator_export",
                "collected_at": "2026-10-07T00:00:00Z",
            }
        ],
        "application_memberships": [],
        "exclusions": [],
        "limits": {"max_files": 99, "max_bytes": 999},
    }


def test_manifest_limits_and_namespace():
    value = parse_manifest(
        json.dumps(manifest()).encode(), system_namespace="sys", max_files=5, max_bytes=50
    )
    assert (value.max_files, value.max_bytes) == (5, 50)
    assert value.payload["default_encoding"] == "CCSID:37"
    with pytest.raises(ValueError, match="NAMESPACE"):
        parse_manifest(json.dumps(manifest()).encode(), system_namespace="other")


@pytest.mark.parametrize(
    "path", ["../x", "/x", "C:/x", "x\\y", "x//y", "./x", "x/..", "cafe\u0301", "x\x00y"]
)
def test_paths_reject_aliases_and_traversal(path):
    with pytest.raises(ValueError):
        normalized_relative_path(path)


def test_manifest_duplicate_and_invalid_identity():
    data = manifest()
    data["artifacts"].append(dict(data["artifacts"][0], relative_path="SRC/MAIN.RPGLE"))
    with pytest.raises(ValueError, match="DUPLICATE"):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")
    data = manifest()
    data["artifacts"][0]["source_member_identity"]["qualified_identity"] = ["MAIN"]
    with pytest.raises(ValueError, match="IDENTITY"):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        parse_manifest(b'{"schema_version":0,"schema_version":1}', system_namespace="sys")
    with pytest.raises(ValueError, match="LIMIT"):
        parse_manifest(b" " * 10, system_namespace="sys", max_manifest_bytes=5)


def test_membership_must_reference_manifest_identity_and_evidence():
    data = manifest()
    data["application_memberships"] = [
        {
            "application_identity": {
                "system_namespace": "sys",
                "kind": "Application",
                "qualified_identity": ["APP"],
            },
            "member_identity": data["artifacts"][0]["source_member_identity"],
            "evidence_path": "missing.json",
        }
    ]
    with pytest.raises(ValueError, match="MEMBERSHIP"):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")


@pytest.mark.parametrize("kind", ["inventory", "schema", "binding", "job", "dsp_pgmref"])
def test_metadata_envelopes_preserve_records(kind):
    data = {"schema_version": "0.1.0", "kind": kind, "records": [{"name": "MAIN", "size": 2}]}
    result = parse_metadata(json.dumps(data).encode(), kind=kind)
    assert result.payload == data
    assert result.records == ({"name": "MAIN", "size": 2},)
    with pytest.raises(ValueError):
        parse_metadata(json.dumps(dict(data, unexpected=True)).encode(), kind=kind)
    with pytest.raises(ValueError):
        parse_metadata(json.dumps(dict(data, records=[1])).encode(), kind=kind)
    with pytest.raises(ValueError, match="LIMIT"):
        parse_metadata(json.dumps(data).encode(), kind=kind, max_records=0)


def test_membership_and_object_identity_are_explicit():
    data = manifest()
    item = data["artifacts"][0]
    item["object_identity"] = {
        "system_namespace": "sys",
        "kind": "Program",
        "qualified_identity": ["LIB", "MAIN"],
    }
    data["application_memberships"] = [
        {
            "application_identity": {
                "system_namespace": "sys",
                "kind": "Application",
                "qualified_identity": ["APP"],
            },
            "member_identity": item["source_member_identity"],
            "evidence_path": item["relative_path"],
        }
    ]
    assert parse_manifest(json.dumps(data).encode(), system_namespace="sys").payload == data
    item["object_identity"]["system_namespace"] = "foreign"
    with pytest.raises(ValueError, match="NAMESPACE"):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "count",
        "object_kind",
        "identity_unicode",
        "library_duplicate",
        "library_limit",
        "member_kind",
    ],
)
def test_manifest_semantic_invalid_cases(mutation):
    data = manifest()
    item = data["artifacts"][0]
    if mutation == "schema":
        data["schema_version"] = "1.0.0"
    elif mutation == "count":
        data["artifacts"].append(dict(item, relative_path="other", source_member_identity=None))
        data["limits"]["max_files"] = 1
    elif mutation == "object_kind":
        item["object_identity"] = item["source_member_identity"]
    elif mutation == "identity_unicode":
        item["source_member_identity"]["qualified_identity"][2] = "cafe\u0301"
    elif mutation == "library_duplicate":
        data["library_list"] = ["LIB", "lib"]
    elif mutation == "library_limit":
        data["library_list"] = ["x" * 257]
    else:
        item["source_member_identity"]["kind"] = "Program"
    with pytest.raises(ValueError):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")


@pytest.mark.parametrize(
    "raw", [b"[]", b"\xff", b'{"x":NaN}', b'{"x":9007199254740992}', b'{"x":0.5}']
)
def test_strict_json_invalid_values(raw):
    with pytest.raises(ValueError):
        parse_manifest(raw, system_namespace="sys")


def test_metadata_unknown_fields_and_kind():
    data = {"schema_version": "0.1.0", "kind": "inventory", "records": [{"x\n": "name"}]}
    with pytest.raises(ValueError, match="FIELD"):
        parse_metadata(json.dumps(data).encode(), kind="inventory")
    with pytest.raises(ValueError, match="ENVELOPE"):
        parse_metadata(json.dumps(data).encode(), kind="source")


def test_multiple_source_members_can_describe_same_compiled_object():
    data = manifest()
    first = data["artifacts"][0]
    program = {"system_namespace": "sys", "kind": "Program", "qualified_identity": ["LIB", "MAIN"]}
    first["object_identity"] = program
    second = dict(
        first,
        relative_path="src/helper.rpgle",
        source_member_identity={
            "system_namespace": "sys",
            "kind": "SourceMember",
            "qualified_identity": ["LIB", "QRPGLESRC", "HELPER"],
        },
    )
    data["artifacts"].append(second)
    assert parse_manifest(json.dumps(data).encode(), system_namespace="sys").payload == data
    second["source_member_identity"] = first["source_member_identity"]
    with pytest.raises(ValueError, match="DUPLICATE_ARTIFACT_IDENTITY"):
        parse_manifest(json.dumps(data).encode(), system_namespace="sys")
