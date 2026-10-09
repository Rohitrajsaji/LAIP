import json
from dataclasses import replace
from pathlib import Path

import pytest

from laip.artifacts import LocalArtifactStore
from laip.canonical import identity_id
from laip.inventory import InventoryLimits, build_inventory
from laip.persistence import validate
from laip.source_import import ImportContext, prepare_local

FIXTURE = Path(__file__).parent / "fixtures/import"


def identity(kind, *parts):
    if kind == "Program" and len(parts) == 2:
        parts = (parts[0], "*PGM", parts[1])
    return {"system_namespace": "synthetic:import", "kind": kind, "qualified_identity": list(parts)}


def prepared(tmp_path):
    store_root = tmp_path / "private"
    store_root.mkdir(mode=0o700)
    return prepare_local(
        "fixture",
        {"fixture": FIXTURE / "source"},
        (FIXTURE / "manifest.json").read_bytes(),
        system_namespace="synthetic:import",
        store=LocalArtifactStore(store_root),
        context=ImportContext(
            "import", "import_run", "2026-10-07T12:00:00Z", "synthetic-v1", "job", 1
        ),
    )


def with_records(value, records):
    items = list(value.items)
    index = next(i for i, item in enumerate(items) if item.path == "metadata/inventory.json")
    items[index] = replace(
        items[index], metadata={"schema_version": "0.1.0", "kind": "inventory", "records": records}
    )
    return replace(value, items=tuple(items))


def entity(kind, *parts):
    return {"record_type": "entity", "identity": identity(kind, *parts)}


def reference(name="TARGET", library=None, dynamic=False, **options):
    return {
        "record_type": "reference",
        "from_identity": identity("Program", "LIB1", "ENTRY"),
        "relationship": "CALLS",
        "target": {"kind": "Program", "name": name, "library": library, "dynamic": dynamic},
        **options,
    }


def test_fixture_inventory_is_canonical_and_accounts_every_input(tmp_path):
    value = prepared(tmp_path)
    result = build_inventory(value, run_id="analysis")
    assert len(result.accounting) == len(value.items)
    assert result.system_namespace == "synthetic:import"
    assert result.import_id == "import" and result.run_id == "analysis"
    assert sum(e["identity"]["kind"] == "SourceMember" for e in result.entities) == 2
    assert all(e["identity"]["kind"] != "Program" for e in result.entities)
    for kind, records in [
        ("Entity", result.entities),
        ("Evidence", result.evidence),
        ("Dependency", result.dependencies),
    ]:
        for record in records:
            validate(kind, record)
    assert all(e["run_id"] == "analysis" for e in result.evidence)
    assert build_inventory(value, run_id="analysis") == result


@pytest.mark.parametrize(
    "target_library,resolution", [(None, "ambiguous"), ("LIB1", "resolved"), ("ABSENT", "missing")]
)
def test_qualified_library_resolution_does_not_pick_first(tmp_path, target_library, resolution):
    value = with_records(
        prepared(tmp_path),
        [
            entity("Program", "LIB1", "TARGET"),
            entity("Program", "LIB2", "TARGET"),
            reference(library=target_library),
        ],
    )
    result = build_inventory(value, run_id="analysis")
    dep = next(d for d in result.dependencies if d["relationship"] == "CALLS")
    assert dep["resolution"] == resolution
    if resolution == "ambiguous":
        assert len(dep["candidate_entity_ids"]) == 2 and dep["to_entity_id"] is None
    elif resolution == "resolved":
        assert dep["to_entity_id"] == identity_id(identity("Program", "LIB1", "TARGET"))
    else:
        assert dep["to_entity_id"] is None


def test_dynamic_and_explicit_order(tmp_path):
    value = with_records(
        prepared(tmp_path),
        [
            entity("Program", "LIB1", "TARGET"),
            entity("Program", "LIB2", "TARGET"),
            reference(dynamic=True),
        ],
    )
    result = build_inventory(value, run_id="analysis")
    assert result.dependencies[0]["resolution"] == "dynamic"
    value = with_records(
        value,
        [
            entity("Program", "LIB1", "TARGET"),
            entity("Program", "LIB2", "TARGET"),
            reference(ordered_library_list=["LIB2", "LIB1"]),
        ],
    )
    dep = build_inventory(value, run_id="analysis").dependencies[0]
    assert dep["to_entity_id"] == identity_id(identity("Program", "LIB2", "TARGET"))


