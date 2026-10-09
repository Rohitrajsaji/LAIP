"""Admission remains occupied after a response timeout until work actually stops."""

import asyncio
import threading

import httpx
from test_runtime import FakeProbe, settings

from laip import analyst_api
from laip.api import create_app


def test_response_timeout_retains_four_operation_bound(tmp_path, monkeypatch):
    original_wait_for = asyncio.wait_for

    async def short_response_deadline(awaitable, timeout):
        return await original_wait_for(awaitable, 0.2 if timeout == 35 else timeout)

    monkeypatch.setattr(analyst_api.asyncio, "wait_for", short_response_deadline)
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    class BlockingAnalyst:
        def workspace(self):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                assert release.wait(5)
                return {"namespace": "local:workspace", "ai_enabled": False}
            finally:
                with lock:
                    active -= 1

    configured = settings(tmp_path)
    app = create_app(configured, FakeProbe(), analyst_factory=BlockingAnalyst)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + configured.service_token.get_secret_value()},
        ) as client:
            try:
                responses = await asyncio.gather(
                    *(client.get("/api/v1/workspace") for _ in range(4))
                )
                assert [response.status_code for response in responses] == [503] * 4
                with lock:
                    assert active == 4
                fifth = await client.get("/api/v1/workspace")
                assert fifth.status_code == 429
                assert maximum_active == 4
            finally:
                release.set()

            # Completion callbacks run on this loop and return the occupied admission slots.
            async def await_completion():
                while True:
                    with lock:
                        completed = active == 0
                    if completed:
                        await asyncio.sleep(0)
                        return
                    await asyncio.sleep(0.001)

            await original_wait_for(await_completion(), 2)
            assert (await client.get("/api/v1/workspace")).status_code == 200

    async def with_lifespan():
        async with app.router.lifespan_context(app):
            await scenario()

    asyncio.run(with_lifespan())
