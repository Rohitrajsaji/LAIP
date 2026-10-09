import pytest
from test_banking_persistence import source_rule
from test_persistence import db as db_fixture
from test_persistence import seed
from test_retrieval import published
from test_runtime import settings

from laip.analyst_service import AnalystService
from laip.exports import build_export, validate_bundle
from laip.public_projection import semantic_payload
from laip.read_service import ReadService, safe_error
from laip.retrieval import RetrievalService

db = db_fixture


def test_snapshot_negotiation_preserves_legacy_and_rejects_new_envelope(db):
    repo, bundle = published(db)
    legacy = RetrievalService(repo, "snap_retrieval")
    legacy.require_version("0.1.0")
    legacy.require_version("0.2.0")
    payload = db.execute(
        "SELECT payload FROM snapshots WHERE snapshot_id='snap_retrieval'"
    ).fetchone()[0]
    repo.publish_snapshot(
        "new_envelope", bundle["runs"][0]["run_id"], payload, schema_version="0.2.0"
    )
    new = RetrievalService(repo, "new_envelope")
    new.require_version("0.2.0")
    with pytest.raises(ValueError, match="INCOMPATIBLE_SNAPSHOT_VERSION"):
        new.require_version("0.1.0")
    with pytest.raises(ValueError, match="INCOMPATIBLE_SNAPSHOT_VERSION"):
        ReadService(repo).call(
            "evidence",
            {
                "schema_version": "0.1.0",
                "snapshot_id": "new_envelope",
                "evidence_id": bundle["evidence"][0]["evidence_id"],
            },
        )


def test_compatibility_errors_are_named():
    assert safe_error(ValueError("INCOMPATIBLE_SNAPSHOT_VERSION")) == (
        400,
        "INCOMPATIBLE_SNAPSHOT_VERSION",
    )
    assert safe_error(ValueError("UNSUPPORTED_SCHEMA_VERSION")) == (
        400,
        "UNSUPPORTED_SCHEMA_VERSION",
    )


def test_new_reads_accept_old_records_without_relabelling(db):
    repo, bundle = published(db)
    service = ReadService(repo)
    base = {"schema_version": "0.2.0", "snapshot_id": "snap_retrieval"}
    cases = {
        "entity": {**base, "entity_id": bundle["entities"][0]["entity_id"]},
        "evidence": {**base, "evidence_id": bundle["evidence"][0]["evidence_id"]},
        "search": {**base, "query": "customer"},
        "graph": {**base, "root_entity_ids": [bundle["entities"][0]["entity_id"]]},
        "context": {**base, "level": "system", "entity_ids": [], "max_context_tokens": 32768},
    }
    for operation, arguments in cases.items():
        value = service.call(operation, arguments)
        assert value["schema_version"] == "0.2.0"
        if operation == "context":
            assert value["data"]["schema_version"] == "0.2.0"
        else:
            for record in value["data"]["records"]:
                assert record["payload"].get("schema_version", "0.1.0") == "0.1.0"


def test_public_metadata_excludes_source_rows_but_preserves_semantics():
    payload = {
        "metadata": {
            "definitions": [
                {"attributes": {"physical_lines": ["PRIVATE SOURCE"], "datatype": "T"}}
            ],
            "body": "PRIVATE SOURCE",
            "source_locations": [{"line": 26}],
        },
        "text": "approved keyword chunk",
    }
    projected = semantic_payload(payload)
    assert "PRIVATE SOURCE" not in str(projected)
    assert projected["text"] == "approved keyword chunk"
    assert projected["metadata"]["definitions"][0]["attributes"]["datatype"] == "T"
    assert payload["metadata"]["body"] == "PRIVATE SOURCE"


def test_new_export_preserves_historical_record_versions(db):
    repo, _ = published(db)
    exported = build_export(
        repo,
        "snap_retrieval",
        export_id="dual_version",
        created_at="2026-10-08T10:00:00Z",
        schema_version="0.2.0",
    )
    assert exported.manifest["schema_version"] == "0.2.0"
    validate_bundle(exported)
    assert not any(path.startswith("source/") for path in exported.files)


