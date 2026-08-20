from __future__ import annotations

import asyncpg
import httpx

from teamora_worker.background_jobs import (
    BackgroundJobClaim,
    JobExecutionContext,
    JobExecutionError,
    JobHandler,
    JobResult,
    _insert_event,
    _insert_realtime_event,
    _json_object,
    _sync_cancelled_domain,
    _sync_failed_domain,
)
from teamora_worker.config import WorkerSettings
from teamora_worker.customer_import import CustomerImportProcessor
from teamora_worker.knowledge_ingestion import KnowledgeIngestionProcessor
from teamora_worker.recording_upload import RecordingUploadProcessor
from teamora_worker.storage_maintenance import StorageMaintenance


class MaintenanceHandlers:
    def __init__(
        self,
        settings: WorkerSettings,
        knowledge: KnowledgeIngestionProcessor,
    ) -> None:
        self.knowledge = knowledge
        self.storage = StorageMaintenance(settings)
        self.customer_import = CustomerImportProcessor(settings)
        self.recordings = RecordingUploadProcessor(settings)

    def registry(self) -> dict[str, JobHandler]:
        handlers: dict[str, JobHandler] = {
            "knowledge.ingest_document": self.knowledge.handle_background_job,
            "system.stale_job_recovery": self.recover_stale_jobs,
            "system.dead_letter_monitor": self.monitor_dead_letters,
            "realtime.outbox_cleanup": self.cleanup_realtime_outbox,
            "customer_import.expired_preview_cleanup": self.cleanup_expired_import_previews,
            "recording.upload": self.recordings.handle,
            "transfer.offer_timeout": self.process_transfer_offer_timeout,
        }
        handlers.update(self.storage.registry())
        handlers.update(self.customer_import.registry())
        return handlers

    async def process_transfer_offer_timeout(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        transfer_id = job.safe_payload.get("transfer_request_id")
        if not isinstance(transfer_id, str):
            raise JobExecutionError(
                "transfer_id_missing",
                "Transfer timeout job is missing its request identifier",
                retryable=False,
            )
        token = context.settings.gateway_service_token
        if token is None:
            raise JobExecutionError(
                "service_token_unavailable",
                "Internal transfer service authentication is not configured",
                retryable=False,
            )
        try:
            async with httpx.AsyncClient(
                base_url=context.settings.api_internal_url,
                timeout=httpx.Timeout(5.0),
            ) as client:
                response = await client.post(
                    "/internal/v1/transfers/process-expired",
                    headers={"Authorization": f"Bearer {token.get_secret_value()}"},
                    json={
                        "tenant_id": str(job.tenant_id),
                        "transfer_request_id": transfer_id,
                        "job_id": str(job.id),
                    },
                )
        except httpx.HTTPError as exc:
            raise JobExecutionError(
                "transfer_service_unavailable",
                "Internal transfer service is unavailable",
                retryable=True,
            ) from exc
        if response.status_code in {401, 403, 422}:
            raise JobExecutionError(
                "transfer_service_rejected",
                "Internal transfer service rejected the timeout command",
                retryable=False,
            )
        if response.status_code >= 400:
            raise JobExecutionError(
                "transfer_service_failed",
                "Internal transfer timeout processing failed",
                retryable=True,
            )
        result = response.json()
        return JobResult(
            {
                "processed": bool(result.get("processed")),
                "status": str(result.get("status", "unchanged"))[:40],
            }
        )

    async def cleanup_realtime_outbox(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        # Stage 14 routes irreversible event cleanup through the same reviewed,
        # two-phase retention candidates as other tenant data. This legacy
        # schedule remains as a health signal but deliberately performs no
        # direct DELETE when a tenant policy has not been confirmed.
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                expired = await connection.fetchval(
                    """
                    SELECT count(*) FROM realtime_events
                    WHERE tenant_id=$1 AND expires_at <= now()
                      AND publish_status='published'
                    """,
                    job.tenant_id,
                )
        return JobResult({"eligible_events": int(expired or 0), "deferred_to_retention": True})

    async def cleanup_expired_import_previews(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                expired_result = await connection.execute(
                    """
                    UPDATE customer_imports
                    SET status='expired', source_rows='{}'::jsonb, updated_at=now()
                    WHERE tenant_id=$1 AND status IN ('preview','ready') AND expires_at <= now()
                    """,
                    job.tenant_id,
                )
                staging_result = await connection.execute(
                    """
                    DELETE FROM customer_import_staging_rows AS staging
                    USING customer_imports AS import_record
                    WHERE staging.tenant_id=$1
                      AND import_record.tenant_id=staging.tenant_id
                      AND import_record.id=staging.import_id
                      AND import_record.status IN ('completed','cancelled','expired','failed')
                    """,
                    job.tenant_id,
                )
        return JobResult(
            {
                "expired_previews": _affected_rows(expired_result),
                "staging_rows_removed": _affected_rows(staging_result),
            }
        )

    async def monitor_dead_letters(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                count = await connection.fetchval(
                    """
                    SELECT count(*) FROM background_jobs
                    WHERE tenant_id=$1 AND status='dead_letter'
                    """,
                    job.tenant_id,
                )
        return JobResult({"dead_letter_count": int(count or 0)})

    async def recover_stale_jobs(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        recovered = 0
        dead_lettered = 0
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                rows = await connection.fetch(
                    """
                    SELECT id, tenant_id, project_id, type, queue, status, safe_payload,
                           attempt_count, max_attempts, lease_owner, lease_token,
                           correlation_id, causation_id
                    FROM background_jobs
                    WHERE tenant_id=$1 AND id<>$2
                      AND status IN ('running','cancel_requested')
                      AND lease_expires_at < now()
                    ORDER BY lease_expires_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 100
                    """,
                    job.tenant_id,
                    job.id,
                )
                for row in rows:
                    cancelled = row["status"] == "cancel_requested"
                    terminal = not cancelled and row["attempt_count"] >= row["max_attempts"]
                    status = (
                        "cancelled" if cancelled else ("dead_letter" if terminal else "retry_wait")
                    )
                    error_code = "cancelled" if cancelled else "lease_expired"
                    error_message = None if cancelled else "Worker lease expired before completion"
                    old_token = row["lease_token"]
                    await connection.execute(
                        """
                        UPDATE background_jobs
                        SET status=$3::varchar,
                            available_at=CASE WHEN $3='retry_wait' THEN now() ELSE available_at END,
                            cancelled_at=CASE WHEN $3='cancelled' THEN now() ELSE cancelled_at END,
                            safe_error_code=$4, safe_error_message=$5,
                            lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                            heartbeat_at=now(), lock_version=lock_version+1, updated_at=now()
                        WHERE tenant_id=$1 AND id=$2
                        """,
                        job.tenant_id,
                        row["id"],
                        status,
                        error_code,
                        error_message,
                    )
                    await connection.execute(
                        """
                        UPDATE background_job_attempts
                        SET status=$5::varchar, completed_at=now(), heartbeat_at=now(),
                            safe_error_code=$6, safe_error_message=$7,
                            updated_at=now()
                        WHERE tenant_id=$1 AND job_id=$2 AND attempt_number=$3
                          AND lease_token=$4 AND status='running'
                        """,
                        job.tenant_id,
                        row["id"],
                        row["attempt_count"],
                        old_token,
                        "cancelled" if cancelled else "failed",
                        error_code,
                        error_message,
                    )
                    stale = BackgroundJobClaim(
                        id=row["id"],
                        tenant_id=row["tenant_id"],
                        project_id=row["project_id"],
                        job_type=row["type"],
                        queue=row["queue"],
                        safe_payload=_json_object(row["safe_payload"]),
                        attempt_count=row["attempt_count"],
                        max_attempts=row["max_attempts"],
                        lease_owner=str(row["lease_owner"] or "unknown"),
                        lease_token=old_token,
                        correlation_id=row["correlation_id"],
                        causation_id=row["causation_id"],
                    )
                    if cancelled:
                        await _sync_cancelled_domain(connection, stale)
                    elif terminal:
                        await _sync_failed_domain(
                            connection,
                            stale,
                            error_code=error_code,
                            safe_error_message=error_message or "Background job failed",
                        )
                    await _insert_event(
                        connection,
                        stale,
                        "cancelled"
                        if cancelled
                        else ("dead_lettered" if terminal else "lease_recovered"),
                        {"status": status, "error_code": error_code},
                    )
                    await _insert_realtime_event(
                        connection,
                        stale,
                        "job.cancelled"
                        if cancelled
                        else ("job.failed" if terminal else "job.progress"),
                        {"status": status, "error_code": error_code},
                    )
                    recovered += int(not cancelled)
                    dead_lettered += int(terminal)
        return JobResult({"recovered_jobs": recovered, "dead_lettered_jobs": dead_lettered})


async def _set_tenant(connection: asyncpg.Connection, tenant_id: object) -> None:
    await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


def _affected_rows(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0
