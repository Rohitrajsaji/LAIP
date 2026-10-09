import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from laip import healthcheck, runtime, worker
from laip.config import Settings
from laip.runtime import DependencyProbe, PostgresRuntimeRepository, ProbeResult


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql://laip:pass@database/laip",
        service_token="a" * 32,
        artifact_root=tmp_path,
    )


@pytest.mark.parametrize("table_available", [False, True])
def test_worker_recovers_and_executes_registered_jobs(
    tmp_path: Path, table_available: bool
) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        probe = MagicMock()
        probe.check = AsyncMock(return_value=ProbeResult(database=True, storage=True))
        repository = MagicMock()

        async def available() -> bool:
            stop.set()
            return table_available

        repository.queue_available = available
        repository.recover = AsyncMock(return_value=0)
        repository.execute_one = AsyncMock(return_value=False)
        await worker.run_worker(settings(tmp_path), stop, probe, repository)

    asyncio.run(scenario())
    data = json.loads((tmp_path / worker.HEARTBEAT).read_text())
    assert data["status"] == ("idle" if table_available else "waiting_for_schema")
    assert data["observed_at"] <= time.time()
    assert (tmp_path / worker.HEARTBEAT).stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".heartbeat-*"))


def test_database_probe_and_queue_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    cursor = AsyncMock()
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = list(runtime.migration_checksums().items())
    connection.execute.return_value = cursor
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(runtime.psycopg.AsyncConnection, "connect", connect)
    config = settings(tmp_path)
    assert asyncio.run(DependencyProbe(config).database_ready())
    assert asyncio.run(PostgresRuntimeRepository(config).queue_available())
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert "SELECT 1" in statements
    assert "SELECT to_regclass('public.jobs') IS NOT NULL" in statements
    assert all(not statement.startswith(("CREATE", "INSERT", "UPDATE")) for statement in statements)


def test_worker_health_rejects_stale_missing_and_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        DependencyProbe, "check", AsyncMock(return_value=ProbeResult(database=True, storage=True))
    )
    config = settings(tmp_path)
    assert not asyncio.run(healthcheck.check_worker(config))
    for status in ("waiting_for_schema", "idle", "busy", "processed_job"):
        worker.publish_heartbeat(tmp_path, status)
        assert asyncio.run(healthcheck.check_worker(config))
    heartbeat = tmp_path / worker.HEARTBEAT
    data = json.loads(heartbeat.read_text())
    data["observed_at"] = time.time() - 200
    heartbeat.write_text(json.dumps(data))
    assert not asyncio.run(healthcheck.check_worker(config))
    heartbeat.write_text("malformed")
    assert not asyncio.run(healthcheck.check_worker(config))
    monkeypatch.setattr(
        DependencyProbe, "check", AsyncMock(return_value=ProbeResult(database=False, storage=True))
    )
    assert not asyncio.run(healthcheck.check_worker(config))


def test_api_health_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = settings(tmp_path)
    response = MagicMock()
    response.__enter__.return_value.status = 200
    urlopen = MagicMock(return_value=response)
    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", urlopen)
    assert healthcheck.check_api(config)
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8000/api/v1/health/ready"
    assert request.get_header("Authorization") == "Bearer " + "a" * 32
    urlopen.side_effect = OSError("private unavailable")
    assert not healthcheck.check_api(config)
    monkeypatch.setattr(healthcheck, "Settings", lambda: config)
    monkeypatch.setattr(healthcheck.sys, "argv", ["healthcheck", "invalid"])
    assert healthcheck.main() == 1


def test_storage_unavailable(tmp_path: Path) -> None:
    config = settings(tmp_path)
    config.artifact_root = tmp_path / "missing"
    assert not DependencyProbe(config).storage_ready()


def test_schema_mismatch_cannot_report_database_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    cursor = AsyncMock()
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [("unknown.sql", "invalid")]
    connection.execute.return_value = cursor
    monkeypatch.setattr(
        runtime.psycopg.AsyncConnection, "connect", AsyncMock(return_value=connection)
    )
    assert not asyncio.run(DependencyProbe(settings(tmp_path)).database_ready())


def test_worker_main_installs_shutdown_signals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = settings(tmp_path)
    run = AsyncMock()
    loop = MagicMock()
    monkeypatch.setattr(worker, "Settings", lambda: config)
    monkeypatch.setattr(worker, "run_worker", run)
    monkeypatch.setattr(worker.asyncio, "get_running_loop", lambda: loop)
    asyncio.run(worker.main())
    assert loop.add_signal_handler.call_count == 2
    run.assert_awaited_once()


def test_worker_dependency_error_is_sanitized_and_waits_for_stop(tmp_path: Path) -> None:
    async def scenario():
        stop = asyncio.Event()
        probe = MagicMock()

        async def fail():
            stop.set()
            raise OSError("private dependency")

        probe.check = fail
        await worker.run_worker(settings(tmp_path), stop, probe, MagicMock())

    asyncio.run(scenario())
    assert not (tmp_path / worker.HEARTBEAT).exists()
