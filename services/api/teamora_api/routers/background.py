from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from teamora_api.audit import write_audit
from teamora_api.background_service import (
    acquire_idempotency_lock,
    append_background_job_event,
    enqueue_background_job,
    serialize_background_job,
)
from teamora_api.dependencies import Principal, PrincipalDep, SessionDep
from teamora_api.errors import ApiError
from teamora_api.models import (
    BackgroundJob,
    BackgroundJobAttempt,
    BackgroundJobEvent,
    Call,
    CallRecording,
    Customer,
    CustomerImport,
    DocumentIngestionJob,
    JobCommandSubmission,
    KnowledgeBaseRevision,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeSource,
    LegalHold,
    RealtimeEvent,
    RetentionCandidate,
    RetentionPolicy,
    StorageConsistencyIssue,
    StorageObject,
    TeamCommandSubmission,
    TranscriptSegment,
)
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.rbac import role_has_permission
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.retention_lock import acquire_retention_lock
from teamora_api.schemas.background import (
    BackgroundJobAttemptRead,
    BackgroundJobEventRead,
    BackgroundJobRead,
    BackgroundQueueStatistics,
    JobCommandRequest,
    LegalHoldCreate,
    LegalHoldRead,
    LegalHoldRelease,
    LegalHoldScope,
    RetentionCandidateRead,
    RetentionPolicyRead,
    RetentionPolicyUpdate,
    RetentionPreviewRead,
    RetentionPreviewRequest,
    RetentionRunRequest,
    StorageCategorySummary,
    StorageConsistencyIssueRead,
    StorageScanRequest,
    StorageSummaryRead,
)
from teamora_api.schemas.common import Page

router = APIRouter(tags=["background-operations"])

_JOB_TERMINAL = {"completed", "failed", "dead_letter", "cancelled"}
_JOB_CANCELABLE = {"pending", "scheduled", "running", "retry_wait", "cancel_requested"}
_JOB_RETRYABLE = {"failed", "dead_letter"}
_DOMAIN_MANAGED_JOB_COMMANDS = {
    ("cancel", "customer_import.prepare_preview"),
    ("retry", "customer_import.prepare_preview"),
    ("cancel", "customer_import.process"),
    ("retry", "customer_import.process"),
    ("retry", "knowledge.ingest_document"),
}


def _can_read_jobs(principal: Principal) -> bool:
    return role_has_permission(principal.role, "jobs:read") or role_has_permission(principal.role, "jobs:own")


def _can_manage_jobs(principal: Principal) -> bool:
    return role_has_permission(principal.role, "jobs:manage")


