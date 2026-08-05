from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import structlog

from teamora_worker.config import WorkerSettings

log = structlog.get_logger(service="worker", component="background_jobs")


class JobExecutionError(RuntimeError):
    """A safe handler failure that can be persisted without leaking its payload."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code[:80]
        self.safe_message = message[:500]
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class JobCancelled(JobExecutionError):
    def __init__(self) -> None:
        super().__init__("cancelled", "Job cancellation was requested", retryable=False)


@dataclass(frozen=True)
class BackgroundJobClaim:
    id: UUID
    tenant_id: UUID
    project_id: UUID | None
    job_type: str
    queue: str
    safe_payload: dict[str, object]
    attempt_count: int
    max_attempts: int
    lease_owner: str
    lease_token: UUID
    correlation_id: str
    causation_id: UUID | None


@dataclass(frozen=True)
class JobResult:
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class JobExecutionContext:
    pool: asyncpg.Pool
    settings: WorkerSettings
    job: BackgroundJobClaim
    cancellation_requested: asyncio.Event = field(default_factory=asyncio.Event)
    lease_lost: asyncio.Event = field(default_factory=asyncio.Event)

    def ensure_active(self) -> None:
        if self.cancellation_requested.is_set():
            raise JobCancelled()
        if self.lease_lost.is_set():
            raise JobExecutionError(
                "lease_lost",
                "Background job lease was lost",
                retryable=True,
            )

    async def update_progress(
        self,
        progress: int,
        *,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        self.ensure_active()
        bounded = max(0, min(100, progress))
        safe_detail = _safe_scalars(detail or {})
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, self.job.tenant_id)
                updated = await connection.fetchval(
                    """
                    UPDATE background_jobs
                    SET progress=$4, heartbeat_at=now(),
                        lease_expires_at=now()+($5::integer * interval '1 second'),
                        lock_version=lock_version+1, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                      AND status IN ('running','cancel_requested')
                    RETURNING id
                    """,
                    self.job.tenant_id,
                    self.job.id,
                    self.job.lease_token,
                    bounded,
                    self.settings.background_job_lease_seconds,
                )
                if updated is None:
                    self.lease_lost.set()
                    raise JobExecutionError(
                        "lease_lost", "Background job lease was lost", retryable=True
                    )
                await _insert_event(
                    connection,
                    self.job,
                    "progress",
                    {"progress": bounded, **safe_detail},
                )
                await _insert_realtime_event(
                    connection,
                    self.job,
                    "job.progress",
                    {"status": "running", "progress": bounded},
                )


JobHandler = Callable[[BackgroundJobClaim, JobExecutionContext], Awaitable[JobResult | None]]


class BackgroundJobProcessor:
    """PostgreSQL-backed at-least-once runner.

    PostgreSQL owns job state. Redis may wake consumers elsewhere, but is never
    required for claiming, retrying, or recovering work.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        pool: asyncpg.Pool,
        handlers: Mapping[str, JobHandler],
        *,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.handlers = dict(handlers)
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self._tenant_cursor = 0

    async def run(self, stopping: asyncio.Event) -> None:
        workers = [
            asyncio.create_task(self._worker_loop(stopping, index), name=f"job-worker-{index}")
            for index in range(self.settings.background_job_workers)
        ]
        try:
            await stopping.wait()
            try:
                async with asyncio.timeout(self.settings.background_job_shutdown_timeout_seconds):
                    await asyncio.gather(*workers)
            except TimeoutError:
                log.warning(
                    "background_worker_shutdown_timeout",
                    active_workers=sum(not task.done() for task in workers),
                )
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _worker_loop(self, stopping: asyncio.Event, slot: int) -> None:
        while not stopping.is_set():
            try:
                job = await self.claim_once()
                if job is None:
                    try:
                        await asyncio.wait_for(
                            stopping.wait(), timeout=self.settings.background_job_poll_seconds
                        )
                    except TimeoutError:
                        pass
                    continue
                await self.execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "background_worker_retry",
                    worker_slot=slot,
                    error_type=type(exc).__name__,
                    database_code=getattr(exc, "sqlstate", None),
                )
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=1.0)
                except TimeoutError:
                    pass

    async def claim_once(self) -> BackgroundJobClaim | None:
        async with self.pool.acquire() as connection:
            tenant_rows = await connection.fetch("SELECT id FROM tenants ORDER BY id")
        if not tenant_rows:
            return None
        start = self._tenant_cursor % len(tenant_rows)
        ordered = tenant_rows[start:] + tenant_rows[:start]
        self._tenant_cursor = (start + 1) % len(tenant_rows)
        limits = json.dumps(self.settings.background_job_type_concurrency, separators=(",", ":"))
        for tenant_row in ordered:
            tenant_id = tenant_row["id"]
            lease_token = uuid4()
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await _set_tenant(connection, tenant_id)
                    # Serialize only the claim decision for one tenant. SKIP LOCKED
                    # prevents duplicate job claims, while this short transaction
                    # lock also makes the tenant/type concurrency counts strict
                    # across multiple worker processes.
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"background-job-claim:{tenant_id}",
                    )
                    row = await connection.fetchrow(
                        """
                        WITH candidate AS (
                            SELECT job.id
                            FROM background_jobs AS job
                            WHERE job.tenant_id=$1
                              AND job.status IN ('pending','scheduled','retry_wait')
                              AND job.available_at <= now()
                              AND (
                                SELECT count(*) FROM background_jobs AS running
                                WHERE running.tenant_id=job.tenant_id
                                  AND running.status IN ('running','cancel_requested')
                                  AND running.lease_expires_at > now()
                              ) < $2
                              AND (
                                SELECT count(*) FROM background_jobs AS typed
                                WHERE typed.tenant_id=job.tenant_id
                                  AND typed.type=job.type
                                  AND typed.status IN ('running','cancel_requested')
                                  AND typed.lease_expires_at > now()
                              ) < COALESCE(
                                  NULLIF(($3::jsonb ->> job.type), '')::integer,
                                  $4
                              )
                            ORDER BY
                              job.priority + LEAST(
                                $5,
                                FLOOR(
                                  EXTRACT(EPOCH FROM (now()-job.created_at)) / $6
                                )::integer
                              ) DESC,
                              job.available_at,
                              job.created_at,
                              job.id
                            FOR UPDATE OF job SKIP LOCKED
                            LIMIT 1
                        )
                        UPDATE background_jobs AS job
                        SET status='running', started_at=COALESCE(job.started_at, now()),
                            attempt_count=job.attempt_count+1, lease_owner=$7,
                            lease_token=$8,
                            lease_expires_at=now()+($9::integer * interval '1 second'),
                            heartbeat_at=now(), lock_version=job.lock_version+1, updated_at=now()
                        FROM candidate
                        WHERE job.id=candidate.id AND job.tenant_id=$1
                        RETURNING job.id, job.tenant_id, job.project_id, job.type, job.queue,
                                  job.safe_payload, job.attempt_count, job.max_attempts,
                                  job.correlation_id, job.causation_id
                        """,
                        tenant_id,
                        self.settings.background_job_tenant_concurrency,
                        limits,
                        self.settings.background_job_default_type_concurrency,
                        self.settings.background_job_priority_age_cap,
                        self.settings.background_job_priority_aging_seconds,
                        self.worker_id,
                        lease_token,
                        self.settings.background_job_lease_seconds,
                    )
                    if row is None:
                        continue
                    claim = BackgroundJobClaim(
                        id=row["id"],
                        tenant_id=row["tenant_id"],
                        project_id=row["project_id"],
                        job_type=row["type"],
                        queue=row["queue"],
                        safe_payload=_json_object(row["safe_payload"]),
                        attempt_count=row["attempt_count"],
                        max_attempts=row["max_attempts"],
                        lease_owner=self.worker_id,
                        lease_token=lease_token,
                        correlation_id=row["correlation_id"],
                        causation_id=row["causation_id"],
                    )
                    await connection.execute(
                        """
                        INSERT INTO background_job_attempts
                            (id, tenant_id, project_id, job_id, attempt_number, status,
                             worker_id, lease_token, started_at, heartbeat_at,
                             result_metadata, created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,$2,$3,$4,'running',$5,$6,now(),now(),
                                '{}'::jsonb,now(),now())
                        ON CONFLICT (tenant_id, job_id, attempt_number) DO NOTHING
                        """,
                        claim.tenant_id,
                        claim.project_id,
                        claim.id,
                        claim.attempt_count,
                        claim.lease_owner,
                        claim.lease_token,
                    )
                    await _insert_event(
                        connection,
                        claim,
                        "started",
                        {"attempt": claim.attempt_count},
                    )
                    await _insert_realtime_event(
                        connection,
                        claim,
                        "job.started",
                        {"status": "running", "attempt": claim.attempt_count},
                    )
                    return claim
        return None

    async def execute(self, job: BackgroundJobClaim) -> None:
        context = JobExecutionContext(self.pool, self.settings, job)
        heartbeat = asyncio.create_task(
            self._maintain_lease(context), name=f"job-heartbeat-{job.id}"
        )
        try:
            handler = self.handlers.get(job.job_type)
            if handler is None:
                raise JobExecutionError(
                    "handler_unavailable",
                    f"No handler is registered for job type {job.job_type}",
                    retryable=False,
                )
            async with asyncio.timeout(self.settings.background_job_handler_timeout_seconds):
                result = await handler(job, context)
            context.ensure_active()
            await self._complete(job, result or JobResult())
        except JobCancelled:
            await self._cancel(job)
        except asyncio.CancelledError:
            # A process shutdown deliberately leaves the durable lease in place.
            # The recovery job can safely retry it after expiry.
            raise
        except TimeoutError:
            await self._fail(
                job,
                JobExecutionError("handler_timeout", "Job handler timed out", retryable=True),
            )
        except JobExecutionError as exc:
            await self._fail(job, exc)
        except Exception as exc:
            log.warning(
                "background_job_handler_failed",
                job_id=str(job.id),
                job_type=job.job_type,
                error_type=type(exc).__name__,
            )
            await self._fail(
                job,
                JobExecutionError("handler_error", "Background job handler failed", retryable=True),
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _maintain_lease(self, context: JobExecutionContext) -> None:
        interval = min(
            self.settings.background_job_heartbeat_seconds,
            max(1.0, self.settings.background_job_lease_seconds / 3),
        )
        while True:
            await asyncio.sleep(interval)
            async with self.pool.acquire() as connection:
                async with connection.transaction():
                    await _set_tenant(connection, context.job.tenant_id)
                    row = await connection.fetchrow(
                        """
                    UPDATE background_jobs
                    SET heartbeat_at=now(),
                            lease_expires_at=now()+($4::integer * interval '1 second'),
                            updated_at=now()
                        WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                          AND status IN ('running','cancel_requested')
                        RETURNING status
                        """,
                        context.job.tenant_id,
                        context.job.id,
                        context.job.lease_token,
                        self.settings.background_job_lease_seconds,
                    )
                    if row is None:
                        context.lease_lost.set()
                        return
                    if row["status"] == "cancel_requested":
                        context.cancellation_requested.set()
                    await connection.execute(
                        """
                        UPDATE background_job_attempts
                        SET heartbeat_at=now(), updated_at=now()
                        WHERE tenant_id=$1 AND job_id=$2 AND attempt_number=$3
                          AND lease_token=$4 AND status='running'
                        """,
                        context.job.tenant_id,
                        context.job.id,
                        context.job.attempt_count,
                        context.job.lease_token,
                    )

    async def _complete(self, job: BackgroundJobClaim, result: JobResult) -> None:
        await self._finish(
            job,
            status="completed",
            event_type="completed",
            attempt_status="completed",
            result_metadata=_safe_scalars(result.metadata),
            realtime_event="job.completed",
        )

    async def _cancel(self, job: BackgroundJobClaim) -> None:
        await self._finish(
            job,
            status="cancelled",
            event_type="cancelled",
            attempt_status="cancelled",
            result_metadata={},
            realtime_event="job.cancelled",
        )

    async def _finish(
        self,
        job: BackgroundJobClaim,
        *,
        status: str,
        event_type: str,
        attempt_status: str,
        result_metadata: dict[str, object],
        realtime_event: str,
    ) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                current_status = await connection.fetchval(
                    """
                    SELECT status FROM background_jobs
                    WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                      AND status IN ('running','cancel_requested')
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    job.id,
                    job.lease_token,
                )
                if current_status is None:
                    return
                effective_status = "cancelled" if current_status == "cancel_requested" else status
                effective_event = "cancelled" if effective_status == "cancelled" else event_type
                effective_attempt = (
                    "cancelled" if effective_status == "cancelled" else attempt_status
                )
                effective_realtime = (
                    "job.cancelled" if effective_status == "cancelled" else realtime_event
                )
                effective_result = {} if effective_status == "cancelled" else result_metadata
                row = await connection.fetchrow(
                    """
                    UPDATE background_jobs
                    SET status=$4::varchar,
                        progress=CASE WHEN $4='completed' THEN 100 ELSE progress END,
                        completed_at=CASE WHEN $4='completed' THEN now() ELSE completed_at END,
                        cancelled_at=CASE WHEN $4='cancelled' THEN now() ELSE cancelled_at END,
                        result_metadata=$5::jsonb, lease_owner=NULL, lease_token=NULL,
                        lease_expires_at=NULL, heartbeat_at=now(), lock_version=lock_version+1,
                        updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                      AND status IN ('running','cancel_requested')
                    RETURNING id
                    """,
                    job.tenant_id,
                    job.id,
                    job.lease_token,
                    effective_status,
                    json.dumps(effective_result, separators=(",", ":")),
                )
                if row is None:
                    return
                if effective_status == "cancelled":
                    await _sync_cancelled_domain(connection, job)
                await connection.execute(
                    """
                    UPDATE background_job_attempts
                    SET status=$5::varchar, completed_at=now(), heartbeat_at=now(),
                        result_metadata=$6::jsonb, updated_at=now()
                    WHERE tenant_id=$1 AND job_id=$2 AND attempt_number=$3 AND lease_token=$4
                    """,
                    job.tenant_id,
                    job.id,
                    job.attempt_count,
                    job.lease_token,
                    effective_attempt,
                    json.dumps(effective_result, separators=(",", ":")),
                )
                await _insert_event(
                    connection,
                    job,
                    effective_event,
                    {"status": effective_status},
                )
                await _insert_realtime_event(
                    connection,
                    job,
                    effective_realtime,
                    {"status": effective_status},
                )

    async def _fail(self, job: BackgroundJobClaim, error: JobExecutionError) -> None:
        can_retry = error.retryable and job.attempt_count < job.max_attempts
        if can_retry:
            status = "retry_wait"
            delay = error.retry_after_seconds or retry_delay_seconds(job, self.settings)
            event_type = "retry_scheduled"
            realtime_event = "job.progress"
        elif error.retryable:
            status = "dead_letter"
            delay = 0.0
            event_type = "dead_lettered"
            realtime_event = "job.failed"
        else:
            status = "failed"
            delay = 0.0
            event_type = "failed"
            realtime_event = "job.failed"
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                current_status = await connection.fetchval(
                    """
                    SELECT status FROM background_jobs
                    WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                      AND status IN ('running','cancel_requested')
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    job.id,
                    job.lease_token,
                )
                if current_status is None:
                    return
                if current_status == "cancel_requested":
                    await connection.execute(
                        """
                        UPDATE background_jobs
                        SET status='cancelled', cancelled_at=now(), lease_owner=NULL,
                            lease_token=NULL, lease_expires_at=NULL, heartbeat_at=now(),
                            safe_error_code='cancelled', safe_error_message=NULL,
                            lock_version=lock_version+1, updated_at=now()
                        WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                          AND status='cancel_requested'
                        """,
                        job.tenant_id,
                        job.id,
                        job.lease_token,
                    )
                    await connection.execute(
                        """
                        UPDATE background_job_attempts
                        SET status='cancelled', completed_at=now(), heartbeat_at=now(),
                            safe_error_code='cancelled', safe_error_message=NULL,
                            updated_at=now()
                        WHERE tenant_id=$1 AND job_id=$2 AND attempt_number=$3
                          AND lease_token=$4 AND status='running'
                        """,
                        job.tenant_id,
                        job.id,
                        job.attempt_count,
                        job.lease_token,
                    )
                    await _sync_cancelled_domain(connection, job)
                    await _insert_event(connection, job, "cancelled", {"status": "cancelled"})
                    await _insert_realtime_event(
                        connection,
                        job,
                        "job.cancelled",
                        {"status": "cancelled"},
                    )
                    return
                updated = await connection.fetchval(
                    """
                    UPDATE background_jobs
                    SET status=$4::varchar,
                        available_at=CASE WHEN $4='retry_wait'
                          THEN now()+($5::double precision * interval '1 second')
                          ELSE available_at END,
                        safe_error_code=$6, safe_error_message=$7,
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                        heartbeat_at=now(), lock_version=lock_version+1, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND lease_token=$3
                      AND status IN ('running','cancel_requested')
                    RETURNING id
                    """,
                    job.tenant_id,
                    job.id,
                    job.lease_token,
                    status,
                    delay,
                    error.code,
                    error.safe_message,
                )
                if updated is None:
                    return
                await connection.execute(
                    """
                    UPDATE background_job_attempts
                    SET status='failed', completed_at=now(), safe_error_code=$5,
                        safe_error_message=$6, heartbeat_at=now(), updated_at=now()
                    WHERE tenant_id=$1 AND job_id=$2 AND attempt_number=$3 AND lease_token=$4
                    """,
                    job.tenant_id,
                    job.id,
                    job.attempt_count,
                    job.lease_token,
                    error.code,
                    error.safe_message,
                )
                snapshot: dict[str, object] = {"status": status, "error_code": error.code}
                if can_retry:
                    snapshot["retry_after_seconds"] = round(delay, 3)
                await _insert_event(connection, job, event_type, snapshot)
                await _insert_realtime_event(
                    connection,
                    job,
                    realtime_event,
                    {"status": status, "error_code": error.code},
                )


def retry_delay_seconds(job: BackgroundJobClaim, settings: WorkerSettings) -> float:
    base = min(
        settings.background_job_retry_max_seconds,
        settings.background_job_retry_base_seconds * (2 ** max(0, job.attempt_count - 1)),
    )
    if settings.background_job_retry_jitter_ratio == 0:
        return float(base)
    digest = hashlib.blake2b(f"{job.id}:{job.attempt_count}".encode(), digest_size=8).digest()
    unit = int.from_bytes(digest, "big") / float(2**64 - 1)
    multiplier = 1 + ((unit * 2) - 1) * settings.background_job_retry_jitter_ratio
    return float(max(0.1, min(settings.background_job_retry_max_seconds, base * multiplier)))


async def _set_tenant(connection: asyncpg.Connection, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


async def _insert_event(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    event_type: str,
    snapshot: Mapping[str, object],
) -> None:
    await connection.execute(
        """
        INSERT INTO background_job_events
            (id, tenant_id, project_id, job_id, event_type, safe_snapshot,
             actor_user_id, correlation_id, occurred_at, created_at, updated_at)
        VALUES (gen_random_uuid(),$1,$2,$3,$4,$5::jsonb,NULL,$6,now(),now(),now())
        """,
        job.tenant_id,
        job.project_id,
        job.id,
        event_type,
        json.dumps(_safe_scalars(snapshot), separators=(",", ":")),
        job.correlation_id,
    )


async def _insert_realtime_event(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    event_type: str,
    payload: Mapping[str, object],
) -> None:
    await connection.execute(
        """
        INSERT INTO realtime_events
            (id, tenant_id, project_id, target_membership_id, event_type,
             aggregate_type, aggregate_id, aggregate_version, safe_payload,
             occurred_at, publish_status, publish_attempts, expires_at,
             correlation_id, causation_id, created_at, updated_at)
        VALUES (gen_random_uuid(),$1,$2,NULL,$3,'background_job',$4,NULL,$5::jsonb,
                now(),'pending',0,now()+interval '24 hours',$6,$7,now(),now())
        """,
        job.tenant_id,
        job.project_id,
        event_type,
        job.id,
        json.dumps(_safe_scalars(payload), separators=(",", ":")),
        job.correlation_id,
        job.causation_id,
    )


def _safe_scalars(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, (str, bool, int, float)) or value is None:
            result[key] = value[:500] if isinstance(value, str) else value
        else:
            raise ValueError(f"Job metadata must contain scalar values: {key}")
    return result


def _json_object(value: Any) -> dict[str, object]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


async def _sync_cancelled_domain(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
) -> None:
    """Converge linked domain state when a durable job is cancelled."""

    if job.job_type == "recording.upload":
        recording_id = _payload_uuid_or_none(job.safe_payload.get("recording_id"))
        if recording_id is not None:
            await connection.execute(
                "UPDATE call_recordings SET status='failed', updated_at=now() "
                "WHERE tenant_id=$1 AND id=$2 AND status<>'available'",
                job.tenant_id,
                recording_id,
            )
        return
    if job.job_type.startswith("customer_import."):
        import_id = _payload_uuid_or_none(job.safe_payload.get("import_id"))
        if import_id is not None:
            await connection.execute(
                """
                UPDATE customer_imports
                SET status='cancelled', cancelled_at=COALESCE(cancelled_at,now()),
                    processing_completed_at=COALESCE(processing_completed_at,now()),
                    updated_at=now()
                WHERE tenant_id=$1 AND id=$2
                  AND status NOT IN ('completed','committed','cancelled')
                """,
                job.tenant_id,
                import_id,
            )
        return
    if job.job_type == "knowledge.ingest_document":
        document_version_id = _payload_uuid_or_none(job.safe_payload.get("document_version_id"))
        await connection.execute(
            """
            UPDATE document_ingestion_jobs
            SET status='cancelled', stage='failed', safe_error_code='cancelled',
                lease_token=NULL, lease_expires_at=NULL, heartbeat_at=now(), updated_at=now()
            WHERE tenant_id=$1 AND background_job_id=$2
              AND status NOT IN ('succeeded','cancelled')
            """,
            job.tenant_id,
            job.id,
        )
        if document_version_id is not None:
            await connection.execute(
                """
                UPDATE knowledge_document_versions
                SET status='failed', safe_error_code='cancelled',
                    safe_error_message='Document processing was cancelled',
                    failed_at=COALESCE(failed_at,now()), lock_version=lock_version+1,
                    updated_at=now()
                WHERE tenant_id=$1 AND id=$2 AND status NOT IN ('ready','archived')
                """,
                job.tenant_id,
                document_version_id,
            )


async def _sync_failed_domain(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    *,
    error_code: str,
    safe_error_message: str,
) -> None:
    """Converge linked domain state after a terminal common-job failure."""

    if job.job_type == "recording.upload":
        recording_id = _payload_uuid_or_none(job.safe_payload.get("recording_id"))
        if recording_id is not None:
            await connection.execute(
                "UPDATE call_recordings SET status='failed', updated_at=now() "
                "WHERE tenant_id=$1 AND id=$2 AND status<>'available'",
                job.tenant_id,
                recording_id,
            )
        return
    if job.job_type.startswith("customer_import."):
        import_id = _payload_uuid_or_none(job.safe_payload.get("import_id"))
        if import_id is not None:
            await connection.execute(
                """
                UPDATE customer_imports
                SET status='failed',
                    processing_completed_at=COALESCE(processing_completed_at,now()),
                    updated_at=now()
                WHERE tenant_id=$1 AND id=$2
                  AND status NOT IN ('completed','committed','cancelled','expired')
                """,
                job.tenant_id,
                import_id,
            )
        return
    if job.job_type == "knowledge.ingest_document":
        document_version_id = _payload_uuid_or_none(job.safe_payload.get("document_version_id"))
        await connection.execute(
            """
            UPDATE document_ingestion_jobs
            SET status='failed', stage='failed', safe_error_code=$3,
                lease_token=NULL, lease_expires_at=NULL, heartbeat_at=now(),
                completed_at=COALESCE(completed_at,now()), updated_at=now()
            WHERE tenant_id=$1 AND background_job_id=$2
              AND status NOT IN ('succeeded','cancelled','failed')
            """,
            job.tenant_id,
            job.id,
            error_code[:80],
        )
        if document_version_id is not None:
            await connection.execute(
                """
                UPDATE knowledge_document_versions
                SET status='failed', safe_error_code=$3, safe_error_message=$4,
                    failed_at=COALESCE(failed_at,now()), lock_version=lock_version+1,
                    updated_at=now()
                WHERE tenant_id=$1 AND id=$2
                  AND status NOT IN ('ready','archived','needs_ocr','failed')
                """,
                job.tenant_id,
                document_version_id,
                error_code[:80],
                safe_error_message[:500],
            )


def _payload_uuid_or_none(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
