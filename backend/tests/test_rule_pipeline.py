import copy

import pytest
from test_rpgle_analysis import db as db_fixture
from test_rpgle_analysis import imported

from laip.canonical import identity_id
from laip.persistence import Repository
from laip.rpgle_analysis import analyze_rpgle, persist_analysis, register_analysis
from laip.rule_store import (
    approve_rule,
    current_rule,
    persist_bundle,
    register_bundle,
    review_history,
)
from laip.rules import extract_rules
from laip.source_import import persist_import, register_import

db = db_fixture


def test_actual_source_rules_publish_with_original_evidence_basis(db, tmp_path):
    prepared, store, root = imported(tmp_path)
    repo = Repository(db, "synthetic:rpg")
    register_import(prepared, repo)
    persist_import(prepared, repo)
    db.execute("UPDATE imports SET state='sealed'")
    analysis = analyze_rpgle(prepared, root, store, run_id="rule_pipeline")
    register_analysis(analysis, prepared, repo)
    persist_analysis(analysis, prepared, repo)
    source = db.execute(
        "SELECT payload FROM entity_observations "
        "WHERE payload->'identity'->>'kind'='SourceMember' LIMIT 1"
    ).fetchone()[0]
    program = copy.deepcopy(source)
    program["identity"] = {
        "system_namespace": repo.namespace,
        "kind": "Program",
        "qualified_identity": ["LIB1", "*PGM", "MAIN"],
    }
    program["entity_id"] = identity_id(program["identity"])
    program["evidence_ids"] = []
    repo.put_entity(program, analysis.run_id)
    bundle = extract_rules(
        analysis,
        run_id=analysis.run_id,
        system_namespace=repo.namespace,
        created_at="2026-10-08T00:00:00Z",
        program_entity_ids=(program["entity_id"],),
    )
    assert bundle["rule_revisions"]
    providers = db.execute(
        "SELECT payload FROM runs WHERE run_id=%s", (analysis.run_id,)
    ).fetchone()[0]["providers"]
    register_bundle(
        analysis.run_id, analysis.import_id, analysis.configuration_sha256, providers, repo
    )
    result = persist_bundle(
        analysis.run_id, bundle, {"entities": [], "workflows": [], "diagnostics": []}, repo
    )
    assert result["rule_count"] == len(bundle["rule_revisions"])
    for rule in bundle["rule_revisions"]:
        assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "pending_review"
        if rule["actions"][0]["value"]["operation"] == "callp":
            # A source call alone does not establish a resolved cross-program target.
            with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
                approve_rule(
                    rule["rule_entity_id"], rule["revision_id"], None, "analyst", "check", repo
                )
    reviewed = next(
        rule
        for rule in bundle["rule_revisions"]
        if rule["actions"][0]["value"]["operation"] == "assignment"
    )
    approve_rule(
        reviewed["rule_entity_id"], reviewed["revision_id"], None, "analyst", "source checked", repo
    )
    renewed = analyze_rpgle(prepared, root, store, run_id="rule_pipeline_reanalysis")
    register_analysis(renewed, prepared, repo)
    persist_analysis(renewed, prepared, repo)
    next_bundle = extract_rules(
        renewed,
        run_id=renewed.run_id,
        system_namespace=repo.namespace,
        created_at="2026-10-08T01:00:00Z",
        program_entity_ids=(program["entity_id"],),
        previous_revisions=bundle["rule_revisions"],
    )
    register_bundle(
        renewed.run_id, renewed.import_id, renewed.configuration_sha256, providers, repo
    )
    persist_bundle(
        renewed.run_id, next_bundle, {"entities": [], "workflows": [], "diagnostics": []}, repo
    )
    head = current_rule(reviewed["rule_entity_id"], repo)
    assert head["review_status"] == "pending_review"
    assert head["revision"]["previous_revision_id"] == reviewed["revision_id"]
    assert head["revision"]["support_fingerprint"] != reviewed["support_fingerprint"]
    assert review_history(reviewed["rule_entity_id"], repo)[0]["status"] == "verified"
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 2 * len(
        bundle["rule_revisions"]
    )
