import copy
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from laip.canonical import digest, record_id
from laip.migrations import migrate
from laip.persistence import Repository

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set LAIP_TEST_DATABASE_URL to isolated PostgreSQL16 test database")
    schema = "test_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migrate(conn)
        try:
            yield conn
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def seed(db):
    bundle = json.loads((ROOT / "docs/contracts/0.1.0/synthetic.example.json").read_text())
    run = bundle["runs"][0]
    repo = Repository(db, run["system_namespace"])
    repo.create_system()
    repo.create_import(run["import_id"], bundle["imports"][0])
    repo.create_run(run["run_id"], run["import_id"], run)
    for entity in bundle["entities"]:
        repo.put_entity(entity, run["run_id"])
    for artifact in bundle["artifacts"]:
        repo.put_blob(artifact["sha256"], artifact["byte_length"])
        repo.put_artifact(artifact)
        repo.attach_artifact(run["run_id"], artifact["artifact_id"])
    for mapping in bundle["origin_maps"]:
        repo.put_origin_map(mapping)
    pending = list(bundle["evidence"])
    while pending:
        for record in pending[:]:
            if not any(
                eid in {e["evidence_id"] for e in pending}
                for eid in record["supporting_evidence_ids"] + record["contradiction_evidence_ids"]
            ):
                repo.put_evidence(record)
                pending.remove(record)
    for dependency in bundle["dependencies"]:
        repo.put_dependency(dependency)
    return repo, bundle


def test_clean_migration_replay_and_checksum(db, tmp_path):
    assert migrate(db) == []
    db.execute("UPDATE schema_migrations SET checksum='corrupt' WHERE version='001_core.sql'")
    with pytest.raises(ValueError, match="CHECKSUM"):
        migrate(db)


def test_repeat_analysis_and_namespaces(db):
    repo, bundle = seed(db)
    run = bundle["runs"][0]
    observation = repo.put_entity(bundle["entities"][0], run["run_id"])
    assert repo.put_entity(bundle["entities"][0], run["run_id"]) == observation
    for evidence in bundle["evidence"]:
        repo.put_evidence(evidence)
    assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == len(bundle["evidence"])
    other = Repository(db, "other:namespace")
    other.create_system()
    other.create_import("other_import", {})
    other.create_run("other_run", "other_import", {})
    with pytest.raises(ValueError):
        other.put_entity(bundle["entities"][0], "other_run")
    changed = copy.deepcopy(bundle["entities"][0])
    changed["identity"]["qualified_identity"][0] = "CHANGED"
    with pytest.raises(ValueError, match="IDENTITY"):
        repo.put_entity(changed, run["run_id"])
    repo.create_run("run_repeat", run["import_id"], {**run, "run_id": "run_repeat"})
    assert repo.put_entity(bundle["entities"][0], "run_repeat") != observation
    assert db.execute("SELECT count(*) FROM entities").fetchone()[0] == len(bundle["entities"])


def test_integrity_reviews_rules_search_and_history(db):
    repo, bundle = seed(db)
    rule = bundle["rules"][0]
    repo.put_rule(rule, None)
    repo.put_rule(rule, None)
    review = repo.review(
        "rule_revision", rule["revision_id"], None, "verified", "analyst", "checked"
    )
    with pytest.raises(ValueError, match="STALE_REVIEW"):
        repo.review("rule_revision", rule["revision_id"], None, "rejected", "analyst", "stale")
    repo.review(
        "rule_revision", rule["revision_id"], review, "pending_review", "analyst", "new review"
    )
    assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 2
    wrong = copy.deepcopy(bundle["evidence"][0])
    wrong["content_sha256"] = "0" * 64
    wrong["evidence_id"] = record_id("ev_", wrong, "evidence_id")
    with pytest.raises(ValueError, match="DIGEST"):
        repo.put_evidence(wrong)
    run = bundle["runs"][0]
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    manifest = {
        "observations": [
            row[0]
            for row in db.execute("SELECT observation_id FROM entity_observations").fetchall()
        ],
        "evidence": [e["evidence_id"] for e in bundle["evidence"]],
        "revisions": [rule["revision_id"]],
    }
    repo.publish_snapshot("snapshot_test", run["run_id"], manifest, True)
    repo.publish_snapshot("snapshot_test", run["run_id"], manifest, True)
    repo.put_chunk(
        "chunk_test",
        "snapshot_test",
        "Customer balance static rule",
        [bundle["evidence"][0]["evidence_id"]],
        "policy1",
        "provider1",
        "tokens1",
    )
    repo.put_chunk(
        "chunk_test",
        "snapshot_test",
        "Customer balance static rule",
        [bundle["evidence"][0]["evidence_id"]],
        "policy1",
        "provider1",
        "tokens1",
    )
    assert len(repo.search("snapshot_test", "policy1", "balance")) == 1
    assert repo.search("snapshot_test", "policy2", "balance") == []
    assert repo.search("unknown", "policy1", "balance") == []
    with pytest.raises(ValueError):
        repo.search("snapshot_test", "policy1", "balance", 101)
    with pytest.raises(ValueError):
        repo.put_chunk("uncited", "snapshot_test", "text", [], "p", "v", "t")
    with pytest.raises(ValueError):
        repo.review("unknown", "missing", None, "verified", "analyst", "reason")
    assert digest(rule) == db.execute("SELECT record_sha256 FROM rule_revisions").fetchone()[0]


