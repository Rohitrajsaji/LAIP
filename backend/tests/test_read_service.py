import pytest
from jsonschema import Draft202012Validator
from test_persistence import db as db_fixture
from test_retrieval import published

from laip.masking import POLICY
from laip.read_service import ReadService, output_schema
from laip.retrieval import RetrievalLimits, RetrievalService

db = db_fixture


def test_collect_snapshot_closure_policy_and_limits(db):
    repo, bundle = published(db)
    eid = bundle["evidence"][0]["evidence_id"]
    repo.put_chunk("allowed", "snap_retrieval", "customer validation", [eid], POLICY, "v1", "none")
    repo.put_chunk("other", "snap_retrieval", "customer validation", [eid], "other", "v1", "none")
    result = RetrievalService(repo, "snap_retrieval").collect()
    assert {r["payload"]["entity_id"] for r in result["records"] if r["type"] == "entity"} == {
        e["entity_id"] for e in bundle["entities"]
    }
    assert {r["id"] for r in result["records"] if r["type"] == "chunk"} == {"allowed"}
    assert result["boundaries"]
    with pytest.raises(ValueError, match="LIMIT"):
        RetrievalService(repo, "snap_retrieval", limits=RetrievalLimits(max_records=1)).collect()


def test_shared_reads_validate_schemas_and_context(db):
    repo, bundle = published(db)
    service = ReadService(repo)
    entity = bundle["entities"][0]["entity_id"]
    evidence = bundle["evidence"][0]["evidence_id"]
    repo.put_chunk(
        "allowed",
        "snap_retrieval",
        "customer password=secret validation",
        [evidence],
        POLICY,
        "v1",
        "none",
    )
    args = {"schema_version": "0.1.0", "snapshot_id": "snap_retrieval"}
    cases = {
        "entity": {**args, "entity_id": entity},
        "evidence": {**args, "evidence_id": evidence},
        "graph": {**args, "root_entity_ids": [bundle["dependencies"][0]["from_entity_id"]]},
        "search": {**args, "query": "customer"},
        "context": {**args, "level": "system", "entity_ids": [], "max_context_tokens": 32768},
    }
    for op, request in cases.items():
        response = service.call(op, request)
        Draft202012Validator(output_schema(op)).validate(response)
        assert response["schema_version"] == "0.1.0"
        assert "password=secret" not in str(response)
    context = service.call("context", cases["context"])["data"]
    assert (
        context["budget"]["used_context_tokens"] + context["budget"]["reserved_output_tokens"]
        <= 32768
    )
    assert service.call("context", cases["context"])["data"] == context


def test_invalid_reads_unknown_namespace_and_missing_entity(db):
    repo, bundle = published(db)
    service = ReadService(repo)
    args = {
        "schema_version": "0.1.0",
        "snapshot_id": "snap_retrieval",
        "entity_id": bundle["entities"][0]["entity_id"],
    }
    for changed in (
        {"source_included": True},
        {"schema_version": "9"},
        {"snapshot_id": "other"},
        {"entity_id": "ent_" + "f" * 64},
    ):
        with pytest.raises(ValueError):
            service.call("entity", {**args, **changed})
    with pytest.raises(ValueError):
        service.call("execute", args)
    with pytest.raises(ValueError, match="CANCEL"):
        service.call("entity", args, cancelled=lambda: True)


def test_graph_relationships_unresolved_options_and_scoped_context(db):
    repo, bundle = published(db)
    read = ReadService(repo)
    root = bundle["dependencies"][0]["from_entity_id"]
    args = {"schema_version": "0.1.0", "snapshot_id": "snap_retrieval"}
    graph = read.call(
        "graph",
        {**args, "root_entity_ids": [root], "relationships": ["CALLS"], "include_unresolved": True},
    )["data"]
    dependencies = [r for r in graph["records"] if r["type"] == "dependency"]
    assert dependencies and all(r["payload"]["relationship"] == "CALLS" for r in dependencies)
    default = read.call("graph", {**args, "root_entity_ids": [root]})["data"]
    assert default["boundaries"]
    assert all(
        r["payload"]["resolution"] == "resolved"
        for r in default["records"]
        if r["type"] == "dependency"
    )
    entity = bundle["entities"][0]["entity_id"]
    context = read.call(
        "context", {**args, "level": "program", "entity_ids": [entity], "max_context_tokens": 32768}
    )["data"]
    assert context["entity_ids"] == [entity]
    assert context["chunks"]
    assert (
        read.call(
            "context",
            {
                **args,
                "level": "system",
                "entity_ids": [],
                "query": "absentword",
                "max_context_tokens": 32768,
            },
        )["data"]["chunks"]
        == []
    )
    with pytest.raises(ValueError):
        read.call("context", {**args, "level": "program", "entity_ids": []})
    with pytest.raises(ValueError):
        read.call(
            "context",
            {
                **args,
                "level": "system",
                "entity_ids": [],
                "max_context_tokens": 100,
                "reserved_output_tokens": 100,
            },
        )
