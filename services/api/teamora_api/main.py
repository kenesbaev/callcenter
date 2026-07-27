from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from teamora_api.config import get_settings
from teamora_api.db import engine
from teamora_api.errors import ApiError, api_error_handler, validation_error_handler
from teamora_api.logging import configure_logging
from teamora_api.middleware import CorrelationMiddleware, CSRFMiddleware, LocalRateLimitMiddleware
from teamora_api.routers import (
    analytics,
    auth,
    calls,
    health,
    knowledge,
    operations,
    operators,
    simulator,
    tenants,
    webhooks,
)

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(
    title="K-Line API",
    version="0.1.0",
    description="Multi-tenant AI call-center control plane",
    docs_url="/api/docs" if settings.app_env != "production" else None,
    openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
    lifespan=lifespan,
)
app.state.settings = settings
app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key"],
)
app.add_middleware(CSRFMiddleware)
app.add_middleware(LocalRateLimitMiddleware, settings=settings)
app.add_middleware(CorrelationMiddleware)

api_v1_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_v1_prefix)
app.include_router(health.router, prefix=api_v1_prefix)
app.include_router(tenants.router, prefix=api_v1_prefix)
app.include_router(operators.router, prefix=api_v1_prefix)
app.include_router(knowledge.router, prefix=api_v1_prefix)
app.include_router(calls.router, prefix=api_v1_prefix)
app.include_router(simulator.router, prefix=api_v1_prefix)
app.include_router(analytics.router, prefix=api_v1_prefix)
app.include_router(operations.router, prefix=api_v1_prefix)
app.include_router(webhooks.router, prefix=api_v1_prefix)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
