from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from teamora_api.audit import write_audit
from teamora_api.call_state import CAPACITY_CALL_STATES, TERMINAL_CALL_STATES
from teamora_api.customer_service import contacts_for_customers, serialize_customer
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.dialer_flow_service import (
    advance_execution,
    back_execution,
    complete_execution,
    ensure_execution_for_call,
    serialize_execution,
)
from teamora_api.enums import TaskEventType, TaskStatus, TaskType
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallbackTask,
    CallFlowExecution,
    CallOutcome,
    CallSummary,
    Customer,
    CustomerContact,
    DialerCompletionSubmission,
    TenantSettings,
    TranscriptSegment,
    User,
)
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.routers.calls import save_call_result_transactional, serialize_call
from teamora_api.schemas.calls import CallRead
from teamora_api.schemas.crm import DialerAssignment, DialerLeaseRequest, DialerTaskSummary
from teamora_api.schemas.dialer import (
    DialerCompleteAndNextRequest,
    DialerCompleteAndNextResponse,
    DialerFlowBackRequest,
    DialerFlowExecutionRead,
    DialerFlowStepRequest,
    DialerHistoryItem,
    DialerWorkspaceRead,
)
from teamora_api.task_service import append_task_event

router = APIRouter(prefix="/dialer", tags=["dialer"])
LOCK_MINUTES = 15
DialerSource = Literal["callback", "retry", "new"]


async def start_of_project_day(
    session: SessionDep,
    tenant_id: UUID,
    project_timezone: str | None,
    now: datetime,
) -> datetime:
    settings = await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    zone = ZoneInfo(project_timezone or (settings.timezone if settings else "Asia/Tashkent"))
    local_now = now.astimezone(zone)
    return datetime.combine(local_now.date(), time.min, tzinfo=zone).astimezone(UTC)


def unresolved_operator_call(
    tenant_id: UUID,
    customer_id: UUID,
    user_id: UUID,
) -> ColumnElement[bool]:
    return (
        select(Call.id)
        .outerjoin(
            CallOutcome,
            and_(CallOutcome.call_id == Call.id, CallOutcome.tenant_id == Call.tenant_id),
        )
        .where(
            Call.tenant_id == tenant_id,
            Call.customer_id == customer_id,
            Call.operator_user_id == user_id,
            or_(
                Call.status.in_(CAPACITY_CALL_STATES),
                and_(Call.status.in_(TERMINAL_CALL_STATES), CallOutcome.id.is_(None)),
            ),
        )
        .exists()
    )


def active_customer_call() -> ColumnElement[bool]:
    return (
        select(Call.id)
        .where(
            Call.tenant_id == Customer.tenant_id,
            Call.customer_id == Customer.id,
            Call.status.in_(CAPACITY_CALL_STATES),
        )
        .exists()
    )


def customer_has_callable_phone() -> ColumnElement[bool]:
    return (
        select(CustomerContact.id)
        .where(
            CustomerContact.tenant_id == Customer.tenant_id,
            CustomerContact.project_id == Customer.project_id,
            CustomerContact.customer_id == Customer.id,
            CustomerContact.kind == "phone",
        )
        .exists()
    )


async def assignment(
    session: SessionDep,
    tenant_id: UUID,
    customer: Customer,
    task: CallbackTask | None,
    user_id: UUID,
    source: DialerSource | None = None,
) -> DialerAssignment:
    if customer.lock_token is None:
        raise ApiError(500, "dialer_lease_missing", "Не удалось создать безопасную блокировку клиента")
    grouped = await contacts_for_customers(session, tenant_id, [customer.id])
    pending_tasks = list(
        await session.scalars(
            select(CallbackTask)
            .where(
                CallbackTask.tenant_id == tenant_id,
                CallbackTask.customer_id == customer.id,
                CallbackTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                or_(
                    CallbackTask.assigned_user_id.is_(None),
                    CallbackTask.assigned_user_id == user_id,
                ),
            )
            .order_by(CallbackTask.due_at, CallbackTask.created_at)
            .limit(20)
        )
    )

    def task_summary(value: CallbackTask) -> DialerTaskSummary:
        return DialerTaskSummary(
            id=value.id,
            task_type=value.task_type.value,
            title=value.title,
            priority=value.priority.value,
            status=value.status.value,
            due_at=value.due_at,
            comment=value.comment,
            assigned_user_id=value.assigned_user_id,
        )

    persisted_source: DialerSource | None = None
    if customer.dialer_assignment_source == "callback":
        persisted_source = "callback"
    elif customer.dialer_assignment_source == "retry":
        persisted_source = "retry"
    elif customer.dialer_assignment_source == "new":
        persisted_source = "new"

    return DialerAssignment(
        customer=serialize_customer(customer, grouped[customer.id]),
        source=source or persisted_source or ("callback" if task else "retry"),
        callback_task_id=task.id if task else None,
        task=task_summary(task) if task else None,
        pending_tasks=[task_summary(value) for value in pending_tasks],
        lock_token=customer.lock_token,
    )


