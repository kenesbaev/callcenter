from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.enums import (
    TaskEventType,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallbackTask,
    CallOutcome,
    Customer,
    Membership,
    ProjectUser,
    TaskCommandSubmission,
    TaskEvent,
    User,
)
from teamora_api.realtime import enqueue_analytics_invalidation, enqueue_realtime_event


def utc_due_at(value: datetime, *, allow_now: bool = False) -> datetime:
    if value.tzinfo is None:
        raise ApiError(422, "task_timezone_required", "Дата выполнения должна содержать часовой пояс")
    normalized = value.astimezone(UTC)
    threshold = datetime.now(UTC)
    if normalized < threshold and not allow_now:
        raise ApiError(422, "task_due_at_past", "Дата выполнения должна быть в будущем")
    return normalized


async def validate_task_links(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    customer_id: UUID,
    call_id: UUID | None = None,
    call_outcome_id: UUID | None = None,
) -> Customer:
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == tenant_id,
            Customer.project_id == project_id,
            Customer.id == customer_id,
            Customer.is_anonymized.is_(False),
            Customer.archived_at.is_(None),
        )
    )
    if customer is None:
        raise ApiError(404, "task_customer_not_found", "Клиент проекта не найден")
    if call_id is not None:
        linked_call = await session.scalar(
            select(Call.id).where(
                Call.tenant_id == tenant_id,
                Call.project_id == project_id,
                Call.customer_id == customer_id,
                Call.id == call_id,
            )
        )
        if linked_call is None:
            raise ApiError(422, "task_call_mismatch", "Звонок не принадлежит клиенту проекта")
    if call_outcome_id is not None:
        outcome = await session.scalar(
            select(CallOutcome)
            .join(
                Call,
                and_(
                    Call.tenant_id == CallOutcome.tenant_id,
                    Call.id == CallOutcome.call_id,
                ),
            )
            .where(
                CallOutcome.tenant_id == tenant_id,
                CallOutcome.project_id == project_id,
                CallOutcome.id == call_outcome_id,
                Call.customer_id == customer_id,
            )
        )
        if outcome is None:
            raise ApiError(
                422,
                "task_outcome_mismatch",
                "Результат звонка не принадлежит клиенту проекта",
            )
        if call_id is not None and outcome.call_id != call_id:
            raise ApiError(422, "task_outcome_call_mismatch", "Результат относится к другому звонку")
    return customer


