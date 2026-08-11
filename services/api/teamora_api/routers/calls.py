from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Header, Request
from sqlalchemy import and_, func, or_, select

from teamora_api.audit import write_audit
from teamora_api.call_events import append_call_event
from teamora_api.call_flow_service import active_version_for_project
from teamora_api.call_result_service import localized_result_name
from teamora_api.call_state import (
    CAPACITY_CALL_STATES,
    TERMINAL_CALL_STATES,
    CallStateService,
    allowed_actions,
)
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import (
    CallDirection,
    CallerType,
    CallStatus,
    HangupCause,
    TaskEventType,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
    TelephonyCallState,
    TelephonyCommandName,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallbackTask,
    CallEvent,
    CallOutcome,
    CallParticipant,
    CallResultDefinition,
    CallResultSubmission,
    CallSummary,
    Customer,
    CustomerContact,
    CustomerNote,
    HumanOperator,
    TenantSettings,
    TranscriptSegment,
)
from teamora_api.project_access import resolve_project
from teamora_api.realtime import enqueue_analytics_invalidation, enqueue_realtime_event
from teamora_api.schemas.calls import (
    CallDetail,
    CallOutcomeSnapshotRead,
    CallRead,
    CallResultRequest,
    CallResultResponse,
    CallStartRequest,
    CallSummaryRead,
    TranscriptSegmentRead,
)
from teamora_api.schemas.common import Page
from teamora_api.schemas.telephony import (
    CallLastEventRead,
    CallReconciliationRead,
    CallStateRead,
    CallTransferRequest,
)
from teamora_api.task_service import (
    append_task_event,
    cancel_task_record,
    create_task_record,
    sync_customer_callback_state,
    utc_due_at,
)
from teamora_api.team_service import require_transfer_candidate
from teamora_api.telephony.reconciliation import CallReconciliationService
from teamora_api.telephony.service import TelephonyService

router = APIRouter(prefix="/calls", tags=["calls"])


def serialize_call(call: Call) -> CallRead:
    return CallRead(
        id=call.id,
        project_id=call.project_id,
        channel=call.channel,
        status=call.status,
        language=call.language,
        customer_id=call.customer_id,
        operator_user_id=call.operator_user_id,
        ai_operator_id=call.ai_operator_id,
        call_flow_version_id=call.call_flow_version_id,
        knowledge_base_revision_id=call.knowledge_base_revision_id,
        direction=call.direction,
        caller_type=call.caller_type,
        provider=call.provider,
        provider_call_id=call.external_call_id,
        provider_state=call.provider_state,
        recording_state=call.recording_state,
        from_number=call.from_number,
        to_number=call.to_number,
        started_at=call.started_at,
        ringing_at=call.ringing_at,
        answered_at=call.answered_at,
        held_at=call.held_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        transfer_reason=call.transfer_reason,
        hangup_cause=call.hangup_cause,
        raw_provider_cause=call.raw_provider_cause,
        last_provider_event_at=call.last_provider_event_at,
        state_version=call.state_version,
        is_demo=call.is_demo,
    )


async def controlled_call(session: SessionDep, tenant_id: UUID, user_id: UUID, call_id: UUID) -> Call:
    call = await session.scalar(
        select(Call)
        .where(
            Call.tenant_id == tenant_id,
            Call.id == call_id,
            Call.operator_user_id == user_id,
        )
        .with_for_update()
    )
    if call is None:
        raise ApiError(404, "call_not_found", "Звонок не найден")
    return call


