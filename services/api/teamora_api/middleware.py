from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

import structlog.contextvars
from fastapi import Request, Response
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from teamora_api.config import Settings
from teamora_api.errors import ErrorBody, ErrorEnvelope
from teamora_api.security import CSRF_COOKIE

REQUEST_COUNT = Counter("teamora_api_http_requests_total", "HTTP requests", ["method", "route", "status"])
REQUEST_LATENCY = Histogram(
    "teamora_api_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get("x-request-id", "")
        if not correlation_id or len(correlation_id) > 80:
            correlation_id = secrets.token_hex(16)
        request.state.correlation_id = correlation_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        REQUEST_COUNT.labels(request.method, route_path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, route_path).observe(time.perf_counter() - started)
        response.headers["X-Request-ID"] = correlation_id
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/team/invitations/accept",
        "/api/v1/webhooks/openai",
        "/api/v1/webhooks/telephony",
    }

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get("x-csrf-token")
        origin = request.headers.get("origin")
        allowed = set(request.app.state.settings.cors_origin_list)
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return self._error(request, "csrf_invalid", "CSRF token is missing or invalid")
        if origin and origin not in allowed:
            return self._error(request, "origin_not_allowed", "Request origin is not allowed")
        return await call_next(request)

    @staticmethod
    def _error(request: Request, code: str, message: str) -> Response:
        body = ErrorEnvelope(
            error=ErrorBody(
                code=code,
                message=message,
                correlation_id=getattr(request.state, "correlation_id", "unknown"),
            )
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content=body.model_dump(mode="json"))


class LocalRateLimitMiddleware(BaseHTTPMiddleware):
    """Single-process development guard; production deployment must use the Redis limiter."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = settings.rate_limit_requests_per_minute
        self.buckets: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        client = request.client.host if request.client else "unknown"
        bucket = self.buckets[client]
        cutoff = time.monotonic() - 60
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            body = ErrorEnvelope(
                error=ErrorBody(
                    code="rate_limited",
                    message="Too many requests",
                    correlation_id=getattr(request.state, "correlation_id", "unknown"),
                )
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=429, content=body.model_dump(mode="json"), headers={"Retry-After": "60"}
            )
        bucket.append(time.monotonic())
        return await call_next(request)
