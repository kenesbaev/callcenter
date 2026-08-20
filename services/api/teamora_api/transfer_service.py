from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.audit import write_audit
from teamora_api.background_service import enqueue_background_job
from teamora_api.call_state import CallStateService
from teamora_api.config import Settings
from teamora_api.dependencies import Principal
from teamora_api.enums import (
    CallStatus,
    QueueStatus,
    RoleName,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
    TransferStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    AIRealtimeSession,
    Call,
    CallbackTask,
    CallFlowExecution,
    CallSummary,
    Customer,
    HumanOperator,
    Membership,
    OperatorStatus,
    OperatorTransferEndpoint,
    ProjectUser,
    ToolExecution,
    TranscriptSegment,
    TransferAttempt,
    TransferRequest,
    User,
)
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.task_service import create_task_record
from teamora_api.team_service import get_operator_state
from teamora_api.telephony.control import release_transfer_channel

OPEN_TRANSFER_STATUSES = {
    TransferStatus.REQUESTED,
    TransferStatus.QUEUED,
    TransferStatus.OFFERED,
    TransferStatus.CLAIMED,
    TransferStatus.CONNECTING,
    TransferStatus.CONNECTED,
    TransferStatus.ASSIGNED,
}
ACTIVE_ATTEMPT_STATUSES = {"offered", "claimed", "connecting"}


async def build_context_snapshot(session: AsyncSession, call: Call) -> dict[str, object]:
    customer = await session.scalar(
        select(Customer).where(Customer.tenant_id == call.tenant_id, Customer.id == call.customer_id)
    )
    flow = await session.scalar(
        select(CallFlowExecution).where(
            CallFlowExecution.tenant_id == call.tenant_id,
            CallFlowExecution.call_id == call.id,
        )
    )
    summary = await session.scalar(
        select(CallSummary.summary).where(
            CallSummary.tenant_id == call.tenant_id,
            CallSummary.call_id == call.id,
        )
    )
    final_segments = list(
        await session.scalars(
            select(TranscriptSegment.text)
            .where(
                TranscriptSegment.tenant_id == call.tenant_id,
                TranscriptSegment.call_id == call.id,
                TranscriptSegment.is_final.is_(True),
            )
            .order_by(TranscriptSegment.sequence.desc())
            .limit(8)
        )
    )
    tools = list(
        await session.scalars(
            select(ToolExecution.tool_name)
            .where(ToolExecution.tenant_id == call.tenant_id, ToolExecution.call_id == call.id)
            .order_by(ToolExecution.created_at.desc())
            .limit(20)
        )
    )
    recent_calls = (
        list(
            await session.scalars(
                select(Call)
                .where(
                    Call.tenant_id == call.tenant_id,
                    Call.customer_id == call.customer_id,
                    Call.id != call.id,
                )
                .order_by(Call.started_at.desc().nullslast(), Call.created_at.desc())
                .limit(3)
            )
        )
        if call.customer_id
        else []
    )
    related_tasks = (
        list(
            await session.scalars(
                select(CallbackTask)
                .where(
                    CallbackTask.tenant_id == call.tenant_id,
                    CallbackTask.customer_id == call.customer_id,
                    CallbackTask.status.in_({TaskStatus.PENDING, TaskStatus.IN_PROGRESS}),
                )
                .order_by(CallbackTask.due_at, CallbackTask.created_at)
                .limit(5)
            )
        )
        if call.customer_id
        else []
    )
    return {
        "customer_id": str(call.customer_id) if call.customer_id else None,
        "customer_name": (customer.display_name if customer else None),
        "project_id": str(call.project_id),
        "language": call.language.value if call.language else "ru",
        "flow_node_id": str(flow.current_node_id) if flow and flow.current_node_id else None,
        "summary": (summary or "")[:2000],
        "transcript_excerpt": "\n".join(reversed(final_segments))[-6000:],
        "tools": list(dict.fromkeys(tools)),
        "recent_calls": [
            {
                "id": str(previous.id),
                "status": previous.status.value,
                "started_at": previous.started_at.isoformat() if previous.started_at else None,
            }
            for previous in recent_calls
        ],
        "related_tasks": [
            {
                "id": str(task.id),
                "type": task.task_type.value,
                "status": task.status.value,
                "due_at": task.due_at.isoformat(),
            }
            for task in related_tasks
        ],
    }


