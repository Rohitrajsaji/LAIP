import copy

import pytest

from laip.canonical import identity_id, record_id
from laip.persistence import validate
from laip.workflows import compose_workflows

RUN = "workflow_run"
NAMESPACE = "synthetic.local"
TIME = "2026-10-08T00:00:00Z"


def program(name, library="APP", kind="Program", namespace=NAMESPACE):
    identity = {
        "system_namespace": namespace,
        "kind": kind,
        "qualified_identity": [library, "*PGM", name],
    }
    return {
        "schema_version": "0.1.0",
        "entity_id": identity_id(identity),
        "identity": identity,
        "display_name": name,
        "original_names": [name],
        "aliases": [],
        "parent_id": None,
        "source_availability": "available",
        "analysis_status": "analyzed",
        "attributes": {},
        "evidence_ids": [],
    }


def observation(label="call", **changes):
    value = {
        "schema_version": "0.1.0",
        "run_id": RUN,
        "artifact_id": "art_" + "a" * 64,
        "content_sha256": "a" * 64,
        "subject_entity_ids": [],
        "source_locations": [],
        "collection_method": "offline_inventory",
        "collected_at": TIME,
        "provider": {"id": "fixture", "version": "0.1.0", "source_revision": None},
        "authority": "imported_metadata",
        "classification": "observed",
        "excerpt": None,
        "metadata": {"label": label},
        "limitations": ["Synthetic static metadata."],
        "supporting_evidence_ids": [],
        "contradiction_evidence_ids": [],
        "upstream_record": None,
    }
    value.update(changes)
    value["evidence_id"] = record_id("ev_", value, "evidence_id")
    return value


def call(source, target, support, **changes):
    value = {
        "schema_version": "0.1.0",
        "run_id": RUN,
        "from_entity_id": source["entity_id"],
        "relationship": "CALLS",
        "to_entity_id": target["entity_id"] if target else None,
        "target_expression": target["display_name"] if target else "&TARGET",
        "resolution": "resolved" if target else "dynamic",
        "candidate_entity_ids": [],
        "resolution_context": {
            "library_list": ["APP"],
            "namespace": NAMESPACE,
            "method": "qualified_offline_scope",
        },
        "classification": "observed" if target else "unresolved",
        "evidence_ids": [support["evidence_id"]],
        "limitations": ["Static call reference only."],
    }
    value.update(changes)
    value["dependency_id"] = record_id("dep_", value, "dependency_id")
    return value


def compose(edges, programs, evidence, **kwargs):
    return compose_workflows(
        edges,
        run_id=RUN,
        system_namespace=NAMESPACE,
        created_at=TIME,
        program_entities=programs,
        evidence=evidence,
        **kwargs,
    )


def test_maximal_possible_paths_with_closed_evidence_and_stable_entity():
    a, b, c = [program(name) for name in ("ENTRY", "CHECK", "SAVE")]
    observed = observation()
    inferred = observation(
        "resolution", classification="inferred", supporting_evidence_ids=[observed["evidence_id"]]
    )
    ab, bc = call(a, b, observed), call(b, c, inferred)
    inputs = copy.deepcopy(([ab, bc], [a, b, c], [observed, inferred]))
    result = compose(*inputs)
    assert inputs == ([ab, bc], [a, b, c], [observed, inferred])
    assert len(result["workflows"]) == 1
    workflow = result["workflows"][0]
    assert workflow["program_entity_ids"] == [a["entity_id"], b["entity_id"], c["entity_id"]]
    assert workflow["dependency_ids"] == [ab["dependency_id"], bc["dependency_id"]]
    assert workflow["evidence_ids"] == sorted([observed["evidence_id"], inferred["evidence_id"]])
    assert workflow["classification"] == "inferred"
    assert workflow["review_status"] == "pending_review"
    assert workflow["workflow_id"] == record_id("wf_", workflow, "workflow_id")
    validate("Entity", result["entities"][0])
    assert result["entities"][0]["entity_id"] == workflow["workflow_entity_id"]
    assert not result["diagnostics"]
    assert compose([bc, ab], [c, b, a], [inferred, observed]) == result
    later = compose_workflows(
        [ab, bc],
        run_id=RUN,
        system_namespace=NAMESPACE,
        created_at="2026-10-09T00:00:00Z",
        program_entities=[a, b, c],
        evidence=[observed, inferred],
    )
    assert later["entities"][0]["entity_id"] == workflow["workflow_entity_id"]
    assert later["workflows"][0]["workflow_id"] != workflow["workflow_id"]


