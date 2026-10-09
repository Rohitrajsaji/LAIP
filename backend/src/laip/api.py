from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from laip.analyst_api import install_analyst_routes
from laip.analyst_service import AnalystService
from laip.config import Settings
from laip.read_api import install_read_routes
from laip.read_service import ReadService
from laip.runtime import DependencyProbe, ReadinessProbe


def create_app(
    settings: Settings | None = None,
    probe: ReadinessProbe | None = None,
    *,
    read_factory: Callable[[], ReadService] | None = None,
    analyst_factory: Callable[[], AnalystService] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = settings if settings is not None else Settings()
        application.state.probe = probe or DependencyProbe(application.state.settings)
        yield

    application = FastAPI(
        title="LAIP local prototype",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts
        if settings
        else ["api", "localhost", "127.0.0.1", "[::1]"],
    )

    @application.get("/api/v1/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/health/ready", response_model=None)
    async def ready(authorization: Annotated[str | None, Header()] = None) -> JSONResponse:
        request_id = str(uuid4())
        expected = "Bearer " + application.state.settings.service_token.get_secret_value()
        if authorization is None or not compare_digest(authorization.encode(), expected.encode()):
            return JSONResponse(
                status_code=401,
                content={
                    "schema_version": "0.1.0",
                    "request_id": request_id,
                    "error": {
                        "code": "UNAUTHENTICATED",
                        "message": "Authentication required",
                        "retryable": False,
                        "details": {},
                    },
                },
            )
        result = await application.state.probe.check()
        if not result.ready:
            return JSONResponse(
                status_code=503,
                content={
                    "schema_version": "0.1.0",
                    "request_id": request_id,
                    "status": "unavailable",
                    "dependencies": result.model_dump(),
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": "Runtime is unavailable",
                        "retryable": True,
                        "details": {},
                    },
                },
            )
        return JSONResponse(
            content={
                "schema_version": "0.1.0",
                "request_id": request_id,
                "data": {
                    "status": "ready",
                    "dependencies": result.model_dump(),
                    "persistence": "ready",
                    "ai_enabled": application.state.settings.ai_enabled,
                    "embeddings_enabled": application.state.settings.embeddings_enabled,
                },
            }
        )

    install_read_routes(application, read_factory)
    install_analyst_routes(application, analyst_factory)
    return application


app = create_app()