def test_confirmed_and_inferred_memberships_allow_shared_objects(tmp_path):
    value = prepared(tmp_path)
    manifest = json.loads(json.dumps(value.manifest))
    member = manifest["artifacts"][0]["source_member_identity"]
    program = identity("Program", "LIB1", "ENTRY")
    manifest["artifacts"][0]["object_identity"] = program
    manifest["application_memberships"] = [
        {
            "application_identity": identity("Application", app),
            "member_identity": member,
            "evidence_path": manifest["artifacts"][0]["relative_path"],
        }
        for app in ["APP1", "APP2"]
    ]
    value = with_records(
        replace(value, manifest=manifest),
        [entity("Program", "LIB1", "TARGET"), reference(library="LIB1")],
    )
    result = build_inventory(value, run_id="analysis")
    links = [d for d in result.dependencies if d["relationship"] == "MEMBER_OF"]
    assert len([d for d in links if d["classification"] == "observed"]) == 2
    target = identity_id(identity("Program", "LIB1", "TARGET"))
    assert (
        len(
            [
                d
                for d in links
                if d["from_entity_id"] == target and d["classification"] == "inferred"
            ]
        )
        == 2
    )
    assert len([d for d in result.dependencies if d["relationship"] == "SOURCE_OF"]) == 1


def test_missing_members_are_still_entities_and_accounted(tmp_path):
    value = prepared(tmp_path)
    index = next(i for i, item in enumerate(value.items) if item.entity is not None)
    items = list(value.items)
    items[index] = replace(
        items[index],
        status="failed",
        scope_state="missing",
        artifact=None,
        evidence=None,
        raw_blob=None,
        diagnostics=("MISSING_SOURCE",),
    )
    result = build_inventory(replace(value, items=tuple(items)), run_id="analysis")
    assert any(e["source_availability"] == "missing" for e in result.entities)
    assert any(a["processing_status"] == "failed" for a in result.accounting)


def test_bounds_cancel_and_cross_namespace_reject(tmp_path):
    value = prepared(tmp_path)
    with pytest.raises(ValueError, match="LIMIT"):
        build_inventory(value, run_id="analysis", limits=InventoryLimits(max_entities=1))
    with pytest.raises(ValueError, match="CANCELLED"):
        build_inventory(value, run_id="analysis", cancelled=lambda: True)
    foreign = entity("Program", "LIB1", "BAD")
    foreign["identity"]["system_namespace"] = "foreign"
    with pytest.raises(ValueError, match="NAMESPACE"):
        build_inventory(with_records(value, [foreign]), run_id="analysis")
    malformed = reference()
    malformed["target"]["dynamic"] = "yes"
    with pytest.raises(ValueError, match="METADATA"):
        build_inventory(with_records(value, [malformed]), run_id="analysis")


@pytest.mark.parametrize("field", ["max_records", "max_evidence", "max_dependencies", "max_work"])
def test_each_budget_is_enforced(tmp_path, field):
    value = with_records(
        prepared(tmp_path),
        [entity("Program", "LIB1", "TARGET"), reference(library="LIB1"), reference(name="MISSING")],
    )
    with pytest.raises(ValueError, match="LIMIT"):
        build_inventory(value, run_id="analysis", limits=InventoryLimits(**{field: 1}))


@pytest.mark.parametrize(
    "record",
    [
        {"record_type": "unknown"},
        {"record_type": "entity", "identity": identity("Program", "LIB1", "X"), "unknown": True},
        {
            "record_type": "entity",
            "identity": identity("Program", "LIB1", "X"),
            "source_availability": "verified",
        },
        {"record_type": "entity", "identity": identity("Program", "LIB1", "X"), "attributes": []},
        {
            "record_type": "reference",
            "from_identity": identity("Program", "LIB1", "ENTRY"),
            "relationship": "INVOKES",
            "target": {},
        },
        reference(library_list=["LIB1", "LIB1"]),
        reference(ordered_library_list="LIB1"),
        reference(name="cafe\u0301"),
        reference(library="LIB1\n"),
    ],
)
def test_invalid_normalized_records_rejected(tmp_path, record):
    with pytest.raises(ValueError, match="METADATA"):
        build_inventory(with_records(prepared(tmp_path), [record]), run_id="analysis")


