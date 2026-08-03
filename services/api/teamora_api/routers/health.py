from __future__ import annotations

import asyncio
import json

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


async def _realtime_publisher_status(timeout_seconds: float) -> dict[str, object]:
    settings = get_settings()
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            raw_health = await redis.get("kline:realtime:publisher:health")
        parsed = json.loads(raw_health) if raw_health else None
        return parsed if isinstance(parsed, dict) else {"status": "unavailable"}
    except Exception:
        return {"status": "unavailable"}
    finally:
        await redis.aclose()


@router.get("/ready", response_model=None)
async def ready() -> dict[str, object] | JSONResponse:
    timeout_seconds = get_settings().dependency_health_timeout_seconds
    postgres, redis, publisher = await asyncio.gather(
        _postgres_status(timeout_seconds),
        _redis_status(timeout_seconds),
        _realtime_publisher_status(timeout_seconds),
    )
    checks = {"postgres": postgres, "redis": redis}
    if postgres == "unavailable":
        logger.warning("readiness_unavailable", checks=checks)
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    status = "ok" if redis == "ok" and publisher.get("status") == "ok" else "degraded"
    if status == "degraded":
        logger.warning("readiness_degraded", checks=checks)
    return {"status": status, "checks": checks, "realtime": {"publisher": publisher}}
