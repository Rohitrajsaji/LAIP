"""Authenticated analyst operations; all source and namespace authority is server-side."""

import asyncio
from collections.abc import Callable, Iterator
from secrets import compare_digest
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from laip.analyst_fixture import fixture_payload
from laip.analyst_service import AnalystService
from laip.canonical import loads
from laip.read_service import error_envelope, safe_error


def install_analyst_routes(
    application: FastAPI, factory: Callable[[], AnalystService] | None
) -> None:
    capacity = asyncio.Semaphore(4)

    def fail(status: int, code: str) -> JSONResponse:
        return JSONResponse(status_code=status, content=error_envelope(code))

    async def dispatch(request: Request, operation: str, ident: str | None = None) -> Response:
        settings = application.state.settings
        expected = "Bearer " + settings.service_token.get_secret_value()
        if not compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
            return fail(401, "UNAUTHENTICATED")
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.allowed_read_origins:
            return fail(403, "FORBIDDEN_ORIGIN")
        arguments: dict[str, Any] = {}
        if request.method == "POST":
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return fail(415, "UNSUPPORTED_CONTENT_TYPE")
            if request.query_params:
                return fail(400, "INVALID_REQUEST")
            maximum = 12 * 1024 * 1024 if operation == "accept_import" else 1024 * 1024
            raw = bytearray()
            async for part in request.stream():
                if len(raw) + len(part) > maximum:
                    return fail(413, "RESOURCE_LIMIT")
                raw.extend(part)
            try:
                arguments = loads(raw.decode("utf-8"))
                if not isinstance(arguments, dict):
                    raise ValueError("INVALID_REQUEST")
            except (ValueError, UnicodeError, RecursionError):
                return fail(400, "INVALID_REQUEST")
        else:
            arguments = dict(request.query_params)
            allowed = (
                {"snapshot_id", "schema_version"}
                if operation in {"inventory", "dependencies", "rules"}
                else set()
            )
            if operation == "source":
                allowed = {"snapshot_id", "start_line", "end_line", "schema_version"}
            if (
                len(arguments) != len(request.query_params.multi_items())
                or set(arguments) - allowed
            ):
                return fail(400, "INVALID_REQUEST")
            if allowed and not arguments.get("snapshot_id"):
                return fail(400, "INVALID_REQUEST")
        if ident is not None and (len(ident) > 256 or not ident):
            return fail(400, "INVALID_REQUEST")

        def call(service: AnalystService) -> Any:
            if operation == "fixture":
                return fixture_payload(settings.analyst_namespace)
            if operation in {"workspace"}:
                return service.workspace()
            if operation in {"inventory", "dependencies", "rules"}:
                return getattr(service, operation)(
                    arguments["snapshot_id"], arguments.get("schema_version", "0.1.0")
                )
            if operation == "source":
                assert ident is not None
                return service.source(
                    ident,
                    arguments["snapshot_id"],
                    int(arguments.get("start_line", 1)),
                    int(arguments.get("end_line", 80)),
                    schema_version=arguments.get("schema_version", "0.1.0"),
                )
            if operation in {"cancel", "correct"}:
                return getattr(service, operation)(ident, arguments)
            if operation in {"export", "download"}:
                return getattr(service, operation)(ident)
            return getattr(service, operation)(arguments)

        def execute() -> Any:
            if factory:
                return call(factory())
            with psycopg.connect(
                settings.database_url.get_secret_value(), autocommit=True, connect_timeout=3
            ) as connection:
                connection.execute("SET statement_timeout='30000ms'")
                return call(AnalystService(connection, settings))

        try:
            await asyncio.wait_for(capacity.acquire(), timeout=0.05)
        except TimeoutError:
            return fail(429, "READ_CAPACITY_EXCEEDED")
        task = asyncio.create_task(asyncio.to_thread(execute))
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=35)
            if operation == "download":
                stream, length = result

                def chunks() -> Iterator[bytes]:
                    try:
                        while part := stream.read(65536):
                            yield part
                    finally:
                        stream.close()

                return StreamingResponse(
                    chunks(),
                    media_type="application/zip",
                    headers={
                        "Content-Length": str(length),
                        "Content-Disposition": 'attachment; filename="laip-knowledge.zip"',
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            response = {
                "schema_version": arguments.get("schema_version", "0.1.0"),
                "request_id": str(uuid4()),
                "data": result,
            }
            from laip.canonical import canonical

            if len(canonical(response)) > 12 * 1024 * 1024:
                return fail(413, "RESOURCE_LIMIT")
            return JSONResponse(content=response, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            if isinstance(exc, ValueError) and "STALE" in str(exc):
                return fail(409, "STALE_REVISION")
            status, code = safe_error(exc)
            return fail(status, code)
        finally:
            if task.done():
                capacity.release()
            else:
                # A timed-out/cancelled waiter cannot free admission while its thread still runs.
                def finished(completed: asyncio.Task[Any]) -> None:
                    try:
                        late_result = completed.result()
                        if operation == "download":
                            late_result[0].close()
                    except Exception:
                        pass
                    finally:
                        capacity.release()

                task.add_done_callback(finished)

    @application.get("/api/v1/workspace")
    async def workspace(request: Request) -> Response:
        return await dispatch(request, "workspace")

    @application.get("/api/v1/fixtures/analyst")
    async def fixture(request: Request) -> Response:
        return await dispatch(request, "fixture")

    @application.get("/api/v1/inventory")
    async def inventory(request: Request) -> Response:
        return await dispatch(request, "inventory")

    @application.get("/api/v1/dependencies")
    async def dependencies(request: Request) -> Response:
        return await dispatch(request, "dependencies")

    @application.get("/api/v1/rules")
    async def rules(request: Request) -> Response:
        return await dispatch(request, "rules")

    @application.get("/api/v1/artifacts/{artifact_id}/source")
    async def source(artifact_id: str, request: Request) -> Response:
        return await dispatch(request, "source", artifact_id)

    @application.post("/api/v1/imports")
    async def imports(request: Request) -> Response:
        return await dispatch(request, "accept_import")

    @application.post("/api/v1/runs")
    async def runs(request: Request) -> Response:
        return await dispatch(request, "queue_analysis")

    @application.post("/api/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str, request: Request) -> Response:
        return await dispatch(request, "cancel", run_id)

    @application.post("/api/v1/rules/{rule_id}/revisions")
    async def correct(rule_id: str, request: Request) -> Response:
        return await dispatch(request, "correct", rule_id)

    @application.post("/api/v1/exports")
    async def exports(request: Request) -> Response:
        return await dispatch(request, "queue_export")

    @application.get("/api/v1/exports/{export_id}")
    async def export(export_id: str, request: Request) -> Response:
        return await dispatch(request, "export", export_id)

    @application.get("/api/v1/exports/{export_id}/download")
    async def download(export_id: str, request: Request) -> Response:
        return await dispatch(request, "download", export_id)
