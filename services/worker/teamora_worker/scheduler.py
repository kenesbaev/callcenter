from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import structlog
from redis.asyncio import Redis

from teamora_worker.config import WorkerSettings

log = structlog.get_logger(service="worker", component="scheduler")


@dataclass(frozen=True)
class MaintenanceSchedule:
    key: str
    job_type: str
    interval_seconds: int
    priority: int = 0


MAINTENANCE_SCHEDULES = (
    MaintenanceSchedule("stale-job-recovery", "system.stale_job_recovery", 30, 100),
    MaintenanceSchedule(
        "expired-import-preview-cleanup",
        "customer_import.expired_preview_cleanup",
        900,
        10,
    ),
    MaintenanceSchedule("realtime-outbox-cleanup", "realtime.outbox_cleanup", 300, 10),
    MaintenanceSchedule("minio-orphan-scan", "storage.orphan_scan", 3600, 0),
    MaintenanceSchedule("retention-eligibility-scan", "retention.eligibility_scan", 3600, 0),
    MaintenanceSchedule("retention-purge", "retention.purge", 900, 15),
    MaintenanceSchedule("expired-temporary-object-cleanup", "storage.temporary_cleanup", 900, 5),
    MaintenanceSchedule("dead-letter-monitor", "system.dead_letter_monitor", 300, 25),
)