def test_validation_failures_are_atomic(db):
    repo, bundle = seed(db)
    run = bundle["runs"][0]
    with pytest.raises(ValueError):
        repo.create_run("terminal", run["import_id"], {}, "succeeded")
    with pytest.raises(ValueError):
        repo.create_import(run["import_id"], {"different": True})
    artifact = copy.deepcopy(bundle["artifacts"][0])
    artifact["artifact_id"] = "art_" + "0" * 64
    with pytest.raises(ValueError):
        repo.put_artifact(artifact)
    original = copy.deepcopy(bundle["evidence"][0])

    def changed_evidence(**changes):
        record = copy.deepcopy(original)
        record.update(changes)
        record["evidence_id"] = record_id("ev_", record, "evidence_id")
        return record

    with pytest.raises(ValueError, match="PROVIDER"):
        repo.put_evidence(
            changed_evidence(provider={"id": "other", "version": "1", "source_revision": None})
        )
    with pytest.raises(ValueError, match="METADATA"):
        repo.put_evidence(changed_evidence(artifact_id=None, metadata={}))
    span = copy.deepcopy(original["source_locations"][0])
    span["byte_end"] = 999999
    with pytest.raises(ValueError, match="SPAN"):
        repo.put_evidence(changed_evidence(source_locations=[span]))
    invalid = copy.deepcopy(original)
    invalid["evidence_id"] = "ev_" + "0" * 64
    with pytest.raises(ValueError, match="EVIDENCE_ID"):
        repo.put_evidence(invalid)
    invalid = copy.deepcopy(bundle["dependencies"][0])
    invalid["resolution_context"]["namespace"] = "other"
    invalid["dependency_id"] = record_id("dep_", invalid, "dependency_id")
    with pytest.raises(ValueError):
        repo.put_dependency(invalid)
    ambiguous = copy.deepcopy(bundle["dependencies"][2])
    ambiguous["candidate_entity_ids"] = [ambiguous["candidate_entity_ids"][0]] * 2
    ambiguous["dependency_id"] = record_id("dep_", ambiguous, "dependency_id")
    with pytest.raises(ValueError):
        repo.put_dependency(ambiguous)
    rule = copy.deepcopy(bundle["rules"][0])
    rule["support_fingerprint"] = "0" * 64
    rule["revision_id"] = record_id("rev_", rule, "revision_id")
    with pytest.raises(ValueError, match="SUPPORT"):
        repo.put_rule(rule, None)
    with pytest.raises(ValueError, match="REVISION"):
        repo.put_rule(bundle["rules"][0], "rev_missing")
    with pytest.raises(ValueError, match="NOT_FOUND"):
        repo.review("evidence", "ev_missing", None, "verified", "analyst", "reason")
    with pytest.raises(ValueError, match="MANIFEST"):
        repo.publish_snapshot("bad", run["run_id"], {"unknown": []})
    with pytest.raises(ValueError, match="PUBLISHABLE"):
        repo.publish_snapshot("bad", run["run_id"], {})
    db.execute(
        "UPDATE runs SET state='partial',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    with pytest.raises(ValueError, match="PUBLISHABLE"):
        repo.publish_snapshot("bad", run["run_id"], {}, True)
    with pytest.raises(ValueError, match="LINEAGE"):
        repo.publish_snapshot("bad", run["run_id"], {"evidence": ["ev_missing"]})
    assert db.execute("SELECT count(*) FROM snapshots WHERE snapshot_id='bad'").fetchone()[0] == 0


def test_reconnected_repository_preserves_published_history(db):
    repo, bundle = seed(db)
    run = bundle["runs"][0]
    eid = bundle["evidence"][0]["evidence_id"]
    review = repo.review("evidence", eid, None, "verified", "analyst", "original observation")
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    with psycopg.connect(os.environ["LAIP_TEST_DATABASE_URL"], autocommit=True) as fresh:
        fresh.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        restarted = Repository(fresh, run["system_namespace"])
        for record in bundle["evidence"]:
            restarted.put_evidence(record)
        new_review = restarted.review(
            "evidence", eid, review, "pending_review", "analyst", "new observation review"
        )
        assert new_review != review
        assert fresh.execute("SELECT count(*) FROM evidence").fetchone()[0] == len(
            bundle["evidence"]
        )
        assert fresh.execute("SELECT count(*) FROM reviews").fetchone()[0] == 2


def test_snapshot_rejects_missing_citation_closure(db):
    repo, bundle = seed(db)
    run = bundle["runs"][0]
    rule = bundle["rules"][0]
    repo.put_rule(rule, None)
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    with pytest.raises(ValueError, match="CLOSURE"):
        repo.publish_snapshot(
            "missing_citations", run["run_id"], {"revisions": [rule["revision_id"]]}
        )
    assert (
        db.execute(
            "SELECT count(*) FROM snapshots WHERE snapshot_id='missing_citations'"
        ).fetchone()[0]
        == 0
    )


def test_observation_citations_and_upstream_provenance_require_run_scope(db):
    repo, bundle = seed(db)
    run = bundle["runs"][0]
    system = copy.deepcopy(next(e for e in bundle["entities"] if e["identity"]["kind"] == "System"))
    system["evidence_ids"] = ["ev_" + "0" * 64]
    observation = repo.put_entity(system, run["run_id"])
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    with pytest.raises(ValueError, match="CLOSURE"):
        repo.publish_snapshot(
            "dangling_observation", run["run_id"], {"observations": [observation]}
        )
    evidence = copy.deepcopy(bundle["evidence"][0])
    evidence["upstream_record"] = {
        "provider": "rea",
        "upstream_id": None,
        "payload_artifact_id": "art_" + "0" * 64,
    }
    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
    with pytest.raises(ValueError, match="NOT_FOUND"):
        repo.put_evidence(evidence)
    other = Repository(db, "other")
    other.create_system()
    other.create_import("other_import", {})
    artifact = copy.deepcopy(bundle["artifacts"][0])
    artifact.update(import_id="other_import", source_member_id=None)
    artifact["artifact_id"] = "art_" + digest(
        {key: artifact[key] for key in ["import_id", "locator", "sha256"]}
    )
    other.put_artifact(artifact)
    evidence["upstream_record"]["payload_artifact_id"] = artifact["artifact_id"]
    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
    with pytest.raises(ValueError, match="NOT_FOUND"):
        repo.put_evidence(evidence)


def test_snapshot_waits_for_review_commit_before_watermark(db):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    repo, bundle = seed(db)
    run = bundle["runs"][0]
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    schema = db.execute("SELECT current_schema()").fetchone()[0]
    started = threading.Event()
    pid = []

    def snapshot():
        with psycopg.connect(os.environ["LAIP_TEST_DATABASE_URL"], autocommit=True) as fresh:
            fresh.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            pid.append(fresh.info.backend_pid)
            started.set()
            Repository(fresh, run["system_namespace"]).publish_snapshot(
                "watermark", run["run_id"], {}
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction():
            repo.review(
                "evidence",
                bundle["evidence"][0]["evidence_id"],
                None,
                "verified",
                "analyst",
                "review before snapshot",
            )
            future = pool.submit(snapshot)
            assert started.wait(3)
            with psycopg.connect(os.environ["LAIP_TEST_DATABASE_URL"], autocommit=True) as observer:
                deadline = time.monotonic() + 3
                waiting = False
                while time.monotonic() < deadline:
                    row = observer.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (pid[0],)
                    ).fetchone()
                    if row and row[0] == "transactionid":
                        waiting = True
                        break
                    if future.done():
                        break
                    time.sleep(0.01)
                assert waiting and not future.done()
        future.result(timeout=5)
    assert (
        db.execute(
            "SELECT review_sequence FROM snapshots WHERE snapshot_id='watermark'"
        ).fetchone()[0]
        == db.execute("SELECT max(sequence) FROM reviews").fetchone()[0]
    )