async def current_task(
    session: SessionDep,
    tenant_id: UUID,
    project_id: UUID,
    customer_id: UUID,
    user_id: UUID,
) -> CallbackTask | None:
    return (
        await session.scalars(
            select(CallbackTask)
            .where(
                CallbackTask.tenant_id == tenant_id,
                CallbackTask.project_id == project_id,
                CallbackTask.customer_id == customer_id,
                CallbackTask.task_type == TaskType.CALLBACK,
                or_(
                    and_(
                        CallbackTask.status == TaskStatus.PENDING,
                        or_(
                            CallbackTask.assigned_user_id.is_(None),
                            CallbackTask.assigned_user_id == user_id,
                        ),
                    ),
                    and_(
                        CallbackTask.status == TaskStatus.IN_PROGRESS,
                        CallbackTask.assigned_user_id == user_id,
                    ),
                ),
            )
            .order_by(CallbackTask.due_at)
            .limit(1)
        )
    ).first()


async def assigned_customer(
    session: SessionDep,
    principal: Principal,
    now: datetime,
) -> Customer | None:
    project_ids = await accessible_project_ids(session, principal)
    if not project_ids:
        return None
    unresolved_call = (
        select(Call.id)
        .outerjoin(
            CallOutcome,
            and_(CallOutcome.call_id == Call.id, CallOutcome.tenant_id == Call.tenant_id),
        )
        .where(
            Call.tenant_id == principal.tenant_id,
            Call.customer_id == Customer.id,
            Call.operator_user_id == principal.user_id,
            or_(
                Call.status.in_(CAPACITY_CALL_STATES),
                and_(Call.status.in_(TERMINAL_CALL_STATES), CallOutcome.id.is_(None)),
            ),
        )
        .exists()
    )
    return (
        await session.scalars(
            select(Customer).where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id.in_(project_ids),
                Customer.locked_by_user_id == principal.user_id,
                or_(
                    Customer.locked_until > now,
                    unresolved_call,
                ),
                Customer.is_anonymized.is_(False),
                Customer.archived_at.is_(None),
                customer_has_callable_phone(),
            )
        )
    ).first()


async def renew_assignment(
    session: SessionDep,
    principal: Principal,
    customer: Customer,
    now: datetime,
    *,
    commit: bool = True,
) -> DialerAssignment:
    if customer.lock_token is None:
        customer.lock_token = uuid4()
    customer.locked_until = now + timedelta(minutes=LOCK_MINUTES)
    task = await current_task(
        session,
        principal.tenant_id,
        customer.project_id,
        customer.id,
        principal.user_id,
    )
    response = await assignment(session, principal.tenant_id, customer, task, principal.user_id)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return response


