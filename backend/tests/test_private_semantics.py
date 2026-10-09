"""Separate synthetic private HTTP + Docker pgvector integration; no external model."""

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_retrieval import db as db_fixture
from test_retrieval import published

from laip.context import build_context
from laip.private_ai import PrivateAIClient, PrivateAILimits, PrivateAIProfile
from laip.retrieval import RetrievalService
from laip.semantic import hybrid_query, semantic_query
from laip.vectors import VectorRepository

db = db_fixture


@pytest.fixture
def endpoint(request):
    mode = getattr(request, "param", "normal")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            response = {key: request[key] for key in ("model_id", "model_sha256", "policy_version")}
            if request["operation"] == "embedding":
                response["vectors"] = [
                    [1.0, 0.0, 0.0] if "customer" in text.lower() else [0.0, 1.0, 0.0]
                    for text in request["texts"]
                ]
            else:
                response["references"] = [
                    dict(
                        fact_id=key,
                        fact_sha256=value["fact_sha256"],
                        evidence_ids=value["fact"]["evidence_ids"],
                    )
                    for key, value in request["facts"].items()
                ]
            raw = json.dumps(response).encode()
            if mode == "slow":
                time.sleep(0.15)
            self.send_response(302 if mode == "redirect" else 200)
            if mode == "redirect":
                self.send_header("Location", "http://127.0.0.1:1/forbidden")
            self.send_header(
                "Content-Type", "text/html" if mode == "wrong-content" else "application/json"
            )
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/model"
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_private_semantic_fixture_real_http_pgvector_and_exact_context(db, endpoint):
    repo, bundle = published(db)
    evidence_id = bundle["evidence"][0]["evidence_id"]
    for chunk_id, text in [("customer", "Customer validation"), ("shipping", "Shipping")]:
        repo.put_chunk(
            chunk_id, "snap_retrieval", text, [evidence_id], "test-policy", "fixture", "none"
        )
    profile = PrivateAIProfile(
        enabled=True,
        approved=True,
        endpoint=endpoint,
        approved_endpoints=(endpoint,),
        model_id="private-fixture",
        model_sha256="c" * 64,
        policy_version="test-policy",
        tokenizer_id="fixture-codepoints-v1",
        model_window=100000,
        dimension=3,
    )
    client = PrivateAIClient(profile, tokenizer=len, tokenizer_id=profile.tokenizer_id)
    vectors = VectorRepository(db, repo.namespace)
    vectors.install(
        profile.model_id, profile.model_sha256, 3, profile.policy_version, approved=True
    )
    for chunk_id, text in [("customer", "Customer validation"), ("shipping", "Shipping")]:
        vectors.put(profile.model_id, chunk_id, client.embed([text])[0])
    retrieval = RetrievalService(repo, "snap_retrieval")
    result = semantic_query(retrieval, client, vectors, "customer", limit=1)
    assert [r["id"] for r in result["records"] if r["type"] == "chunk"] == ["customer"]
    combined = hybrid_query(retrieval, client, vectors, "customer", limit=1)
    assert len([r for r in combined["records"] if r["type"] == "chunk"]) == 1
    context = build_context(
        result,
        snapshot_id=retrieval.snapshot_id,
        context_id="semantic_fixture",
        level="evidence",
        entity_ids=[],
        max_context_tokens=16384,
        tokenizer_id=profile.tokenizer_id,
        token_counter=len,
    )
    assert context["budget"]["used_context_tokens"] + 1024 <= 16384
    assert context["semantic_mode"] == "private_configured"
    selected = next(c for c in context["chunks"] if c["chunk_id"] == "customer")
    facts = {
        "source_quote": dict(
            chunk_id=selected["chunk_id"],
            evidence_ids=selected["evidence_ids"],
            classification=selected["classification"],
            value=selected["text"],
        )
    }
    explanation = PrivateAIClient(
        replace(profile, dimension=None), tokenizer=len, tokenizer_id=profile.tokenizer_id
    ).explain(context, facts)
    assert explanation["classification"] == "inferred"
    assert explanation["review_status"] == "pending_review"
    facts["source_quote"]["value"] = "Unsupported runtime assertion with genuine citations"
    with pytest.raises(ValueError, match="INVALID_FACT_SCOPE"):
        PrivateAIClient(
            replace(profile, dimension=None), tokenizer=len, tokenizer_id=profile.tokenizer_id
        ).explain(context, facts)


@pytest.mark.parametrize("endpoint", ["redirect", "wrong-content", "slow"], indirect=True)
def test_real_transport_rejects_redirect_types_and_timeouts(endpoint):
    profile = PrivateAIProfile(
        enabled=True,
        approved=True,
        endpoint=endpoint,
        approved_endpoints=(endpoint,),
        model_id="private-fixture",
        model_sha256="c" * 64,
        policy_version="test-policy",
        tokenizer_id="fixture-codepoints-v1",
        model_window=100000,
        dimension=3,
    )
    client = PrivateAIClient(
        profile,
        tokenizer=len,
        tokenizer_id=profile.tokenizer_id,
        limits=PrivateAILimits(timeout_seconds=0.05),
    )
    with pytest.raises(ValueError, match="PRIVATE_MODEL_UNAVAILABLE"):
        client.embed(["fixture"])