async def validate_task_assignee(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    assigned_user_id: UUID | None,
) -> None:
    if assigned_user_id is None:
        return
    membership = await session.scalar(
        select(ProjectUser.id)
        .join(
            Membership,
            and_(
                Membership.tenant_id == ProjectUser.tenant_id,
                Membership.user_id == ProjectUser.user_id,
            ),
        )
        .join(User, User.id == ProjectUser.user_id)
        .where(
            ProjectUser.tenant_id == tenant_id,
            ProjectUser.project_id == project_id,
            ProjectUser.user_id == assigned_user_id,
            ProjectUser.is_active.is_(True),
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if membership is None:
        raise ApiError(
            422,
            "task_assignee_project_access_required",
            "Ответственный сотрудник не назначен на проект",
        )


async def append_task_event(
    session: AsyncSession,
    *,
    task: CallbackTask,
    event_type: TaskEventType,
    actor_user_id: UUID | None,
    correlation_id: str,
    safe_snapshot: dict[str, object] | None = None,
) -> TaskEvent:
    event = TaskEvent(
        tenant_id=task.tenant_id,
        task_id=task.id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
        safe_snapshot=safe_snapshot or {},
    )
    session.add(event)
    realtime_type = {
        TaskEventType.CREATED: "task.created",
        TaskEventType.COMPLETED: "task.completed",
        TaskEventType.CANCELLED: "task.cancelled",
    }.get(event_type, "task.updated")
    await enqueue_realtime_event(
        session,
        tenant_id=task.tenant_id,
        project_id=task.project_id,
        target_membership_id=None,
        event_type=realtime_type,
        aggregate_type="task",
        aggregate_id=task.id,
        payload={
            "task_type": task.task_type.value,
            "status": task.status.value,
            "assigned_user_id": task.assigned_user_id,
        },
        correlation_id=correlation_id,
    )
    await enqueue_analytics_invalidation(
        session,
        tenant_id=task.tenant_id,
        project_id=task.project_id,
        aggregate_type="task",
        aggregate_id=task.id,
        correlation_id=correlation_id,
        reason="task_changed",
    )
    return event


async def create_task_record(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    customer_id: UUID,
    created_by_user_id: UUID,
    task_type: TaskType,
    title: str,
    description: str,
    priority: TaskPriority,
    due_at: datetime,
    assigned_user_id: UUID | None,
    source: TaskSource,
    correlation_id: str,
    comment: str = "",
    call_id: UUID | None = None,
    call_outcome_id: UUID | None = None,
    idempotency_key: str | None = None,
    allow_due_now: bool = False,
) -> CallbackTask:
    await validate_task_links(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        customer_id=customer_id,
        call_id=call_id,
        call_outcome_id=call_outcome_id,
    )
    await validate_task_assignee(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        assigned_user_id=assigned_user_id,
    )
    normalized_comment = comment.strip()
    task = CallbackTask(
        tenant_id=tenant_id,
        project_id=project_id,
        customer_id=customer_id,
        call_id=call_id,
        call_outcome_id=call_outcome_id,
        task_type=task_type,
        title=title.strip(),
        description=description.strip(),
        priority=priority,
        assigned_user_id=assigned_user_id,
        created_by_user_id=created_by_user_id,
        due_at=utc_due_at(due_at, allow_now=allow_due_now),
        status=TaskStatus.PENDING,
        comment=normalized_comment,
        source=source,
        idempotency_key=idempotency_key,
        note=normalized_comment,
    )
    session.add(task)
    await session.flush()
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.CREATED,
        actor_user_id=created_by_user_id,
        correlation_id=correlation_id,
        safe_snapshot={
            "task_type": task_type.value,
            "priority": priority.value,
            "status": TaskStatus.PENDING.value,
            "due_at": task.due_at.isoformat(),
            "source": source.value,
        },
    )
    if assigned_user_id is not None:
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.ASSIGNED,
            actor_user_id=created_by_user_id,
            correlation_id=correlation_id,
            safe_snapshot={"assigned_user_id": str(assigned_user_id)},
        )
    return task


async def sync_customer_callback_state(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    customer: Customer,
) -> None:
    next_due = await session.scalar(
        select(func.min(CallbackTask.due_at)).where(
            CallbackTask.tenant_id == tenant_id,
            CallbackTask.customer_id == customer.id,
            CallbackTask.task_type == TaskType.CALLBACK,
            CallbackTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
        )
    )
    customer.next_call_at = next_due
    if customer.status != "do_not_call":
        if next_due is not None:
            customer.status = "callback"
        elif customer.status == "callback":
            customer.status = "completed"


async def cancel_task_record(
    session: AsyncSession,
    *,
    task: CallbackTask,
    actor_user_id: UUID | None,
    reason: str,
    correlation_id: str,
) -> None:
    if task.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
        return
    now = datetime.now(UTC)
    task.status = TaskStatus.CANCELLED
    task.cancelled_at = now
    task.completed_at = None
    task.cancellation_reason = reason.strip()
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.CANCELLED,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
        safe_snapshot={"reason": reason.strip()[:240]},
    )


def task_command_fingerprint(operation: str, payload: object) -> str:
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def acquire_task_command(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    idempotency_key: str | None,
    fingerprint: str,
) -> TaskCommandSubmission | None:
    if idempotency_key is None:
        return None
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"task:{tenant_id}:{idempotency_key}", 0)))
    )
    submission = await session.scalar(
        select(TaskCommandSubmission).where(
            TaskCommandSubmission.tenant_id == tenant_id,
            TaskCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if submission is not None and submission.request_fingerprint != fingerprint:
        raise ApiError(409, "idempotency_key_reused", "Idempotency-Key уже использован")
    return submission


def store_task_command(
    session: AsyncSession,
    *,
    task: CallbackTask,
    operation: str,
    idempotency_key: str | None,
    fingerprint: str,
    response_payload: dict[str, object],
) -> None:
    if idempotency_key is None:
        return
    session.add(
        TaskCommandSubmission(
            tenant_id=task.tenant_id,
            task_id=task.id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            response_payload=response_payload,
        )
    )