@router.get("/current", response_model=DialerAssignment | None)
async def current_customer(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    customer = await assigned_customer(session, principal, now)
    if customer is None:
        return None
    return await renew_assignment(session, principal, customer, now)


@router.get("/history", response_model=list[CallRead])
async def assigned_customer_history(
    customer_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> list[CallRead]:
    assigned = await session.scalar(
        select(Customer.id).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
        )
    )
    if assigned is None:
        raise ApiError(403, "customer_not_assigned", "Клиент не назначен текущему оператору")
    calls = list(
        await session.scalars(
            select(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.customer_id == customer_id,
                Call.operator_user_id == principal.user_id,
            )
            .order_by(Call.created_at.desc())
            .limit(10)
        )
    )
    return [serialize_call(call) for call in calls]


async def detailed_history(
    session: SessionDep,
    *,
    tenant_id: UUID,
    customer_id: UUID,
) -> list[DialerHistoryItem]:
    rows = list(
        (
            await session.execute(
                select(Call, CallOutcome, User, CallSummary)
                .outerjoin(
                    CallOutcome,
                    and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id),
                )
                .outerjoin(User, User.id == Call.operator_user_id)
                .outerjoin(
                    CallSummary,
                    and_(CallSummary.tenant_id == Call.tenant_id, CallSummary.call_id == Call.id),
                )
                .where(Call.tenant_id == tenant_id, Call.customer_id == customer_id)
                .order_by(Call.created_at.desc())
                .limit(20)
            )
        ).all()
    )
    call_ids = [row[0].id for row in rows]
    transcript_rows = (
        list(
            await session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.tenant_id == tenant_id,
                    TranscriptSegment.call_id.in_(call_ids),
                )
                .order_by(TranscriptSegment.call_id, TranscriptSegment.sequence)
            )
        )
        if call_ids
        else []
    )
    transcripts: dict[UUID, list[dict[str, object]]] = {}
    for segment in transcript_rows:
        transcripts.setdefault(segment.call_id, []).append(
            {
                "speaker": segment.speaker.value,
                "language": segment.language.value,
                "text": segment.text,
                "sequence": segment.sequence,
            }
        )
    result: list[DialerHistoryItem] = []
    for call, outcome, operator, summary in rows:
        result.append(
            DialerHistoryItem(
                call=serialize_call(call),
                result_code=outcome.code if outcome else None,
                result_label=outcome.label if outcome else None,
                result_category=outcome.category.value if outcome else None,
                comment=str(outcome.details.get("comment", "")) if outcome else "",
                operator_name=operator.display_name if operator else None,
                transcript=transcripts.get(call.id, []),
                summary=summary.summary if summary else None,
            )
        )
    return result


@router.get("/history/details", response_model=list[DialerHistoryItem])
async def assigned_customer_history_details(
    customer_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> list[DialerHistoryItem]:
    assigned = await session.scalar(
        select(Customer.id).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
        )
    )
    if assigned is None:
        raise ApiError(403, "customer_not_assigned", "Клиент не назначен текущему оператору")
    return await detailed_history(
        session,
        tenant_id=principal.tenant_id,
        customer_id=customer_id,
    )


@router.post("/next-client", response_model=DialerAssignment | None)
async def next_client(
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    project_id: UUID | None = None,
) -> DialerAssignment | None:
    return await allocate_next_assignment(
        request=request,
        session=session,
        principal=principal,
        project_id=project_id,
        commit=True,
        recover_current=True,
    )