async def request_transfer(
    session: AsyncSession,
    *,
    call: Call,
    reason: str,
    summary: str,
    destination_type: str,
    idempotency_key: str,
    correlation_id: str,
    actor_user_id: UUID | None,
    settings: Settings,
    expected_call_version: int | None = None,
    routing_strategy: str | None = None,
    max_attempts: int | None = None,
) -> TransferRequest:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"transfer:{call.tenant_id}:{call.id}", 0)))
    )
    replay = await session.scalar(
        select(TransferRequest).where(
            TransferRequest.tenant_id == call.tenant_id,
            TransferRequest.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.call_id != call.id:
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
        return replay
    existing = await session.scalar(
        select(TransferRequest)
        .where(
            TransferRequest.tenant_id == call.tenant_id,
            TransferRequest.call_id == call.id,
            TransferRequest.status.in_(OPEN_TRANSFER_STATUSES),
        )
        .with_for_update()
    )
    if existing is not None:
        return existing
    if call.status not in {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFER_REQUESTED}:
        raise ApiError(409, "transfer_state_invalid", "Call is not available for live transfer")
    if expected_call_version is not None and call.state_version != expected_call_version:
        raise ApiError(409, "call_state_version_conflict", "Call state was changed by another operation")
    now = datetime.now(UTC)
    row = TransferRequest(
        tenant_id=call.tenant_id,
        project_id=call.project_id,
        call_id=call.id,
        status=TransferStatus.REQUESTED,
        reason=reason.strip()[:500],
        summary=summary.strip()[:4000],
        language_code=(call.language.value if call.language else "ru").lower(),
        routing_strategy=routing_strategy or settings.transfer_routing_strategy,
        destination_type=destination_type,
        context_snapshot=await build_context_snapshot(session, call),
        attempt_count=0,
        max_attempts=max_attempts or settings.transfer_max_attempts,
        idempotency_key=idempotency_key,
        lock_version=1,
        requested_at=now,
    )
    session.add(row)
    await session.flush()
    if call.status != CallStatus.TRANSFER_REQUESTED:
        await CallStateService().transition(
            session,
            call=call,
            target=CallStatus.TRANSFER_REQUESTED,
            event_type="transfer.requested",
            occurred_at=now,
            correlation_id=correlation_id,
            actor_user_id=actor_user_id,
            expected_version=expected_call_version,
            safe_payload={"transfer_request_id": str(row.id)},
        )
    await write_audit(
        session,
        tenant_id=call.tenant_id,
        actor_user_id=actor_user_id,
        action="transfer.requested",
        resource_type="transfer_request",
        resource_id=row.id,
        correlation_id=correlation_id,
        safe_metadata={"call_id": str(call.id), "destination_type": destination_type},
    )
    await enqueue_realtime_event(
        session,
        tenant_id=call.tenant_id,
        project_id=call.project_id,
        event_type="transfer.requested",
        aggregate_type="transfer_request",
        aggregate_id=row.id,
        aggregate_version=row.lock_version,
        payload={"status": row.status.value, "call_id": call.id},
        correlation_id=correlation_id,
    )
    await offer_next_operator(session, transfer=row, settings=settings, correlation_id=correlation_id)
    return row


async def _candidate_rows(
    session: AsyncSession, transfer: TransferRequest
) -> list[tuple[Membership, User, HumanOperator, OperatorStatus]]:
    attempted = select(TransferAttempt.membership_id).where(
        TransferAttempt.tenant_id == transfer.tenant_id,
        TransferAttempt.transfer_request_id == transfer.id,
    )
    current_call_owner = await session.scalar(
        select(Call.operator_user_id).where(
            Call.tenant_id == transfer.tenant_id,
            Call.id == transfer.call_id,
        )
    )
    candidate_filters = [
        Membership.tenant_id == transfer.tenant_id,
        Membership.is_active.is_(True),
        Membership.role.in_({RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER, RoleName.HUMAN_OPERATOR}),
        HumanOperator.is_transfer_available.is_(True),
        ~Membership.id.in_(attempted),
    ]
    if current_call_owner is not None:
        candidate_filters.append(Membership.user_id != current_call_owner)
    rows = (
        await session.execute(
            select(Membership, User, HumanOperator, OperatorStatus)
            .join(User, User.id == Membership.user_id)
            .join(
                ProjectUser,
                and_(
                    ProjectUser.tenant_id == Membership.tenant_id,
                    ProjectUser.user_id == Membership.user_id,
                    ProjectUser.project_id == transfer.project_id,
                    ProjectUser.is_active.is_(True),
                ),
            )
            .join(
                HumanOperator,
                and_(
                    HumanOperator.tenant_id == Membership.tenant_id,
                    HumanOperator.membership_id == Membership.id,
                ),
            )
            .join(
                OperatorStatus,
                and_(
                    OperatorStatus.tenant_id == Membership.tenant_id,
                    OperatorStatus.human_operator_id == HumanOperator.id,
                ),
            )
            .where(*candidate_filters)
        )
    ).all()
    available: list[tuple[Membership, User, HumanOperator, OperatorStatus]] = []
    for membership, user, operator, status in rows:
        state = await get_operator_state(
            session,
            tenant_id=transfer.tenant_id,
            membership=membership,
            operator=operator,
            status=status,
        )
        if state.effective_status == QueueStatus.AVAILABLE.value:
            available.append((membership, user, operator, status))
    language = transfer.language_code.lower()
    available.sort(
        key=lambda item: (
            0 if item[0].interface_language.lower() == language else 1,
            item[0].presence_last_seen_at or datetime.min.replace(tzinfo=UTC),
            str(item[0].id),
        )
    )
    if transfer.routing_strategy == "priority":
        available.sort(
            key=lambda item: (
                0 if item[0].interface_language.lower() == language else 1,
                item[0].created_at,
                str(item[0].id),
            )
        )
    elif transfer.routing_strategy == "round_robin":
        anchor = transfer.id.int
        available.sort(
            key=lambda item: (
                0 if item[0].interface_language.lower() == language else 1,
                (item[0].id.int - anchor) % (1 << 128),
            )
        )
    return available


async def offer_next_operator(
    session: AsyncSession,
    *,
    transfer: TransferRequest,
    settings: Settings,
    correlation_id: str,
) -> TransferAttempt | None:
    now = datetime.now(UTC)
    active_attempts = list(
        await session.scalars(
            select(TransferAttempt)
            .where(
                TransferAttempt.tenant_id == transfer.tenant_id,
                TransferAttempt.transfer_request_id == transfer.id,
                TransferAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
            )
            .with_for_update()
        )
    )
    for attempt in active_attempts:
        if attempt.status == "offered" and attempt.expires_at <= now:
            attempt.status = "timed_out"
            attempt.resolved_at = now
            attempt.safe_error_code = "offer_timeout"
        else:
            return attempt
    if transfer.attempt_count >= transfer.max_attempts:
        await fallback_transfer(
            session,
            transfer=transfer,
            reason="operator_attempts_exhausted",
            correlation_id=correlation_id,
        )
        return None
    candidates = await _candidate_rows(session, transfer)
    if not candidates:
        await fallback_transfer(
            session,
            transfer=transfer,
            reason="no_available_operator",
            correlation_id=correlation_id,
        )
        return None
    membership, _user, operator, _status = candidates[0]
    endpoint = await session.scalar(
        select(OperatorTransferEndpoint).where(
            OperatorTransferEndpoint.tenant_id == transfer.tenant_id,
            OperatorTransferEndpoint.membership_id == membership.id,
            OperatorTransferEndpoint.endpoint_type == transfer.destination_type,
            OperatorTransferEndpoint.is_enabled.is_(True),
            OperatorTransferEndpoint.is_verified.is_(True),
        )
    )
    if transfer.destination_type == "browser":
        destination_ref = f"webrtc:{membership.id}"
    elif endpoint is not None:
        destination_ref = endpoint.destination
    elif transfer.destination_type == "sip" and operator.extension:
        destination_ref = f"sip:{operator.extension}"
    else:
        # This operator has no allowlisted endpoint for the requested transport.
        # Record the failed offer and recurse to the next candidate.
        transfer.attempt_count += 1
        skipped = TransferAttempt(
            tenant_id=transfer.tenant_id,
            project_id=transfer.project_id,
            transfer_request_id=transfer.id,
            membership_id=membership.id,
            attempt_number=transfer.attempt_count,
            destination_type=transfer.destination_type,
            status="failed",
            idempotency_key=f"{transfer.id}:attempt:{transfer.attempt_count}",
            offered_at=now,
            expires_at=now,
            resolved_at=now,
            safe_error_code="operator_endpoint_unavailable",
        )
        session.add(skipped)
        await session.flush()
        return await offer_next_operator(
            session, transfer=transfer, settings=settings, correlation_id=correlation_id
        )
    transfer.attempt_count += 1
    transfer.status = TransferStatus.OFFERED
    transfer.lock_version += 1
    transfer.offer_expires_at = now + timedelta(seconds=settings.transfer_offer_timeout_seconds)
    attempt = TransferAttempt(
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        transfer_request_id=transfer.id,
        membership_id=membership.id,
        attempt_number=transfer.attempt_count,
        destination_type=transfer.destination_type,
        destination_ref=destination_ref,
        status="offered",
        idempotency_key=f"{transfer.id}:attempt:{transfer.attempt_count}",
        offered_at=now,
        expires_at=transfer.offer_expires_at,
    )
    session.add(attempt)
    await session.flush()
    await enqueue_background_job(
        session,
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        job_type="transfer.offer_timeout",
        queue="telephony",
        priority=90,
        safe_payload={"transfer_request_id": str(transfer.id)},
        correlation_id=correlation_id,
        idempotency_key=f"transfer-timeout:{attempt.id}",
        scheduled_at=attempt.expires_at,
        max_attempts=8,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        target_membership_id=membership.id,
        event_type="transfer.offered",
        aggregate_type="transfer_request",
        aggregate_id=transfer.id,
        aggregate_version=transfer.lock_version,
        payload={"status": "offered", "attempt_id": attempt.id, "call_id": transfer.call_id},
        correlation_id=correlation_id,
    )
    return attempt


async def claim_transfer(
    session: AsyncSession,
    *,
    transfer_id: UUID,
    principal: Principal,
    expected_version: int,
    correlation_id: str,
) -> TransferRequest:
    transfer = await session.scalar(
        select(TransferRequest)
        .where(TransferRequest.tenant_id == principal.tenant_id, TransferRequest.id == transfer_id)
        .with_for_update()
    )
    if transfer is None:
        raise ApiError(404, "transfer_not_found", "Transfer request was not found")
    if transfer.lock_version != expected_version:
        raise ApiError(409, "transfer_version_conflict", "Transfer request was changed")
    attempt = await session.scalar(
        select(TransferAttempt)
        .where(
            TransferAttempt.tenant_id == principal.tenant_id,
            TransferAttempt.transfer_request_id == transfer.id,
            TransferAttempt.membership_id == principal.membership_id,
            TransferAttempt.status == "offered",
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if attempt is None or attempt.expires_at <= now:
        raise ApiError(409, "transfer_offer_unavailable", "Transfer offer is no longer available")
    if transfer.claimed_membership_id not in {None, principal.membership_id}:
        raise ApiError(409, "transfer_already_claimed", "Another operator accepted this transfer")
    call = await session.scalar(
        select(Call)
        .where(Call.tenant_id == transfer.tenant_id, Call.id == transfer.call_id)
        .with_for_update()
    )
    if call is None or call.status != CallStatus.TRANSFER_REQUESTED:
        raise ApiError(409, "transfer_call_state_invalid", "Call is no longer waiting for transfer")
    transfer.claimed_membership_id = principal.membership_id
    transfer.status = TransferStatus.CLAIMED
    transfer.claimed_at = now
    transfer.lock_version += 1
    attempt.status = "claimed"
    attempt.claimed_at = now
    call.operator_user_id = principal.user_id
    await CallStateService().transition(
        session,
        call=call,
        target=CallStatus.TRANSFERRING,
        event_type="transfer.started",
        occurred_at=now,
        correlation_id=correlation_id,
        actor_user_id=principal.user_id,
        safe_payload={"transfer_request_id": str(transfer.id)},
    )
    await enqueue_realtime_event(
        session,
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        target_membership_id=principal.membership_id,
        event_type="transfer.claimed",
        aggregate_type="transfer_request",
        aggregate_id=transfer.id,
        aggregate_version=transfer.lock_version,
        payload={"status": "claimed", "call_id": transfer.call_id},
        correlation_id=correlation_id,
    )
    return transfer


async def complete_transfer(
    session: AsyncSession,
    *,
    transfer: TransferRequest,
    principal: Principal,
    provider_channel_id: str,
    correlation_id: str,
) -> TransferRequest:
    if transfer.claimed_membership_id != principal.membership_id:
        raise ApiError(403, "transfer_not_claimed", "This transfer belongs to another operator")
    now = datetime.now(UTC)
    call = await session.scalar(
        select(Call)
        .where(Call.tenant_id == transfer.tenant_id, Call.id == transfer.call_id)
        .with_for_update()
    )
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    if call.status != CallStatus.TRANSFERRING:
        raise ApiError(409, "transfer_call_state_invalid", "Call is not connecting to an operator")
    attempt = await session.scalar(
        select(TransferAttempt)
        .where(
            TransferAttempt.tenant_id == transfer.tenant_id,
            TransferAttempt.transfer_request_id == transfer.id,
            TransferAttempt.membership_id == principal.membership_id,
            TransferAttempt.status.in_({"claimed", "connecting"}),
        )
        .with_for_update()
    )
    if attempt is None:
        raise ApiError(409, "transfer_attempt_missing", "Transfer attempt is unavailable")
    attempt.status = "connected"
    attempt.answered_at = now
    attempt.resolved_at = now
    attempt.provider_channel_id = provider_channel_id[:200]
    transfer.status = TransferStatus.CONNECTED
    transfer.connected_at = now
    transfer.operator_channel_id = provider_channel_id[:200]
    transfer.lock_version += 1
    await CallStateService().transition(
        session,
        call=call,
        target=CallStatus.TRANSFERRED,
        event_type="transfer.completed",
        occurred_at=now,
        correlation_id=correlation_id,
        actor_user_id=principal.user_id,
        safe_payload={"transfer_request_id": str(transfer.id)},
    )
    await enqueue_realtime_event(
        session,
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        event_type="transfer.completed",
        aggregate_type="transfer_request",
        aggregate_id=transfer.id,
        aggregate_version=transfer.lock_version,
        payload={"status": "connected", "call_id": transfer.call_id},
        correlation_id=correlation_id,
    )
    return transfer


async def apply_provider_transfer_event(
    session: AsyncSession,
    *,
    call: Call,
    event_type: str,
    provider_channel_id: str | None,
    safe_error_code: str | None,
    correlation_id: str,
    settings: Settings,
) -> TransferRequest | None:
    """Synchronize orchestration records after the canonical Call transition.

    The provider event is authoritative only for media connection state. Tenant,
    project and Call scope were already verified by the telephony webhook.
    """

    transfer = await session.scalar(
        select(TransferRequest)
        .where(
            TransferRequest.tenant_id == call.tenant_id,
            TransferRequest.call_id == call.id,
            TransferRequest.status.in_(OPEN_TRANSFER_STATUSES),
        )
        .order_by(TransferRequest.requested_at.desc())
        .limit(1)
        .with_for_update()
    )
    if transfer is None:
        return None
    attempt = await session.scalar(
        select(TransferAttempt)
        .where(
            TransferAttempt.tenant_id == call.tenant_id,
            TransferAttempt.transfer_request_id == transfer.id,
            TransferAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
        )
        .order_by(TransferAttempt.attempt_number.desc())
        .limit(1)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if event_type == "transfer.completed":
        if attempt is not None:
            attempt.status = "connected"
            attempt.answered_at = attempt.answered_at or now
            attempt.resolved_at = attempt.resolved_at or now
            if provider_channel_id:
                attempt.provider_channel_id = provider_channel_id[:200]
        transfer.status = TransferStatus.CONNECTED
        transfer.connected_at = transfer.connected_at or now
        transfer.operator_channel_id = (
            provider_channel_id[:200] if provider_channel_id else transfer.operator_channel_id
        )
        transfer.lock_version += 1
        await enqueue_realtime_event(
            session,
            tenant_id=transfer.tenant_id,
            project_id=transfer.project_id,
            event_type="transfer.completed",
            aggregate_type="transfer_request",
            aggregate_id=transfer.id,
            aggregate_version=transfer.lock_version,
            payload={"status": "connected", "call_id": transfer.call_id},
            correlation_id=correlation_id,
        )
    elif event_type == "transfer.failed":
        await release_transfer_channel(
            session,
            call=call,
            reason=safe_error_code or "provider_transfer_failed",
        )
        if attempt is not None:
            attempt.status = "failed"
            attempt.safe_error_code = (safe_error_code or "provider_transfer_failed")[:80]
            attempt.resolved_at = now
        transfer.status = TransferStatus.QUEUED
        transfer.claimed_membership_id = None
        transfer.claimed_at = None
        transfer.last_error_code = (safe_error_code or "provider_transfer_failed")[:80]
        transfer.lock_version += 1
        if call.status == CallStatus.ACTIVE:
            await CallStateService().transition(
                session,
                call=call,
                target=CallStatus.TRANSFER_REQUESTED,
                event_type="transfer.requested",
                occurred_at=now,
                correlation_id=correlation_id,
                actor_user_id=None,
                safe_payload={"retry": True, "transfer_request_id": str(transfer.id)},
            )
        await offer_next_operator(
            session,
            transfer=transfer,
            settings=settings,
            correlation_id=correlation_id,
        )
    return transfer


async def decline_transfer(
    session: AsyncSession,
    *,
    transfer: TransferRequest,
    principal: Principal,
    expected_version: int,
    reason: str,
    settings: Settings,
    correlation_id: str,
) -> TransferRequest:
    if transfer.lock_version != expected_version:
        raise ApiError(409, "transfer_version_conflict", "Transfer request was changed")
    attempt = await session.scalar(
        select(TransferAttempt)
        .where(
            TransferAttempt.tenant_id == principal.tenant_id,
            TransferAttempt.transfer_request_id == transfer.id,
            TransferAttempt.membership_id == principal.membership_id,
            TransferAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES),
        )
        .with_for_update()
    )
    if attempt is None:
        raise ApiError(409, "transfer_offer_unavailable", "Transfer offer is no longer available")
    now = datetime.now(UTC)
    attempt.status = "declined"
    attempt.safe_error_code = reason[:80]
    attempt.resolved_at = now
    transfer.claimed_membership_id = None
    transfer.claimed_at = None
    transfer.status = TransferStatus.QUEUED
    transfer.lock_version += 1
    await offer_next_operator(session, transfer=transfer, settings=settings, correlation_id=correlation_id)
    return transfer


async def fallback_transfer(
    session: AsyncSession,
    *,
    transfer: TransferRequest,
    reason: str,
    correlation_id: str,
) -> None:
    now = datetime.now(UTC)
    call = await session.scalar(
        select(Call)
        .where(Call.tenant_id == transfer.tenant_id, Call.id == transfer.call_id)
        .with_for_update()
    )
    realtime = await session.scalar(
        select(AIRealtimeSession).where(
            AIRealtimeSession.tenant_id == transfer.tenant_id,
            AIRealtimeSession.call_id == transfer.call_id,
        )
    )
    if call is not None and realtime is not None and realtime.state in {"active", "degraded", "reconnecting"}:
        if call.status in {CallStatus.TRANSFER_REQUESTED, CallStatus.TRANSFERRING}:
            await CallStateService().transition(
                session,
                call=call,
                target=CallStatus.ACTIVE,
                event_type="transfer.failed",
                occurred_at=now,
                correlation_id=correlation_id,
                actor_user_id=None,
                safe_payload={"cause": reason},
            )
        transfer.status = TransferStatus.FAILED
        transfer.last_error_code = reason
        transfer.resolved_at = now
    elif call is not None and call.customer_id is not None:
        actor = call.operator_user_id or await session.scalar(
            select(Membership.user_id)
            .where(Membership.tenant_id == call.tenant_id, Membership.is_active.is_(True))
            .order_by(Membership.created_at)
            .limit(1)
        )
        if actor is not None:
            await create_task_record(
                session,
                tenant_id=call.tenant_id,
                project_id=call.project_id,
                customer_id=call.customer_id,
                created_by_user_id=actor,
                task_type=TaskType.CALLBACK,
                title="Перезвонить после неудачного перевода",
                description=transfer.reason,
                priority=TaskPriority.HIGH,
                due_at=now + timedelta(minutes=15),
                assigned_user_id=None,
                source=TaskSource.SYSTEM,
                correlation_id=correlation_id,
                call_id=call.id,
                idempotency_key=f"transfer-fallback:{transfer.id}",
            )
        transfer.status = TransferStatus.CALLBACK_REQUESTED
        transfer.last_error_code = reason
        transfer.resolved_at = now
        if call.status in {CallStatus.TRANSFER_REQUESTED, CallStatus.TRANSFERRING}:
            await CallStateService().transition(
                session,
                call=call,
                target=CallStatus.FAILED,
                event_type="call.failed",
                occurred_at=now,
                correlation_id=correlation_id,
                actor_user_id=None,
                safe_payload={"cause": "human_transfer_unavailable"},
            )
    else:
        transfer.status = TransferStatus.FAILED
        transfer.last_error_code = reason
        transfer.resolved_at = now
        if call is not None and call.status in {
            CallStatus.TRANSFER_REQUESTED,
            CallStatus.TRANSFERRING,
        }:
            await CallStateService().transition(
                session,
                call=call,
                target=CallStatus.FAILED,
                event_type="call.failed",
                occurred_at=now,
                correlation_id=correlation_id,
                actor_user_id=None,
                safe_payload={"cause": "human_transfer_unavailable"},
            )
    transfer.lock_version += 1
    await enqueue_realtime_event(
        session,
        tenant_id=transfer.tenant_id,
        project_id=transfer.project_id,
        event_type="transfer.failed",
        aggregate_type="transfer_request",
        aggregate_id=transfer.id,
        aggregate_version=transfer.lock_version,
        payload={"status": transfer.status.value, "cause": reason},
        correlation_id=correlation_id,
    )
