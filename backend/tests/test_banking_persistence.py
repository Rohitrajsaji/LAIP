"""Additive persistence acceptance, requiring an isolated PostgreSQL schema."""

import copy

import psycopg
import pytest
from jsonschema import ValidationError
from test_persistence import db as db_fixture
from test_persistence import seed

from laip.canonical import record_id
from laip.rule_store import approve_rule

db = db_fixture


def source_rule(repo, fixture, state="supported_within_profile"):
    rule = copy.deepcopy(fixture["rules"][0])
    subject = next(e for e in fixture["entities"] if e["identity"]["kind"] == "Field")
    rule.update(
        schema_version="0.2.0",
        program_entity_ids=[],
        procedure_entity_ids=[],
        subject_entity_ids=[subject["entity_id"]],
    )
    rule["claim_support"] = {
        "support_schema_version": "0.2.0",
        "state": state,
        "profile_id": "dds-bounded",
        "profile_version": "0.2.0",
        "subject_entity_ids": rule["subject_entity_ids"],
        "evidence_ids": rule["evidence_ids"],
        "controlling_predicates": rule["conditions"],
        "symbol_dependencies": [],
        "effect_dependencies": [],
        "resolution_decisions": [],
        "barriers": [],
        "limitations": [],
    }
    rule["support_fingerprint"] = repo.rule_support_fingerprint(rule)
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    return rule


def test_source_only_rule_subject_replay_and_approval(db):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    repo.put_rule(rule, None)
    repo.put_rule(rule, None)
    subjects = db.execute(
        "SELECT entity_id FROM rule_subjects WHERE revision_id=%s", (rule["revision_id"],)
    ).fetchall()
    assert subjects == [(rule["subject_entity_ids"][0],)]
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 1
    assert approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "source-only validation", repo
    )


@pytest.mark.parametrize("state", ["conditional", "blocked", "unresolved"])
def test_direct_repository_cannot_approve_unsupported_claim(db, state):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture, state)
    repo.put_rule(rule, None)
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        repo.review("rule_revision", rule["revision_id"], None, "verified", "analyst", "bypass")


def test_subjects_and_fingerprint_cannot_be_forged(db):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    rule["support_fingerprint"] = "f" * 64
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    with pytest.raises(ValueError, match="INVALID_SUPPORT_FINGERPRINT"):
        repo.put_rule(rule, None)
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 0


def fact_bundle(fixture):
    from laip.canonical import record_id

    evidence = fixture["evidence"][0]
    fact = {
        "kind": "declaration",
        "scope_id": "synthetic:declaration",
        "evidence_ids": [evidence["evidence_id"]],
        "source_locations": evidence["source_locations"],
        "data": {"name": "SFLRCD"},
    }
    fact["fact_id"] = record_id("fact_", fact, "fact_id")
    return {
        "fact_schema_version": "0.2.0",
        "system_namespace": fixture["runs"][0]["system_namespace"],
        "run_id": fixture["runs"][0]["run_id"],
        "artifact_id": evidence["artifact_id"],
        "provider": fixture["runs"][0]["providers"][0],
        "profile_id": "dds-bounded",
        "profile_version": "0.2.0",
        "facts": [fact],
        "accounting": {
            "supplied": 1,
            "analyzed": 1,
            "supported": 1,
            "unsupported": 0,
            "unresolved": 0,
            "failed": 0,
        },
    }


def test_fact_bundle_replay_immutable_and_cited_spans(db):
    from laip.canonical import record_id

    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    bundle_id = repo.put_fact_bundle(bundle)
    assert repo.put_fact_bundle(bundle) == bundle_id
    assert db.execute("SELECT count(*) FROM analyzer_fact_bundles").fetchone()[0] == 1
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
        db.execute("UPDATE analyzer_fact_bundles SET payload=payload")
    broken = copy.deepcopy(bundle)
    broken["facts"][0]["source_locations"][0]["byte_end"] += 100000
    broken["facts"][0]["fact_id"] = record_id("fact_", broken["facts"][0], "fact_id")
    with pytest.raises(ValueError, match="UNCITED_SOURCE_SPAN"):
        repo.put_fact_bundle(broken)