def test_exact_qualified_identity_disambiguates_and_type_distinguishes(tmp_path):
    ref = reference()
    ref["target"]["kind"] = "SourceMember"
    ref["target"]["qualified_identity"] = ["LIB1", "SRC1", "TARGET"]
    value = with_records(
        prepared(tmp_path),
        [
            entity("SourceMember", "LIB1", "SRC1", "TARGET"),
            entity("SourceMember", "LIB1", "SRC2", "TARGET"),
            entity("Table", "LIB1", "*FILE", "TARGET"),
            ref,
        ],
    )
    dep = build_inventory(value, run_id="analysis").dependencies[0]
    assert dep["resolution"] == "resolved"
    assert dep["to_entity_id"] == identity_id(identity("SourceMember", "LIB1", "SRC1", "TARGET"))
    ref["target"]["qualified_identity"][-1] = "WRONG"
    with pytest.raises(ValueError, match="METADATA"):
        build_inventory(with_records(value, [ref]), run_id="analysis")


def test_resolution_scope_and_empty_order_are_not_defaults(tmp_path):
    value = with_records(prepared(tmp_path), [entity("Program", "OUTSIDE", "TARGET"), reference()])
    assert build_inventory(value, run_id="analysis").dependencies[0]["resolution"] == "missing"
    value = with_records(
        value, [entity("Program", "LIB1", "TARGET"), reference(ordered_library_list=[])]
    )
    assert build_inventory(value, run_id="analysis").dependencies[0]["resolution"] == "missing"


def test_mutually_conflicting_availability_stays_unknown_and_attributes_reject(tmp_path):
    one = dict(
        entity("Program", "LIB1", "X"),
        source_availability="available",
        attributes={"record_length": 12},
    )
    two = dict(
        entity("Program", "LIB1", "X"),
        source_availability="missing",
        attributes={"record_length": 12},
    )
    value = with_records(prepared(tmp_path), [one, two])
    result = build_inventory(value, run_id="analysis")
    assert (
        next(e for e in result.entities if e["identity"]["kind"] == "Program")[
            "source_availability"
        ]
        == "unknown"
    )
    two["attributes"] = {"record_length": 13}
    with pytest.raises(ValueError, match="CONFLICTING"):
        build_inventory(with_records(value, [one, two]), run_id="analysis")


def test_metadata_confirmed_membership_and_cycle_terminate(tmp_path):
    membership = reference()
    membership["relationship"] = "MEMBER_OF"
    membership["target"] = {"kind": "Application", "name": "APP", "library": None, "dynamic": False}
    target_to_entry = reference(name="ENTRY", library="LIB1")
    target_to_entry["from_identity"] = identity("Program", "LIB1", "TARGET")
    records = [
        entity("Application", "APP"),
        entity("Program", "LIB1", "TARGET"),
        membership,
        reference(library="LIB1"),
        target_to_entry,
    ]
    result = build_inventory(with_records(prepared(tmp_path), records), run_id="analysis")
    memberships = [d for d in result.dependencies if d["relationship"] == "MEMBER_OF"]
    assert len(memberships) == 2
    inferred = next(d for d in memberships if d["classification"] == "inferred")
    ev = next(e for e in result.evidence if e["evidence_id"] == inferred["evidence_ids"][0])
    assert len(ev["supporting_evidence_ids"]) == 2
    assert ev["metadata"]["membership_status"] == "inferred"


def test_run_namespace_stability_and_analysis_run_required(tmp_path):
    value = prepared(tmp_path)
    first = build_inventory(value, run_id="analysis_one")
    second = build_inventory(value, run_id="analysis_two")
    assert [e["entity_id"] for e in first.entities] == [e["entity_id"] for e in second.entities]
    assert first.configuration_sha256 == second.configuration_sha256
    assert {e["evidence_id"] for e in first.evidence}.isdisjoint(
        {e["evidence_id"] for e in second.evidence}
    )
    with pytest.raises(ValueError, match="ANALYSIS_RUN"):
        build_inventory(value, run_id=value.context.run_id)
    with pytest.raises(ValueError, match="LIMIT"):
        InventoryLimits(max_work=0)
