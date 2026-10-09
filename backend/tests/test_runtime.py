import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from laip.api import create_app
from laip.config import Settings
from laip.runtime import DependencyProbe, ProbeResult

TOKEN = "a" * 32


def settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(
        database_url="postgresql://laip:pass@database/laip",
        service_token=TOKEN,
        artifact_root=tmp_path,
        **kwargs,
    )


class FakeProbe:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def check(self) -> ProbeResult:
        return ProbeResult(database=self.ready, storage=True)


def test_private_health_requires_token_and_sanitizes_failure(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), FakeProbe(False))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/health/live").json() == {"status": "ok"}
        assert client.get("/api/v1/health/ready").status_code == 401
        assert (
            client.get(
                "/api/v1/health/ready", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        result = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {TOKEN}"})
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert "postgresql" not in result.text


def test_ready_and_host_boundary(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), FakeProbe()), base_url="http://api") as client:
        response = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert response.json()["data"]["persistence"] == "ready"
        assert (
            client.get("/api/v1/health/live", headers={"Host": "evil.example"}).status_code == 400
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ai_enabled": True},
        {"embeddings_enabled": True},
        {"ai_endpoint": "https://example.com"},
        {"database_url": "sqlite:///tmp.db"},
        {"service_token": "short"},
    ],
)
def test_config_fails_closed(tmp_path: Path, kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "database_url": "postgresql://laip:pass@database/laip",
        "service_token": TOKEN,
        "artifact_root": tmp_path,
    }
    values.update(kwargs)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_secret_file(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    config = Settings(
        database_url="postgresql://laip:pass@database/laip",
        service_token_file=token,
        artifact_root=tmp_path,
    )
    assert config.service_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(config)


def test_database_secret_file_and_conflicts(tmp_path: Path) -> None:
    db_file = tmp_path / "database_url"
    db_file.write_text("postgresql://laip:pass@database/laip\n")
    config = Settings(database_url_file=db_file, service_token=TOKEN, artifact_root=tmp_path)
    assert config.database_url.get_secret_value() == "postgresql://laip:pass@database/laip"
    assert "pass@" not in repr(config)
    with pytest.raises(ValidationError):
        Settings(
            database_url_file=db_file,
            database_url="postgresql://other/db",
            service_token=TOKEN,
            artifact_root=tmp_path,
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url_file=tmp_path / "missing", service_token=TOKEN, artifact_root=tmp_path
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url_file=db_file,
            service_token_file=tmp_path / "missing",
            artifact_root=tmp_path,
        )
    with pytest.raises(ValidationError):
        Settings(
            database_url_file=db_file,
            service_token_file=db_file,
            service_token=TOKEN,
            artifact_root=tmp_path,
        )


@pytest.mark.parametrize(
    "kwargs", [{"artifact_root": "relative"}, {"allowed_hosts": []}, {"allowed_hosts": ["*"]}]
)
def test_invalid_local_boundaries(tmp_path: Path, kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "database_url": "postgresql://laip:pass@database/laip",
        "service_token": TOKEN,
        "artifact_root": tmp_path,
    }
    values.update(kwargs)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_real_dependency_failure_and_storage(tmp_path: Path) -> None:
    config = Settings(
        database_url="postgresql://laip:pass@127.0.0.1:1/laip",
        service_token=TOKEN,
        artifact_root=tmp_path,
    )
    result = asyncio.run(DependencyProbe(config).check())
    assert not result.database
    assert result.storage
    assert not list(tmp_path.glob(".probe-*"))
