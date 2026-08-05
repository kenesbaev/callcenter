from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from minio import Minio
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


async def _scheduler_status(timeout_seconds: float) -> dict[str, object]:
    settings = get_settings()
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            raw_health = await redis.get("kline:background-scheduler:health")
        parsed = json.loads(raw_health) if raw_health else None
        return parsed if isinstance(parsed, dict) else {"status": "unavailable"}
    except Exception:
        return {"status": "unavailable"}
    finally:
        await redis.aclose()


async def _minio_status(timeout_seconds: float) -> str:
    settings = get_settings()
    parsed = urlparse(settings.minio_endpoint)
    endpoint = parsed.netloc or parsed.path
    password = settings.minio_root_password.get_secret_value()
    if not endpoint or not settings.minio_root_user or not password:
        return "unavailable"
    client = Minio(
        endpoint,
        access_key=settings.minio_root_user,
        secret_key=password,
        secure=parsed.scheme == "https",
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            exists = await asyncio.to_thread(client.bucket_exists, settings.minio_bucket)
        return "ok" if exists else "unavailable"
    except Exception:
        return "unavailable"


@router.get("/ready", response_model=None)
async def ready() -> dict[str, object] | JSONResponse:
    timeout_seconds = get_settings().dependency_health_timeout_seconds
    postgres, redis, publisher, scheduler, minio = await asyncio.gather(
        _postgres_status(timeout_seconds),
        _redis_status(timeout_seconds),
        _realtime_publisher_status(timeout_seconds),
        _scheduler_status(timeout_seconds),
        _minio_status(timeout_seconds),
    )
    checks = {"postgres": postgres, "redis": redis}
    if postgres == "unavailable":
        logger.warning("readiness_unavailable", checks=checks)
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    status = (
        "ok"
        if redis == "ok"
        and minio == "ok"
        and publisher.get("status") == "ok"
        and scheduler.get("status") in {"ok", "standby"}
        else "degraded"
    )
    if status == "degraded":
        logger.warning("readiness_degraded", checks=checks)
    return {
        "status": status,
        "checks": checks,
        "storage": {"minio": minio},
        "realtime": {"publisher": publisher},
        "background": {"scheduler": scheduler},
    }
