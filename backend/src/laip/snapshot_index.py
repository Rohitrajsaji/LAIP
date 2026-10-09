"""Deterministic, masked keyword chunks from a frozen snapshot's cited records."""

from laip.canonical import canonical, digest
from laip.masking import POLICY
from laip.persistence import Repository
from laip.public_projection import semantic_payload
from laip.retrieval import RetrievalService


def index_snapshot(repository: Repository, snapshot_id: str) -> None:
    service = RetrievalService(repository, snapshot_id)
    service.require_version("0.2.0")
    bundle = service.collect()
    for record in bundle["records"]:
        if record["type"] == "chunk":
            continue
        evidence_ids = record["evidence_ids"]
        if record["type"] == "evidence":
            evidence_ids = sorted(set([record["id"], *evidence_ids]))
        if not evidence_ids:
            continue
        text = canonical(semantic_payload(record["payload"])).decode("utf-8")
        if len(text) > 100000:
            raise ValueError("KEYWORD_RECORD_LIMIT")
        repository.put_chunk(
            "chunk_"
            + digest({"snapshot_id": snapshot_id, "type": record["type"], "id": record["id"]}),
            snapshot_id,
            text,
            evidence_ids,
            POLICY,
            service.snapshot_schema_version,
            "utf8-byte-upper-bound-v1",
        )
