import pytest
from pydantic import ValidationError

from laip.config import Settings


def config(tmp_path, **values):
    return Settings(
        database_url="postgresql://fixture@localhost/test",
        service_token="x" * 32,
        artifact_root=tmp_path,
        **values,
    )


def contract(dimension=None):
    return dict(
        model_id="synthetic",
        model_sha256="a" * 64,
        policy_version="fixture-v1",
        tokenizer_id="fixture-byte-tokenizer",
        model_window=10000,
        dimension=dimension,
    )


def test_private_ai_defaults_and_operator_approval(tmp_path):
    settings = config(tmp_path)
    assert not settings.private_ai_config().llm.enabled
    assert not settings.private_ai_config().embedding.enabled
    with pytest.raises(ValidationError):
        config(
            tmp_path,
            ai_enabled=True,
            ai_endpoint="http://127.0.0.1:9999/llm",
            llm_model_contract=contract(),
        )
    approved = config(
        tmp_path,
        ai_enabled=True,
        ai_operator_approved=True,
        ai_endpoint="http://127.0.0.1:9999/llm",
        ai_approved_endpoints=["http://127.0.0.1:9999/llm"],
        llm_model_contract=contract(),
    )
    assert approved.private_ai_config().llm.enabled


def test_model_approval_does_not_allow_public_or_unlisted_endpoints(tmp_path):
    for endpoint, allowlist in [
        ("https://example.com/llm", ["https://example.com/llm"]),
        ("http://127.0.0.1:9999/llm", []),
    ]:
        with pytest.raises(ValidationError):
            config(
                tmp_path,
                ai_enabled=True,
                ai_operator_approved=True,
                ai_endpoint=endpoint,
                ai_approved_endpoints=allowlist,
                llm_model_contract=contract(),
            )


def test_embedding_configuration_and_private_secret_file(tmp_path):
    path = tmp_path / "synthetic-token"
    path.write_text("fixture-private-model-token")
    values = dict(
        embeddings_enabled=True,
        ai_operator_approved=True,
        embedding_endpoint="http://127.0.0.1:9999/embed",
        ai_approved_endpoints=["http://127.0.0.1:9999/embed"],
        embedding_model_contract=contract(3),
        private_ai_token_file=path,
    )
    settings = config(tmp_path, **values)
    assert settings.private_ai_config().embedding.dimension == 3
    assert "fixture-private-model-token" not in repr(settings)
    assert "fixture-private-model-token" not in repr(settings.private_ai_config())
    with pytest.raises(ValidationError):
        config(tmp_path, **{**values, "embedding_model_contract": contract()})
