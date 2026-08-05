from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.errors import ApiError
from teamora_api.models import BackgroundJob, BackgroundJobEvent
from teamora_api.schemas.background import BackgroundJobRead, BackgroundJobStatus

_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "jwt",
    "credential",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "transcript",
    "document_text",
    "customer_row",
)
_MAX_PAYLOAD_BYTES = 16 * 1024
_ENQUEUE_FINGERPRINT_KEY = "_enqueue_fingerprint"


def _safe_value(value: object, *, depth: int = 0) -> object:
    if depth > 3:
        raise ValueError("Background job payload nesting is too deep")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, Mapping):
        return sanitize_job_payload(value, depth=depth + 1)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        if len(value) > 100:
            raise ValueError("Background job payload list is too large")
        return [_safe_value(item, depth=depth + 1) for item in value]
    raise ValueError(f"Unsupported background job payload value: {type(value).__name__}")


def sanitize_job_payload(payload: Mapping[str, object], *, depth: int = 0) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in payload.items():
        normalized = key.casefold().replace("-", "_")
        if any(marker in normalized for marker in _SENSITIVE_MARKERS):
            raise ValueError(f"Sensitive background job payload key is forbidden: {key}")
        safe[key[:120]] = _safe_value(value, depth=depth)
    encoded = json.dumps(safe, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Background job payload is too large")
    return safe


async def acquire_idempotency_lock(
    session: AsyncSession,
    *,
    namespace: str,
    tenant_id: UUID,
    idempotency_key: str,
) -> None:
    """Serialize one tenant-scoped idempotent operation for this transaction."""

    lock_name = f"{namespace}:{tenant_id}:{idempotency_key}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_name, 0))))


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _enqueue_fingerprint(
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    created_by_user_id: UUID | None,
    job_type: str,
    queue: str,
    priority: int,
    safe_payload: Mapping[str, object],
    scheduled_at: datetime | None,
    max_attempts: int,
    causation_id: UUID | None,
) -> str:
    canonical = {
        "tenant_id": str(tenant_id),
        "project_id": str(project_id) if project_id else None,
        "created_by_user_id": str(created_by_user_id) if created_by_user_id else None,
        "job_type": job_type,
        "queue": queue,
        "priority": priority,
        "safe_payload": safe_payload,
        "scheduled_at": _iso(scheduled_at),
        "max_attempts": max_attempts,
        "causation_id": str(causation_id) if causation_id else None,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


async def append_background_job_event(
    session: AsyncSession,
    *,
    job: BackgroundJob,
    event_type: str,
    safe_snapshot: Mapping[str, object] | None = None,
    actor_user_id: UUID | None = None,
    correlation_id: str | None = None,
) -> BackgroundJobEvent:
    event = BackgroundJobEvent(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        event_type=event_type[:80],
        safe_snapshot=sanitize_job_payload(safe_snapshot or {}),
        actor_user_id=actor_user_id,
        correlation_id=(correlation_id or job.correlation_id)[:160],
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    return event


async def enqueue_background_job(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    job_type: str,
    safe_payload: Mapping[str, object],
    correlation_id: str,
    project_id: UUID | None = None,
    created_by_user_id: UUID | None = None,
    queue: str = "default",
    priority: int = 50,
    idempotency_key: str | None = None,
    scheduled_at: datetime | None = None,
    max_attempts: int = 5,
    causation_id: UUID | None = None,
) -> tuple[BackgroundJob, bool]:
    """Enqueue in the caller transaction and return ``(job, created)``.

    Committing is deliberately left to the business transaction so a rollback
    cannot leave executable work behind.
    """

    normalized_job_type = job_type[:80]
    normalized_queue = queue[:40]
    normalized_priority = max(0, min(100, priority))
    normalized_max_attempts = max(1, min(25, max_attempts))
    normalized_scheduled_at = scheduled_at.astimezone(UTC) if scheduled_at else None
    sanitized_payload = sanitize_job_payload(safe_payload)
    fingerprint = _enqueue_fingerprint(
        tenant_id=tenant_id,
        project_id=project_id,
        created_by_user_id=created_by_user_id,
        job_type=normalized_job_type,
        queue=normalized_queue,
        priority=normalized_priority,
        safe_payload=sanitized_payload,
        scheduled_at=normalized_scheduled_at,
        max_attempts=normalized_max_attempts,
        causation_id=causation_id,
    )
    if idempotency_key:
        await acquire_idempotency_lock(
            session,
            namespace=f"background-job:{normalized_job_type}",
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        existing = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.type == normalized_job_type,
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            stored_fingerprint = existing.safe_payload.get(_ENQUEUE_FINGERPRINT_KEY)
            if stored_fingerprint != fingerprint:
                raise ApiError(
                    409,
                    "idempotency_key_reused",
                    "Idempotency-Key was reused with different background job parameters",
                )
            return existing, False
    now = datetime.now(UTC)
    available_at = normalized_scheduled_at or now
    stored_payload = dict(sanitized_payload)
    stored_payload[_ENQUEUE_FINGERPRINT_KEY] = fingerprint
    job = BackgroundJob(
        tenant_id=tenant_id,
        project_id=project_id,
        created_by_user_id=created_by_user_id,
        type=normalized_job_type,
        queue=normalized_queue,
        priority=normalized_priority,
        status="scheduled" if available_at > now else "pending",
        safe_payload=stored_payload,
        idempotency_key=idempotency_key or f"auto:{uuid4()}",
        scheduled_at=normalized_scheduled_at,
        available_at=available_at,
        attempt_count=0,
        max_attempts=normalized_max_attempts,
        progress=0,
        correlation_id=correlation_id[:160],
        causation_id=causation_id,
        result_metadata={},
        lock_version=1,
    )
    session.add(job)
    await session.flush()
    await append_background_job_event(
        session,
        job=job,
        event_type="created",
        safe_snapshot={"status": job.status, "type": job.type, "queue": job.queue},
        actor_user_id=created_by_user_id,
        correlation_id=correlation_id,
    )
    return job, True


def public_payload_summary(job: BackgroundJob) -> dict[str, object]:
    allowed = {
        "import_id",
        "document_version_id",
        "knowledge_base_id",
        "storage_object_id",
        "scan_scope",
        "policy_id",
        "project_id",
    }
    return {key: value for key, value in job.safe_payload.items() if key in allowed}


def serialize_background_job(job: BackgroundJob) -> BackgroundJobRead:
    dedicated_command_job = job.type in {
        "customer_import.prepare_preview",
        "customer_import.process",
        "knowledge.ingest_document",
    }
    return BackgroundJobRead(
        id=job.id,
        project_id=job.project_id,
        type=job.type,
        queue=job.queue,
        priority=job.priority,
        status=cast(BackgroundJobStatus, job.status),
        payload_summary=public_payload_summary(job),
        progress=job.progress,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        scheduled_at=job.scheduled_at,
        available_at=job.available_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        cancelled_at=job.cancelled_at,
        heartbeat_at=job.heartbeat_at,
        safe_error_code=job.safe_error_code,
        safe_error_message=job.safe_error_message,
        result_metadata=job.result_metadata,
        state_version=job.lock_version,
        correlation_id=job.correlation_id,
        causation_id=job.causation_id,
        can_cancel=(
            job.status in {"pending", "scheduled", "running", "retry_wait"}
            and job.type not in {"customer_import.prepare_preview", "customer_import.process"}
        ),
        can_retry=(
            job.status in {"failed", "dead_letter"}
            and not dedicated_command_job
            and job.result_metadata.get("retention_payload_purged") is not True
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