def _safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _admin_command_fingerprint(
    operation: str,
    resource_id: UUID | None,
    payload: BaseModel,
) -> str:
    body = payload.model_dump(mode="json")
    encoded = json.dumps(
        {"operation": operation, "resource_id": str(resource_id) if resource_id else None, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(encoded.encode()).hexdigest()


def _require(principal: Principal, permission: str) -> None:
    if not role_has_permission(principal.role, permission):
        raise ApiError(403, "permission_denied", "You do not have permission for this action")


async def _job_scope_filter(session: SessionDep, principal: Principal) -> ColumnElement[bool]:
    if role_has_permission(principal.role, "jobs:read"):
        return BackgroundJob.project_id.is_(None) | BackgroundJob.project_id.in_(
            await accessible_project_ids(session, principal)
        )
    return BackgroundJob.created_by_user_id == principal.user_id


async def _resolve_job(
    session: SessionDep,
    principal: Principal,
    job_id: UUID,
    *,
    for_update: bool = False,
) -> BackgroundJob:
    if not _can_read_jobs(principal):
        raise ApiError(403, "permission_denied", "You do not have permission for this action")
    statement = select(BackgroundJob).where(
        BackgroundJob.tenant_id == principal.tenant_id,
        BackgroundJob.id == job_id,
        await _job_scope_filter(session, principal),
    )
    if for_update:
        statement = statement.with_for_update(of=BackgroundJob)
    job = await session.scalar(statement)
    if job is None:
        raise ApiError(404, "background_job_not_found", "Background job was not found")
    return job


async def _list_jobs(
    *,
    session: SessionDep,
    principal: Principal,
    status: str | None,
    project_id: UUID | None,
    job_type: str | None,
    queue: str | None,
    limit: int,
    offset: int,
) -> Page[BackgroundJobRead]:
    if not _can_read_jobs(principal):
        raise ApiError(403, "permission_denied", "You do not have permission for this action")
    filters: list[ColumnElement[bool]] = [
        BackgroundJob.tenant_id == principal.tenant_id,
        await _job_scope_filter(session, principal),
    ]
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
        filters.append(BackgroundJob.project_id == project_id)
    if status:
        filters.append(BackgroundJob.status == status)
    if job_type:
        filters.append(BackgroundJob.type == job_type[:80])
    if queue:
        filters.append(BackgroundJob.queue == queue[:40])
    total = int(await session.scalar(select(func.count()).select_from(BackgroundJob).where(*filters)) or 0)
    rows = list(
        await session.scalars(
            select(BackgroundJob)
            .where(*filters)
            .order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[serialize_background_job(job) for job in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/background-jobs", response_model=Page[BackgroundJobRead])
async def list_background_jobs(
    session: SessionDep,
    principal: PrincipalDep,
    status: str | None = None,
    project_id: UUID | None = None,
    job_type: str | None = None,
    queue: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[BackgroundJobRead]:
    return await _list_jobs(
        session=session,
        principal=principal,
        status=status,
        project_id=project_id,
        job_type=job_type,
        queue=queue,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )


@router.get("/background-jobs/dead-letter", response_model=Page[BackgroundJobRead])
async def list_dead_letter_jobs(
    session: SessionDep,
    principal: PrincipalDep,
    project_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[BackgroundJobRead]:
    return await _list_jobs(
        session=session,
        principal=principal,
        status="dead_letter",
        project_id=project_id,
        job_type=None,
        queue=None,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )


@router.get("/background-jobs/stats", response_model=BackgroundQueueStatistics)
async def background_job_stats(session: SessionDep, principal: PrincipalDep) -> BackgroundQueueStatistics:
    if not _can_read_jobs(principal):
        raise ApiError(403, "permission_denied", "You do not have permission for this action")
    scope = await _job_scope_filter(session, principal)
    counts = {
        status: int(count)
        for status, count in (
            await session.execute(
                select(BackgroundJob.status, func.count())
                .where(BackgroundJob.tenant_id == principal.tenant_id, scope)
                .group_by(BackgroundJob.status)
            )
        ).all()
    }
    oldest = await session.scalar(
        select(func.min(BackgroundJob.created_at)).where(
            BackgroundJob.tenant_id == principal.tenant_id,
            scope,
            BackgroundJob.status.in_(("pending", "scheduled", "retry_wait")),
        )
    )
    return BackgroundQueueStatistics(
        queued=sum(counts.get(value, 0) for value in ("pending", "scheduled")),
        running=counts.get("running", 0),
        retry_wait=counts.get("retry_wait", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        dead_letter=counts.get("dead_letter", 0),
        cancel_requested=counts.get("cancel_requested", 0),
        oldest_pending_at=oldest,
        can_manage=_can_manage_jobs(principal),
    )


@router.get("/background-jobs/{job_id}", response_model=BackgroundJobRead)
async def get_background_job(job_id: UUID, session: SessionDep, principal: PrincipalDep) -> BackgroundJobRead:
    return serialize_background_job(await _resolve_job(session, principal, job_id))


@router.get("/background-jobs/{job_id}/attempts", response_model=list[BackgroundJobAttemptRead])
async def list_background_job_attempts(
    job_id: UUID, session: SessionDep, principal: PrincipalDep
) -> list[BackgroundJobAttemptRead]:
    job = await _resolve_job(session, principal, job_id)
    attempts = list(
        await session.scalars(
            select(BackgroundJobAttempt)
            .where(
                BackgroundJobAttempt.tenant_id == principal.tenant_id,
                BackgroundJobAttempt.job_id == job.id,
            )
            .order_by(BackgroundJobAttempt.attempt_number)
        )
    )
    return [
        BackgroundJobAttemptRead(
            id=item.id,
            job_id=item.job_id,
            attempt=item.attempt_number,
            worker_id=item.worker_id,
            status=item.status,
            started_at=item.started_at,
            completed_at=item.completed_at,
            duration_ms=(
                max(0, int((item.completed_at - item.started_at).total_seconds() * 1000))
                if item.completed_at
                else None
            ),
            safe_error_code=item.safe_error_code,
            safe_error_message=item.safe_error_message,
        )
        for item in attempts
    ]


@router.get("/background-jobs/{job_id}/events", response_model=list[BackgroundJobEventRead])
async def list_background_job_events(
    job_id: UUID, session: SessionDep, principal: PrincipalDep
) -> list[BackgroundJobEventRead]:
    job = await _resolve_job(session, principal, job_id)
    events = list(
        await session.scalars(
            select(BackgroundJobEvent)
            .where(
                BackgroundJobEvent.tenant_id == principal.tenant_id,
                BackgroundJobEvent.job_id == job.id,
            )
            .order_by(BackgroundJobEvent.occurred_at, BackgroundJobEvent.id)
        )
    )
    return [
        BackgroundJobEventRead(
            id=item.id,
            job_id=item.job_id,
            event_type=item.event_type,
            progress=(
                _safe_int(item.safe_snapshot["progress"])
                if isinstance(item.safe_snapshot.get("progress"), int)
                else None
            ),
            safe_snapshot=item.safe_snapshot,
            occurred_at=item.occurred_at,
        )
        for item in events
    ]


async def _job_command(
    *,
    command: str,
    job_id: UUID,
    payload: JobCommandRequest,
    request: Request,
    session: SessionDep,
    principal: Principal,
    idempotency_key: str,
) -> BackgroundJobRead:
    _require(principal, "jobs:manage")
    await acquire_idempotency_lock(
        session,
        namespace="job-command-submission",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    job = await _resolve_job(session, principal, job_id, for_update=True)
    fingerprint = sha256(
        f"{command}:{job.id}:{payload.expected_version}:{payload.reason or ''}".encode()
    ).hexdigest()
    previous = await session.scalar(
        select(JobCommandSubmission).where(
            JobCommandSubmission.tenant_id == principal.tenant_id,
            JobCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if previous is not None:
        if (
            previous.job_id != job.id
            or previous.command != command
            or previous.request_fingerprint != fingerprint
        ):
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
        return serialize_background_job(job)
    if (command, job.type) in _DOMAIN_MANAGED_JOB_COMMANDS:
        raise ApiError(
            409,
            "domain_job_command_required",
            "Use the customer import or knowledge document API for this job command",
        )
    if command == "retry" and job.result_metadata.get("retention_payload_purged") is True:
        raise ApiError(
            409,
            "background_job_payload_purged",
            "Background job payload was removed by retention and cannot be retried",
        )
    if job.lock_version != payload.expected_version:
        raise ApiError(409, "background_job_conflict", "Background job changed; refresh and retry")
    now = datetime.now(UTC)
    if command == "cancel":
        if job.status not in _JOB_CANCELABLE or job.status == "cancel_requested":
            raise ApiError(409, "background_job_not_cancelable", "Background job cannot be cancelled")
        if job.status == "running":
            job.status = "cancel_requested"
            event_type = "cancel_requested"
        else:
            job.status = "cancelled"
            job.cancelled_at = now
            event_type = "cancelled"
        if job.type == "knowledge.ingest_document":
            ingestion = await session.scalar(
                select(DocumentIngestionJob)
                .where(
                    DocumentIngestionJob.tenant_id == principal.tenant_id,
                    DocumentIngestionJob.background_job_id == job.id,
                )
                .with_for_update()
            )
            if ingestion is not None and ingestion.status not in {"succeeded", "cancelled"}:
                ingestion.status = "cancelled"
                ingestion.stage = "failed"
                ingestion.safe_error_code = "cancelled"
                ingestion.lease_token = None
                ingestion.lease_expires_at = None
                ingestion.completed_at = now
                version = await session.scalar(
                    select(KnowledgeDocumentVersion)
                    .where(
                        KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
                        KnowledgeDocumentVersion.id == ingestion.document_version_id,
                    )
                    .with_for_update()
                )
                if version is not None and version.status in {
                    "queued",
                    "extracting",
                    "chunking",
                    "embedding",
                }:
                    version.status = "failed"
                    version.safe_error_code = "cancelled"
                    version.safe_error_message = "Document processing was cancelled"
                    version.failed_at = now
                    version.lock_version += 1
    else:
        if job.status not in _JOB_RETRYABLE:
            raise ApiError(409, "background_job_not_retryable", "Background job cannot be retried")
        job.status = "pending"
        job.available_at = now
        job.progress = 0
        job.safe_error_code = None
        job.safe_error_message = None
        if job.attempt_count >= job.max_attempts:
            job.max_attempts = job.attempt_count + 1
        event_type = "manual_retry_requested"
    job.lock_version += 1
    job.updated_at = now
    submission = JobCommandSubmission(
        tenant_id=principal.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        submitted_by_user_id=principal.user_id,
        command=command,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        status="completed",
        response_metadata={"status": job.status, "version": job.lock_version},
    )
    session.add(submission)
    await append_background_job_event(
        session,
        job=job,
        event_type=event_type,
        safe_snapshot={"status": job.status, "version": job.lock_version},
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=job.project_id,
        event_type="job.cancelled" if job.status == "cancelled" else "job.progress",
        aggregate_type="background_job",
        aggregate_id=job.id,
        aggregate_version=job.lock_version,
        payload={"status": job.status, "progress": job.progress},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action=f"background_job.{command}",
        resource_type="background_job",
        resource_id=job.id,
        correlation_id=request.state.correlation_id,
        reason=payload.reason,
        safe_metadata={"status": job.status},
    )
    await session.flush()
    response = serialize_background_job(job)
    await session.commit()
    return response


@router.post("/background-jobs/{job_id}/cancel", response_model=BackgroundJobRead)
async def cancel_background_job(
    job_id: UUID,
    payload: JobCommandRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundJobRead:
    return await _job_command(
        command="cancel",
        job_id=job_id,
        payload=payload,
        request=request,
        session=session,
        principal=principal,
        idempotency_key=idempotency_key,
    )


@router.post("/background-jobs/{job_id}/retry", response_model=BackgroundJobRead)
async def retry_background_job(
    job_id: UUID,
    payload: JobCommandRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundJobRead:
    return await _job_command(
        command="retry",
        job_id=job_id,
        payload=payload,
        request=request,
        session=session,
        principal=principal,
        idempotency_key=idempotency_key,
    )


@router.get("/storage/summary", response_model=StorageSummaryRead)
async def storage_summary(session: SessionDep, principal: PrincipalDep) -> StorageSummaryRead:
    _require(principal, "storage:read")
    project_ids = await accessible_project_ids(session, principal)
    scope = StorageObject.project_id.is_(None) | StorageObject.project_id.in_(project_ids)
    grouped = (
        await session.execute(
            select(StorageObject.category, func.count(), func.coalesce(func.sum(StorageObject.size_bytes), 0))
            .where(StorageObject.tenant_id == principal.tenant_id, scope)
            .group_by(StorageObject.category)
            .order_by(StorageObject.category)
        )
    ).all()
    issue_scope = StorageConsistencyIssue.project_id.is_(None) | StorageConsistencyIssue.project_id.in_(
        project_ids
    )
    issue_count = int(
        await session.scalar(
            select(func.count())
            .select_from(StorageConsistencyIssue)
            .where(
                StorageConsistencyIssue.tenant_id == principal.tenant_id,
                issue_scope,
                StorageConsistencyIssue.status.in_(("open", "confirmed")),
            )
        )
        or 0
    )
    pending = int(
        await session.scalar(
            select(func.count())
            .select_from(StorageObject)
            .where(
                StorageObject.tenant_id == principal.tenant_id,
                scope,
                StorageObject.retention_state == "pending_purge",
            )
        )
        or 0
    )
    last_scan = await session.scalar(
        select(func.max(StorageConsistencyIssue.last_detected_at)).where(
            StorageConsistencyIssue.tenant_id == principal.tenant_id,
            issue_scope,
        )
    )
    categories = [
        StorageCategorySummary(category=category, object_count=int(count), bytes=int(size))
        for category, count, size in grouped
    ]
    return StorageSummaryRead(
        object_count=sum(item.object_count for item in categories),
        total_bytes=sum(item.bytes for item in categories),
        issue_count=issue_count,
        pending_purge_count=pending,
        categories=categories,
        last_scan_at=last_scan,
        can_scan=role_has_permission(principal.role, "storage:manage"),
    )


@router.get("/storage/issues", response_model=Page[StorageConsistencyIssueRead])
async def storage_issues(
    session: SessionDep,
    principal: PrincipalDep,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[StorageConsistencyIssueRead]:
    _require(principal, "storage:read")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_ids = await accessible_project_ids(session, principal)
    filters: list[ColumnElement[bool]] = [
        StorageConsistencyIssue.tenant_id == principal.tenant_id,
        StorageConsistencyIssue.project_id.is_(None) | StorageConsistencyIssue.project_id.in_(project_ids),
    ]
    if status:
        filters.append(StorageConsistencyIssue.status == status)
    total = int(
        await session.scalar(select(func.count()).select_from(StorageConsistencyIssue).where(*filters)) or 0
    )
    rows = (
        await session.execute(
            select(StorageConsistencyIssue, StorageObject.category)
            .outerjoin(
                StorageObject,
                and_(
                    StorageObject.tenant_id == StorageConsistencyIssue.tenant_id,
                    StorageObject.id == StorageConsistencyIssue.storage_object_id,
                ),
            )
            .where(*filters)
            .order_by(StorageConsistencyIssue.first_detected_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    status_map = {
        "open": "candidate",
        "confirmed": "confirmed",
        "resolved": "resolved",
        "ignored": "resolved",
    }
    type_map = {"incomplete_multipart": "multipart_upload"}
    items = [
        StorageConsistencyIssueRead(
            id=issue.id,
            project_id=issue.project_id,
            object_id=issue.storage_object_id,
            issue_type=type_map.get(issue.issue_type, issue.issue_type),
            status=status_map.get(issue.status, issue.status),
            object_category=category or str(issue.safe_metadata.get("category", "unknown")),
            detected_at=issue.first_detected_at,
            last_verified_at=issue.last_detected_at,
            grace_expires_at=issue.grace_until,
        )
        for issue, category in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/storage/scan", response_model=dict[str, UUID], status_code=202)
async def start_storage_scan(
    payload: StorageScanRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> dict[str, UUID]:
    _require(principal, "storage:manage")
    if payload.project_id is not None:
        await resolve_project(session, principal, payload.project_id, active_only=False)
    job, _ = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        project_id=payload.project_id,
        created_by_user_id=principal.user_id,
        job_type="storage.orphan_scan",
        queue="maintenance",
        priority=30,
        safe_payload={"project_id": payload.project_id, "dry_run": payload.dry_run},
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="storage.consistency_scan_requested",
        resource_type="background_job",
        resource_id=job.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"dry_run": payload.dry_run},
    )
    await session.commit()
    return {"job_id": job.id}


def _policy_read(policy: RetentionPolicy, principal: Principal) -> RetentionPolicyRead:
    return RetentionPolicyRead(
        id=policy.id,
        automatic_purge_enabled=policy.enabled,
        grace_period_days=policy.grace_period_days,
        call_recording_days=policy.recording_days,
        transcript_days=policy.transcript_days,
        temporary_import_days=policy.temporary_import_days,
        import_report_days=policy.import_report_days,
        archived_knowledge_days=policy.archived_knowledge_days,
        realtime_event_hours=(policy.realtime_event_days * 24 if policy.realtime_event_days else None),
        completed_job_days=policy.completed_job_days,
        failed_job_days=policy.failed_job_days,
        state_version=policy.policy_version,
        updated_at=policy.updated_at,
        can_manage=role_has_permission(principal.role, "retention:manage"),
    )


async def _tenant_policy(
    session: SessionDep, principal: Principal, *, for_update: bool = False
) -> RetentionPolicy:
    statement = select(RetentionPolicy).where(
        RetentionPolicy.tenant_id == principal.tenant_id,
        RetentionPolicy.project_id.is_(None),
    )
    if for_update:
        statement = statement.with_for_update(of=RetentionPolicy)
    policy = await session.scalar(statement)
    if policy is None:
        raise ApiError(404, "retention_policy_not_found", "Retention policy was not found")
    return policy


@router.get("/retention/policy", response_model=RetentionPolicyRead)
async def get_retention_policy(session: SessionDep, principal: PrincipalDep) -> RetentionPolicyRead:
    _require(principal, "retention:read")
    return _policy_read(await _tenant_policy(session, principal), principal)


@router.patch("/retention/policy", response_model=RetentionPolicyRead)
async def update_retention_policy(
    payload: RetentionPolicyUpdate,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> RetentionPolicyRead:
    _require(principal, "retention:manage")
    await acquire_retention_lock(session, principal.tenant_id)
    policy = await _tenant_policy(session, principal, for_update=True)
    if policy.policy_version != payload.expected_version:
        raise ApiError(409, "retention_policy_conflict", "Retention policy changed; refresh and retry")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    field_map = {
        "automatic_purge_enabled": "enabled",
        "call_recording_days": "recording_days",
        "realtime_event_hours": "realtime_event_days",
    }
    changed_fields: list[str] = []
    for field, value in changes.items():
        target = field_map.get(field, field)
        if field == "realtime_event_hours" and value is not None:
            # The current schema stores whole days. Round up so retention never
            # purges earlier than the administrator's requested hour window.
            value = max(1, (int(value) + 23) // 24)
        if getattr(policy, target) != value:
            setattr(policy, target, value)
            changed_fields.append(field)
    if changed_fields:
        policy.policy_version += 1
        policy.updated_by_user_id = principal.user_id
        policy.updated_at = datetime.now(UTC)
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="retention.policy_updated",
            resource_type="retention_policy",
            resource_id=policy.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"changed_fields": sorted(changed_fields), "enabled": policy.enabled},
        )
        await session.flush()
        response = _policy_read(policy, principal)
        await session.commit()
        return response
    return _policy_read(policy, principal)


def _eligible_storage_filter(policy: RetentionPolicy, now: datetime) -> ColumnElement[bool]:
    rules: list[ColumnElement[bool]] = []
    configured = (
        ("call_recording", policy.recording_days, False),
        ("temporary_preview", policy.temporary_import_days, False),
        ("import_source", policy.temporary_import_days, False),
        ("import_report", policy.import_report_days, False),
        ("generated_report", policy.import_report_days, False),
        ("knowledge_original", policy.archived_knowledge_days, True),
    )
    for category, days, archived_only in configured:
        if days is None:
            continue
        age_filter = func.coalesce(StorageObject.archived_at, StorageObject.created_at) <= now - timedelta(
            days=days
        )
        category_filter = StorageObject.category == category
        if archived_only:
            category_filter = and_(category_filter, StorageObject.status == "archived")
        operational_expiry = or_(
            StorageObject.expires_at.is_(None),
            StorageObject.expires_at <= now,
        )
        rules.append(and_(category_filter, age_filter, operational_expiry))
    return or_(*rules) if rules else StorageObject.id.is_(None)


async def _storage_preview_block_reasons(
    session: SessionDep,
    *,
    tenant_id: UUID,
    storages: list[StorageObject],
    holds: list[LegalHold] | None = None,
) -> dict[UUID, str]:
    """Resolve holds/live references in bounded set-based queries for one preview page."""

    if not storages:
        return {}
    storage_ids = [storage.id for storage in storages]
    reasons: dict[UUID, str] = {}
    if holds is None:
        holds = list(
            await session.scalars(
                select(LegalHold).where(
                    LegalHold.tenant_id == tenant_id,
                    LegalHold.released_at.is_(None),
                )
            )
        )
    project_holds = {
        hold.scope_id for hold in holds if hold.scope_type == "project" and hold.scope_id is not None
    }
    scoped_holds = {
        (hold.scope_type, hold.scope_id)
        for hold in holds
        if hold.scope_type in {"call", "customer", "document"} and hold.scope_id is not None
    }
    if any(hold.scope_type == "tenant" for hold in holds):
        return {storage.id: "legal_hold" for storage in storages}
    for storage in storages:
        if (
            storage.project_id in project_holds
            or (
                storage.owner_aggregate_type,
                storage.owner_aggregate_id,
            )
            in scoped_holds
        ):
            reasons[storage.id] = "legal_hold"

    recording_rows = (
        await session.execute(
            select(CallRecording.storage_object_id, Call.id, Call.customer_id, Call.status)
            .join(
                Call,
                and_(
                    Call.tenant_id == CallRecording.tenant_id,
                    Call.id == CallRecording.call_id,
                ),
            )
            .where(
                CallRecording.tenant_id == tenant_id,
                CallRecording.storage_object_id.in_(storage_ids),
            )
        )
    ).all()
    active_call_states = {
        "queued",
        "initiated",
        "ringing",
        "active",
        "on_hold",
        "transfer_requested",
        "transferring",
        "transferred",
    }
    for storage_id, call_id, customer_id, status in recording_rows:
        if storage_id is None:
            continue
        if ("call", call_id) in scoped_holds or ("customer", customer_id) in scoped_holds:
            reasons[storage_id] = "legal_hold"
        elif status in active_call_states and storage_id not in reasons:
            reasons[storage_id] = "active_reference"

    knowledge_rows = (
        await session.execute(
            select(
                KnowledgeDocumentVersion.storage_object_id,
                KnowledgeDocumentVersion.document_id,
                KnowledgeDocumentVersion.status,
                KnowledgeBaseRevision.status,
                KnowledgeBaseRevision.id,
                KnowledgeSource.active_revision_id,
            )
            .join(
                KnowledgeBaseRevision,
                and_(
                    KnowledgeBaseRevision.tenant_id == KnowledgeDocumentVersion.tenant_id,
                    KnowledgeBaseRevision.id == KnowledgeDocumentVersion.revision_id,
                ),
            )
            .join(
                KnowledgeSource,
                and_(
                    KnowledgeSource.tenant_id == KnowledgeBaseRevision.tenant_id,
                    KnowledgeSource.id == KnowledgeBaseRevision.knowledge_source_id,
                ),
            )
            .where(
                KnowledgeDocumentVersion.tenant_id == tenant_id,
                KnowledgeDocumentVersion.storage_object_id.in_(storage_ids),
            )
        )
    ).all()
    for (
        storage_id,
        document_id,
        version_status,
        revision_status,
        revision_id,
        active_revision_id,
    ) in knowledge_rows:
        if storage_id is None:
            continue
        if ("document", document_id) in scoped_holds:
            reasons[storage_id] = "legal_hold"
        elif (
            version_status != "archived"
            and (revision_status == "draft" or active_revision_id == revision_id)
            and storage_id not in reasons
        ):
            reasons[storage_id] = "active_reference"

    import_rows = (
        await session.execute(
            select(
                CustomerImport.source_storage_object_id,
                CustomerImport.report_storage_object_id,
                CustomerImport.status,
                BackgroundJob.status,
            )
            .outerjoin(
                BackgroundJob,
                and_(
                    BackgroundJob.tenant_id == CustomerImport.tenant_id,
                    BackgroundJob.id == CustomerImport.background_job_id,
                ),
            )
            .where(
                CustomerImport.tenant_id == tenant_id,
                or_(
                    CustomerImport.source_storage_object_id.in_(storage_ids),
                    CustomerImport.report_storage_object_id.in_(storage_ids),
                ),
            )
        )
    ).all()
    active_import_states = {
        "processing_preview",
        "ready",
        "queued",
        "running",
        "finalizing",
        "cancel_requested",
    }
    active_job_states = {"pending", "scheduled", "running", "retry_wait", "cancel_requested"}
    for source_id, report_id, import_status, job_status in import_rows:
        if import_status not in active_import_states and job_status not in active_job_states:
            continue
        for storage_id in (source_id, report_id):
            if storage_id is not None and storage_id not in reasons:
                reasons[storage_id] = "active_reference"
    return reasons


async def _database_preview_block_reasons(
    session: SessionDep,
    *,
    tenant_id: UUID,
    candidates: list[RetentionCandidate],
    policy: RetentionPolicy,
    now: datetime,
    holds: list[LegalHold],
) -> dict[UUID, str]:
    """Recheck database-only candidates against current resources and legal holds."""

    if not candidates:
        return {}
    tenant_held = any(hold.scope_type == "tenant" for hold in holds)
    project_holds = {
        hold.scope_id for hold in holds if hold.scope_type == "project" and hold.scope_id is not None
    }
    scoped_holds = {
        (hold.scope_type, hold.scope_id)
        for hold in holds
        if hold.scope_type in {"call", "customer", "document"} and hold.scope_id is not None
    }

    transcript_ids = {candidate.resource_id for candidate in candidates if candidate.category == "transcript"}
    job_ids = {
        candidate.resource_id
        for candidate in candidates
        if candidate.category in {"completed_job", "failed_job"}
    }
    event_ids = {candidate.resource_id for candidate in candidates if candidate.category == "realtime_event"}
    events = (
        {
            event.id: event
            for event in (
                await session.scalars(
                    select(RealtimeEvent).where(
                        RealtimeEvent.tenant_id == tenant_id,
                        RealtimeEvent.id.in_(event_ids),
                    )
                )
            ).all()
        }
        if event_ids
        else {}
    )
    call_ids = transcript_ids | {
        event.aggregate_id for event in events.values() if event.aggregate_type == "call"
    }
    calls = (
        {
            call.id: call
            for call in (
                await session.scalars(select(Call).where(Call.tenant_id == tenant_id, Call.id.in_(call_ids)))
            ).all()
        }
        if call_ids
        else {}
    )
    live_transcript_ids = (
        set(
            (
                await session.scalars(
                    select(TranscriptSegment.call_id).where(
                        TranscriptSegment.tenant_id == tenant_id,
                        TranscriptSegment.call_id.in_(transcript_ids),
                        TranscriptSegment.retention_redacted_at.is_(None),
                    )
                )
            ).all()
        )
        if transcript_ids
        else set()
    )
    jobs = (
        {
            background.id: background
            for background in (
                await session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.tenant_id == tenant_id,
                        BackgroundJob.id.in_(job_ids),
                    )
                )
            ).all()
        }
        if job_ids
        else {}
    )
    version_ids = {
        event.aggregate_id
        for event in events.values()
        if event.aggregate_type == "knowledge_document_version"
    }
    document_ids_by_version = (
        {
            version.id: version.document_id
            for version in (
                await session.scalars(
                    select(KnowledgeDocumentVersion).where(
                        KnowledgeDocumentVersion.tenant_id == tenant_id,
                        KnowledgeDocumentVersion.id.in_(version_ids),
                    )
                )
            ).all()
        }
        if version_ids
        else {}
    )

    terminal_call_states = {"completed", "busy", "no_answer", "failed", "cancelled"}
    reasons: dict[UUID, str] = {}
    for candidate in candidates:
        current = False
        project_id = candidate.project_id
        resource_holds: set[tuple[str, UUID | None]] = set()
        if candidate.category == "transcript":
            call = calls.get(candidate.resource_id)
            if call is not None:
                project_id = call.project_id
                resource_holds.add(("call", call.id))
                resource_holds.add(("customer", call.customer_id))
                current = (
                    policy.transcript_days is not None
                    and call.status in terminal_call_states
                    and call.ended_at is not None
                    and call.ended_at <= now - timedelta(days=policy.transcript_days)
                    and call.id in live_transcript_ids
                )
        elif candidate.category in {"completed_job", "failed_job"}:
            background = jobs.get(candidate.resource_id)
            if background is not None:
                project_id = background.project_id
                if candidate.category == "completed_job":
                    retention_days = policy.completed_job_days
                    aged_at = background.completed_at or background.updated_at
                    valid_status = background.status == "completed"
                else:
                    retention_days = policy.failed_job_days
                    aged_at = background.completed_at or background.cancelled_at or background.updated_at
                    valid_status = background.status in {"failed", "dead_letter", "cancelled"}
                current = (
                    retention_days is not None
                    and valid_status
                    and aged_at is not None
                    and aged_at <= now - timedelta(days=retention_days)
                )
        elif candidate.category == "realtime_event":
            event = events.get(candidate.resource_id)
            if event is not None:
                project_id = event.project_id
                current = (
                    policy.realtime_event_days is not None
                    and event.publish_status == "published"
                    and event.created_at <= now - timedelta(days=policy.realtime_event_days)
                )
                if event.aggregate_type in {"call", "customer", "document"}:
                    resource_holds.add((event.aggregate_type, event.aggregate_id))
                elif event.aggregate_type == "knowledge_document":
                    resource_holds.add(("document", event.aggregate_id))
                elif event.aggregate_type == "knowledge_document_version":
                    resource_holds.add(("document", document_ids_by_version.get(event.aggregate_id)))
                if event.aggregate_type == "call":
                    call = calls.get(event.aggregate_id)
                    if call is not None:
                        resource_holds.add(("customer", call.customer_id))

        if not current:
            reasons[candidate.id] = "active_reference"
        elif (
            tenant_held
            or project_id in project_holds
            or any(scope in scoped_holds for scope in resource_holds if scope[1] is not None)
        ):
            reasons[candidate.id] = "legal_hold"
    return reasons


@router.post("/retention/preview", response_model=RetentionPreviewRead, status_code=201)
async def retention_preview(
    payload: RetentionPreviewRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> RetentionPreviewRead:
    _require(principal, "retention:manage")
    await acquire_retention_lock(session, principal.tenant_id)
    policy = await _tenant_policy(session, principal, for_update=True)
    if policy.policy_version != payload.policy_version:
        raise ApiError(409, "retention_policy_conflict", "Retention policy changed; refresh and retry")
    now = datetime.now(UTC)
    project_ids = await accessible_project_ids(session, principal)
    scope = StorageObject.project_id.is_(None) | StorageObject.project_id.in_(project_ids)
    active_holds = list(
        (
            await session.scalars(
                select(LegalHold).where(
                    LegalHold.tenant_id == principal.tenant_id,
                    LegalHold.released_at.is_(None),
                )
            )
        ).all()
    )
    global_hold = any(hold.scope_type == "tenant" for hold in active_holds)
    job, created = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        created_by_user_id=principal.user_id,
        job_type="retention.preview",
        queue="maintenance",
        priority=20,
        safe_payload={"policy_id": policy.id, "policy_version": policy.policy_version},
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    if created:
        eligible_filter = _eligible_storage_filter(policy, now)
        base_filters = (
            StorageObject.tenant_id == principal.tenant_id,
            scope,
            eligible_filter,
            StorageObject.status.in_(("active", "archived")),
        )
        held_filters: tuple[ColumnElement[bool], ...] = base_filters
        if not global_hold:
            held_filters = (*held_filters, StorageObject.legal_hold.is_(True))
        held_count = int(
            await session.scalar(select(func.count()).select_from(StorageObject).where(*held_filters)) or 0
        )
        active_excluded = 0
        if not global_hold:
            cursor: UUID | None = None
            while True:
                object_filters: list[ColumnElement[bool]] = [
                    *base_filters,
                    StorageObject.legal_hold.is_(False),
                ]
                if cursor is not None:
                    object_filters.append(StorageObject.id > cursor)
                objects = list(
                    (
                        await session.scalars(
                            select(StorageObject)
                            .where(*object_filters)
                            .order_by(StorageObject.id)
                            .limit(500)
                            .with_for_update(of=StorageObject)
                        )
                    ).all()
                )
                if not objects:
                    break
                keys = {f"retention:{policy.id}:{policy.policy_version}:{storage.id}" for storage in objects}
                existing = {
                    candidate.idempotency_key: candidate
                    for candidate in (
                        await session.scalars(
                            select(RetentionCandidate).where(
                                RetentionCandidate.tenant_id == principal.tenant_id,
                                RetentionCandidate.idempotency_key.in_(keys),
                            )
                        )
                    ).all()
                }
                block_reasons = await _storage_preview_block_reasons(
                    session,
                    tenant_id=principal.tenant_id,
                    storages=objects,
                    holds=active_holds,
                )
                for storage in objects:
                    block_reason = block_reasons.get(storage.id)
                    if block_reason == "legal_hold":
                        held_count += 1
                        continue
                    if block_reason == "active_reference":
                        active_excluded += 1
                        continue
                    key = f"retention:{policy.id}:{policy.policy_version}:{storage.id}"
                    candidate = existing.get(key)
                    if candidate is None:
                        candidate = RetentionCandidate(
                            tenant_id=principal.tenant_id,
                            project_id=storage.project_id,
                            policy_id=policy.id,
                            storage_object_id=storage.id,
                            category=storage.category,
                            resource_type=storage.owner_aggregate_type or "storage_object",
                            resource_id=storage.owner_aggregate_id or storage.id,
                            policy_version=policy.policy_version,
                            status="eligible",
                            eligible_at=now,
                            idempotency_key=key,
                            safe_metadata={"preview_id": str(job.id)},
                            lock_version=1,
                        )
                        session.add(candidate)
                    elif candidate.status in {"eligible", "blocked"}:
                        # The preview has just re-evaluated all current hold and
                        # live-reference blockers for this object. Re-open a
                        # previously blocked candidate immediately once those
                        # blockers are gone instead of omitting it until the
                        # hourly maintenance pass.
                        candidate.status = "eligible"
                        candidate.safe_metadata = {
                            **{
                                key: value
                                for key, value in candidate.safe_metadata.items()
                                if key != "blocked_reason"
                            },
                            "preview_id": str(job.id),
                        }
                        candidate.lock_version += 1
                    else:
                        continue
                    storage.retention_state = "eligible"
                    storage.lock_version += 1
                await session.flush()
                cursor = objects[-1].id

        # Database-only candidates are discovered by the scheduled dry-run
        # scanner. Re-evaluate its reversible eligible/blocked candidates so
        # the materialized preview reflects holds and resource state now.
        database_cursor: UUID | None = None
        while True:
            database_filters: list[ColumnElement[bool]] = [
                RetentionCandidate.tenant_id == principal.tenant_id,
                RetentionCandidate.policy_id == policy.id,
                RetentionCandidate.policy_version == policy.policy_version,
                RetentionCandidate.storage_object_id.is_(None),
                RetentionCandidate.status.in_(("eligible", "blocked")),
                RetentionCandidate.project_id.is_(None) | RetentionCandidate.project_id.in_(project_ids),
            ]
            if database_cursor is not None:
                database_filters.append(RetentionCandidate.id > database_cursor)
            database_candidates = list(
                (
                    await session.scalars(
                        select(RetentionCandidate)
                        .where(*database_filters)
                        .order_by(RetentionCandidate.id)
                        .limit(500)
                        .with_for_update(of=RetentionCandidate)
                    )
                ).all()
            )
            if not database_candidates:
                break
            block_reasons = await _database_preview_block_reasons(
                session,
                tenant_id=principal.tenant_id,
                candidates=database_candidates,
                policy=policy,
                now=now,
                holds=active_holds,
            )
            for candidate in database_candidates:
                block_reason = block_reasons.get(candidate.id)
                metadata = {
                    key: value
                    for key, value in candidate.safe_metadata.items()
                    if key not in {"blocked_reason", "preview_id"}
                }
                if block_reason is not None:
                    candidate.status = "blocked"
                    candidate.pending_purge_at = None
                    candidate.grace_until = None
                    candidate.safe_metadata = {**metadata, "blocked_reason": block_reason}
                    if block_reason == "legal_hold":
                        held_count += 1
                    else:
                        active_excluded += 1
                else:
                    candidate.status = "eligible"
                    candidate.safe_metadata = {**metadata, "preview_id": str(job.id)}
                candidate.lock_version += 1
            await session.flush()
            database_cursor = database_candidates[-1].id

        reviewed_filters = (
            RetentionCandidate.tenant_id == principal.tenant_id,
            RetentionCandidate.policy_id == policy.id,
            RetentionCandidate.policy_version == policy.policy_version,
            RetentionCandidate.status == "eligible",
            RetentionCandidate.safe_metadata["preview_id"].as_string() == str(job.id),
        )
        eligible_count = int(
            await session.scalar(
                select(func.count()).select_from(RetentionCandidate).where(*reviewed_filters)
            )
            or 0
        )
        eligible_bytes = int(
            await session.scalar(
                select(func.coalesce(func.sum(StorageObject.size_bytes), 0))
                .select_from(RetentionCandidate)
                .join(
                    StorageObject,
                    and_(
                        StorageObject.tenant_id == RetentionCandidate.tenant_id,
                        StorageObject.id == RetentionCandidate.storage_object_id,
                    ),
                )
                .where(*reviewed_filters)
            )
            or 0
        )
        job.status = "completed"
        job.progress = 100
        job.started_at = now
        job.completed_at = now
        job.result_metadata = {
            "policy_version": policy.policy_version,
            "eligible_objects": int(eligible_count),
            "eligible_bytes": int(eligible_bytes),
            "excluded_by_legal_hold": held_count,
            "excluded_by_active_reference": active_excluded,
            "candidate_count": int(eligible_count),
        }
        job.lock_version += 1
        await append_background_job_event(
            session,
            job=job,
            event_type="completed",
            safe_snapshot={"status": "completed", "eligible_objects": int(eligible_count)},
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
        )
        await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            event_type="retention.preview_ready",
            aggregate_type="background_job",
            aggregate_id=job.id,
            aggregate_version=job.lock_version,
            payload={"eligible_objects": int(eligible_count)},
            correlation_id=request.state.correlation_id,
        )
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="retention.preview_created",
            resource_type="background_job",
            resource_id=job.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"policy_version": policy.policy_version},
        )
        await session.commit()
    result = job.result_metadata
    return RetentionPreviewRead(
        id=job.id,
        policy_version=_safe_int(result.get("policy_version"), policy.policy_version),
        eligible_objects=_safe_int(result.get("eligible_objects")),
        eligible_bytes=_safe_int(result.get("eligible_bytes")),
        excluded_by_legal_hold=_safe_int(result.get("excluded_by_legal_hold")),
        excluded_by_active_reference=_safe_int(result.get("excluded_by_active_reference")),
        created_at=job.created_at,
    )


@router.post("/retention/run", response_model=dict[str, UUID], status_code=202)
async def run_retention(
    payload: RetentionRunRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> dict[str, UUID]:
    _require(principal, "retention:manage")
    await acquire_retention_lock(session, principal.tenant_id)
    policy = await _tenant_policy(session, principal, for_update=True)
    # Resolve an exact replay before mutable preview/candidate validation. A new
    # enqueue remains in this transaction and is rolled back with any validation
    # error below, so an invalid request can never leave a runnable job behind.
    job, created = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        created_by_user_id=principal.user_id,
        job_type="retention.eligibility_scan",
        queue="maintenance",
        priority=25,
        safe_payload={
            "policy_id": policy.id,
            "policy_version": payload.policy_version,
            "preview_id": payload.preview_id,
        },
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
        causation_id=payload.preview_id,
    )
    if not created:
        return {"job_id": job.id}
    if policy.policy_version != payload.policy_version:
        raise ApiError(409, "retention_policy_conflict", "Retention policy changed; refresh and retry")
    if not policy.enabled:
        raise ApiError(
            409,
            "retention_automatic_purge_disabled",
            "Automatic purge is disabled; enable the reviewed policy first",
        )
    preview = await session.scalar(
        select(BackgroundJob).where(
            BackgroundJob.tenant_id == principal.tenant_id,
            BackgroundJob.id == payload.preview_id,
            BackgroundJob.type == "retention.preview",
            BackgroundJob.status == "completed",
        )
    )
    if preview is None or preview.result_metadata.get("policy_version") != policy.policy_version:
        raise ApiError(409, "retention_preview_stale", "Create a new retention preview")
    expected_candidates = _safe_int(preview.result_metadata.get("candidate_count"), -1)
    reviewed_candidates = int(
        await session.scalar(
            select(func.count())
            .select_from(RetentionCandidate)
            .where(
                RetentionCandidate.tenant_id == principal.tenant_id,
                RetentionCandidate.policy_id == policy.id,
                RetentionCandidate.policy_version == policy.policy_version,
                RetentionCandidate.status == "eligible",
                RetentionCandidate.safe_metadata["preview_id"].as_string() == str(preview.id),
            )
        )
        or 0
    )
    if expected_candidates < 0 or reviewed_candidates != expected_candidates:
        raise ApiError(
            409,
            "retention_preview_stale",
            "The reviewed retention candidate set changed; create a new preview",
        )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="retention.run_requested",
        resource_type="background_job",
        resource_id=job.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"policy_version": policy.policy_version},
    )
    await session.commit()
    return {"job_id": job.id}


def _candidate_read(
    candidate: RetentionCandidate, storage: StorageObject | None, principal: Principal
) -> RetentionCandidateRead:
    return RetentionCandidateRead(
        id=candidate.id,
        project_id=candidate.project_id,
        object_category=candidate.category,
        owner_aggregate_type=candidate.resource_type,
        owner_aggregate_id=candidate.resource_id,
        status=candidate.status,
        eligible_at=candidate.eligible_at,
        purge_after=candidate.grace_until,
        bytes=storage.size_bytes if storage else 0,
        legal_hold=storage.legal_hold if storage else False,
        can_cancel=(
            role_has_permission(principal.role, "retention:manage")
            and candidate.status in {"eligible", "pending_purge", "blocked"}
        ),
        state_version=candidate.lock_version,
    )


@router.get("/retention/candidates", response_model=Page[RetentionCandidateRead])
async def list_retention_candidates(
    session: SessionDep,
    principal: PrincipalDep,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[RetentionCandidateRead]:
    _require(principal, "retention:read")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_ids = await accessible_project_ids(session, principal)
    filters: list[ColumnElement[bool]] = [
        RetentionCandidate.tenant_id == principal.tenant_id,
        RetentionCandidate.project_id.is_(None) | RetentionCandidate.project_id.in_(project_ids),
    ]
    if status:
        filters.append(RetentionCandidate.status == status)
    total = int(
        await session.scalar(select(func.count()).select_from(RetentionCandidate).where(*filters)) or 0
    )
    rows = (
        await session.execute(
            select(RetentionCandidate, StorageObject)
            .outerjoin(
                StorageObject,
                and_(
                    StorageObject.tenant_id == RetentionCandidate.tenant_id,
                    StorageObject.id == RetentionCandidate.storage_object_id,
                ),
            )
            .where(*filters)
            .order_by(RetentionCandidate.eligible_at.desc(), RetentionCandidate.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[_candidate_read(candidate, storage, principal) for candidate, storage in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/retention/candidates/{candidate_id}/cancel", response_model=RetentionCandidateRead)
async def cancel_retention_candidate(
    candidate_id: UUID,
    payload: JobCommandRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> RetentionCandidateRead:
    _require(principal, "retention:manage")
    fingerprint = _admin_command_fingerprint("retention.candidate.cancel", candidate_id, payload)
    await acquire_idempotency_lock(
        session,
        namespace="team",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    await acquire_retention_lock(session, principal.tenant_id)
    candidate = await session.scalar(
        select(RetentionCandidate)
        .where(
            RetentionCandidate.tenant_id == principal.tenant_id,
            RetentionCandidate.id == candidate_id,
        )
        .with_for_update()
    )
    if candidate is None:
        raise ApiError(404, "retention_candidate_not_found", "Retention candidate was not found")
    if candidate.project_id is not None:
        await resolve_project(session, principal, candidate.project_id, active_only=False)
    replay = await session.scalar(
        select(TeamCommandSubmission).where(
            TeamCommandSubmission.tenant_id == principal.tenant_id,
            TeamCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if (
            replay.operation != "retention.candidate.cancel"
            or replay.resource_id != candidate.id
            or replay.request_fingerprint != fingerprint
        ):
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
        return RetentionCandidateRead.model_validate(replay.response_payload)
    if candidate.status == "cancelled":
        storage = (
            await session.scalar(
                select(StorageObject).where(
                    StorageObject.tenant_id == principal.tenant_id,
                    StorageObject.id == candidate.storage_object_id,
                )
            )
            if candidate.storage_object_id
            else None
        )
        response = _candidate_read(candidate, storage, principal)
        session.add(
            TeamCommandSubmission(
                tenant_id=principal.tenant_id,
                operation="retention.candidate.cancel",
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                resource_id=candidate.id,
                response_payload=response.model_dump(mode="json"),
            )
        )
        await session.commit()
        return response
    if candidate.lock_version != payload.expected_version:
        raise ApiError(409, "retention_candidate_conflict", "Retention candidate changed")
    if candidate.status not in {"eligible", "pending_purge", "blocked"}:
        raise ApiError(409, "retention_candidate_not_cancelable", "Candidate cannot be cancelled")
    candidate.status = "cancelled"
    candidate.cancelled_at = datetime.now(UTC)
    candidate.cancellation_reason = payload.reason
    candidate.lock_version += 1
    storage = None
    if candidate.storage_object_id:
        storage = await session.scalar(
            select(StorageObject)
            .where(
                StorageObject.tenant_id == principal.tenant_id,
                StorageObject.id == candidate.storage_object_id,
            )
            .with_for_update()
        )
        if storage and storage.retention_state != "purged":
            storage.retention_state = "retained"
            storage.status = "archived" if storage.archived_at is not None else "active"
            storage.pending_purge_at = None
            storage.lock_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="retention.pending_purge_cancelled",
        resource_type="retention_candidate",
        resource_id=candidate.id,
        correlation_id=request.state.correlation_id,
        reason=payload.reason,
    )
    response = _candidate_read(candidate, storage, principal)
    session.add(
        TeamCommandSubmission(
            tenant_id=principal.tenant_id,
            operation="retention.candidate.cancel",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            resource_id=candidate.id,
            response_payload=response.model_dump(mode="json"),
        )
    )
    await session.commit()
    return response


def _legal_hold_read(hold: LegalHold, principal: Principal) -> LegalHoldRead:
    return LegalHoldRead(
        id=hold.id,
        project_id=hold.project_id,
        scope_type=cast(LegalHoldScope, hold.scope_type),
        scope_id=hold.scope_id,
        reason=hold.reason,
        created_by_user_id=hold.created_by_user_id,
        created_at=hold.created_at,
        released_by_user_id=hold.released_by_user_id,
        released_at=hold.released_at,
        can_release=(role_has_permission(principal.role, "legal_holds:manage") and hold.released_at is None),
        state_version=hold.lock_version,
    )


async def _legal_hold_scope_project(
    session: SessionDep, principal: Principal, scope_type: str, scope_id: UUID | None
) -> UUID | None:
    if scope_type == "tenant":
        if scope_id is not None:
            raise ApiError(422, "legal_hold_scope_invalid", "Tenant hold cannot have a scope ID")
        return None
    if scope_id is None:
        raise ApiError(422, "legal_hold_scope_invalid", "Scope ID is required")
    if scope_type == "project":
        return (await resolve_project(session, principal, scope_id, active_only=False)).id
    model: Any
    if scope_type == "call":
        model = Call
    elif scope_type == "customer":
        model = Customer
    elif scope_type == "document":
        model = KnowledgeDocument
    else:
        raise ApiError(422, "legal_hold_scope_invalid", "Unsupported legal hold scope")
    resource = await session.scalar(
        select(model).where(model.tenant_id == principal.tenant_id, model.id == scope_id)
    )
    if resource is None:
        raise ApiError(404, "legal_hold_resource_not_found", "Legal hold resource was not found")
    project_id = getattr(resource, "project_id", None)
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
    return project_id


@router.get("/legal-holds", response_model=Page[LegalHoldRead])
async def list_legal_holds(
    session: SessionDep,
    principal: PrincipalDep,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Page[LegalHoldRead]:
    _require(principal, "retention:read")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_ids = await accessible_project_ids(session, principal)
    filters: list[ColumnElement[bool]] = [
        LegalHold.tenant_id == principal.tenant_id,
        LegalHold.project_id.is_(None) | LegalHold.project_id.in_(project_ids),
    ]
    if active_only:
        filters.append(LegalHold.released_at.is_(None))
    total = int(await session.scalar(select(func.count()).select_from(LegalHold).where(*filters)) or 0)
    holds = list(
        await session.scalars(
            select(LegalHold)
            .where(*filters)
            .order_by(LegalHold.created_at.desc(), LegalHold.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[_legal_hold_read(hold, principal) for hold in holds],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/legal-holds", response_model=LegalHoldRead, status_code=201)
async def create_legal_hold(
    payload: LegalHoldCreate,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> LegalHoldRead:
    _require(principal, "legal_holds:manage")
    fingerprint = _admin_command_fingerprint("legal_hold.create", None, payload)
    await acquire_idempotency_lock(
        session,
        namespace="team",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    await acquire_retention_lock(session, principal.tenant_id)
    project_id = await _legal_hold_scope_project(session, principal, payload.scope_type, payload.scope_id)
    replay = await session.scalar(
        select(TeamCommandSubmission).where(
            TeamCommandSubmission.tenant_id == principal.tenant_id,
            TeamCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.operation != "legal_hold.create" or replay.request_fingerprint != fingerprint:
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
        return LegalHoldRead.model_validate(replay.response_payload)
    existing = await session.scalar(
        select(LegalHold).where(
            LegalHold.tenant_id == principal.tenant_id,
            LegalHold.scope_type == payload.scope_type,
            LegalHold.scope_id.is_(None)
            if payload.scope_id is None
            else LegalHold.scope_id == payload.scope_id,
            LegalHold.released_at.is_(None),
        )
    )
    if existing is not None:
        if existing.reason != payload.reason:
            raise ApiError(409, "legal_hold_exists", "An active legal hold already exists")
        response = _legal_hold_read(existing, principal)
        session.add(
            TeamCommandSubmission(
                tenant_id=principal.tenant_id,
                operation="legal_hold.create",
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                resource_id=existing.id,
                response_payload=response.model_dump(mode="json"),
            )
        )
        await session.commit()
        return response
    hold = LegalHold(
        tenant_id=principal.tenant_id,
        project_id=project_id,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        reason=payload.reason.strip(),
        created_by_user_id=principal.user_id,
        lock_version=1,
    )
    session.add(hold)
    await session.flush()
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="legal_hold.created",
        resource_type="legal_hold",
        resource_id=hold.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"scope_type": hold.scope_type, "project_id": str(project_id) if project_id else None},
    )
    response = _legal_hold_read(hold, principal)
    session.add(
        TeamCommandSubmission(
            tenant_id=principal.tenant_id,
            operation="legal_hold.create",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            resource_id=hold.id,
            response_payload=response.model_dump(mode="json"),
        )
    )
    await session.commit()
    return response


@router.post("/legal-holds/{hold_id}/release", response_model=LegalHoldRead)
async def release_legal_hold(
    hold_id: UUID,
    payload: LegalHoldRelease,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> LegalHoldRead:
    _require(principal, "legal_holds:manage")
    fingerprint = _admin_command_fingerprint("legal_hold.release", hold_id, payload)
    await acquire_idempotency_lock(
        session,
        namespace="team",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    await acquire_retention_lock(session, principal.tenant_id)
    hold = await session.scalar(
        select(LegalHold)
        .where(LegalHold.tenant_id == principal.tenant_id, LegalHold.id == hold_id)
        .with_for_update()
    )
    if hold is None:
        raise ApiError(404, "legal_hold_not_found", "Legal hold was not found")
    if hold.project_id is not None:
        await resolve_project(session, principal, hold.project_id, active_only=False)
    replay = await session.scalar(
        select(TeamCommandSubmission).where(
            TeamCommandSubmission.tenant_id == principal.tenant_id,
            TeamCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if (
            replay.operation != "legal_hold.release"
            or replay.resource_id != hold.id
            or replay.request_fingerprint != fingerprint
        ):
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
        return LegalHoldRead.model_validate(replay.response_payload)
    if hold.released_at is not None:
        response = _legal_hold_read(hold, principal)
        session.add(
            TeamCommandSubmission(
                tenant_id=principal.tenant_id,
                operation="legal_hold.release",
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                resource_id=hold.id,
                response_payload=response.model_dump(mode="json"),
            )
        )
        await session.commit()
        return response
    if hold.lock_version != payload.expected_version:
        raise ApiError(409, "legal_hold_conflict", "Legal hold changed; refresh and retry")
    hold.released_at = datetime.now(UTC)
    hold.released_by_user_id = principal.user_id
    hold.lock_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="legal_hold.released",
        resource_type="legal_hold",
        resource_id=hold.id,
        correlation_id=request.state.correlation_id,
        reason=payload.reason,
        safe_metadata={"scope_type": hold.scope_type},
    )
    response = _legal_hold_read(hold, principal)
    session.add(
        TeamCommandSubmission(
            tenant_id=principal.tenant_id,
            operation="legal_hold.release",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            resource_id=hold.id,
            response_payload=response.model_dump(mode="json"),
        )
    )
    await session.commit()
    return response