def test_fact_bundle_namespace_and_run_closure(db):
    from laip.canonical import record_id

    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    broken = copy.deepcopy(bundle)
    broken["system_namespace"] += ":other"
    with pytest.raises(ValueError, match="CROSS_NAMESPACE"):
        repo.put_fact_bundle(broken)
    broken = copy.deepcopy(bundle)
    broken["provider"]["version"] = "wrong"
    with pytest.raises(ValueError, match="FACT_PROVIDER_MISMATCH"):
        repo.put_fact_bundle(broken)
    broken = copy.deepcopy(bundle)
    fact = broken["facts"][0]
    fact["kind"] = "reference"
    fact["data"] = {
        "name": "unknown",
        "relationship": "CALLS",
        "resolution": "resolved",
        "target_entity_id": "ent_" + "f" * 64,
        "candidate_entity_ids": [],
    }
    fact["fact_id"] = record_id("fact_", fact, "fact_id")
    with pytest.raises(ValueError, match="CROSS_NAMESPACE"):
        repo.put_fact_bundle(broken)


def test_subject_binding_and_support_conditions_are_closed(db):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    for key in ("subject_entity_ids", "evidence_ids", "controlling_predicates"):
        invalid = copy.deepcopy(rule)
        invalid["claim_support"][key] = []
        with pytest.raises((ValueError, ValidationError)):
            repo.rule_support_fingerprint(invalid)
    invalid = copy.deepcopy(rule)
    system = next(e for e in fixture["entities"] if e["identity"]["kind"] == "System")
    invalid["subject_entity_ids"] = [system["entity_id"]]
    invalid["claim_support"]["subject_entity_ids"] = invalid["subject_entity_ids"]
    with pytest.raises(ValueError, match="INVALID_SUBJECT_KIND"):
        repo.rule_support_fingerprint(invalid)


def test_upgrade_from_populated_legacy_preserves_rules_reviews_and_identity(db):
    from laip.migrations import migrate

    db.execute("DROP TABLE analyzer_fact_bundles,rule_subjects")
    db.execute("DELETE FROM schema_migrations WHERE version='012_analysis_facts_subjects.sql'")
    repo, fixture = seed(db)
    old = fixture["rules"][0]
    repo.put_rule(old, None)
    repo.review("rule_revision", old["revision_id"], None, "verified", "analyst", "legacy")
    before = db.execute(
        "SELECT revision_id,record_sha256,support_fingerprint,payload FROM rule_revisions"
    ).fetchall()
    reviews = db.execute("SELECT review_id,subject_fingerprint FROM reviews").fetchall()
    entities = db.execute("SELECT entity_id,identity FROM entities ORDER BY entity_id").fetchall()
    assert migrate(db) == ["012_analysis_facts_subjects.sql"]
    assert migrate(db) == []
    assert (
        db.execute(
            "SELECT revision_id,record_sha256,support_fingerprint,payload FROM rule_revisions"
        ).fetchall()
        == before
    )
    assert db.execute("SELECT review_id,subject_fingerprint FROM reviews").fetchall() == reviews
    assert (
        db.execute("SELECT entity_id,identity FROM entities ORDER BY entity_id").fetchall()
        == entities
    )
    rule = source_rule(repo, fixture)
    rule["previous_revision_id"] = old["revision_id"]
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    repo.put_rule(rule, old["revision_id"])
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 2


def test_direct_put_rule_rejects_uncited_conditions(db):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    rule["conditions"][0]["source_locations"] = []
    rule["claim_support"]["controlling_predicates"] = rule["conditions"]
    rule["support_fingerprint"] = repo.rule_support_fingerprint(rule)
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    with pytest.raises(ValueError, match="UNCITED_SOURCE_SPAN"):
        repo.put_rule(rule, None)


def test_fact_ids_are_content_addressed(db):
    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    bundle["facts"][0]["fact_id"] = "fact_" + "f" * 64
    with pytest.raises(ValueError, match="INVALID_FACT_ID"):
        repo.put_fact_bundle(bundle)


def test_unknown_persisted_effect_cannot_be_approved_even_with_fresh_fingerprint(db):
    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    fact = bundle["facts"][0]
    fact["kind"] = "effect"
    fact["data"] = {
        "operation": "opaque",
        "reads": [],
        "writes": [],
        "nondeterministic": False,
        "may_raise": True,
        "unknown": True,
    }
    fact["fact_id"] = record_id("fact_", fact, "fact_id")
    repo.put_fact_bundle(bundle)
    rule = source_rule(repo, fixture)
    rule["claim_support"]["effect_dependencies"] = [fact["fact_id"]]
    rule["support_fingerprint"] = repo.rule_support_fingerprint(rule)
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    repo.put_rule(rule, None)
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        repo.review("rule_revision", rule["revision_id"], None, "verified", "analyst", "bypass")
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(rule["rule_entity_id"], rule["revision_id"], None, "analyst", "check", repo)


