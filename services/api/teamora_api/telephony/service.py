from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.audit import write_audit
from teamora_api.call_state import (
    PROVIDER_STATE_TARGETS,
    CallStateService,
    normalize_provider_metadata,
    validate_command_state,
)
from teamora_api.config import Settings, get_settings
from teamora_api.enums import (
    CallChannel,
    CallStatus,
    HangupCause,
    IntegrationStatus,
    TelephonyCallState,
    TelephonyCommandName,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    PhoneNumber,
    Project,
    SipTrunk,
    TelephonyCommandSubmission,
)
from teamora_api.schemas.telephony import TelephonyCommand, TelephonyCommandResult
from teamora_api.telephony.providers import (
    GatewayTelephonyProvider,
    MockTelephonyProvider,
    ProviderCommandError,
    TelephonyProvider,
)


@dataclass(frozen=True)
class ProviderSelection:
    provider: TelephonyProvider
    from_number: str
    channel: CallChannel
    is_demo: bool


class TelephonyService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        mock_provider: TelephonyProvider | None = None,
        gateway_provider: TelephonyProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._mock_provider = mock_provider
        self._gateway_provider = gateway_provider
        self.state_service = CallStateService()

    async def select_provider(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        project: Project,
        requested_from_number: str | None = None,
    ) -> ProviderSelection:
        if project.outbound_phone_number_id is not None:
            row = (
                await session.execute(
                    select(PhoneNumber, SipTrunk)
                    .join(SipTrunk, SipTrunk.id == PhoneNumber.sip_trunk_id)
                    .where(
                        PhoneNumber.tenant_id == tenant_id,
                        PhoneNumber.id == project.outbound_phone_number_id,
                        PhoneNumber.is_active.is_(True),
                        SipTrunk.tenant_id == tenant_id,
                        SipTrunk.status.in_([IntegrationStatus.CONFIGURED, IntegrationStatus.VERIFIED]),
                    )
                )
            ).first()
            if row is not None:
                provider = self._gateway_provider or GatewayTelephonyProvider(self.settings)
                return ProviderSelection(
                    provider=provider,
                    from_number=row[0].e164,
                    channel=CallChannel.SIP,
                    is_demo=False,
                )
        if self.settings.mock_telephony_available:
            provider = self._mock_provider or MockTelephonyProvider(self.settings)
            return ProviderSelection(
                provider=provider,
                from_number=requested_from_number or project.outbound_number or "MOCK",
                channel=CallChannel.DEVELOPMENT_SIMULATOR,
                is_demo=True,
            )
        raise ApiError(
            503,
            "telephony_not_configured",
            "Telephony is not configured for this project",
        )

    async def selection_for_call(
        self,
        session: AsyncSession,
        *,
        call: Call,
        project: Project,
    ) -> ProviderSelection:
        if call.provider == "mock":
            if not self.settings.mock_telephony_available:
                raise ApiError(503, "mock_telephony_forbidden", "Mock telephony is disabled")
            return ProviderSelection(
                provider=self._mock_provider or MockTelephonyProvider(self.settings),
                from_number=call.from_number or "MOCK",
                channel=CallChannel.DEVELOPMENT_SIMULATOR,
                is_demo=True,
            )
        if call.provider == "asterisk-ari":
            provider = self._gateway_provider or GatewayTelephonyProvider(self.settings)
            return ProviderSelection(
                provider=provider,
                from_number=call.from_number or "",
                channel=CallChannel.SIP,
                is_demo=False,
            )
        return await self.select_provider(
            session,
            tenant_id=call.tenant_id,
            project=project,
            requested_from_number=call.from_number,
        )

    async def execute(
        self,
        session: AsyncSession,
        *,
        call: Call,
        project: Project,
        selection: ProviderSelection,
        command_name: TelephonyCommandName,
        correlation_id: str,
        actor_user_id: UUID | None,
        idempotency_key: str | None = None,
        parameters: dict[str, str | int | float | bool] | None = None,
        expected_version: int | None = None,
    ) -> TelephonyCommandResult:
        key = idempotency_key or (f"implicit:{correlation_id}:{call.id}:{command_name.value}")
        body = {
            "call_id": str(call.id),
            "project_id": str(project.id),
            "command": command_name.value,
            "provider": selection.provider.name,
            "parameters": parameters or {},
        }
        fingerprint = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"{call.tenant_id}:{key}", 0)))
        )
        replay = await session.scalar(
            select(TelephonyCommandSubmission).where(
                TelephonyCommandSubmission.tenant_id == call.tenant_id,
                TelephonyCommandSubmission.idempotency_key == key,
            )
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
            if replay.status == "succeeded":
                return TelephonyCommandResult.model_validate(replay.response_payload)
            raise ApiError(
                503,
                replay.error_code or "telephony_command_failed",
                "The previous telephony command failed",
            )

        if expected_version is not None and call.state_version != expected_version:
            raise ApiError(
                409,
                "call_state_version_conflict",
                "Call state was changed by another operation",
            )
        validate_command_state(call, command_name)

        command = TelephonyCommand(
            command=command_name,
            tenantId=call.tenant_id,
            projectId=project.id,
            callId=call.id,
            providerCallId=call.external_call_id,
            commandId=uuid4(),
            idempotencyKey=key,
            timestamp=datetime.now(UTC),
            correlationId=correlation_id,
            parameters={
                **(parameters or {}),
                "currentState": call.provider_state,
            },
        )
        submission = TelephonyCommandSubmission(
            tenant_id=call.tenant_id,
            project_id=project.id,
            call_id=call.id,
            command_id=command.command_id,
            idempotency_key=key,
            command_name=command_name.value,
            provider=selection.provider.name,
            request_fingerprint=fingerprint,
            status="pending",
            correlation_id=correlation_id,
        )
        session.add(submission)
        await session.flush()
        try:
            async with asyncio.timeout(self.settings.gateway_command_timeout_seconds):
                result = await selection.provider.execute(command)
        except (ProviderCommandError, TimeoutError) as exc:
            provider_error = (
                exc
                if isinstance(exc, ProviderCommandError)
                else ProviderCommandError(
                    "telephony_timeout",
                    "Telephony provider did not respond before the timeout",
                    status_code=504,
                )
            )
            submission.status = "failed"
            submission.error_code = provider_error.code
            if command_name == TelephonyCommandName.ORIGINATE:
                await self.state_service.transition(
                    session,
                    call=call,
                    target=CallStatus.FAILED,
                    event_type="call.failed",
                    occurred_at=datetime.now(UTC),
                    correlation_id=correlation_id,
                    actor_user_id=actor_user_id,
                    hangup_cause=HangupCause.PROVIDER_ERROR,
                    safe_payload={"cause": provider_error.code},
                )
            await write_audit(
                session,
                tenant_id=call.tenant_id,
                actor_user_id=actor_user_id,
                action="telephony.command_failed",
                resource_type="call",
                resource_id=call.id,
                correlation_id=correlation_id,
                safe_metadata={"command": command_name.value, "code": provider_error.code},
            )
            await session.commit()
            raise ApiError(
                provider_error.status_code,
                provider_error.code,
                provider_error.message,
            ) from exc

        submission.status = "succeeded"
        submission.response_payload = result.model_dump(mode="json", by_alias=True)
        submission.external_call_id = result.provider_call_id
        await self._apply_result(
            session,
            call=call,
            command_name=command_name,
            result=result,
            correlation_id=correlation_id,
            actor_user_id=actor_user_id,
            parameters=parameters or {},
        )
        await write_audit(
            session,
            tenant_id=call.tenant_id,
            actor_user_id=actor_user_id,
            action=f"telephony.{command_name.value}",
            resource_type="call",
            resource_id=call.id,
            correlation_id=correlation_id,
            safe_metadata={
                "provider": selection.provider.name,
                "state": result.state.value,
                "command_id": str(command.command_id),
            },
        )
        await session.flush()
        return result

    async def _apply_result(
        self,
        session: AsyncSession,
        *,
        call: Call,
        command_name: TelephonyCommandName,
        result: TelephonyCommandResult,
        correlation_id: str,
        actor_user_id: UUID | None,
        parameters: dict[str, str | int | float | bool],
    ) -> None:
        call.provider = result.provider
        if result.provider_call_id:
            call.external_call_id = result.provider_call_id
        safe_metadata = normalize_provider_metadata(result.safe_metadata)
        call.provider_metadata = {
            **(call.provider_metadata or {}),
            **safe_metadata,
        }
        if command_name == TelephonyCommandName.TRANSFER:
            call.transfer_reason = str(parameters.get("reason", "")) or None
        recording_state = result.safe_metadata.get("recordingState")
        if isinstance(recording_state, str):
            call.recording_state = recording_state
        if command_name == TelephonyCommandName.GET_CALL_STATE:
            return

        transitions: list[tuple[CallStatus, str, HangupCause | None]] = []
        if command_name == TelephonyCommandName.ORIGINATE:
            transitions = [(CallStatus.INITIATED, "call.initiated", None)]
            target = PROVIDER_STATE_TARGETS.get(result.state)
            if target is not None and target != CallStatus.INITIATED:
                cause = None
                if target == CallStatus.BUSY:
                    cause = HangupCause.BUSY
                elif target == CallStatus.NO_ANSWER:
                    cause = HangupCause.NO_ANSWER
                elif target == CallStatus.FAILED:
                    cause = HangupCause.PROVIDER_ERROR
                transitions.append((target, _event_type(target), cause))
        elif command_name == TelephonyCommandName.TRANSFER:
            transitions = [
                (CallStatus.TRANSFER_REQUESTED, "transfer.requested", None),
            ]
            if result.state in {
                TelephonyCallState.TRANSFERRING,
                TelephonyCallState.TRANSFERRED,
            }:
                transitions.append((CallStatus.TRANSFERRING, "transfer.started", None))
            if result.state == TelephonyCallState.TRANSFERRED:
                transitions.append((CallStatus.TRANSFERRED, "transfer.completed", None))
        else:
            target = PROVIDER_STATE_TARGETS.get(result.state)
            if (
                command_name == TelephonyCommandName.HANGUP
                and target == CallStatus.COMPLETED
                and call.status in {CallStatus.INITIATED, CallStatus.RINGING}
            ):
                target = CallStatus.CANCELLED
            if target is not None and target != call.status:
                cause = None
                if command_name == TelephonyCommandName.HANGUP:
                    cause = (
                        HangupCause.CANCELLED
                        if target == CallStatus.CANCELLED
                        else HangupCause.OPERATOR_HANGUP
                    )
                elif target == CallStatus.BUSY:
                    cause = HangupCause.BUSY
                elif target == CallStatus.NO_ANSWER:
                    cause = HangupCause.NO_ANSWER
                elif target == CallStatus.FAILED:
                    cause = HangupCause.PROVIDER_ERROR
                transitions.append((target, _event_type(target), cause))

        for target, event_type, cause in transitions:
            await self.state_service.transition(
                session,
                call=call,
                target=target,
                event_type=event_type,
                occurred_at=result.occurred_at,
                correlation_id=correlation_id,
                actor_user_id=actor_user_id,
                provider=result.provider,
                external_call_id=result.provider_call_id,
                safe_payload=safe_metadata,
                hangup_cause=cause,
            )


def _event_type(status: CallStatus) -> str:
    return {
        CallStatus.INITIATED: "call.initiated",
        CallStatus.RINGING: "call.ringing",
        CallStatus.ACTIVE: "call.answered",
        CallStatus.ON_HOLD: "call.held",
        CallStatus.COMPLETED: "call.hangup",
        CallStatus.BUSY: "call.busy",
        CallStatus.NO_ANSWER: "call.no_answer",
        CallStatus.FAILED: "call.failed",
        CallStatus.CANCELLED: "call.cancelled",
    }.get(status, f"call.{status.value}")
