from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from teamora_api.config import get_settings
from teamora_api.db import SessionFactory
from teamora_api.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _postgres_status(timeout_seconds: float) -> str:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with SessionFactory() as session:
                await session.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "unavailable"


async def _redis_status(timeout_seconds: float) -> str:
    settings = get_settings()
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            return "ok" if await redis.ping() else "unavailable"
    except Exception:
        return "unavailable"
    finally:
        await redis.aclose()


@router.get("/ready", response_model=None)
async def ready() -> dict[str, object] | JSONResponse:
    timeout_seconds = get_settings().dependency_health_timeout_seconds
    postgres, redis = await asyncio.gather(
        _postgres_status(timeout_seconds),
        _redis_status(timeout_seconds),
    )
    checks = {"postgres": postgres, "redis": redis}
    if "unavailable" in checks.values():
        logger.warning("readiness_unavailable", checks=checks)
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    return {"status": "ok", "checks": checks}
