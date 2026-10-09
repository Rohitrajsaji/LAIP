from copy import deepcopy

import pytest
from test_persistence import db as db_fixture
from test_persistence import seed

from laip.canonical import digest

db = db_fixture


def test_snapshot_versions_are_additive_and_legacy_bytes_unchanged(db):
    repo, bundle = seed(db)
    run_id = bundle["runs"][0]["run_id"]
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run_id,),
    )
    repo.publish_snapshot("legacy", run_id, {})
    assert db.execute(
        "SELECT payload,manifest_sha256 FROM snapshots WHERE snapshot_id='legacy'"
    ).fetchone() == ({}, digest({}))
    repo.publish_snapshot("new", run_id, {}, schema_version="0.2.0", record_versions=[])
    repo.publish_snapshot("new", run_id, {}, schema_version="0.2.0", record_versions=[])
    payload = db.execute("SELECT payload FROM snapshots WHERE snapshot_id='new'").fetchone()[0]
    assert payload == {"schema_version": "0.2.0", "record_versions": []}
    with pytest.raises(ValueError, match="SNAPSHOT_RECORD_VERSION_MISMATCH"):
        repo.publish_snapshot(
            "forged", run_id, {}, schema_version="0.2.0", record_versions=["0.1.0"]
        )


def test_mixed_snapshot_cannot_be_published_as_legacy(db):
    repo, bundle = seed(db)
    run_id = bundle["runs"][0]["run_id"]
    entity = deepcopy(next(e for e in bundle["entities"] if e["identity"]["kind"] == "Field"))
    entity["schema_version"] = "0.2.0"
    repo.put_entity(entity, run_id)
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run_id,),
    )
    manifest = {
        "observations": [
            r[0] for r in db.execute("SELECT observation_id FROM entity_observations")
        ],
        "evidence": [e["evidence_id"] for e in bundle["evidence"]],
    }
    with pytest.raises(ValueError, match="INCOMPATIBLE_SNAPSHOT_VERSION"):
        repo.publish_snapshot("mixed-legacy", run_id, manifest)
    with pytest.raises(ValueError, match="SNAPSHOT_RECORD_VERSION_MISMATCH"):
        repo.publish_snapshot(
            "mixed-forged", run_id, manifest, schema_version="0.2.0", record_versions=["0.1.0"]
        )
    repo.publish_snapshot("mixed", run_id, manifest, schema_version="0.2.0")
    payload = db.execute("SELECT payload FROM snapshots WHERE snapshot_id='mixed'").fetchone()[0]
    assert payload["record_versions"] == ["0.1.0", "0.2.0"]