async def reserve_call_capacity(
    session: SessionDep,
    principal: Principal,
    customer: Customer,
) -> Call | None:
    tenant_settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == principal.tenant_id).with_for_update()
    )
    project = await resolve_project(
        session,
        principal,
        customer.project_id,
        for_update=True,
    )
    existing = await session.scalar(
        select(Call).where(
            Call.tenant_id == principal.tenant_id,
            Call.operator_user_id == principal.user_id,
            Call.status.in_(CAPACITY_CALL_STATES),
        )
    )
    if existing is not None:
        if existing.customer_id == customer.id:
            return existing
        raise ApiError(409, "operator_has_active_call", "Сначала завершите текущий звонок")

    active_tenant_calls = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
        )
        or 0
    )
    tenant_limit = (
        tenant_settings.max_concurrent_calls
        if tenant_settings is not None
        else get_settings().default_max_concurrent_calls
    )
    if active_tenant_calls >= tenant_limit:
        raise ApiError(409, "no_free_channels", "Нет свободных телефонных каналов")

    active_project_calls = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.project_id == project.id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
        )
        or 0
    )
    project_limit = project.max_concurrent_calls or tenant_limit
    if active_project_calls >= project_limit:
        raise ApiError(409, "project_call_limit", "Лимит одновременных звонков проекта исчерпан")

    operator_limit = await session.scalar(
        select(HumanOperator.max_concurrent_calls).where(
            HumanOperator.tenant_id == principal.tenant_id,
            HumanOperator.membership_id == principal.membership_id,
        )
    )
    active_operator_calls = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.operator_user_id == principal.user_id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
        )
        or 0
    )
    if active_operator_calls >= (operator_limit or 1):
        raise ApiError(409, "operator_call_limit", "Лимит звонков оператора исчерпан")
    return None


@router.post("/start", response_model=CallRead, status_code=201)
async def start_call(
    payload: CallStartRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> CallRead:
    existing = await session.scalar(
        select(Call).where(
            Call.tenant_id == principal.tenant_id,
            Call.operator_user_id == principal.user_id,
            Call.status.in_(CAPACITY_CALL_STATES),
        )
    )
    if existing:
        if existing.customer_id == payload.customer_id:
            return serialize_call(existing)
        raise ApiError(409, "operator_has_active_call", "Сначала завершите текущий звонок")

    now = datetime.now(UTC)
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == payload.customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.locked_until > now,
            Customer.lock_token == payload.lock_token,
            Customer.archived_at.is_(None),
        )
    )
    if customer is None:
        raise ApiError(409, "customer_not_assigned", "Сначала получите клиента через диалер")
    capacity_call = await reserve_call_capacity(session, principal, customer)
    if capacity_call is not None:
        return serialize_call(capacity_call)
    phone_filters = [
        CustomerContact.tenant_id == principal.tenant_id,
        CustomerContact.project_id == customer.project_id,
        CustomerContact.customer_id == customer.id,
        CustomerContact.kind == "phone",
    ]
    if payload.customer_contact_id is not None:
        phone_filters.append(CustomerContact.id == payload.customer_contact_id)
    phone = await session.scalar(
        select(CustomerContact)
        .where(*phone_filters)
        .order_by(CustomerContact.is_primary.desc(), CustomerContact.created_at)
        .limit(1)
    )
    if phone is None:
        raise ApiError(409, "customer_phone_missing", "У клиента отсутствует основной телефон")
    project = await resolve_project(session, principal, customer.project_id)
    telephony = TelephonyService()
    selection = await telephony.select_provider(
        session,
        tenant_id=principal.tenant_id,
        project=project,
        requested_from_number=payload.from_number,
    )
    call_flow_version = await active_version_for_project(
        session,
        tenant_id=principal.tenant_id,
        project_id=customer.project_id,
    )
    from teamora_api.knowledge_service import active_revision_for_project as active_knowledge_revision

    knowledge_revision = await active_knowledge_revision(
        session,
        tenant_id=principal.tenant_id,
        project_id=customer.project_id,
    )

    callback: CallbackTask | None = None
    if payload.callback_task_id:
        callback = await session.scalar(
            select(CallbackTask).where(
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.project_id == customer.project_id,
                CallbackTask.id == payload.callback_task_id,
                CallbackTask.customer_id == customer.id,
                CallbackTask.task_type == TaskType.CALLBACK,
                CallbackTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                CallbackTask.assigned_user_id == principal.user_id,
            )
        )
        if callback is None:
            raise ApiError(409, "callback_unavailable", "Задача на перезвон уже недоступна")

    call = Call(
        tenant_id=principal.tenant_id,
        project_id=customer.project_id,
        external_call_id=None,
        channel=selection.channel,
        status=CallStatus.QUEUED,
        direction=CallDirection.OUTBOUND,
        caller_type=CallerType.HUMAN_OPERATOR,
        customer_id=customer.id,
        operator_user_id=principal.user_id,
        call_flow_version_id=call_flow_version.id if call_flow_version else None,
        knowledge_base_revision_id=knowledge_revision.id if knowledge_revision else None,
        language=customer.preferred_language,
        provider=selection.provider.name,
        provider_state=TelephonyCallState.QUEUED.value,
        from_number=selection.from_number,
        to_number=phone.normalized_value,
        is_demo=selection.is_demo,
    )
    session.add(call)
    await session.flush()
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=call.project_id,
        event_type="call.created",
        aggregate_type="call",
        aggregate_id=call.id,
        aggregate_version=call.state_version,
        payload={"status": call.status.value, "direction": call.direction.value},
        occurred_at=now,
        correlation_id=request.state.correlation_id,
    )
    session.add(
        CallParticipant(
            tenant_id=principal.tenant_id,
            call_id=call.id,
            participant_type="human_operator",
            display_label=principal.display_name,
            joined_at=now,
        )
    )
    await telephony.execute(
        session,
        call=call,
        project=project,
        selection=selection,
        command_name=TelephonyCommandName.ORIGINATE,
        correlation_id=request.state.correlation_id,
        actor_user_id=principal.user_id,
        idempotency_key=idempotency_key,
        parameters={
            "fromNumber": selection.from_number,
            "toNumber": phone.normalized_value,
        },
    )
    if callback:
        callback.call_id = call.id
        callback.assigned_user_id = principal.user_id
        callback.status = TaskStatus.IN_PROGRESS
        if callback.started_at is None:
            callback.started_at = now
    customer.last_call_at = now
    customer.locked_until = now + timedelta(minutes=15)
    await session.commit()
    return serialize_call(call)


