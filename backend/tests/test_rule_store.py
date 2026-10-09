import copy

import pytest
from test_persistence import db as db_fixture
from test_persistence import seed

from laip.canonical import digest, record_id
from laip.jobs import JobRepository, PublicationKey
from laip.rule_store import (
    approve_rule,
    correct_rule,
    current_rule,
    persist_bundle,
    register_bundle,
    reject_rule,
    review_history,
)

db = db_fixture


def published(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    bundle = {"entities": [], "rule_revisions": fixture["rules"], "diagnostics": []}
    persist_bundle(
        run["run_id"], bundle, {"entities": [], "workflows": [], "diagnostics": []}, repo
    )
    return repo, fixture, fixture["rules"][0]


def test_rule_fenced_replay_and_frozen_basis(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    with pytest.raises(ValueError, match="BASIS_MISMATCH"):
        register_bundle(run["run_id"], run["import_id"], "f" * 64, run["providers"], repo)
    jobs = JobRepository(db)
    jobs.enqueue("rule_job", run["run_id"], "analysis")
    lease = jobs.claim("rules_worker")
    assert lease
    key = PublicationKey(
        fixture["artifacts"][0]["artifact_id"],
        run["providers"][0]["id"],
        "0.1.0",
        None,
        run["configuration_sha256"],
        "rule_extraction",
    )
    calls = []

    def publish(_):
        calls.append(True)
        return persist_bundle(
            run["run_id"],
            {"entities": [], "rule_revisions": fixture["rules"], "diagnostics": []},
            {"entities": [], "workflows": [], "diagnostics": []},
            repo,
        )

    first = jobs.publish_checkpoint(lease, key, run["configuration_sha256"], publish)
    assert jobs.publish_checkpoint(lease, key, run["configuration_sha256"], publish) == first
    assert calls == [True]
    assert db.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 1


def test_approve_reject_stale_review_and_correction_history(db):
    repo, fixture, rule = published(db)
    verified = approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "checked source", repo
    )
    assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "verified"
    with pytest.raises(ValueError, match="STALE_REVIEW"):
        reject_rule(rule["rule_entity_id"], rule["revision_id"], None, "analyst", "stale", repo)
    rejected = reject_rule(
        rule["rule_entity_id"], rule["revision_id"], verified, "analyst", "reconsider", repo
    )
    correction = copy.deepcopy(rule)
    correction.update(
        previous_revision_id=rule["revision_id"],
        description="Corrected source interpretation",
        interpretation_method="analyst",
    )
    correction["revision_id"] = record_id("rev_", correction, "revision_id")
    new_review = correct_rule(
        correction, rule["revision_id"], rejected, "analyst", "corrected wording", repo
    )
    head = current_rule(rule["rule_entity_id"], repo)
    assert head["revision"]["revision_id"] == correction["revision_id"]
    assert head["review_status"] == "pending_review" and head["review_id"] == new_review
    assert any(row["status"] == "verified" for row in review_history(rule["rule_entity_id"], repo))
    with pytest.raises(ValueError, match="STALE_REVISION"):
        approve_rule(
            rule["rule_entity_id"], rule["revision_id"], rejected, "analyst", "old revision", repo
        )


def test_reanalysis_changed_support_resets_current_approval(db):
    repo, fixture, rule = published(db)
    approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "source checked", repo
    )
    old_evidence = fixture["evidence"][0]
    changed_evidence = copy.deepcopy(old_evidence)
    changed_evidence["metadata"] = {
        "predicate": "changed source support",
        "conclusions_allowed": True,
    }
    changed_evidence["evidence_id"] = record_id("ev_", changed_evidence, "evidence_id")
    repo.put_evidence(changed_evidence)
    changed = copy.deepcopy(rule)
    changed.update(
        previous_revision_id=rule["revision_id"], evidence_ids=[changed_evidence["evidence_id"]]
    )
    run = fixture["runs"][0]
    changed["support_fingerprint"] = digest(
        {
            "evidence": [
                {
                    "evidence_id": changed_evidence["evidence_id"],
                    "content_sha256": changed_evidence["content_sha256"],
                }
            ],
            "providers": run["providers"],
            "resolution_decisions": [],
            "configuration_sha256": run["configuration_sha256"],
        }
    )
    changed["revision_id"] = record_id("rev_", changed, "revision_id")
    persist_bundle(
        run["run_id"],
        {"entities": [], "rule_revisions": [changed], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "pending_review"
    assert any(row["status"] == "verified" for row in review_history(rule["rule_entity_id"], repo))
    with pytest.raises(ValueError, match="STALE_REVISION"):
        persist_bundle(
            run["run_id"],
            {"entities": [], "rule_revisions": [rule], "diagnostics": []},
            {"entities": [], "workflows": [], "diagnostics": []},
            repo,
        )


def test_unresolved_rule_and_support_cannot_be_verified(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    rule = copy.deepcopy(fixture["rules"][0])
    rule["classification"] = "unresolved"
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    persist_bundle(
        run["run_id"],
        {"entities": [], "rule_revisions": [rule], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"], rule["revision_id"], None, "analyst", "cannot bypass", repo
        )
    correction = copy.deepcopy(rule)
    correction.update(
        previous_revision_id=rule["revision_id"],
        classification="inferred",
        interpretation_method="analyst",
        evidence_ids=sorted(
            [fixture["evidence"][2]["evidence_id"], fixture["evidence"][0]["evidence_id"]]
        ),
    )
    correction["support_fingerprint"] = digest(
        {
            "evidence": sorted(
                [
                    {
                        "evidence_id": fixture["evidence"][i]["evidence_id"],
                        "content_sha256": fixture["evidence"][i]["content_sha256"],
                    }
                    for i in (0, 2)
                ],
                key=lambda value: value["evidence_id"],
            ),
            "providers": run["providers"],
            "resolution_decisions": [],
            "configuration_sha256": run["configuration_sha256"],
        }
    )
    correction["revision_id"] = record_id("rev_", correction, "revision_id")
    review = correct_rule(
        correction, rule["revision_id"], None, "analyst", "still unsupported", repo
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"],
            correction["revision_id"],
            review,
            "analyst",
            "cannot bypass",
            repo,
        )


def test_rule_barrier_limitation_blocks_approval_and_immutability(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    rule = copy.deepcopy(fixture["rules"][0])
    rule["limitations"].append(
        "Unknown constructs or effects prevent complete business conclusions."
    )
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    persist_bundle(
        run["run_id"],
        {"entities": [], "rule_revisions": [rule], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"],
            rule["revision_id"],
            None,
            "analyst",
            "cannot override barrier",
            repo,
        )
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE rule_revisions SET payload='{}'")


def test_concurrent_review_optimistic_lock(db):
    from concurrent.futures import ThreadPoolExecutor

    import psycopg
    from psycopg import sql

    from laip.persistence import Repository

    repo, fixture, rule = published(db)
    schema = db.execute("SELECT current_schema()").fetchone()[0]

    def review(_):
        with psycopg.connect(db.info.dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            try:
                approve_rule(
                    rule["rule_entity_id"],
                    rule["revision_id"],
                    None,
                    "analyst",
                    "concurrent check",
                    Repository(connection, repo.namespace),
                )
                return "verified"
            except ValueError as error:
                return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(review, range(2)))
    assert sorted(outcomes) == ["STALE_REVIEW", "verified"]
    assert len(review_history(rule["rule_entity_id"], repo)) == 1


def test_missing_candidate_explicitly_invalidates_old_approval(db):
    from laip.rule_store import invalidate_scope

    repo, fixture, rule = published(db)
    approval = approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "source checked", repo
    )
    invalidate_scope(
        {rule["rule_entity_id"]: (rule["revision_id"], approval)},
        "analysis-worker",
        "Candidate absent from supplied reanalysis scope",
        repo,
    )
    assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "pending_review"
    history = review_history(rule["rule_entity_id"], repo)
    assert [entry["status"] for entry in history] == ["verified", "pending_review"]
    with pytest.raises(ValueError, match="STALE_REVIEW"):
        invalidate_scope(
            {rule["rule_entity_id"]: (rule["revision_id"], approval)},
            "analysis-worker",
            "stale invalidation",
            repo,
        )


def test_call_rule_without_resolved_target_cannot_be_verified(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    rule = copy.deepcopy(fixture["rules"][0])
    rule["actions"][0]["value"] = {"operation": "call", "target": "notify"}
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    persist_bundle(
        run["run_id"],
        {"entities": [], "rule_revisions": [rule], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"], rule["revision_id"], None, "analyst", "target unresolved", repo
        )


def test_workflow_contract_replay_and_forged_path_rejected(db):
    from laip.canonical import identity_id

    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    programs = [e["entity_id"] for e in fixture["entities"] if e["identity"]["kind"] == "Program"]
    edge = copy.deepcopy(fixture["dependencies"][0])
    edge.update(
        relationship="CALLS",
        from_entity_id=programs[0],
        to_entity_id=programs[1],
        evidence_ids=[fixture["evidence"][0]["evidence_id"]],
    )
    edge["dependency_id"] = record_id("dep_", edge, "dependency_id")
    repo.put_dependency(edge)
    identity = {
        "system_namespace": repo.namespace,
        "kind": "Workflow",
        "qualified_identity": ["possible-call-path", *programs],
    }
    entity = copy.deepcopy(
        next(e for e in fixture["entities"] if e["identity"]["kind"] == "Workflow")
    )
    entity.update(entity_id=identity_id(identity), identity=identity, parent_id=None)
    workflow = {
        "schema_version": "0.1.0",
        "run_id": run["run_id"],
        "workflow_entity_id": entity["entity_id"],
        "program_entity_ids": programs,
        "dependency_ids": [edge["dependency_id"]],
        "evidence_ids": edge["evidence_ids"],
        "classification": "inferred",
        "review_status": "pending_review",
        "limitations": ["Static possible path; no runtime proof."],
        "created_at": "2026-10-08T00:00:00Z",
    }
    workflow["workflow_id"] = "wf_" + digest(workflow)
    rules = {"entities": [], "rule_revisions": [], "diagnostics": []}
    workflows = {"entities": [entity], "workflows": [workflow], "diagnostics": []}
    first = persist_bundle(run["run_id"], rules, workflows, repo)
    assert persist_bundle(run["run_id"], rules, workflows, repo) == first
    assert db.execute("SELECT count(*) FROM workflow_revisions").fetchone()[0] == 1
    forged = copy.deepcopy(workflow)
    forged["program_entity_ids"].reverse()
    forged["workflow_id"] = "wf_" + digest({k: v for k, v in forged.items() if k != "workflow_id"})
    with pytest.raises(ValueError, match="INVALID_WORKFLOW"):
        persist_bundle(
            run["run_id"], rules, {"entities": [], "workflows": [forged], "diagnostics": []}, repo
        )
    verified = copy.deepcopy(workflow)
    verified["review_status"] = "verified"
    verified["workflow_id"] = "wf_" + digest(
        {k: v for k, v in verified.items() if k != "workflow_id"}
    )
    with pytest.raises(ValueError, match="INVALID_WORKFLOW"):
        persist_bundle(
            run["run_id"], rules, {"entities": [], "workflows": [verified], "diagnostics": []}, repo
        )


def test_publication_serializes_approval_current_head_check(db):
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    import psycopg
    from psycopg import sql

    from laip.persistence import Repository

    repo, fixture, rule = published(db)
    successor = copy.deepcopy(rule)
    successor.update(
        previous_revision_id=rule["revision_id"], description="Changed condition wording"
    )
    successor["revision_id"] = record_id("rev_", successor, "revision_id")
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    ready = threading.Event()

    def approve():
        with psycopg.connect(db.info.dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            ready.set()
            try:
                approve_rule(
                    rule["rule_entity_id"],
                    rule["revision_id"],
                    None,
                    "analyst",
                    "racing publication",
                    Repository(connection, repo.namespace),
                )
                return "verified"
            except ValueError as error:
                return str(error)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction():
            repo.put_rule(successor, rule["revision_id"])
            future = pool.submit(approve)
            assert ready.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        assert future.result(timeout=5) == "STALE_REVISION"
    assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "pending_review"
    assert review_history(rule["rule_entity_id"], repo) == []


def test_namespace_isolation_and_unregistered_publication(db):
    from laip.persistence import Repository

    repo, fixture, rule = published(db)
    other = Repository(db, "other:rules")
    other.create_system()
    with pytest.raises(ValueError):
        approve_rule(
            rule["rule_entity_id"], rule["revision_id"], None, "analyst", "wrong namespace", other
        )
    with pytest.raises(ValueError, match="RULE_NOT_FOUND"):
        current_rule(rule["rule_entity_id"], other)
    assert review_history(rule["rule_entity_id"], other) == []
    with pytest.raises(ValueError, match="BASIS_MISSING"):
        persist_bundle(
            "absent",
            {"entities": [], "rule_revisions": [], "diagnostics": []},
            {"entities": [], "workflows": [], "diagnostics": []},
            repo,
        )


def test_real_composer_output_persists_same_run_metadata_support(db):
    from laip.workflows import compose_workflows

    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    programs = [e for e in fixture["entities"] if e["identity"]["kind"] == "Program"]
    evidence = copy.deepcopy(fixture["evidence"][0])
    evidence.update(
        authority="imported_metadata", source_locations=[], metadata={"qualified_call": True}
    )
    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
    repo.put_evidence(evidence)
    edge = copy.deepcopy(fixture["dependencies"][0])
    edge.update(
        relationship="CALLS",
        from_entity_id=programs[0]["entity_id"],
        to_entity_id=programs[1]["entity_id"],
        evidence_ids=[evidence["evidence_id"]],
    )
    edge["dependency_id"] = record_id("dep_", edge, "dependency_id")
    repo.put_dependency(edge)
    workflows = compose_workflows(
        [edge],
        run_id=run["run_id"],
        system_namespace=repo.namespace,
        created_at="2026-10-08T00:00:00Z",
        program_entities=programs,
        evidence=[evidence],
    )
    assert len(workflows["workflows"]) == 1
    result = persist_bundle(
        run["run_id"], {"entities": [], "rule_revisions": [], "diagnostics": []}, workflows, repo
    )
    assert result["workflow_count"] == 1
    assert (
        db.execute("SELECT payload FROM workflow_revisions").fetchone()[0]
        == workflows["workflows"][0]
    )


def test_concurrent_basis_registration_is_exact_replay(db):
    from concurrent.futures import ThreadPoolExecutor

    import psycopg
    from psycopg import sql

    from laip.persistence import Repository

    repo, fixture = seed(db)
    run = fixture["runs"][0]
    schema = db.execute("SELECT current_schema()").fetchone()[0]

    def register(_):
        with psycopg.connect(db.info.dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            register_bundle(
                run["run_id"],
                run["import_id"],
                run["configuration_sha256"],
                run["providers"],
                Repository(connection, repo.namespace),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(register, range(2)))
    assert db.execute("SELECT count(*) FROM rule_bundle_bases").fetchone()[0] == 1


def test_explicit_missing_scope_invalidates_inside_publication(db):
    repo, fixture, rule = published(db)
    approval = approve_rule(
        rule["rule_entity_id"], rule["revision_id"], None, "analyst", "checked source", repo
    )
    persist_bundle(
        rule["run_id"],
        {"entities": [], "rule_revisions": [], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
        expected_missing_heads={rule["rule_entity_id"]: (rule["revision_id"], approval)},
    )
    assert current_rule(rule["rule_entity_id"], repo)["review_status"] == "pending_review"
    assert [review["status"] for review in review_history(rule["rule_entity_id"], repo)] == [
        "verified",
        "pending_review",
    ]


@pytest.mark.parametrize("field", ["conditions", "actions"])
def test_uncited_expression_source_spans_block_publication_and_correction(db, field):
    repo, fixture, rule = published(db)
    forged = copy.deepcopy(rule)
    forged[field][0]["source_locations"][0]["artifact_id"] = fixture["artifacts"][1]["artifact_id"]
    forged.update(previous_revision_id=rule["revision_id"], interpretation_method="analyst")
    forged["revision_id"] = record_id("rev_", forged, "revision_id")
    with pytest.raises(ValueError, match="UNCITED_SOURCE_SPAN"):
        persist_bundle(
            rule["run_id"],
            {"entities": [], "rule_revisions": [forged], "diagnostics": []},
            {"entities": [], "workflows": [], "diagnostics": []},
            repo,
        )
    with pytest.raises(ValueError, match="UNCITED_SOURCE_SPAN"):
        correct_rule(forged, rule["revision_id"], None, "analyst", "uncited artifact", repo)
    assert (
        current_rule(rule["rule_entity_id"], repo)["revision"]["revision_id"] == rule["revision_id"]
    )
    assert review_history(rule["rule_entity_id"], repo) == []


def test_opaque_action_value_cannot_be_verified(db):
    repo, fixture = seed(db)
    run = fixture["runs"][0]
    register_bundle(
        run["run_id"], run["import_id"], run["configuration_sha256"], run["providers"], repo
    )
    rule = copy.deepcopy(fixture["rules"][0])
    rule["actions"][0]["value"] = None
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    persist_bundle(
        run["run_id"],
        {"entities": [], "rule_revisions": [rule], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"], rule["revision_id"], None, "analyst", "opaque action", repo
        )


def test_opaque_expression_node_and_reversed_decoded_span(db):
    repo, fixture, rule = published(db)
    opaque = copy.deepcopy(rule)
    opaque.update(previous_revision_id=rule["revision_id"])
    opaque["actions"][0]["node"] = "opaque"
    opaque["revision_id"] = record_id("rev_", opaque, "revision_id")
    persist_bundle(
        rule["run_id"],
        {"entities": [], "rule_revisions": [opaque], "diagnostics": []},
        {"entities": [], "workflows": [], "diagnostics": []},
        repo,
    )
    with pytest.raises(ValueError, match="UNRESOLVED_SUPPORT"):
        approve_rule(
            rule["rule_entity_id"], opaque["revision_id"], None, "analyst", "opaque meaning", repo
        )
    reversed_span = copy.deepcopy(opaque)
    reversed_span.update(
        previous_revision_id=opaque["revision_id"], interpretation_method="analyst"
    )
    span = reversed_span["actions"][0]["source_locations"][0]
    span.update(start_line=4, start_column=6, end_line=2, end_column=2)
    reversed_span["revision_id"] = record_id("rev_", reversed_span, "revision_id")
    with pytest.raises(ValueError, match="UNCITED_SOURCE_SPAN"):
        correct_rule(
            reversed_span, opaque["revision_id"], None, "analyst", "invalid decoded interval", repo
        )