class BackgroundJobScheduler:
    """Single PostgreSQL advisory-lock leader for recurring tenant jobs."""

    def __init__(
        self,
        settings: WorkerSettings,
        pool: asyncpg.Pool,
        redis: Redis,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.redis = redis
        self.is_leader = False
        self.enqueued = 0
        self.failures = 0

    async def run(self, stopping: asyncio.Event) -> None:
        while not stopping.is_set():
            try:
                async with self.pool.acquire() as connection:
                    acquired = await connection.fetchval(
                        "SELECT pg_try_advisory_lock(hashtextextended($1, 0))",
                        self.settings.scheduler_advisory_key,
                    )
                    if not acquired:
                        self.is_leader = False
                        await self._heartbeat()
                        await _wait(stopping, self.settings.scheduler_poll_seconds)
                        continue
                    self.is_leader = True
                    try:
                        await self._leader_loop(connection, stopping)
                    finally:
                        self.is_leader = False
                        await connection.execute(
                            "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                            self.settings.scheduler_advisory_key,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                self.is_leader = False
                log.warning(
                    "scheduler_retry",
                    error_type=type(exc).__name__,
                    database_code=getattr(exc, "sqlstate", None),
                )
                await _wait(stopping, min(10.0, self.settings.scheduler_poll_seconds * 2))

    async def _leader_loop(
        self,
        connection: asyncpg.Connection,
        stopping: asyncio.Event,
    ) -> None:
        while not stopping.is_set():
            await self.ensure_maintenance_schedules(connection)
            self.enqueued += await self.enqueue_due(connection)
            await self._heartbeat()
            await _wait(stopping, self.settings.scheduler_poll_seconds)

    async def ensure_maintenance_schedules(self, connection: asyncpg.Connection) -> None:
        tenant_rows = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        for tenant_row in tenant_rows:
            tenant_id = tenant_row["id"]
            async with connection.transaction():
                await _set_tenant(connection, tenant_id)
                for schedule in MAINTENANCE_SCHEDULES:
                    await connection.execute(
                        """
                        INSERT INTO scheduled_jobs
                            (id, tenant_id, project_id, schedule_key, job_type, queue,
                             priority, safe_payload, interval_seconds, next_run_at,
                             enabled, lock_version, created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,NULL,$2,$3,'maintenance',$4,'{}'::jsonb,
                                $5,now(),true,1,now(),now())
                        ON CONFLICT DO NOTHING
                        """,
                        tenant_id,
                        schedule.key,
                        schedule.job_type,
                        schedule.priority,
                        schedule.interval_seconds,
                    )

    async def enqueue_due(self, connection: asyncpg.Connection) -> int:
        tenant_rows = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        total = 0
        for tenant_row in tenant_rows:
            tenant_id = tenant_row["id"]
            async with connection.transaction():
                await _set_tenant(connection, tenant_id)
                schedules = await connection.fetch(
                    """
                    SELECT id, project_id, schedule_key, job_type, queue, priority,
                           safe_payload, interval_seconds, next_run_at
                    FROM scheduled_jobs
                    WHERE tenant_id=$1 AND enabled=true AND next_run_at <= now()
                    ORDER BY next_run_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 100
                    """,
                    tenant_id,
                )
                for schedule in schedules:
                    scheduled_for = schedule["next_run_at"]
                    idempotency_key = (
                        f"schedule:{schedule['id']}:{scheduled_for.astimezone(UTC).isoformat()}"
                    )
                    correlation_id = (
                        f"scheduler:{schedule['schedule_key']}:{int(scheduled_for.timestamp())}"
                    )
                    job_id = await connection.fetchval(
                        """
                        INSERT INTO background_jobs
                            (id, tenant_id, project_id, type, queue, priority, status,
                             safe_payload, idempotency_key, scheduled_at, available_at,
                             attempt_count, max_attempts, progress, correlation_id,
                             result_metadata, lock_version, created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,'scheduled',$6::jsonb,$7,$8,$8,
                                0,4,0,$9,'{}'::jsonb,1,now(),now())
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """,
                        tenant_id,
                        schedule["project_id"],
                        schedule["job_type"],
                        schedule["queue"],
                        schedule["priority"],
                        _json_object(schedule["safe_payload"]),
                        idempotency_key,
                        scheduled_for,
                        correlation_id,
                    )
                    await connection.execute(
                        """
                        UPDATE scheduled_jobs
                        SET last_enqueued_at=$3::timestamptz,
                            next_run_at=GREATEST(
                              now()+($4::integer * interval '1 second'),
                              $3::timestamptz+($4::integer * interval '1 second')
                            ),
                            lock_version=lock_version+1, updated_at=now()
                        WHERE tenant_id=$1 AND id=$2
                        """,
                        tenant_id,
                        schedule["id"],
                        scheduled_for,
                        schedule["interval_seconds"],
                    )
                    if job_id is None:
                        continue
                    await connection.execute(
                        """
                        INSERT INTO background_job_events
                            (id, tenant_id, project_id, job_id, event_type, safe_snapshot,
                             actor_user_id, correlation_id, occurred_at, created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,$2,$3,'enqueued',$4::jsonb,NULL,$5,
                                now(),now(),now())
                        """,
                        tenant_id,
                        schedule["project_id"],
                        job_id,
                        json.dumps(
                            {
                                "status": "scheduled",
                                "schedule_key": schedule["schedule_key"],
                            },
                            separators=(",", ":"),
                        ),
                        correlation_id,
                    )
                    total += 1
        return total

    async def _heartbeat(self) -> None:
        value = json.dumps(
            {
                "status": "ok" if self.is_leader else "standby",
                "leader": self.is_leader,
                "checked_at": datetime.now(UTC).isoformat(),
                "jobs_enqueued": self.enqueued,
                "failures": self.failures,
            },
            separators=(",", ":"),
        )
        try:
            await self.redis.set(
                self.settings.scheduler_heartbeat_key,
                value,
                ex=self.settings.scheduler_heartbeat_ttl_seconds,
            )
        except Exception as exc:
            # Redis is not the scheduler source of truth. Losing its health hint
            # must not release the PostgreSQL leader lock or stop enqueueing.
            log.warning("scheduler_heartbeat_unavailable", error_type=type(exc).__name__)


async def _wait(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _set_tenant(connection: asyncpg.Connection, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


def _json_object(value: object) -> str:
    if isinstance(value, str):
        parsed = json.loads(value)
        value = parsed if isinstance(parsed, dict) else {}
    return json.dumps(value if isinstance(value, dict) else {}, separators=(",", ":"))