@router.post("/{call_id}/answer", response_model=CallRead)
async def answer_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status == CallStatus.ACTIVE:
        return serialize_call(call)
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.ANSWER,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


@router.post("/{call_id}/hangup", response_model=CallRead)
async def hangup_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status in TERMINAL_CALL_STATES:
        return serialize_call(call)
    if call.status == CallStatus.QUEUED:
        await CallStateService().transition(
            session,
            call=call,
            target=CallStatus.CANCELLED,
            event_type="call.cancelled",
            occurred_at=datetime.now(UTC),
            correlation_id=request.state.correlation_id,
            actor_user_id=principal.user_id,
            expected_version=expected_version,
            hangup_cause=HangupCause.CANCELLED,
        )
        await session.commit()
        return serialize_call(call)
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.HANGUP,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        parameters={"reason": "normal"},
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


async def execute_call_command(
    session: SessionDep,
    *,
    principal: Principal,
    call: Call,
    command: TelephonyCommandName,
    correlation_id: str,
    idempotency_key: str | None,
    parameters: dict[str, str | int | float | bool] | None = None,
    expected_version: int | None = None,
) -> None:
    project = await resolve_project(session, principal, call.project_id, active_only=False)
    telephony = TelephonyService()
    selection = await telephony.selection_for_call(session, call=call, project=project)
    await telephony.execute(
        session,
        call=call,
        project=project,
        selection=selection,
        command_name=command,
        correlation_id=correlation_id,
        actor_user_id=principal.user_id,
        idempotency_key=idempotency_key,
        parameters=parameters,
        expected_version=expected_version,
    )


@router.post("/{call_id}/hold", response_model=CallRead)
async def hold_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status == CallStatus.ON_HOLD:
        return serialize_call(call)
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.HOLD,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


@router.post("/{call_id}/resume", response_model=CallRead)
async def resume_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status == CallStatus.ACTIVE:
        return serialize_call(call)
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.RESUME,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