def test_branching_does_not_invent_execution_order_or_merge_namespaces():
    a, b, other_b = program("ENTRY"), program("CHECK"), program("CHECK", library="OTHER")
    support = observation()
    result = compose([call(a, b, support), call(a, other_b, support)], [a, b, other_b], [support])
    assert {tuple(w["program_entity_ids"]) for w in result["workflows"]} == {
        (a["entity_id"], b["entity_id"]),
        (a["entity_id"], other_b["entity_id"]),
    }
    assert len({e["entity_id"] for e in result["entities"]}) == 2


@pytest.mark.parametrize("resolution", ["dynamic", "missing", "ambiguous"])
def test_unresolved_calls_are_diagnostics_without_supported_path(resolution):
    a, b, c = program("A"), program("B"), program("C")
    support = observation()
    edge = call(
        a,
        None,
        support,
        resolution=resolution,
        candidate_entity_ids=[b["entity_id"], c["entity_id"]] if resolution == "ambiguous" else [],
    )
    result = compose([edge], [a, b, c], [support])
    assert not result["workflows"]
    assert result["diagnostics"][0]["code"] == (
        "ambiguous_reference" if resolution == "ambiguous" else "unresolved_reference"
    )


@pytest.mark.parametrize("target", [None, "SourceMember", "namespace"])
def test_only_explicit_in_scope_program_entities_can_form_paths(target):
    a = program("A")
    b = program(
        "B",
        kind="SourceMember" if target == "SourceMember" else "Program",
        namespace="elsewhere" if target == "namespace" else NAMESPACE,
    )
    support = observation()
    result = compose([call(a, b, support)], [a] if target is None else [a, b], [support])
    assert not result["workflows"]
    assert result["diagnostics"]


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "other_run"},
        {"classification": "unresolved"},
        {"metadata": {"barrier": True}},
        {"metadata": {"conclusions_allowed": False}},
        {"metadata": {"references": [{"dynamic": True}]}},
        {"contradiction_evidence_ids": ["ev_" + "f" * 64]},
        {"supporting_evidence_ids": ["ev_" + "f" * 64]},
        {"classification": "inferred"},
    ],
)
def test_unresolved_or_incomplete_evidence_closure_blocks_composition(change):
    a, b = program("A"), program("B")
    support = observation(**change)
    result = compose([call(a, b, support)], [a, b], [support])
    assert not result["workflows"]
    assert result["diagnostics"]


def test_missing_and_forged_evidence_or_dependency_digest_are_rejected():
    a, b = program("A"), program("B")
    support = observation()
    edge = call(a, b, support)
    missing = compose([edge], [a, b], [])
    assert missing["diagnostics"][0]["code"] == "missing_evidence"
    forged = dict(support, metadata={"label": "changed without rehash"})
    assert not compose([edge], [a, b], [forged])["workflows"]
    forged_edge = dict(edge, target_expression="forged")
    assert not compose([forged_edge], [a, b], [support])["workflows"]


def test_same_run_support_closure_cannot_hide_cross_run_leaf():
    a, b = program("A"), program("B")
    leaf = observation(run_id="other_run")
    support = observation(classification="inferred", supporting_evidence_ids=[leaf["evidence_id"]])
    result = compose([call(a, b, support)], [a, b], [support, leaf])
    assert not result["workflows"]
    assert result["diagnostics"]


def test_cycles_are_cut_with_diagnostics_without_repeated_programs():
    a, b = program("A"), program("B")
    support = observation()
    result = compose([call(a, b, support), call(b, a, support)], [a, b], [support])
    assert result["workflows"]
    assert all(
        len(set(w["program_entity_ids"])) == len(w["program_entity_ids"])
        for w in result["workflows"]
    )
    assert any(d["code"] == "analysis_barrier" for d in result["diagnostics"])
    assert all(
        any("cycle" in text.lower() for text in w["limitations"]) for w in result["workflows"]
    )


def test_path_depth_and_count_bounds_are_visible_with_valid_partial_paths():
    a, b, c, d = [program(name) for name in "ABCD"]
    support = observation()
    edges = [call(a, b, support), call(b, c, support), call(a, d, support)]
    depth = compose(edges, [a, b, c, d], [support], max_depth=2)
    assert any(d["code"] == "path_limit_reached" for d in depth["diagnostics"])
    assert all(len(w["program_entity_ids"]) <= 2 for w in depth["workflows"])
    bounded = compose(edges, [a, b, c, d], [support], max_paths=1)
    assert len(bounded["workflows"]) == 1
    assert any(d["code"] == "limit_exceeded" for d in bounded["diagnostics"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_depth": 0},
        {"max_depth": 129},
        {"max_paths": True},
        {"max_records": -1},
        {"max_support": 0},
        {"max_steps": 0},
    ],
)
def test_limits_are_positive_integers(kwargs):
    with pytest.raises(ValueError, match="INVALID_WORKFLOW_LIMIT"):
        compose([], [], [], **kwargs)


