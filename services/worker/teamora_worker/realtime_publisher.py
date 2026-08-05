from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import asyncpg
import structlog
from redis.asyncio import Redis

from teamora_worker.config import WorkerSettings

log = structlog.get_logger(service="worker", component="realtime_publisher")


class RealtimePublisher:
    """Publishes the PostgreSQL outbox with at-least-once delivery semantics."""

    def __init__(self, settings: WorkerSettings, redis: Redis) -> None:
        self.settings = settings
        self.redis = redis
        self.published = 0
        self.failures = 0
        self._last_cleanup = 0.0

    async def run(self, stopping: asyncio.Event) -> None:
        delay = self.settings.realtime_publisher_poll_seconds
        connection: asyncpg.Connection | None = None
        while not stopping.is_set():
            try:
                if connection is None or connection.is_closed():
                    connection = await asyncpg.connect(
                        self.settings.asyncpg_database_url,
                        timeout=5,
                    )
                published = await self.publish_once(connection)
                await self._heartbeat(connection)
                if asyncio.get_running_loop().time() - self._last_cleanup >= (
                    self.settings.realtime_retention_cleanup_seconds
                ):
                    await self.cleanup_expired(connection)
                    self._last_cleanup = asyncio.get_running_loop().time()
                delay = self.settings.realtime_publisher_poll_seconds
                if published == 0:
                    await asyncio.wait_for(stopping.wait(), timeout=delay)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                log.warning("realtime_publisher_retry", error_type=type(exc).__name__)
                if connection is not None and not connection.is_closed():
                    await connection.close()
                connection = None
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, 10.0)
        if connection is not None and not connection.is_closed():
            await connection.close()

    async def publish_once(self, connection: asyncpg.Connection) -> int:
        tenant_ids = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        published = 0
        for tenant_row in tenant_ids:
            tenant_id = tenant_row["id"]
            publish_error: Exception | None = None
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id),
                )
                rows = await connection.fetch(
                    """
                    SELECT id, cursor, tenant_id
                    FROM realtime_events
                    WHERE tenant_id = $1 AND publish_status = 'pending'
                    ORDER BY cursor
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                    """,
                    tenant_id,
                    self.settings.realtime_publisher_batch_size,
                )
                for row in rows:
                    notification = json.dumps(
                        {
                            "event_id": str(row["id"]),
                            "cursor": row["cursor"],
                            "tenant_id": str(row["tenant_id"]),
                        },
                        separators=(",", ":"),
                    )
                    try:
                        await self.redis.publish(self.settings.realtime_channel, notification)
                    except Exception as exc:
                        await connection.execute(
                            """
                            UPDATE realtime_events
                            SET publish_attempts = publish_attempts + 1, updated_at = now()
                            WHERE tenant_id = $1 AND id = $2
                            """,
                            tenant_id,
                            row["id"],
                        )
                        self.failures += 1
                        publish_error = exc
                        break
                    await connection.execute(
                        """
                        UPDATE realtime_events
                        SET publish_status = 'published',
                            publish_attempts = publish_attempts + 1,
                            published_at = now(),
                            updated_at = now()
                        WHERE tenant_id = $1 AND id = $2
                        """,
                        tenant_id,
                        row["id"],
                    )
                    published += 1
                    self.published += 1
            if publish_error is not None:
                raise publish_error
        return published

    async def cleanup_expired(self, connection: asyncpg.Connection) -> int:
        """Report expired events without deleting outside tenant retention.

        The Stage 14 retention worker creates reviewed, two-phase candidates.
        Keeping this hook non-destructive preserves the existing health loop
        while ensuring an enabled retention policy is always required.
        """

        tenant_ids = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        eligible = 0
        for tenant_row in tenant_ids:
            tenant_id = tenant_row["id"]
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id),
                )
                count = await connection.fetchval(
                    """
                    SELECT count(*) FROM realtime_events
                    WHERE tenant_id = $1
                      AND expires_at <= now()
                      AND publish_status = 'published'
                    """,
                    tenant_id,
                )
                eligible += int(count or 0)
        return eligible

    async def _heartbeat(self, connection: asyncpg.Connection) -> None:
        oldest_unpublished: datetime | None = None
        tenant_ids = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        for tenant_row in tenant_ids:
            tenant_id = tenant_row["id"]
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id),
                )
                tenant_oldest: datetime | None = await connection.fetchval(
                    """
                    SELECT min(created_at)
                    FROM realtime_events
                    WHERE tenant_id = $1 AND publish_status = 'pending'
                    """,
                    tenant_id,
                )
            if tenant_oldest is not None and (
                oldest_unpublished is None or tenant_oldest < oldest_unpublished
            ):
                oldest_unpublished = tenant_oldest
        now = datetime.now(UTC)
        lag_seconds = (
            max(0.0, (now - oldest_unpublished).total_seconds()) if oldest_unpublished else 0.0
        )
        health: dict[str, Any] = {
            "status": "ok",
            "checked_at": now.isoformat(),
            "events_published": self.published,
            "publish_failures": self.failures,
            "oldest_unpublished_seconds": round(lag_seconds, 3),
        }
        await self.redis.set(
            self.settings.realtime_publisher_heartbeat_key,
            json.dumps(health, separators=(",", ":")),
            ex=15,
        )
