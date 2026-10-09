"""Authenticated private REST routes for shared deterministic snapshot reads."""

import asyncio
import threading
from collections.abc import Callable
from secrets import compare_digest
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from laip.canonical import loads
from laip.persistence import Repository
from laip.read_service import ReadService, error_envelope, safe_error

MAX_BODY = 1024 * 1024


def install_read_routes(
    application: FastAPI, read_factory: Callable[[], ReadService] | None
) -> None:
    capacity = asyncio.Semaphore(4)

    def fail(status: int, code: str) -> JSONResponse:
        return JSONResponse(status_code=status, content=error_envelope(code))

    async def dispatch(
        request: Request, operation: str, arguments: dict[str, Any] | None = None
    ) -> JSONResponse:
        settings = application.state.settings
        expected = "Bearer " + settings.service_token.get_secret_value()
        authorization = request.headers.get("authorization", "")
        if not compare_digest(authorization.encode(), expected.encode()):
            return fail(401, "UNAUTHENTICATED")
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.allowed_read_origins:
            return fail(403, "FORBIDDEN_ORIGIN")
        if arguments is None:
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return fail(415, "UNSUPPORTED_CONTENT_TYPE")
            body = bytearray()
            async for part in request.stream():
                if len(body) + len(part) > MAX_BODY:
                    return fail(413, "RESOURCE_LIMIT")
                body.extend(part)
            try:
                arguments = loads(body.decode("utf-8"))
                if not isinstance(arguments, dict):
                    return fail(400, "INVALID_REQUEST")
            except (ValueError, UnicodeError, RecursionError):
                return fail(400, "INVALID_REQUEST")
        cancelled = threading.Event()

        def execute() -> dict[str, Any]:
            if read_factory:
                return read_factory().call(operation, arguments, cancelled=cancelled.is_set)
            if not settings.read_namespace:
                raise RuntimeError("Read namespace unavailable")
            with psycopg.connect(
                settings.database_url.get_secret_value(), autocommit=True, connect_timeout=3
            ) as conn:
                conn.execute("SET default_transaction_read_only=on")
                conn.execute("SET statement_timeout='30000ms'")
                return ReadService(Repository(conn, settings.read_namespace)).call(
                    operation, arguments, cancelled=cancelled.is_set
                )

        try:
            # Admission is bounded; requests do not accumulate an unbounded DB connection queue.
            await asyncio.wait_for(capacity.acquire(), timeout=0.05)
        except TimeoutError:
            return fail(429, "READ_CAPACITY_EXCEEDED")
        try:
            result = await asyncio.wait_for(asyncio.to_thread(execute), timeout=35)
            return JSONResponse(content=result)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except TimeoutError:
            cancelled.set()
            return fail(413, "RESOURCE_LIMIT")
        except Exception as exc:
            status, code = safe_error(exc)
            return fail(status, code)
        finally:
            capacity.release()

    @application.get("/api/v1/entities/{entity_id}", response_model=None)
    async def entity(entity_id: str, request: Request) -> JSONResponse:
        arguments = dict(request.query_params)
        if len(arguments) != len(request.query_params.multi_items()):
            return fail(400, "INVALID_REQUEST")
        arguments.update(entity_id=entity_id)
        arguments.setdefault("schema_version", "0.1.0")
        return await dispatch(request, "entity", arguments)

    @application.get("/api/v1/evidence/{evidence_id}", response_model=None)
    async def evidence(evidence_id: str, request: Request) -> JSONResponse:
        arguments = dict(request.query_params)
        if len(arguments) != len(request.query_params.multi_items()):
            return fail(400, "INVALID_REQUEST")
        arguments.update(evidence_id=evidence_id)
        arguments.setdefault("schema_version", "0.1.0")
        return await dispatch(request, "evidence", arguments)

    @application.post("/api/v1/retrieval/query", response_model=None)
    async def search(request: Request) -> JSONResponse:
        return await dispatch(request, "search")

    @application.post("/api/v1/graph/query", response_model=None)
    async def graph(request: Request) -> JSONResponse:
        return await dispatch(request, "graph")

    @application.post("/api/v1/context/build", response_model=None)
    async def context(request: Request) -> JSONResponse:
        return await dispatch(request, "context")