@router.post("/{call_id}/transfer", response_model=CallRead)
async def transfer_call(
    call_id: UUID,
    payload: CallTransferRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status == CallStatus.TRANSFERRED:
        return serialize_call(call)
    if payload.destination != "operator-queue":
        try:
            destination_user_id = UUID(payload.destination)
        except ValueError as exc:
            raise ApiError(422, "transfer_operator_invalid", "Выберите доступного оператора") from exc
        await require_transfer_candidate(
            session,
            tenant_id=principal.tenant_id,
            project_id=call.project_id,
            destination_user_id=destination_user_id,
        )
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.TRANSFER,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        parameters={"destination": payload.destination, "reason": payload.reason},
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


@router.get("/{call_id}/state", response_model=CallStateRead)
async def get_call_state(
    call_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> CallStateRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    return await serialize_call_state(session, call)


@router.post("/{call_id}/cancel", response_model=CallRead)
async def cancel_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status in TERMINAL_CALL_STATES:
        return serialize_call(call)
    if call.status == CallStatus.QUEUED:
        await CallStateService().transition(
            session,
            call=call,
            target=CallStatus.CANCELLED,
            event_type="call.cancelled",
            occurred_at=datetime.now(UTC),
            correlation_id=request.state.correlation_id,
            actor_user_id=principal.user_id,
            expected_version=expected_version,
            hangup_cause=HangupCause.CANCELLED,
        )
        await session.commit()
        return serialize_call(call)
    await execute_call_command(
        session,
        principal=principal,
        call=call,
        command=TelephonyCommandName.HANGUP,
        correlation_id=request.state.correlation_id,
        idempotency_key=idempotency_key,
        parameters={"reason": "cancelled"},
        expected_version=expected_version,
    )
    await session.commit()
    return serialize_call(call)


@router.post("/{call_id}/reconcile", response_model=CallReconciliationRead)
async def reconcile_call(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    expected_version: int | None = Header(default=None, alias="X-Call-State-Version", ge=0),
) -> CallReconciliationRead:
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    project = await resolve_project(session, principal, call.project_id, active_only=False)
    telephony = TelephonyService()
    selection = await telephony.selection_for_call(session, call=call, project=project)
    result = await CallReconciliationService(telephony).reconcile(
        session,
        call=call,
        project=project,
        selection=selection,
        correlation_id=request.state.correlation_id,
        actor_user_id=principal.user_id,
        expected_version=expected_version,
    )
    await session.commit()
    return CallReconciliationRead(
        call=await serialize_call_state(session, call),
        provider_state=result.provider_state,
        reconciled=result.reconciled,
        ignored_reason=result.ignored_reason,
    )


async def serialize_call_state(session: SessionDep, call: Call) -> CallStateRead:
    last_event = await session.scalar(
        select(CallEvent)
        .where(CallEvent.tenant_id == call.tenant_id, CallEvent.call_id == call.id)
        .order_by(CallEvent.sequence.desc())
        .limit(1)
    )
    transfer_state = (
        call.status
        if call.status
        in {
            CallStatus.TRANSFER_REQUESTED,
            CallStatus.TRANSFERRING,
            CallStatus.TRANSFERRED,
        }
        else None
    )
    return CallStateRead(
        call_id=call.id,
        status=call.status,
        state_version=call.state_version,
        direction=call.direction,
        provider=call.provider,
        provider_call_id=call.external_call_id,
        state=call.status,
        recording_state=call.recording_state,
        allowed_actions=allowed_actions(call.status, recording_state=call.recording_state),
        transfer_state=transfer_state,
        started_at=call.started_at,
        ringing_at=call.ringing_at,
        answered_at=call.answered_at,
        held_at=call.held_at,
        ended_at=call.ended_at,
        hangup_cause=call.hangup_cause,
        terminal=call.status in TERMINAL_CALL_STATES,
        last_event=(
            CallLastEventRead(
                event_type=last_event.event_type,
                sequence=last_event.sequence,
                occurred_at=last_event.occurred_at,
            )
            if last_event
            else None
        ),
        updated_at=call.updated_at,
    )


def outcome_snapshot(outcome: CallOutcome) -> CallOutcomeSnapshotRead:
    return CallOutcomeSnapshotRead(
        result_definition_id=outcome.result_definition_id,
        code=outcome.code,
        label=outcome.label,
        category=outcome.category,
        color=outcome.color,
    )


def result_fingerprint(payload: CallResultRequest, result_definition_id: UUID) -> str:
    body = json.dumps(
        {
            "result_definition_id": str(result_definition_id),
            "comment": payload.comment.strip(),
            "callback_at": payload.callback_at.isoformat() if payload.callback_at else None,
            "task": payload.task.model_dump(mode="json") if payload.task else None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def result_definition_for_call(
    session: SessionDep,
    *,
    tenant_id: UUID,
    project_id: UUID,
    result_definition_id: UUID | None,
    legacy_code: str | None,
) -> CallResultDefinition:
    filters = [
        CallResultDefinition.tenant_id == tenant_id,
        CallResultDefinition.project_id == project_id,
    ]
    if result_definition_id is not None:
        filters.append(CallResultDefinition.id == result_definition_id)
    else:
        filters.append(CallResultDefinition.system_code == legacy_code)
    definition = await session.scalar(select(CallResultDefinition).where(*filters))
    if definition is None:
        raise ApiError(422, "call_result_invalid", "Результат не принадлежит проекту звонка")
    if not definition.is_active or definition.archived_at is not None:
        raise ApiError(409, "call_result_unavailable", "Результат архивирован или отключён")
    return definition


@router.get("/active", response_model=CallRead | None)
async def active_operator_call(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> CallRead | None:
    call = await session.scalar(
        select(Call)
        .outerjoin(
            CallOutcome,
            and_(
                CallOutcome.call_id == Call.id,
                CallOutcome.tenant_id == principal.tenant_id,
            ),
        )
        .where(
            Call.tenant_id == principal.tenant_id,
            Call.operator_user_id == principal.user_id,
            or_(
                Call.status.in_(CAPACITY_CALL_STATES),
                and_(Call.status.in_(TERMINAL_CALL_STATES), CallOutcome.id.is_(None)),
            ),
        )
        .order_by(Call.started_at.desc())
        .limit(1)
    )
    return serialize_call(call) if call else None


@router.post("/{call_id}/result", response_model=CallResultResponse)
async def save_call_result(
    call_id: UUID,
    payload: CallResultRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> CallResultResponse:
    return await save_call_result_transactional(
        call_id=call_id,
        payload=payload,
        request=request,
        session=session,
        principal=principal,
        idempotency_key=idempotency_key,
        commit=True,
    )


async def save_call_result_transactional(
    *,
    call_id: UUID,
    payload: CallResultRequest,
    request: Request,
    session: SessionDep,
    principal: Principal,
    idempotency_key: str | None,
    commit: bool,
) -> CallResultResponse:
    now = datetime.now(UTC)
    call = await controlled_call(session, principal.tenant_id, principal.user_id, call_id)
    if call.status == CallStatus.QUEUED:
        raise ApiError(
            409,
            "call_not_started",
            "Нельзя сохранить результат до запуска звонка",
        )
    project = await resolve_project(session, principal, call.project_id, active_only=False)
    if call.customer_id is None:
        raise ApiError(409, "call_customer_missing", "У звонка отсутствует клиент")
    customer = await session.scalar(
        select(Customer).where(Customer.tenant_id == principal.tenant_id, Customer.id == call.customer_id)
    )
    if customer is None:
        raise ApiError(404, "customer_not_found", "Клиент не найден")

    definition = await result_definition_for_call(
        session,
        tenant_id=principal.tenant_id,
        project_id=call.project_id,
        result_definition_id=payload.result_definition_id,
        legacy_code=payload.legacy_result_code(),
    )
    comment = payload.comment.strip()
    if definition.requires_comment and not comment:
        raise ApiError(422, "call_result_comment_required", "Для этого результата нужен комментарий")
    if definition.requires_callback_at and (
        payload.callback_at is None or payload.callback_at.tzinfo is None
    ):
        raise ApiError(422, "callback_date_required", "Укажите дату и время перезвона")
    if payload.callback_at is not None:
        if payload.callback_at.tzinfo is None:
            raise ApiError(422, "callback_timezone_required", "Укажите часовой пояс перезвона")
        if payload.callback_at.astimezone(UTC) <= now:
            raise ApiError(422, "callback_date_past", "Дата перезвона должна быть в будущем")

    fingerprint = result_fingerprint(payload, definition.id)
    if idempotency_key is not None:
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"{principal.tenant_id}:{idempotency_key}",
                        0,
                    )
                )
            )
        )
        replay = await session.scalar(
            select(CallResultSubmission).where(
                CallResultSubmission.tenant_id == principal.tenant_id,
                CallResultSubmission.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise ApiError(409, "idempotency_key_reused", "Idempotency-Key уже использован")
            return CallResultResponse.model_validate(replay.response_payload)

    if call.status in CAPACITY_CALL_STATES - {CallStatus.QUEUED}:
        await execute_call_command(
            session,
            principal=principal,
            call=call,
            command=TelephonyCommandName.HANGUP,
            correlation_id=request.state.correlation_id,
            idempotency_key=None,
            parameters={"reason": "result_saved"},
        )

    outcome = await session.scalar(
        select(CallOutcome).where(
            CallOutcome.tenant_id == principal.tenant_id, CallOutcome.call_id == call.id
        )
    )
    previous_comment = "" if outcome is None else str(outcome.details.get("comment", ""))
    if outcome is None:
        outcome = CallOutcome(
            tenant_id=principal.tenant_id,
            project_id=call.project_id,
            call_id=call.id,
            result_definition_id=definition.id,
            code=definition.system_code,
            label=definition.name,
            category=definition.category,
            color=definition.color,
            label_translations=definition.name_translations,
        )
        session.add(outcome)
    outcome.project_id = call.project_id
    outcome.result_definition_id = definition.id
    outcome.code = definition.system_code
    outcome.label = localized_result_name(
        definition,
        call.language.value
        if call.language
        else (customer.preferred_language.value if customer.preferred_language else "ru"),
    )
    outcome.category = definition.category
    outcome.color = definition.color
    outcome.label_translations = dict(definition.name_translations)
    outcome.details = {"comment": comment, "creates_task_requested": definition.creates_task}
    if comment and comment != previous_comment:
        session.add(
            CustomerNote(
                tenant_id=principal.tenant_id,
                customer_id=customer.id,
                author_user_id=principal.user_id,
                content=comment,
            )
        )

    await session.flush()
    prior_tasks = list(
        await session.scalars(
            select(CallbackTask).where(
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.call_id == call.id,
                CallbackTask.task_type == TaskType.CALLBACK,
                CallbackTask.status == TaskStatus.IN_PROGRESS,
            )
        )
    )
    for task in prior_tasks:
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        task.cancelled_at = None
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.COMPLETED,
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"completed_by_call_result": str(outcome.id)},
        )

    pending_callbacks = list(
        await session.scalars(
            select(CallbackTask)
            .where(
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.customer_id == customer.id,
                CallbackTask.task_type == TaskType.CALLBACK,
                CallbackTask.status == TaskStatus.PENDING,
                or_(
                    CallbackTask.call_outcome_id == outcome.id,
                    CallbackTask.call_id == call.id,
                ),
            )
            .order_by(CallbackTask.created_at)
        )
    )
    new_callback: CallbackTask | None = None
    if definition.requires_callback and not definition.do_not_call:
        due_at = payload.callback_at
        if due_at is None:
            configured_delay = project.callback_rules.get("default_delay_minutes", 60)
            delay = configured_delay if isinstance(configured_delay, int) else 60
            due_at = now + timedelta(minutes=delay)
        due_at = due_at.astimezone(UTC)
        if pending_callbacks:
            new_callback = pending_callbacks[0]
            new_callback.due_at = due_at
            new_callback.assigned_user_id = principal.user_id
            new_callback.comment = comment
            new_callback.note = comment
            new_callback.call_id = call.id
            new_callback.call_outcome_id = outcome.id
            await append_task_event(
                session,
                task=new_callback,
                event_type=TaskEventType.RESCHEDULED,
                actor_user_id=principal.user_id,
                correlation_id=request.state.correlation_id,
                safe_snapshot={"due_at": due_at.isoformat(), "call_outcome_id": str(outcome.id)},
            )
            for duplicate_callback in pending_callbacks[1:]:
                await cancel_task_record(
                    session,
                    task=duplicate_callback,
                    actor_user_id=principal.user_id,
                    reason="Дубликат callback для результата звонка",
                    correlation_id=request.state.correlation_id,
                )
        else:
            new_callback = await create_task_record(
                session,
                tenant_id=principal.tenant_id,
                project_id=customer.project_id,
                customer_id=customer.id,
                created_by_user_id=principal.user_id,
                task_type=TaskType.CALLBACK,
                title=f"Перезвон: {outcome.label}",
                description=comment,
                priority=TaskPriority.NORMAL,
                call_id=call.id,
                call_outcome_id=outcome.id,
                assigned_user_id=principal.user_id,
                due_at=due_at,
                source=TaskSource.CALL_RESULT,
                correlation_id=request.state.correlation_id,
                comment=comment,
            )
    else:
        for pending_callback in pending_callbacks:
            await cancel_task_record(
                session,
                task=pending_callback,
                actor_user_id=principal.user_id,
                reason="Результат звонка больше не требует перезвона",
                correlation_id=request.state.correlation_id,
            )
        if definition.do_not_call:
            customer.status = "do_not_call"
        elif definition.return_to_queue:
            customer.status = definition.next_customer_status or "new"
        elif definition.next_customer_status:
            customer.status = definition.next_customer_status
        elif definition.completes_customer:
            customer.status = "completed"
    result_tasks = list(
        await session.scalars(
            select(CallbackTask)
            .where(
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.customer_id == customer.id,
                CallbackTask.call_outcome_id == outcome.id,
                CallbackTask.task_type != TaskType.CALLBACK,
                CallbackTask.source == TaskSource.CALL_RESULT,
                CallbackTask.status == TaskStatus.PENDING,
            )
            .order_by(CallbackTask.created_at)
        )
    )
    new_general_task: CallbackTask | None = None
    if definition.creates_task or payload.task is not None:
        task_payload = payload.task
        default_delay = project.callback_rules.get("default_delay_minutes", 60)
        delay = default_delay if isinstance(default_delay, int) else 60
        task_due_at = task_payload.due_at if task_payload else now + timedelta(minutes=delay)
        task_title = task_payload.title if task_payload else f"Задача: {outcome.label}"
        task_description = task_payload.description if task_payload else comment
        task_priority = task_payload.priority if task_payload else TaskPriority.NORMAL
        task_assignee = task_payload.assigned_user_id if task_payload else principal.user_id
        if task_assignee not in (None, principal.user_id):
            raise ApiError(403, "task_reassign_forbidden", "Оператор может назначить задачу только себе")
        if result_tasks:
            new_general_task = result_tasks[0]
            new_general_task.title = task_title.strip()
            new_general_task.description = task_description.strip()
            new_general_task.priority = task_priority
            new_general_task.due_at = utc_due_at(task_due_at, allow_now=task_payload is None)
            new_general_task.assigned_user_id = task_assignee
            new_general_task.comment = comment
            new_general_task.note = comment
            await append_task_event(
                session,
                task=new_general_task,
                event_type=TaskEventType.UPDATED,
                actor_user_id=principal.user_id,
                correlation_id=request.state.correlation_id,
                safe_snapshot={"updated_by_call_result": str(outcome.id)},
            )
            for duplicate_task in result_tasks[1:]:
                await cancel_task_record(
                    session,
                    task=duplicate_task,
                    actor_user_id=principal.user_id,
                    reason="Дубликат задачи результата звонка",
                    correlation_id=request.state.correlation_id,
                )
        else:
            new_general_task = await create_task_record(
                session,
                tenant_id=principal.tenant_id,
                project_id=customer.project_id,
                customer_id=customer.id,
                created_by_user_id=principal.user_id,
                task_type=TaskType.FOLLOW_UP,
                title=task_title,
                description=task_description,
                priority=task_priority,
                due_at=task_due_at,
                assigned_user_id=task_assignee,
                source=TaskSource.CALL_RESULT,
                correlation_id=request.state.correlation_id,
                comment=comment,
                call_id=call.id,
                call_outcome_id=outcome.id,
                allow_due_now=task_payload is None,
            )
    else:
        for result_task in result_tasks:
            await cancel_task_record(
                session,
                task=result_task,
                actor_user_id=principal.user_id,
                reason="Результат звонка больше не требует общей задачи",
                correlation_id=request.state.correlation_id,
            )

    if definition.do_not_call:
        future_callbacks = list(
            await session.scalars(
                select(CallbackTask).where(
                    CallbackTask.tenant_id == principal.tenant_id,
                    CallbackTask.customer_id == customer.id,
                    CallbackTask.task_type == TaskType.CALLBACK,
                    CallbackTask.status == TaskStatus.PENDING,
                    CallbackTask.due_at > now,
                )
            )
        )
        for future_callback in future_callbacks:
            await cancel_task_record(
                session,
                task=future_callback,
                actor_user_id=principal.user_id,
                reason="Клиент отмечен как do-not-call",
                correlation_id=request.state.correlation_id,
            )
        customer.status = "do_not_call"
    await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    if definition.do_not_call:
        customer.status = "do_not_call"
    customer.locked_by_user_id = None
    customer.locked_until = None
    customer.lock_token = None
    customer.dialer_assignment_source = None
    customer.last_call_at = call.ended_at or now
    await append_call_event(
        session,
        tenant_id=principal.tenant_id,
        call_id=call.id,
        event_type="call.result_saved",
        safe_payload={
            "result_definition_id": str(definition.id),
            "result": definition.system_code,
            "category": definition.category.value,
            "callback": new_callback is not None,
            "task": new_general_task is not None,
        },
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call.result_saved",
        resource_type="call",
        resource_id=call.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "result_definition_id": str(definition.id),
            "result": definition.system_code,
            "category": definition.category.value,
        },
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=call.project_id,
        target_membership_id=principal.membership_id,
        event_type="dialer.call_completed",
        aggregate_type="call",
        aggregate_id=call.id,
        aggregate_version=call.state_version,
        payload={"result_saved": True},
        correlation_id=request.state.correlation_id,
    )
    await enqueue_analytics_invalidation(
        session,
        tenant_id=principal.tenant_id,
        project_id=call.project_id,
        aggregate_type="call_outcome",
        aggregate_id=outcome.id,
        correlation_id=request.state.correlation_id,
        reason="call_result_saved",
    )
    await session.flush()
    response = CallResultResponse(
        call=serialize_call(call),
        customer_status=customer.status,
        callback_task_id=new_callback.id if new_callback else None,
        task_ids=[task.id for task in (new_callback, new_general_task) if task is not None],
        outcome=outcome_snapshot(outcome),
    )
    if idempotency_key is not None:
        session.add(
            CallResultSubmission(
                tenant_id=principal.tenant_id,
                outcome_id=outcome.id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                response_payload=response.model_dump(mode="json"),
            )
        )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return response


@router.get("", response_model=Page[CallRead])
async def list_calls(
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
    project_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[CallRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [Call.tenant_id == principal.tenant_id]
    if project_id is not None:
        project = await resolve_project(session, principal, project_id, active_only=False)
        filters.append(Call.project_id == project.id)
    total = int(await session.scalar(select(func.count()).select_from(Call).where(*filters)) or 0)
    calls = list(
        await session.scalars(
            select(Call).where(*filters).order_by(Call.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return Page(items=[serialize_call(call) for call in calls], total=total, limit=limit, offset=offset)


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> CallDetail:
    call = await session.scalar(select(Call).where(Call.tenant_id == principal.tenant_id, Call.id == call_id))
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    await resolve_project(session, principal, call.project_id, active_only=False)
    segments = list(
        await session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.tenant_id == principal.tenant_id,
                TranscriptSegment.call_id == call.id,
            )
            .order_by(TranscriptSegment.sequence)
        )
    )
    summary = await session.scalar(
        select(CallSummary).where(
            CallSummary.tenant_id == principal.tenant_id, CallSummary.call_id == call.id
        )
    )
    base = serialize_call(call).model_dump()
    return CallDetail(
        **base,
        transcript=[
            TranscriptSegmentRead(
                id=segment.id,
                sequence=segment.sequence,
                speaker=segment.speaker,
                language=segment.language,
                language_code=segment.language_code,
                text=segment.text,
                provider_item_id=segment.provider_item_id,
                is_final=segment.is_final,
                interrupted=segment.interrupted,
                created_at=segment.created_at,
            )
            for segment in segments
        ],
        summary=(
            CallSummaryRead(
                summary=summary.summary,
                topics=summary.topics,
                result=summary.result,
                generated_by=summary.generated_by,
            )
            if summary
            else None
        ),
    )