async def allocate_next_assignment(
    *,
    request: Request,
    session: SessionDep,
    principal: Principal,
    project_id: UUID | None,
    commit: bool,
    recover_current: bool,
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    current = await assigned_customer(session, principal, now) if recover_current else None
    if current is not None:
        return await renew_assignment(session, principal, current, now, commit=commit)

    project = await resolve_project(session, principal, project_id)
    available_lock = or_(Customer.locked_until.is_(None), Customer.locked_until <= now)
    day_start = await start_of_project_day(session, principal.tenant_id, project.timezone, now)
    callback_row = (
        await session.execute(
            select(Customer, CallbackTask)
            .join(
                CallbackTask,
                and_(
                    CallbackTask.customer_id == Customer.id,
                    CallbackTask.project_id == Customer.project_id,
                ),
            )
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id == project.id,
                Customer.is_anonymized.is_(False),
                Customer.archived_at.is_(None),
                customer_has_callable_phone(),
                Customer.status != "do_not_call",
                available_lock,
                ~active_customer_call(),
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.project_id == project.id,
                CallbackTask.task_type == TaskType.CALLBACK,
                or_(
                    and_(
                        CallbackTask.status == TaskStatus.PENDING,
                        or_(
                            CallbackTask.assigned_user_id.is_(None),
                            CallbackTask.assigned_user_id == principal.user_id,
                        ),
                    ),
                    and_(
                        CallbackTask.status == TaskStatus.IN_PROGRESS,
                        CallbackTask.assigned_user_id == principal.user_id,
                    ),
                ),
                CallbackTask.due_at <= now,
            )
            .order_by(
                case(
                    (
                        and_(
                            CallbackTask.assigned_user_id == principal.user_id,
                            CallbackTask.due_at < day_start,
                        ),
                        0,
                    ),
                    (CallbackTask.assigned_user_id == principal.user_id, 1),
                    else_=2,
                ),
                CallbackTask.due_at,
                CallbackTask.created_at,
            )
            .with_for_update(of=(Customer, CallbackTask), skip_locked=True)
            .limit(1)
        )
    ).one_or_none()
    task: CallbackTask | None = None
    source: DialerSource = "callback"
    if callback_row:
        customer, task = callback_row
        task_was_pending = task.status == TaskStatus.PENDING
        task.assigned_user_id = principal.user_id
        task.status = TaskStatus.IN_PROGRESS
        if task.started_at is None:
            task.started_at = now
        await append_task_event(
            session,
            task=task,
            event_type=(TaskEventType.STARTED if task_was_pending else TaskEventType.UPDATED),
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"source": "dialer", "recovered": not task_was_pending},
        )
    else:
        customer = await session.scalar(
            select(Customer)
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id == project.id,
                Customer.is_anonymized.is_(False),
                Customer.archived_at.is_(None),
                customer_has_callable_phone(),
                or_(
                    Customer.status == "new",
                    and_(
                        Customer.status == "assigned",
                        Customer.locked_until <= now,
                    ),
                ),
                available_lock,
                ~active_customer_call(),
                ~select(CallbackTask.id)
                .where(
                    CallbackTask.tenant_id == principal.tenant_id,
                    CallbackTask.project_id == project.id,
                    CallbackTask.customer_id == Customer.id,
                    CallbackTask.task_type == TaskType.CALLBACK,
                    CallbackTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                )
                .exists(),
            )
            .order_by(Customer.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        source = "retry" if customer is not None and customer.status == "assigned" else "new"
    if customer is None:
        return None

    customer.status = "assigned"
    customer.locked_by_user_id = principal.user_id
    customer.locked_until = now + timedelta(minutes=LOCK_MINUTES)
    customer.lock_token = uuid4()
    customer.dialer_assignment_source = source
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="dialer.customer_assigned",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "source": source,
            "project_id": str(project.id),
        },
    )
    response = await assignment(
        session,
        principal.tenant_id,
        customer,
        task,
        principal.user_id,
        source=source,
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return response


async def operator_call(
    session: SessionDep,
    principal: Principal,
    call_id: UUID,
    *,
    for_update: bool = False,
) -> Call:
    statement = select(Call).where(
        Call.tenant_id == principal.tenant_id,
        Call.id == call_id,
        Call.operator_user_id == principal.user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    call = await session.scalar(statement)
    if call is None:
        raise ApiError(404, "call_not_found", "Звонок не найден")
    await resolve_project(session, principal, call.project_id, active_only=False)
    return call


@router.get("/flow/{call_id}", response_model=DialerFlowExecutionRead | None)
async def get_flow_execution(
    call_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> DialerFlowExecutionRead | None:
    call = await operator_call(session, principal, call_id, for_update=True)
    execution = await ensure_execution_for_call(session, principal, call)
    if execution is None:
        return None
    response = await serialize_execution(session, execution)
    await session.commit()
    return response


@router.post("/flow/{call_id}/steps", response_model=DialerFlowExecutionRead)
async def step_flow_execution(
    call_id: UUID,
    payload: DialerFlowStepRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> DialerFlowExecutionRead:
    call = await operator_call(session, principal, call_id, for_update=True)
    execution = await advance_execution(
        session,
        principal=principal,
        call=call,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    response = await serialize_execution(session, execution)
    await session.commit()
    return response


@router.post("/flow/{call_id}/back", response_model=DialerFlowExecutionRead)
async def return_flow_execution(
    call_id: UUID,
    payload: DialerFlowBackRequest,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> DialerFlowExecutionRead:
    call = await operator_call(session, principal, call_id, for_update=True)
    execution = await back_execution(
        session,
        principal=principal,
        call=call,
        expected_state_version=payload.expected_state_version,
        idempotency_key=idempotency_key,
    )
    response = await serialize_execution(session, execution)
    await session.commit()
    return response


@router.get("/workspace", response_model=DialerWorkspaceRead | None)
async def current_workspace(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> DialerWorkspaceRead | None:
    now = datetime.now(UTC)
    customer = await assigned_customer(session, principal, now)
    if customer is None:
        return None
    current_assignment = await renew_assignment(session, principal, customer, now, commit=False)
    call = await session.scalar(
        select(Call)
        .outerjoin(
            CallOutcome,
            and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id),
        )
        .where(
            Call.tenant_id == principal.tenant_id,
            Call.customer_id == customer.id,
            Call.operator_user_id == principal.user_id,
            or_(
                Call.status.in_(CAPACITY_CALL_STATES),
                and_(Call.status.in_(TERMINAL_CALL_STATES), CallOutcome.id.is_(None)),
            ),
        )
        .order_by(Call.created_at.desc())
        .limit(1)
    )
    flow = None
    if call is not None:
        execution = await ensure_execution_for_call(session, principal, call)
        flow = await serialize_execution(session, execution) if execution is not None else None
    history = await detailed_history(
        session,
        tenant_id=principal.tenant_id,
        customer_id=customer.id,
    )
    response = DialerWorkspaceRead(
        assignment=current_assignment,
        call=serialize_call(call) if call else None,
        flow=flow,
        history=history,
    )
    await session.commit()
    return response


def completion_fingerprint(payload: DialerCompleteAndNextRequest) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@router.post("/complete-and-next", response_model=DialerCompleteAndNextResponse)
async def complete_and_next(
    payload: DialerCompleteAndNextRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> DialerCompleteAndNextResponse:
    fingerprint = completion_fingerprint(payload)
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"dialer-complete:{principal.tenant_id}:{idempotency_key}", 0)
            )
        )
    )
    replay = await session.scalar(
        select(DialerCompletionSubmission).where(
            DialerCompletionSubmission.tenant_id == principal.tenant_id,
            DialerCompletionSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.request_fingerprint != fingerprint:
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key уже использован другим запросом")
        stored = DialerCompleteAndNextResponse.model_validate(replay.response_payload)
        return stored.model_copy(update={"replayed": True})

    call = await operator_call(session, principal, payload.call_id, for_update=True)
    if call.project_id != (payload.project_id or call.project_id) or call.customer_id != payload.customer_id:
        raise ApiError(409, "dialer_assignment_conflict", "Звонок не соответствует текущему назначению")
    if call.status not in TERMINAL_CALL_STATES:
        raise ApiError(409, "call_terminal_required", "Сначала завершите звонок")
    if call.state_version != payload.expected_state_version:
        raise ApiError(409, "call_state_version_conflict", "Состояние звонка уже изменилось")
    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.project_id == call.project_id,
            Customer.id == payload.customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.lock_token == payload.lock_token,
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(409, "dialer_lease_lost", "Назначение клиента уже недействительно")
    execution = await session.scalar(
        select(CallFlowExecution)
        .where(
            CallFlowExecution.tenant_id == principal.tenant_id,
            CallFlowExecution.call_id == call.id,
            CallFlowExecution.operator_user_id == principal.user_id,
        )
        .with_for_update()
    )
    if execution is not None:
        complete_execution(execution, expected_version=payload.flow_execution_state_version)
    elif payload.flow_execution_state_version is not None:
        raise ApiError(409, "call_flow_execution_missing", "Выполнение сценария не найдено")

    result = await save_call_result_transactional(
        call_id=call.id,
        payload=payload.result,
        request=request,
        session=session,
        principal=principal,
        idempotency_key=None,
        commit=False,
    )
    next_assignment = await allocate_next_assignment(
        request=request,
        session=session,
        principal=principal,
        project_id=call.project_id,
        commit=False,
        recover_current=False,
    )
    response = DialerCompleteAndNextResponse(
        result=result,
        next_assignment=next_assignment,
        queue_complete=next_assignment is None,
    )
    session.add(
        DialerCompletionSubmission(
            tenant_id=principal.tenant_id,
            call_id=call.id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            response_payload=response.model_dump(mode="json"),
        )
    )
    await session.commit()
    return response


@router.post("/customers/{customer_id}/heartbeat", status_code=204)
async def heartbeat_customer(
    customer_id: UUID,
    payload: DialerLeaseRequest,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> None:
    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.lock_token == payload.lock_token,
            Customer.is_anonymized.is_(False),
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(409, "dialer_lease_lost", "Блокировка клиента уже недействительна")
    customer.locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
    await session.commit()


@router.post("/customers/{customer_id}/release", status_code=204)
async def release_customer(
    customer_id: UUID,
    payload: DialerLeaseRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> None:
    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.lock_token == payload.lock_token,
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(404, "assignment_not_found", "Назначение клиента не найдено")
    if await session.scalar(
        select(unresolved_operator_call(customer.tenant_id, customer.id, principal.user_id))
    ):
        raise ApiError(
            409,
            "call_result_required",
            "Сначала завершите звонок и сохраните его результат",
        )
    task = await current_task(
        session,
        principal.tenant_id,
        customer.project_id,
        customer.id,
        principal.user_id,
    )
    if task is not None and task.status == TaskStatus.IN_PROGRESS:
        task.status = TaskStatus.PENDING
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.UPDATED,
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"dialer_released": True},
        )
    customer.status = "callback" if task else "new"
    customer.locked_by_user_id = None
    customer.locked_until = None
    customer.lock_token = None
    customer.dialer_assignment_source = None
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="dialer.customer_released",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
