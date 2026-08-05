from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

import asyncpg
from minio import Minio
from redis.asyncio import Redis

from teamora_worker.config import WorkerSettings, get_settings


async def _postgres(settings: WorkerSettings) -> bool:
    connection: asyncpg.Connection | None = None
    try:
        connection = await asyncpg.connect(settings.asyncpg_database_url, timeout=3)
        return bool(await connection.fetchval("SELECT 1"))
    except Exception:
        return False
    finally:
        if connection is not None and not connection.is_closed():
            await connection.close()


async def _redis(settings: WorkerSettings) -> tuple[bool, bool, bool]:
    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        available = bool(await client.ping())
        scheduler, publisher = await asyncio.gather(
            client.get(settings.scheduler_heartbeat_key),
            client.get(settings.realtime_publisher_heartbeat_key),
        )
        return available, _healthy_heartbeat(scheduler), _healthy_heartbeat(publisher)
    except Exception:
        return False, False, False
    finally:
        await client.aclose()


async def _minio(settings: WorkerSettings) -> bool:
    parsed = urlparse(settings.minio_endpoint)
    endpoint = parsed.netloc or parsed.path
    if not endpoint or not settings.minio_root_user or not settings.minio_root_password:
        return False
    client = Minio(
        endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=parsed.scheme == "https",
    )
    try:
        return bool(
            await asyncio.wait_for(
                asyncio.to_thread(client.bucket_exists, settings.minio_bucket), timeout=3
            )
        )
    except Exception:
        return False


def _healthy_heartbeat(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and parsed.get("status") in {"ok", "standby"}


async def check() -> None:
    settings = get_settings()
    postgres, redis_details, minio = await asyncio.gather(
        _postgres(settings),
        _redis(settings),
        _minio(settings),
    )
    redis, scheduler, publisher = redis_details
    checks = {
        "postgres": postgres,
        "redis": redis,
        "minio": minio,
        "scheduler": scheduler,
        "realtime_publisher": publisher,
    }
    print(json.dumps({"status": "ok" if all(checks.values()) else "unavailable", **checks}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(check())
