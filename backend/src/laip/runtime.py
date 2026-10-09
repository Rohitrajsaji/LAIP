import asyncio
import os
import tempfile
from typing import Protocol

import psycopg
from pydantic import BaseModel, ConfigDict

from laip.config import Settings
from laip.jobs import JobRepository
from laip.migrations import migration_checksums


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    database: bool
    storage: bool

    @property
    def ready(self) -> bool:
        return self.database and self.storage


class ReadinessProbe(Protocol):
    async def check(self) -> ProbeResult: ...


class RuntimeRepository(Protocol):
    async def queue_available(self) -> bool: ...

    async def recover(self) -> int: ...

    async def execute_one(self) -> bool: ...


class PostgresRuntimeRepository:
    """Inspect the durable queue seam; step 4 owns migrations and job claiming."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def queue_available(self) -> bool:
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url.get_secret_value(), connect_timeout=3
        ) as conn:
            await conn.execute("SET statement_timeout = '3000ms'")
            cursor = await conn.execute("SELECT to_regclass('public.jobs') IS NOT NULL")
            row = await cursor.fetchone()
            return bool(row and row[0])

    async def recover(self) -> int:
        def recover_sync() -> int:
            with psycopg.connect(
                self.settings.database_url.get_secret_value(), autocommit=True, connect_timeout=3
            ) as conn:
                conn.execute("SET statement_timeout='3000ms'")
                return JobRepository(conn).recover()

        return await asyncio.to_thread(recover_sync)

    async def execute_one(self) -> bool:
        from laip.analyst_jobs import AnalystJobs

        def execute_sync() -> bool:
            with psycopg.connect(
                self.settings.database_url.get_secret_value(), autocommit=True, connect_timeout=3
            ) as conn:
                conn.execute("SET statement_timeout='30000ms'")
                return AnalystJobs(conn, self.settings).execute_one()

        return await asyncio.to_thread(execute_sync)


class DependencyProbe:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def database_ready(self) -> bool:
        try:
            async with await psycopg.AsyncConnection.connect(
                self.settings.database_url.get_secret_value(), connect_timeout=3
            ) as conn:
                await conn.execute("SET statement_timeout = '3000ms'")
                cursor = await conn.execute("SELECT 1")
                row = await cursor.fetchone()
                cursor = await conn.execute("SELECT version,checksum FROM schema_migrations")
                versions: dict[str, str] = dict(await cursor.fetchall())
                return bool(row and row[0] == 1 and versions == migration_checksums())
        except (psycopg.Error, OSError):
            return False

    def storage_ready(self) -> bool:
        try:
            root = self.settings.artifact_root
            # Deployment owns root creation/permissions; fail on missing or inaccessible storage.
            with tempfile.NamedTemporaryFile(prefix=".probe-", dir=root) as stream:
                stream.write(b"laip-readiness")
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                return stream.read() == b"laip-readiness"
        except OSError:
            return False

    async def check(self) -> ProbeResult:
        database, storage = await asyncio.gather(
            self.database_ready(), asyncio.to_thread(self.storage_ready)
        )
        return ProbeResult(database=database, storage=storage)
