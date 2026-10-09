import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

import psycopg

from laip.config import Settings
from laip.runtime import (
    DependencyProbe,
    PostgresRuntimeRepository,
    ReadinessProbe,
    RuntimeRepository,
)

HEARTBEAT = ".worker-heartbeat.json"


def publish_heartbeat(root: Path, status: str) -> None:
    temporary = root / f".heartbeat-{os.getpid()}"
    temporary.write_text(
        json.dumps(
            {
                "observed_at": time.time(),
                "pid": os.getpid(),
                "status": status,
                "schema_version": "0.1.0",
            }
        )
    )
    temporary.chmod(0o600)
    temporary.replace(root / HEARTBEAT)


async def run_worker(
    settings: Settings, stop: asyncio.Event, probe: ReadinessProbe, repository: RuntimeRepository
) -> None:
    log = logging.getLogger("laip.worker")
    while not stop.is_set():
        status = "dependency_unavailable"
        try:
            if (await probe.check()).ready:
                # Recover leases before claiming durable offline analyst work.
                status = (
                    "awaiting_handlers"
                    if await repository.queue_available()
                    else "waiting_for_schema"
                )
                if status == "awaiting_handlers":
                    await repository.recover()
                    execute = getattr(repository, "execute_one", None)
                    if execute is not None:
                        task = asyncio.create_task(execute())
                        while not task.done():
                            publish_heartbeat(settings.artifact_root, "busy")
                            await asyncio.wait({task}, timeout=5)
                        status = "processed_job" if await task else "idle"
            publish_heartbeat(settings.artifact_root, status)
        except (psycopg.Error, OSError):
            log.warning("Worker dependencies unavailable")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            continue


async def main() -> None:
    settings = Settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(settings, stop, DependencyProbe(settings), PostgresRuntimeRepository(settings))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