def test_colliding_fact_ids_in_different_bundles_fail(db):
    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    repo.put_fact_bundle(bundle)
    changed = copy.deepcopy(bundle)
    changed["profile_version"] = "0.2.1"
    changed["facts"][0]["data"]["name"] = "different"
    with pytest.raises(ValueError, match="INVALID_FACT_ID"):
        repo.put_fact_bundle(changed)


def test_old_run_scope_cannot_be_injected_into_new_rule(db):
    from laip.canonical import identity_id

    repo, fixture = seed(db)
    run = fixture["runs"][0]
    repo.create_run("other_run", run["import_id"], {**run, "run_id": "other_run"})
    entity = copy.deepcopy(
        next(e for e in fixture["entities"] if e["identity"]["kind"] == "Program")
    )
    entity["identity"]["qualified_identity"][-1] = "OUTSIDE_RUN"
    entity["entity_id"] = identity_id(entity["identity"])
    repo.put_entity(entity, "other_run")
    rule = source_rule(repo, fixture)
    rule["program_entity_ids"] = [entity["entity_id"]]
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    with pytest.raises(ValueError, match="CROSS_NAMESPACE"):
        repo.put_rule(rule, None)
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 0


def test_forged_resolution_cannot_relabel_persisted_dynamic_reference(db):
    repo, fixture = seed(db)
    bundle = fact_bundle(fixture)
    fact = bundle["facts"][0]
    fact["kind"] = "reference"
    fact["data"] = {
        "name": "dynamic",
        "relationship": "CALLS",
        "resolution": "dynamic",
        "target_entity_id": None,
        "candidate_entity_ids": [],
    }
    fact["fact_id"] = record_id("fact_", fact, "fact_id")
    repo.put_fact_bundle(bundle)
    rule = source_rule(repo, fixture)
    target = rule["subject_entity_ids"][0]
    rule["claim_support"]["resolution_decisions"] = [
        {"reference": fact["fact_id"], "resolution": "resolved", "target_entity_id": target}
    ]
    with pytest.raises(ValueError, match="RESOLUTION_SUPPORT_MISMATCH"):
        repo.rule_support_fingerprint(rule)


def test_correction_retains_history_and_requires_new_approval(db):
    from laip.rule_store import correct_rule, current_rule, review_history

    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    repo.put_rule(rule, None)
    review = approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "initial source check", repo
    )
    correction = copy.deepcopy(rule)
    correction.update(previous_revision_id=rule["revision_id"], interpretation_method="analyst")
    correction["claim_support"]["profile_version"] = "0.2.1"
    correction["support_fingerprint"] = repo.rule_support_fingerprint(correction)
    assert correction["support_fingerprint"] != rule["support_fingerprint"]
    correction["revision_id"] = record_id("rev_", correction, "revision_id")
    correct_rule(correction, rule["revision_id"], review, "analyst", "profile correction", repo)
    current = current_rule(rule["rule_entity_id"], repo)
    assert current["review_status"] == "pending_review"
    assert current["revision"]["previous_revision_id"] == rule["revision_id"]
    assert any(
        item["review_id"] == review and item["status"] == "verified"
        for item in review_history(rule["rule_entity_id"], repo)
    )
    assert (
        db.execute(
            "SELECT payload FROM rule_revisions WHERE revision_id=%s", (rule["revision_id"],)
        ).fetchone()[0]
        == rule
    )


def test_explicit_barrier_cannot_be_approved_by_supported_state(db):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture)
    rule["claim_support"]["barriers"] = [
        {"code": "UNKNOWN_EFFECT", "evidence_ids": rule["evidence_ids"]}
    ]
    rule["support_fingerprint"] = repo.rule_support_fingerprint(rule)
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    repo.put_rule(rule, None)
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        repo.review("rule_revision", rule["revision_id"], None, "verified", "analyst", "bypass")


def test_unresolved_cited_evidence_blocks_fresh_fingerprint_approval(db):
    repo, fixture = seed(db)
    evidence = copy.deepcopy(fixture["evidence"][0])
    evidence["classification"] = "unresolved"
    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
    repo.put_evidence(evidence)
    rule = source_rule(repo, fixture)
    rule["evidence_ids"].append(evidence["evidence_id"])
    rule["claim_support"]["evidence_ids"] = rule["evidence_ids"]
    rule["support_fingerprint"] = repo.rule_support_fingerprint(rule)
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    repo.put_rule(rule, None)
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(rule["rule_entity_id"], rule["revision_id"], None, "analyst", "check", repo)
