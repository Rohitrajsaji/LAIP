import pytest
from test_persistence import db as db_fixture
from test_persistence import seed

from laip.retrieval import RetrievalLimits, RetrievalService

db = db_fixture


def published(db):
    repo, bundle = seed(db)
    repo.put_rule(bundle["rules"][0], None)
    run = bundle["runs"][0]
    db.execute(
        "UPDATE runs SET state='succeeded',completed_at=clock_timestamp() WHERE run_id=%s",
        (run["run_id"],),
    )
    manifest = {
        "observations": [
            r[0] for r in db.execute("SELECT observation_id FROM entity_observations").fetchall()
        ],
        "evidence": [e["evidence_id"] for e in bundle["evidence"]],
        "dependencies": [d["dependency_id"] for d in bundle["dependencies"]],
        "revisions": [r["revision_id"] for r in bundle["rules"]],
    }
    repo.publish_snapshot("snap_retrieval", run["run_id"], manifest)
    return repo, bundle


def test_exact_and_citation_closure(db):
    repo, bundle = published(db)
    service = RetrievalService(repo, "snap_retrieval")
    entity = bundle["entities"][0]
    result = service.exact(entity["entity_id"])
    assert result["snapshot_id"] == "snap_retrieval"
    assert any(r["type"] == "entity" and r["payload"] == entity for r in result["records"])
    ids = {r["id"] for r in result["records"] if r["type"] == "evidence"}
    assert set(entity["evidence_ids"]) <= ids


def test_snapshot_namespace_is_required(db):
    from laip.persistence import Repository

    repo, _ = published(db)
    with pytest.raises(ValueError):
        RetrievalService(Repository(db, "other"), "snap_retrieval")


def test_qualified_lookup_keyword_policy_and_direct_evidence(db):
    repo, bundle = published(db)
    service = RetrievalService(repo, "snap_retrieval")
    entity = bundle["entities"][0]
    result = service.exact(
        kind=entity["identity"]["kind"], qualified_identity=entity["identity"]["qualified_identity"]
    )
    assert result["records"]
    eid = bundle["evidence"][0]["evidence_id"]
    repo.put_chunk(
        "selected", "snap_retrieval", "customer validation", [eid], "policy-v1", "v1", "none"
    )
    repo.put_chunk(
        "excluded", "snap_retrieval", "customer validation", [eid], "other", "v1", "none"
    )
    hits = service.search("customer validation", policy_version="policy-v1")
    assert [r["id"] for r in hits["records"] if r["type"] == "chunk"] == ["selected"]
    direct = service.evidence(eid)
    assert direct["records"][0]["id"] == eid
    assert eid in direct["records"][0]["evidence_ids"]
    assert service.chunks(["selected"])["records"]
    with pytest.raises(ValueError, match="CHUNK_NOT_IN_SNAPSHOT"):
        service.chunks(["unknown"])
    with pytest.raises(ValueError, match="CITATION_NOT_IN_SNAPSHOT"):
        service.evidence("ev_" + "f" * 64)


def test_review_watermark_remains_frozen(db):
    repo, bundle = published(db)
    eid = bundle["evidence"][0]["evidence_id"]
    repo.review("evidence", eid, None, "verified", "fixture", "source checked")
    old = RetrievalService(repo, "snap_retrieval").evidence(eid)
    assert next(r for r in old["records"] if r["id"] == eid)["review_status"] == "pending_review"
    manifest = db.execute(
        "SELECT payload FROM snapshots WHERE snapshot_id='snap_retrieval'"
    ).fetchone()[0]
    repo.publish_snapshot("new_review", bundle["runs"][0]["run_id"], manifest)
    new = RetrievalService(repo, "new_review").evidence(eid)
    assert next(r for r in new["records"] if r["id"] == eid)["review_status"] == "verified"


def test_graph_preserves_dynamic_ambiguous_and_depth_boundaries(db):
    repo, bundle = published(db)
    source = bundle["dependencies"][0]["from_entity_id"]
    service = RetrievalService(repo, "snap_retrieval")
    result = service.graph(source)
    assert {r["payload"]["resolution"] for r in result["boundaries"]} >= {"dynamic", "ambiguous"}
    assert any(
        r["type"] == "entity"
        and r["payload"]["entity_id"] == bundle["dependencies"][0]["to_entity_id"]
        for r in result["records"]
    )
    limited = service.graph(source, depth=0)
    assert limited["truncated"]
    assert any(r["boundary_reason"] == "traversal_limit" for r in limited["boundaries"])
    assert service.graph(source, direction="both")["records"]
    with pytest.raises(ValueError, match="ENTITY_NOT_IN_SNAPSHOT"):
        service.graph("ent_" + "f" * 64)


def test_read_timeouts_restore_outer_transaction_and_cancel(db):
    repo, bundle = published(db)
    service = RetrievalService(repo, "snap_retrieval")
    with db.transaction():
        db.execute("SELECT set_config('statement_timeout','4000',true)")
        service.exact(bundle["entities"][0]["entity_id"])
        assert db.execute("SHOW statement_timeout").fetchone()[0] == "4s"
    with pytest.raises(ValueError, match="RESOURCE_LIMIT_OR_CANCELLED"):
        service.exact(bundle["entities"][0]["entity_id"], cancelled=lambda: True)
    bounded = RetrievalService(repo, "snap_retrieval", limits=RetrievalLimits(timeout_seconds=1))
    with pytest.raises(ValueError, match="RESOURCE_LIMIT_OR_CANCELLED"):
        bounded._query(bounded._guard(None), "SELECT pg_sleep(2)", ())
