from types import SimpleNamespace

import pytest

from laip.semantic import hybrid_query, semantic_query


class Client:
    profile = SimpleNamespace(
        enabled=True,
        model_id="fixture",
        model_sha256="a" * 64,
        dimension=3,
        policy_version="fixture-v1",
    )

    def embed(self, texts):
        self.sent = texts
        return [[1.0, 0.0, 0.0]]


class Vectors:
    namespace = "fixture"

    def profile(self, model_id):
        return {"model_sha256": "a" * 64, "dimension": 3, "policy_version": "fixture-v1"}

    def nearest(self, model_id, values, *, snapshot_id, limit):
        assert snapshot_id == "snapshot"
        return ["chunk1"]


class Retrieval:
    namespace = "fixture"
    snapshot_id = "snapshot"
    run_id = "run"
    limits = SimpleNamespace(max_records=100)

    def chunks(self, ids, *, limit, cancelled=None):
        assert ids == ["chunk1"]
        return self.bundle()

    def bundle(self):
        return dict(
            snapshot_id=self.snapshot_id,
            system_namespace=self.namespace,
            run_id=self.run_id,
            entity_ids=[],
            records=[dict(type="chunk", id="chunk1", payload={"text": "source"})],
            boundaries=[],
            truncated=False,
            diagnostics=[],
        )

    def search(self, query, *, policy_version, limit, cancelled):
        assert policy_version == "fixture-v1"
        return self.bundle()


def test_semantic_query_uses_pinned_profile_and_selected_snapshot():
    client = Client()
    result = semantic_query(Retrieval(), client, Vectors(), "customer validation")
    assert result["semantic_mode"] == "private_configured"
    assert client.sent == ["customer validation"]


def test_semantic_disabled_mismatched_profile_and_namespace_fail_before_network():
    client = Client()
    client.profile = SimpleNamespace(**{**vars(client.profile), "enabled": False})
    with pytest.raises(ValueError, match="AI_DISABLED"):
        semantic_query(Retrieval(), client, Vectors(), "query")
    client.profile.enabled = True
    client.profile.model_sha256 = "b" * 64
    with pytest.raises(ValueError, match="VECTOR_PROFILE_MISMATCH"):
        semantic_query(Retrieval(), client, Vectors(), "query")
    client.profile.model_sha256 = "a" * 64
    vectors = Vectors()
    vectors.namespace = "other"
    with pytest.raises(ValueError, match="SEMANTIC_SCOPE_MISMATCH"):
        semantic_query(Retrieval(), client, vectors, "query")
    assert not hasattr(client, "sent")


def test_hybrid_deduplicates_shared_source_and_never_falls_back_when_disabled():
    result = hybrid_query(Retrieval(), Client(), Vectors(), "customer")
    assert len(result["records"]) == 1
    assert result["semantic_mode"] == "private_configured"
    client = Client()
    client.profile = SimpleNamespace(**{**vars(client.profile), "enabled": False})
    with pytest.raises(ValueError, match="AI_DISABLED"):
        hybrid_query(Retrieval(), client, Vectors(), "customer")


def test_hybrid_rejects_conflicting_projection_and_oversized_queries():
    class Conflicting(Retrieval):
        def search(self, *args, **kwargs):
            result = self.bundle()
            result["records"][0]["payload"]["text"] = "unsupported replacement"
            return result

    with pytest.raises(ValueError, match="RETRIEVAL_ID_COLLISION"):
        hybrid_query(Conflicting(), Client(), Vectors(), "customer")
    client = Client()
    with pytest.raises(ValueError, match="INVALID_SEMANTIC_QUERY"):
        hybrid_query(Retrieval(), client, Vectors(), "x" * 1001)
    assert not hasattr(client, "sent")