def test_upgraded_snapshot_retains_legacy_workflow_records(db):
    from test_exports import test_snapshot_workflow_is_exported_with_static_pending_certainty

    from laip.persistence import Repository

    test_snapshot_workflow_is_exported_with_static_pending_certainty(db)
    snapshot, namespace, run, manifest = db.execute(
        "SELECT snapshot_id,system_namespace,run_id,payload FROM snapshots LIMIT 1"
    ).fetchone()
    repo = Repository(db, namespace)
    repo.publish_snapshot("upgraded_workflow", run, manifest, schema_version="0.2.0")
    workflow = db.execute("SELECT payload FROM workflow_revisions LIMIT 1").fetchone()[0]
    result = ReadService(repo).call(
        "entity",
        {
            "schema_version": "0.2.0",
            "snapshot_id": "upgraded_workflow",
            "entity_id": workflow["workflow_entity_id"],
        },
    )
    assert any(
        record["type"] == "workflow" and record["payload"] == workflow
        for record in result["data"]["records"]
    )
    exported = build_export(
        repo,
        "upgraded_workflow",
        export_id="legacy-workflow-upgrade",
        created_at="2026-10-09T00:00:00Z",
        schema_version="0.2.0",
    )
    validate_bundle(exported)


def test_generic_subject_correction_preserves_version_and_support(db, tmp_path):
    repo, fixture = seed(db)
    rule = source_rule(repo, fixture, state="conditional")
    repo.put_rule(rule, None)
    run_id = fixture["runs"][0]["run_id"]
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run_id,),
    )
    manifest = {
        "observations": [
            row[0] for row in db.execute("SELECT observation_id FROM entity_observations")
        ],
        "evidence": [item["evidence_id"] for item in fixture["evidence"]],
        "dependencies": [item["dependency_id"] for item in fixture["dependencies"]],
        "revisions": [rule["revision_id"]],
    }
    repo.publish_snapshot("generic_rule", run_id, manifest, schema_version="0.2.0")
    service = AnalystService(db, settings(tmp_path, analyst_namespace=repo.namespace))
    request = {
        "schema_version": "0.1.0",
        "snapshot_id": "generic_rule",
        "expected_revision_id": rule["revision_id"],
        "name": rule["name"],
        "description": "Corrected description preserves conditional support",
        "conditions": rule["conditions"],
        "actions": rule["actions"],
        "evidence_ids": rule["evidence_ids"],
        "reason": "Explicit analyst correction",
    }
    result = service.correct(rule["rule_entity_id"], request)
    revised = result["revision"]
    assert revised["claim_support"]["state"] == "conditional"
    assert revised["subject_entity_ids"] == rule["subject_entity_ids"]
    assert revised["previous_revision_id"] == rule["revision_id"]
    assert service._snapshot(result["snapshot_id"]).snapshot_schema_version == "0.2.0"
    assert (
        service.rules(result["snapshot_id"], "0.2.0")["rules"][0]["review_status"]
        == "pending_review"
    )
    assert service.rules("generic_rule", "0.2.0")["rules"][0]["revision"] == rule
    read = service._snapshot("generic_rule").exact(rule["subject_entity_ids"][0])
    assert any(item["type"] == "rule" for item in read["records"])
    exported = build_export(
        repo,
        result["snapshot_id"],
        export_id="corrected_generic",
        created_at="2026-10-08T10:00:00Z",
        schema_version="0.2.0",
    )
    validate_bundle(exported)
    with pytest.raises(ValueError, match="INCOMPATIBLE_SNAPSHOT_VERSION"):
        build_export(
            repo,
            result["snapshot_id"],
            export_id="incompatible",
            created_at="2026-10-08T10:00:00Z",
        )
