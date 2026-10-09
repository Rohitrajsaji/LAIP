from dataclasses import replace

import pytest
from test_cl_analysis import imported as cl_imported
from test_dds_analysis import imported as dds_imported
from test_dds_analysis import line
from test_rpgle_analysis import imported

from laip.canonical import digest, record_id
from laip.cl_analysis import analyze_cl
from laip.dds_analysis import analyze_dds
from laip.persistence import validate
from laip.rpgle_analysis import analyze_rpgle
from laip.rules import RuleLimits, extract_rules

PROGRAM = "ent_" + "1" * 64


def test_actual_rpgle_candidates_and_provenance(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    result = extract_rules(
        analysis,
        run_id=analysis.run_id,
        system_namespace=analysis.system_namespace,
        created_at="2026-10-08T00:00:00Z",
        program_entity_ids=(PROGRAM,),
    )
    assert result["rule_revisions"]
    for revision in result["rule_revisions"]:
        assert revision["classification"] == "inferred"
        assert revision["interpretation_method"] == "deterministic"
        assert revision["evidence_ids"]
        assert all(
            expression["source_locations"]
            for expression in revision["conditions"] + revision["actions"]
        )
    assert all(entity["attributes"]["review_status"] == "pending" for entity in result["entities"])


def extract(analysis, **kw):
    return extract_rules(
        analysis,
        run_id=analysis.run_id,
        system_namespace=analysis.system_namespace,
        created_at="2026-10-08T00:00:00Z",
        program_entity_ids=(PROGRAM,),
        **kw,
    )


def test_cl_else_polarity_and_join(tmp_path):
    source = (
        "PGM\nDCL VAR(&N) TYPE(*DEC) LEN(5 0)\n"
        "IF COND(&N *GT 0) THEN(CHGVAR VAR(&N) VALUE(2))\n"
        "ELSE CMD(CHGVAR VAR(&N) VALUE(3))\nCHGVAR VAR(&N) VALUE(4)\nENDPGM"
    )
    prepared, store, root = cl_imported(tmp_path, source=source)
    analysis = analyze_cl(prepared, root, store, run_id="cl_rules")
    revisions = extract(analysis)["rule_revisions"]
    assert [
        r["conditions"][0]["value"]["polarity"] if r["conditions"] else None for r in revisions
    ] == ["true", "false", None]
    for r in revisions:
        validate("RuleRevision", r)


def test_replay_revision_chain_identity_and_support(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    first = extract(analysis)
    assert first == extract(analysis)
    assert extract(analysis, previous_revisions=first["rule_revisions"]) == first
    next_analysis = analyze_rpgle(prepared, root, store, run_id="next_rules_run")
    second = extract(next_analysis, previous_revisions=first["rule_revisions"])
    assert [e["entity_id"] for e in first["entities"]] == [
        e["entity_id"] for e in second["entities"]
    ]
    assert [r["previous_revision_id"] for r in second["rule_revisions"]] == [
        r["revision_id"] for r in first["rule_revisions"]
    ]
    evs = {e["evidence_id"]: e for e in analysis.evidence}
    for r in first["rule_revisions"]:
        assert r["revision_id"] == record_id("rev_", r, "revision_id")
        selected = [evs[eid] for eid in r["evidence_ids"]]
        assert r["support_fingerprint"] == digest(
            {
                "evidence": sorted(
                    [
                        {"evidence_id": e["evidence_id"], "content_sha256": e["content_sha256"]}
                        for e in selected
                    ],
                    key=lambda e: e["evidence_id"],
                ),
                "providers": [analysis.evidence[0]["provider"]],
                "resolution_decisions": [],
                "configuration_sha256": analysis.configuration_sha256,
            }
        )


def test_dds_validation_candidate(tmp_path):
    source = line(level="R", name="REC") + line(
        name="AMOUNT", length="5", dtype="P", decimals="0", keywords="RANGE(1 99)"
    )
    prepared, store, root = dds_imported(tmp_path, source=source)
    analysis = analyze_dds(prepared, root, store, run_id="dds_rules", file_type="physical")
    result = extract(analysis)
    assert len(result["entities"]) == 1
    assert result["entities"][0]["attributes"]["candidate_kind"] == "validation"
    assert result["rule_revisions"][0]["actions"][0]["value"]["validations"][0]["arguments"] == [
        "1",
        "99",
    ]


def test_missing_program_does_not_invent_identity(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    result = extract_rules(
        analysis,
        run_id=analysis.run_id,
        system_namespace=analysis.system_namespace,
        created_at="2026-10-08T00:00:00Z",
    )
    assert result["entities"] and not result["rule_revisions"]
    assert {d["code"] for d in result["diagnostics"]} == {"VERIFIED_PROGRAM_ASSOCIATION_REQUIRED"}


@pytest.mark.parametrize(
    "limits",
    [
        RuleLimits(max_nodes=1),
        RuleLimits(max_candidates=1),
        RuleLimits(max_evidence=1),
        RuleLimits(max_work=1),
    ],
)
def test_resource_budget(tmp_path, limits):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    with pytest.raises(ValueError):
        extract(analysis, limits=limits)


def test_cancellation_basis_and_association_validation(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    with pytest.raises(ValueError):
        extract(analysis, cancelled=lambda: True)
    with pytest.raises(ValueError):
        extract_rules(
            analysis,
            run_id=analysis.run_id,
            system_namespace="other",
            created_at="2026-10-08T00:00:00Z",
        )
    with pytest.raises(ValueError):
        extract_rules(
            analysis,
            run_id="wrong",
            system_namespace=analysis.system_namespace,
            created_at="2026-10-08T00:00:00Z",
        )
    with pytest.raises(ValueError):
        extract_rules(
            analysis,
            run_id=analysis.run_id,
            system_namespace=analysis.system_namespace,
            created_at="2026-10-08T00:00:00Z",
            program_entity_ids=("wrong",),
        )
    with pytest.raises(ValueError):
        RuleLimits(max_work=0)


def test_missing_source_and_barriers(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    ir = {**analysis.ir, "nodes": [{**n, "source_locations": []} for n in analysis.ir["nodes"]]}
    result = extract(replace(analysis, ir=ir))
    assert not result["entities"]
    assert {d["code"] for d in result["diagnostics"]} == {"SOURCE_EVIDENCE_REQUIRED"}
    opaque_nodes = [
        {**n, "statement": {**n["statement"], "barrier": True}} for n in analysis.ir["nodes"]
    ]
    result = extract(
        replace(analysis, ir={**analysis.ir, "nodes": opaque_nodes, "conclusions_allowed": False})
    )
    assert not result["entities"]
    assert {d["code"] for d in result["diagnostics"]} == {"OPAQUE_ACTION_NOT_INTERPRETED"}


def test_history_rejects_conflicting_heads_and_malformed_id(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    first = extract(analysis)["rule_revisions"][0]
    conflicting = {**first, "name": "Different candidate"}
    conflicting["revision_id"] = record_id("rev_", conflicting, "revision_id")
    with pytest.raises(ValueError, match="AMBIGUOUS_PREVIOUS_RULE_HEAD"):
        extract(analysis, previous_revisions=(first, conflicting))
    with pytest.raises(ValueError, match="INVALID_PREVIOUS_RULE_REVISION"):
        extract(analysis, previous_revisions=({**first, "revision_id": "rev_" + "0" * 64},))


def test_graph_input_validation(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    with pytest.raises(ValueError, match="DUPLICATE_IR_NODE"):
        extract(
            replace(
                analysis,
                ir={**analysis.ir, "nodes": analysis.ir["nodes"] + [analysis.ir["nodes"][0]]},
            )
        )
    with pytest.raises(ValueError, match="INVALID_IR_EDGE"):
        extract(
            replace(analysis, ir={**analysis.ir, "edges": [{"from": -1, "to": 0, "kind": "next"}]})
        )
    evidence = tuple({**e, "run_id": "wrong"} for e in analysis.evidence)
    with pytest.raises(ValueError, match="RULE_EVIDENCE_RUN_MISMATCH"):
        extract(replace(analysis, evidence=evidence))


def test_partial_conclusions_remain_explicit_and_source_identity_required(tmp_path):
    prepared, store, root = imported(tmp_path)
    analysis = analyze_rpgle(prepared, root, store, run_id="rules_run")
    partial = replace(analysis, ir={**analysis.ir, "conclusions_allowed": False})
    result = extract(partial)
    assert all(
        any("prevent complete" in message for message in r["limitations"])
        for r in result["rule_revisions"]
    )
    evs = tuple({**e, "subject_entity_ids": []} for e in analysis.evidence)
    result = extract_rules(
        replace(analysis, evidence=evs),
        run_id=analysis.run_id,
        system_namespace=analysis.system_namespace,
        created_at="2026-10-08T00:00:00Z",
    )
    assert not result["entities"]
    assert {d["code"] for d in result["diagnostics"]} == {"STABLE_SOURCE_IDENTITY_REQUIRED"}


def test_loop_body_condition_does_not_leak_to_following_action(tmp_path):
    source = (
        "PGM\nDCL VAR(&N) TYPE(*DEC) LEN(5 0)\n"
        "DOWHILE COND(&N *LT 3)\nCHGVAR VAR(&N) VALUE(&N + 1)\n"
        "ENDDO\nCHGVAR VAR(&N) VALUE(99)\nENDPGM"
    )
    prepared, store, root = cl_imported(tmp_path, source=source)
    analysis = analyze_cl(prepared, root, store, run_id="loop_rules")
    rules = extract(analysis)["rule_revisions"]
    assert rules[0]["conditions"][0]["value"]["polarity"] == "true"
    assert not rules[1]["conditions"]


@pytest.mark.parametrize("separate", [False, True])
def test_conditioned_dds_validation_is_not_unconditional(tmp_path, separate):
    record = line(level="R", name="SCREEN")
    field = line(
        name="VALUE",
        length="10",
        dtype="A",
        usage="B",
        row="2",
        column="3",
        keywords="" if separate else "CHECK(ME)",
    )
    conditional = line(keywords="CHECK(ME)") if separate else field
    conditional = conditional[:7] + "N01" + conditional[10:]
    source = record + field + conditional if separate else record + conditional
    prepared, store, root = dds_imported(tmp_path, source=source)
    analysis = analyze_dds(prepared, root, store, run_id="conditional_dds", file_type="display")
    result = extract(analysis)
    assert not result["rule_revisions"] and not result["entities"]
    assert any(
        d["code"] == "CONDITIONAL_DDS_VALIDATION_REQUIRES_REVIEW" for d in result["diagnostics"]
    )
