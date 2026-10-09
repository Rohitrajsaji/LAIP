"""Optional private semantics over an explicitly installed snapshot/model basis."""

from collections.abc import Callable
from typing import Any

from laip.canonical import canonical
from laip.private_ai import PrivateAIClient
from laip.retrieval import RetrievalService
from laip.vectors import VectorRepository


def semantic_query(
    retrieval: RetrievalService,
    client: PrivateAIClient,
    vectors: VectorRepository,
    query: str,
    *,
    limit: int = 20,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """No fallback, installation or publication; nearest results remain interpretations."""
    if not client.profile.enabled:
        raise ValueError("AI_DISABLED")
    if retrieval.namespace != vectors.namespace:
        raise ValueError("SEMANTIC_SCOPE_MISMATCH")
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 4000
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("INVALID_SEMANTIC_QUERY")
    if cancelled and cancelled():
        raise ValueError("CANCELLED")
    profile = vectors.profile(client.profile.model_id)
    if profile != {
        "model_sha256": client.profile.model_sha256,
        "dimension": client.profile.dimension,
        "policy_version": client.profile.policy_version,
    }:
        raise ValueError("VECTOR_PROFILE_MISMATCH")
    values = client.embed([query])[0]
    if cancelled and cancelled():
        raise ValueError("CANCELLED")
    ids = vectors.nearest(
        client.profile.model_id, values, snapshot_id=retrieval.snapshot_id, limit=limit
    )
    result = retrieval.chunks(ids, limit=limit, cancelled=cancelled)
    result["semantic_mode"] = "private_configured"
    result["semantic_limitations"] = [
        "Similarity selects cited snapshot records; it does not establish claim certainty."
    ]
    return result


def hybrid_query(
    retrieval: RetrievalService,
    client: PrivateAIClient,
    vectors: VectorRepository,
    query: str,
    *,
    limit: int = 20,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Combine keyword and private semantic matches, retaining frozen evidence once."""
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("INVALID_SEMANTIC_QUERY")
    semantic = semantic_query(retrieval, client, vectors, query, limit=limit, cancelled=cancelled)
    keyword = retrieval.search(
        query, policy_version=client.profile.policy_version, limit=limit, cancelled=cancelled
    )
    records: dict[tuple[str, str], dict[str, Any]] = {}
    boundaries: dict[str, dict[str, Any]] = {}
    for bundle in (semantic, keyword):
        if (
            bundle["snapshot_id"] != retrieval.snapshot_id
            or bundle["system_namespace"] != retrieval.namespace
            or bundle["run_id"] != retrieval.run_id
        ):
            raise ValueError("SEMANTIC_SCOPE_MISMATCH")
        for record in bundle["records"]:
            key = (record["type"], record["id"])
            if key in records and canonical(records[key]["payload"]) != canonical(
                record["payload"]
            ):
                raise ValueError("RETRIEVAL_ID_COLLISION")
            records.setdefault(key, record)
        for record in bundle["boundaries"]:
            boundaries.setdefault(record["id"], record)
    if len(records) + len(boundaries) > retrieval.limits.max_records:
        raise ValueError("RETRIEVAL_RECORD_LIMIT")
    return {
        "snapshot_id": retrieval.snapshot_id,
        "system_namespace": retrieval.namespace,
        "run_id": retrieval.run_id,
        "entity_ids": sorted(set(semantic["entity_ids"]) | set(keyword["entity_ids"])),
        "records": list(records.values()),
        "boundaries": list(boundaries.values()),
        "truncated": semantic["truncated"] or keyword["truncated"],
        "diagnostics": semantic["diagnostics"] + keyword["diagnostics"],
        "semantic_mode": "private_configured",
        "semantic_limitations": semantic["semantic_limitations"],
    }
