import copy
from pathlib import Path

import pytest
from test_persistence import db as db_fixture
from test_retrieval import published
from test_runtime import settings

from laip.analyst_service import AnalystService
from laip.artifacts import LocalArtifactStore
from laip.masking import POLICY

db = db_fixture


def test_empty_workspace_and_snapshot_inventory(db, tmp_path):
    service = AnalystService(db, settings(tmp_path))
    assert service.workspace()["snapshot_id"] is None
    assert service.workspace()["imports"] == []
    repo, bundle = published(db)
    configured = settings(tmp_path, analyst_namespace=repo.namespace)
    service = AnalystService(db, configured)
    workspace = service.workspace()
    assert workspace["snapshot_id"] == "snap_retrieval"
    assert workspace["ai_enabled"] is False
    inventory = service.inventory("snap_retrieval")
    assert len([r for r in inventory["records"] if r["type"] == "entity"]) == len(
        bundle["entities"]
    )
    dependencies = service.dependencies("snap_retrieval")
    assert any(
        r["payload"]["resolution"] == "dynamic"
        for r in dependencies["records"]
        if r["type"] == "dependency"
    )
    assert service.rules("snap_retrieval")["rules"]
    with pytest.raises(ValueError):
        AnalystService(db, settings(tmp_path)).inventory("snap_retrieval")


def test_correction_is_immutable_new_snapshot_and_old_evidence_stays(db, tmp_path):
    repo, bundle = published(db)
    service = AnalystService(db, settings(tmp_path, analyst_namespace=repo.namespace))
    old = copy.deepcopy(bundle["rules"][0])
    old_reviews = service.rules("snap_retrieval")["rules"][0]["reviews"]
    request = {
        "schema_version": "0.1.0",
        "snapshot_id": "snap_retrieval",
        "expected_revision_id": old["revision_id"],
        "name": old["name"],
        "description": "Corrected synthetic source interpretation",
        "conditions": old["conditions"],
        "actions": old["actions"],
        "evidence_ids": old["evidence_ids"],
        "reason": "Browser analyst correction",
    }
    result = service.correct(old["rule_entity_id"], request)
    assert result["snapshot_id"] != "snap_retrieval"
    assert result["revision"]["previous_revision_id"] == old["revision_id"]
    assert result["revision"]["interpretation_method"] == "analyst"
    assert result["revision"]["classification"] == "inferred"
    assert service.rules("snap_retrieval")["rules"][0]["revision"] == old
    assert service.rules("snap_retrieval")["rules"][0]["reviews"] == old_reviews
    assert service._snapshot(result["snapshot_id"]).search("Corrected", policy_version=POLICY)[
        "records"
    ]
    new = service.rules(result["snapshot_id"])["rules"][0]
    assert new["review_status"] == "pending_review"
    assert len(new["revisions"]) == 2
    assert service.workspace()["snapshot_id"] == result["snapshot_id"]
    with pytest.raises(ValueError, match="STALE"):
        service.correct(old["rule_entity_id"], request)


def test_source_window_is_authorized_masked_bounded_and_mapped(db, tmp_path):
    repo, bundle = published(db)
    tmp_path.chmod(0o700)
    store = LocalArtifactStore(tmp_path)
    import io

    raw = (Path(__file__).resolve().parents[2] / "docs/contracts/0.1.0/VALIDATE.rpgle").read_bytes()
    store.put(io.BytesIO(raw), max_bytes=10000, job_id="fixture", fence=1)
    artifact = next(a for a in bundle["artifacts"] if a["source_member_id"])
    service = AnalystService(db, settings(tmp_path, analyst_namespace=repo.namespace))
    source = service.source(artifact["artifact_id"], "snap_retrieval", 1, 10)
    assert "CustomerBalance" in source["text"]
    assert source["origin_map_id"]
    assert source["range"] == {"start_line": 1, "end_line": 10}
    with pytest.raises(ValueError):
        service.source(artifact["artifact_id"], "snap_retrieval", 1, 201)
    with pytest.raises(ValueError):
        service.source("art_" + "f" * 64, "snap_retrieval", 1, 10)
