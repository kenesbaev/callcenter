from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.audit import write_audit
from teamora_api.call_events import append_call_event
from teamora_api.enums import (
    CallStatus,
    HangupCause,
    TelephonyCallState,
    TelephonyCommandName,
)
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallEvent
from teamora_api.realtime import enqueue_analytics_invalidation, enqueue_realtime_event

TERMINAL_CALL_STATES = frozenset(
    {
        CallStatus.COMPLETED,
        CallStatus.BUSY,
        CallStatus.NO_ANSWER,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }
)

CAPACITY_CALL_STATES = frozenset(
    {
        CallStatus.QUEUED,
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.ACTIVE,
        CallStatus.ON_HOLD,
        CallStatus.TRANSFER_REQUESTED,
        CallStatus.TRANSFERRING,
        CallStatus.TRANSFERRED,
    }
)

ALLOWED_TRANSITIONS: dict[CallStatus, frozenset[CallStatus]] = {
    CallStatus.QUEUED: frozenset(
        {
            CallStatus.INITIATED,
            CallStatus.RINGING,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
        }
    ),
    CallStatus.INITIATED: frozenset(
        {
            CallStatus.RINGING,
            CallStatus.BUSY,
            CallStatus.NO_ANSWER,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
        }
    ),
    CallStatus.RINGING: frozenset(
        {
            CallStatus.ACTIVE,
            CallStatus.BUSY,
            CallStatus.NO_ANSWER,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
        }
    ),
    CallStatus.ACTIVE: frozenset(
        {
            CallStatus.ON_HOLD,
            CallStatus.TRANSFER_REQUESTED,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    CallStatus.ON_HOLD: frozenset(
        {
            CallStatus.ACTIVE,
            CallStatus.TRANSFER_REQUESTED,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    CallStatus.TRANSFER_REQUESTED: frozenset(
        {
            CallStatus.TRANSFERRING,
            CallStatus.ACTIVE,
            CallStatus.ON_HOLD,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    CallStatus.TRANSFERRING: frozenset(
        {
            CallStatus.TRANSFERRED,
            CallStatus.ACTIVE,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    CallStatus.TRANSFERRED: frozenset(
        {
            CallStatus.ON_HOLD,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    **{state: frozenset() for state in TERMINAL_CALL_STATES},
}

COMMAND_ALLOWED_STATES: dict[TelephonyCommandName, frozenset[CallStatus]] = {
    TelephonyCommandName.ORIGINATE: frozenset({CallStatus.QUEUED}),
    TelephonyCommandName.ANSWER: frozenset({CallStatus.RINGING}),
    TelephonyCommandName.HANGUP: CAPACITY_CALL_STATES - {CallStatus.QUEUED},
    TelephonyCommandName.HOLD: frozenset({CallStatus.ACTIVE, CallStatus.TRANSFERRED}),
    TelephonyCommandName.RESUME: frozenset({CallStatus.ON_HOLD}),
    TelephonyCommandName.TRANSFER: frozenset({CallStatus.ACTIVE, CallStatus.ON_HOLD}),
    TelephonyCommandName.GET_CALL_STATE: CAPACITY_CALL_STATES,
    TelephonyCommandName.START_RECORDING: frozenset(
        {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}
    ),
    TelephonyCommandName.PAUSE_RECORDING: frozenset(
        {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}
    ),
    TelephonyCommandName.RESUME_RECORDING: frozenset(
        {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}
    ),
    TelephonyCommandName.STOP_RECORDING: frozenset(
        {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}
    ),
    TelephonyCommandName.CREATE_EXTERNAL_MEDIA: frozenset(
        {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}
    ),
}

PROVIDER_EVENT_TARGETS: dict[str, CallStatus] = {
    "call.initiated": CallStatus.INITIATED,
    "call.ringing": CallStatus.RINGING,
    "call.answered": CallStatus.ACTIVE,
    "call.active": CallStatus.ACTIVE,
    "call.held": CallStatus.ON_HOLD,
    "call.resumed": CallStatus.ACTIVE,
    "transfer.requested": CallStatus.TRANSFER_REQUESTED,
    "transfer.started": CallStatus.TRANSFERRING,
    "transfer.completed": CallStatus.TRANSFERRED,
    "call.busy": CallStatus.BUSY,
    "call.no_answer": CallStatus.NO_ANSWER,
    "call.failed": CallStatus.FAILED,
    "call.hangup": CallStatus.COMPLETED,
    "call.completed": CallStatus.COMPLETED,
    "call.cancelled": CallStatus.CANCELLED,
    "telephony.initiated": CallStatus.INITIATED,
    "telephony.ringing": CallStatus.RINGING,
    "telephony.active": CallStatus.ACTIVE,
    "telephony.on_hold": CallStatus.ON_HOLD,
    "telephony.transfer_requested": CallStatus.TRANSFER_REQUESTED,
    "telephony.transferring": CallStatus.TRANSFERRING,
    "telephony.transferred": CallStatus.TRANSFERRED,
    "telephony.busy": CallStatus.BUSY,
    "telephony.no_answer": CallStatus.NO_ANSWER,
    "telephony.failed": CallStatus.FAILED,
    "telephony.completed": CallStatus.COMPLETED,
    "telephony.cancelled": CallStatus.CANCELLED,
}

PROVIDER_STATE_TARGETS: dict[TelephonyCallState, CallStatus] = {
    TelephonyCallState.QUEUED: CallStatus.QUEUED,
    TelephonyCallState.INITIATED: CallStatus.INITIATED,
    TelephonyCallState.RINGING: CallStatus.RINGING,
    TelephonyCallState.ACTIVE: CallStatus.ACTIVE,
    TelephonyCallState.ON_HOLD: CallStatus.ON_HOLD,
    TelephonyCallState.TRANSFER_REQUESTED: CallStatus.TRANSFER_REQUESTED,
    TelephonyCallState.TRANSFERRING: CallStatus.TRANSFERRING,
    TelephonyCallState.TRANSFERRED: CallStatus.TRANSFERRED,
    TelephonyCallState.COMPLETED: CallStatus.COMPLETED,
    TelephonyCallState.BUSY: CallStatus.BUSY,
    TelephonyCallState.NO_ANSWER: CallStatus.NO_ANSWER,
    TelephonyCallState.FAILED: CallStatus.FAILED,
    TelephonyCallState.CANCELLED: CallStatus.CANCELLED,
}

SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "bridgeId",
        "channelState",
        "mediaId",
        "recordingId",
        "recordingState",
        "rawCause",
        "cause",
    }
)


@dataclass(frozen=True)
class StateTransitionResult:
    state: CallStatus
    state_version: int
    changed: bool
    ignored_reason: str | None = None


def allowed_actions(status: CallStatus, *, recording_state: str) -> list[str]:
    actions: list[str] = []
    if status == CallStatus.RINGING:
        actions.append("answer")
    if status in {CallStatus.ACTIVE, CallStatus.TRANSFERRED}:
        actions.append("hold")
    if status == CallStatus.ON_HOLD:
        actions.append("resume")
    if status in {CallStatus.ACTIVE, CallStatus.ON_HOLD}:
        actions.append("transfer")
    if status in CAPACITY_CALL_STATES - {CallStatus.QUEUED}:
        actions.append("hangup")
    if status == CallStatus.QUEUED:
        actions.append("cancel")
    if status in {CallStatus.ACTIVE, CallStatus.ON_HOLD, CallStatus.TRANSFERRED}:
        if recording_state == "recording":
            actions.extend(["pause_recording", "stop_recording"])
        elif recording_state == "paused":
            actions.extend(["resume_recording", "stop_recording"])
        else:
            actions.append("start_recording")
    return actions


def validate_command_state(call: Call, command: TelephonyCommandName) -> None:
    allowed = COMMAND_ALLOWED_STATES[command]
    if call.status not in allowed:
        raise ApiError(
            409,
            "telephony_command_state_conflict",
            f"{command.value} is not allowed while call is {call.status.value}",
        )


def validate_transition(current: CallStatus, target: CallStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ApiError(
            409,
            "call_state_transition_invalid",
            f"Transition {current.value} -> {target.value} is not allowed",
        )


def normalize_provider_metadata(
    metadata: Mapping[str, object],
) -> dict[str, str | int | float | bool]:
    normalized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if key not in SAFE_PROVIDER_METADATA_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            normalized[key] = value[:500] if isinstance(value, str) else value
    return normalized


class CallStateService:
    async def transition(
        self,
        session: AsyncSession,
        *,
        call: Call,
        target: CallStatus,
        event_type: str,
        occurred_at: datetime,
        correlation_id: str,
        actor_user_id: UUID | None,
        expected_version: int | None = None,
        provider: str | None = None,
        provider_event_id: str | None = None,
        external_call_id: str | None = None,
        provider_timestamp: datetime | None = None,
        safe_payload: Mapping[str, object] | None = None,
        hangup_cause: HangupCause | None = None,
        raw_provider_cause: str | None = None,
    ) -> StateTransitionResult:
        if expected_version is not None and call.state_version != expected_version:
            raise ApiError(
                409,
                "call_state_version_conflict",
                "Call state was changed by another operation",
            )
        if target == call.status:
            return StateTransitionResult(call.status, call.state_version, False)
        validate_transition(call.status, target)

        timestamp = _utc(occurred_at)
        previous = call.status
        call.status = target
        call.provider_state = target.value
        call.state_version += 1
        if target == CallStatus.INITIATED:
            call.started_at = call.started_at or timestamp
        elif target == CallStatus.RINGING:
            call.started_at = call.started_at or timestamp
            call.ringing_at = call.ringing_at or timestamp
        elif target == CallStatus.ACTIVE:
            call.started_at = call.started_at or timestamp
            call.answered_at = call.answered_at or timestamp
            call.held_at = None
        elif target == CallStatus.ON_HOLD:
            call.held_at = timestamp

        if target in TERMINAL_CALL_STATES:
            call.ended_at = call.ended_at or timestamp
            start = call.answered_at or call.started_at
            if start is not None:
                call.duration_seconds = max(0, int((call.ended_at - start).total_seconds()))
            call.hangup_cause = hangup_cause or _default_hangup_cause(target)
            safe_raw_cause = _safe_raw_provider_cause(raw_provider_cause)
            if safe_raw_cause:
                call.raw_provider_cause = safe_raw_cause

        safe_metadata = normalize_provider_metadata(safe_payload or {})
        if safe_metadata:
            call.provider_metadata = {
                **(call.provider_metadata or {}),
                **safe_metadata,
            }
        if external_call_id and call.external_call_id is None:
            call.external_call_id = external_call_id
        if provider_event_id is not None:
            latest_event_at = _utc(provider_timestamp or timestamp)
            if call.last_provider_event_at is None or latest_event_at > _utc(call.last_provider_event_at):
                call.last_provider_event_at = latest_event_at

        await append_call_event(
            session,
            tenant_id=call.tenant_id,
            call_id=call.id,
            event_type=event_type,
            safe_payload={
                "from": previous.value,
                "to": target.value,
                "state_version": call.state_version,
                **safe_metadata,
            },
            provider=provider,
            provider_event_id=provider_event_id,
            external_call_id=external_call_id,
            occurred_at=timestamp,
            provider_timestamp=provider_timestamp,
            correlation_id=correlation_id,
        )
        await write_audit(
            session,
            tenant_id=call.tenant_id,
            actor_user_id=actor_user_id,
            action="call.state_transition",
            resource_type="call",
            resource_id=call.id,
            correlation_id=correlation_id,
            safe_metadata={
                "from": previous.value,
                "to": target.value,
                "state_version": call.state_version,
                "event_type": event_type,
            },
        )
        await enqueue_realtime_event(
            session,
            tenant_id=call.tenant_id,
            project_id=call.project_id,
            event_type="call.state_changed",
            aggregate_type="call",
            aggregate_id=call.id,
            aggregate_version=call.state_version,
            payload={"status": target.value, "previous_status": previous.value},
            occurred_at=timestamp,
            correlation_id=correlation_id,
        )
        semantic_event = _semantic_realtime_event(previous, target, event_type)
        if semantic_event is not None:
            await enqueue_realtime_event(
                session,
                tenant_id=call.tenant_id,
                project_id=call.project_id,
                event_type=semantic_event,
                aggregate_type="call",
                aggregate_id=call.id,
                aggregate_version=call.state_version,
                payload={"status": target.value},
                occurred_at=timestamp,
                correlation_id=correlation_id,
            )
        await enqueue_analytics_invalidation(
            session,
            tenant_id=call.tenant_id,
            project_id=call.project_id,
            aggregate_type="call",
            aggregate_id=call.id,
            correlation_id=correlation_id,
            reason="call_state_changed",
        )
        return StateTransitionResult(target, call.state_version, True)

    async def ingest_provider_event(
        self,
        session: AsyncSession,
        *,
        call: Call,
        event_type: str,
        provider: str,
        provider_event_id: str,
        occurred_at: datetime,
        provider_timestamp: datetime | None,
        external_call_id: str | None,
        correlation_id: str,
        safe_payload: dict[str, object],
    ) -> StateTransitionResult:
        await session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"provider-event:{call.tenant_id}:{provider}:{provider_event_id}",
                        0,
                    )
                )
            )
        )
        pending_duplicate = any(
            isinstance(event, CallEvent)
            and event.tenant_id == call.tenant_id
            and event.provider == provider
            and event.provider_event_id == provider_event_id
            for event in session.new
        )
        if pending_duplicate:
            return StateTransitionResult(
                call.status,
                call.state_version,
                False,
                "duplicate",
            )
        duplicate = await session.scalar(
            select(CallEvent.id).where(
                CallEvent.tenant_id == call.tenant_id,
                CallEvent.provider == provider,
                CallEvent.provider_event_id == provider_event_id,
            )
        )
        if duplicate is not None:
            return StateTransitionResult(
                call.status,
                call.state_version,
                False,
                "duplicate",
            )

        event_timestamp = _utc(provider_timestamp or occurred_at)
        if call.last_provider_event_at is not None and event_timestamp < _utc(call.last_provider_event_at):
            return await self._ignored_provider_event(
                session,
                call=call,
                event_type=event_type,
                provider=provider,
                provider_event_id=provider_event_id,
                external_call_id=external_call_id,
                occurred_at=occurred_at,
                provider_timestamp=provider_timestamp,
                correlation_id=correlation_id,
                reason="out_of_order",
            )

        target = PROVIDER_EVENT_TARGETS.get(event_type)
        if event_type in {"call.hangup", "call.completed", "telephony.completed"} and call.status in {
            CallStatus.QUEUED,
            CallStatus.INITIATED,
            CallStatus.RINGING,
        }:
            target = CallStatus.CANCELLED
        if target is None:
            state_value = safe_payload.get("state")
            try:
                provider_state = TelephonyCallState(str(state_value))
            except ValueError:
                provider_state = TelephonyCallState.UNKNOWN
            target = PROVIDER_STATE_TARGETS.get(provider_state)
        if target is None:
            return await self._ignored_provider_event(
                session,
                call=call,
                event_type=event_type,
                provider=provider,
                provider_event_id=provider_event_id,
                external_call_id=external_call_id,
                occurred_at=occurred_at,
                provider_timestamp=provider_timestamp,
                correlation_id=correlation_id,
                reason="unknown_event",
            )

        if target == call.status:
            call.last_provider_event_at = event_timestamp
            await append_call_event(
                session,
                tenant_id=call.tenant_id,
                call_id=call.id,
                event_type=event_type,
                safe_payload={
                    "state": call.status.value,
                    "state_version": call.state_version,
                    "duplicate_state": True,
                },
                provider=provider,
                provider_event_id=provider_event_id,
                external_call_id=external_call_id,
                occurred_at=occurred_at,
                provider_timestamp=provider_timestamp,
                correlation_id=correlation_id,
            )
            return StateTransitionResult(call.status, call.state_version, False)

        raw_cause = safe_payload.get("rawCause")
        try:
            return await self.transition(
                session,
                call=call,
                target=target,
                event_type=event_type,
                occurred_at=occurred_at,
                correlation_id=correlation_id,
                actor_user_id=None,
                provider=provider,
                provider_event_id=provider_event_id,
                external_call_id=external_call_id,
                provider_timestamp=provider_timestamp,
                safe_payload=safe_payload,
                hangup_cause=_provider_hangup_cause(target, safe_payload),
                raw_provider_cause=raw_cause if isinstance(raw_cause, str) else None,
            )
        except ApiError as exc:
            if exc.code != "call_state_transition_invalid":
                raise
            return await self._ignored_provider_event(
                session,
                call=call,
                event_type=event_type,
                provider=provider,
                provider_event_id=provider_event_id,
                external_call_id=external_call_id,
                occurred_at=occurred_at,
                provider_timestamp=provider_timestamp,
                correlation_id=correlation_id,
                reason="transition_conflict",
            )

    async def _ignored_provider_event(
        self,
        session: AsyncSession,
        *,
        call: Call,
        event_type: str,
        provider: str,
        provider_event_id: str,
        external_call_id: str | None,
        occurred_at: datetime,
        provider_timestamp: datetime | None,
        correlation_id: str,
        reason: str,
    ) -> StateTransitionResult:
        await append_call_event(
            session,
            tenant_id=call.tenant_id,
            call_id=call.id,
            event_type="provider.event_ignored",
            safe_payload={
                "provider_event_type": event_type,
                "reason": reason,
                "state": call.status.value,
                "state_version": call.state_version,
            },
            provider=provider,
            provider_event_id=provider_event_id,
            external_call_id=external_call_id,
            occurred_at=occurred_at,
            provider_timestamp=provider_timestamp,
            correlation_id=correlation_id,
        )
        await write_audit(
            session,
            tenant_id=call.tenant_id,
            actor_user_id=None,
            action="call.provider_event_ignored",
            resource_type="call",
            resource_id=call.id,
            correlation_id=correlation_id,
            reason=reason,
            safe_metadata={
                "provider": provider,
                "event_type": event_type,
                "state": call.status.value,
            },
        )
        return StateTransitionResult(
            call.status,
            call.state_version,
            False,
            reason,
        )


def _semantic_realtime_event(
    previous: CallStatus,
    target: CallStatus,
    provider_event_type: str,
) -> str | None:
    if "reconcil" in provider_event_type:
        return "call.reconciled"
    if target == CallStatus.ACTIVE and previous == CallStatus.RINGING:
        return "call.answered"
    if target == CallStatus.ON_HOLD:
        return "call.held"
    if target == CallStatus.ACTIVE and previous == CallStatus.ON_HOLD:
        return "call.resumed"
    if target == CallStatus.TRANSFER_REQUESTED:
        return "transfer.requested"
    if target == CallStatus.TRANSFERRING:
        return "transfer.started"
    if target == CallStatus.TRANSFERRED:
        return "transfer.completed"
    if target == CallStatus.FAILED and previous in {
        CallStatus.TRANSFER_REQUESTED,
        CallStatus.TRANSFERRING,
    }:
        return "transfer.failed"
    if target in TERMINAL_CALL_STATES:
        return "call.ended"
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _default_hangup_cause(target: CallStatus) -> HangupCause:
    return {
        CallStatus.COMPLETED: HangupCause.NORMAL,
        CallStatus.BUSY: HangupCause.BUSY,
        CallStatus.NO_ANSWER: HangupCause.NO_ANSWER,
        CallStatus.FAILED: HangupCause.UNKNOWN,
        CallStatus.CANCELLED: HangupCause.CANCELLED,
    }[target]


def _provider_hangup_cause(
    target: CallStatus,
    payload: dict[str, object],
) -> HangupCause | None:
    cause = payload.get("cause")
    if isinstance(cause, str):
        try:
            return HangupCause(cause)
        except ValueError:
            pass
    return _default_hangup_cause(target) if target in TERMINAL_CALL_STATES else None


def _safe_raw_provider_cause(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.lower()
    if any(marker in normalized for marker in ("authorization", "password", "secret", "token", "credential")):
        return None
    return value[:160]