def test_input_work_and_cancellation_are_bounded():
    a, b = program("A"), program("B")
    support = observation()
    with pytest.raises(ValueError, match="WORKFLOW_INPUT_LIMIT"):
        compose([], [a, b], [], max_records=1)
    with pytest.raises(ValueError, match="CANCELLED"):
        compose([call(a, b, support)], [a, b], [support], cancelled=lambda: True)
    result = compose([call(a, b, support)], [a, b], [support], max_steps=1)
    assert not result["workflows"]
    assert result["diagnostics"][0]["code"] == "limit_exceeded"


def test_empty_graph_and_non_call_edges_produce_no_workflows():
    assert compose([], [], []) == {"entities": [], "workflows": [], "diagnostics": []}
    a, b = program("A"), program("B")
    support = observation()
    edge = call(a, b, support, relationship="DEPENDS_ON")
    assert compose([edge], [a, b], [support]) == {
        "entities": [],
        "workflows": [],
        "diagnostics": [],
    }


def test_support_limit_and_call_run_or_namespace_mismatch_are_reported():
    a, b = program("A"), program("B")
    leaf = observation()
    support = observation("derived", supporting_evidence_ids=[leaf["evidence_id"]])
    result = compose([call(a, b, support)], [a, b], [support, leaf], max_support=1)
    assert not result["workflows"]
    assert result["diagnostics"][0]["code"] == "limit_exceeded"
    for changes in (
        {"run_id": "other_run"},
        {
            "resolution_context": {
                "library_list": [],
                "namespace": "other",
                "method": "qualified_offline_scope",
            }
        },
    ):
        result = compose([call(a, b, leaf, **changes)], [a, b], [leaf])
        assert not result["workflows"]
        assert result["diagnostics"][0]["code"] == "invalid_scope"


def test_multiple_static_edges_have_distinct_revisions_but_one_logical_entity():
    a, b = program("A"), program("B")
    first, second = observation("first"), observation("second")
    result = compose([call(a, b, first), call(a, b, second)], [a, b], [first, second])
    assert len(result["workflows"]) == 2
    assert len(result["entities"]) == 1
    assert result["entities"][0]["evidence_ids"] == sorted(
        [
            first["evidence_id"],
            second["evidence_id"],
        ]
    )


def test_invalid_scope_timestamp_input_and_duplicate_collisions_fail_closed():
    with pytest.raises(ValueError, match="INVALID_WORKFLOW_SCOPE"):
        compose_workflows(
            [],
            run_id="",
            system_namespace=NAMESPACE,
            created_at=TIME,
            program_entities=[],
            evidence=[],
        )
    with pytest.raises(ValueError, match="INVALID_WORKFLOW_TIMESTAMP"):
        compose_workflows(
            [],
            run_id=RUN,
            system_namespace=NAMESPACE,
            created_at="2026-10-08",
            program_entities=[],
            evidence=[],
        )
    with pytest.raises(ValueError, match="INVALID_WORKFLOW_INPUT"):
        compose([], [{"identity": {}}], [])
    a = program("A")
    with pytest.raises(ValueError, match="WORKFLOW_INPUT_COLLISION"):
        compose([], [a, dict(a, display_name="FORGED")], [])


def test_self_call_is_a_cycle_diagnostic_without_single_program_workflow():
    a = program("A")
    support = observation()
    result = compose([call(a, a, support)], [a], [support])
    assert not result["workflows"]
    assert result["diagnostics"][0]["code"] == "analysis_barrier"


@pytest.mark.parametrize("parts", [["APP", "B"], ["APP", "*MODULE", "B"], ["B"]])
def test_programs_require_canonical_library_program_name_profile(parts):
    a, b = program("A"), program("B")
    b["identity"]["qualified_identity"] = parts
    b["entity_id"] = identity_id(b["identity"])
    support = observation()
    result = compose([call(a, b, support)], [a, b], [support])
    assert not result["workflows"]
    assert result["diagnostics"][0]["code"] == "invalid_scope"
