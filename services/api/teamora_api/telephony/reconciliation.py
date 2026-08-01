from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.audit import write_audit
from teamora_api.call_events import append_call_event
from teamora_api.call_state import (
    ALLOWED_TRANSITIONS,
    PROVIDER_STATE_TARGETS,
    TERMINAL_CALL_STATES,
    CallStateService,
)
from teamora_api.enums import TelephonyCallState, TelephonyCommandName
from teamora_api.models import Call, Project
from teamora_api.telephony.service import ProviderSelection, TelephonyService


@dataclass(frozen=True)
class ReconciliationResult:
    provider_state: TelephonyCallState | None
    reconciled: bool
    ignored_reason: str | None = None


class CallReconciliationService:
    def __init__(self, telephony: TelephonyService | None = None) -> None:
        self.telephony = telephony or TelephonyService()
        self.state_service = CallStateService()

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        call: Call,
        project: Project,
        selection: ProviderSelection,
        correlation_id: str,
        actor_user_id: UUID,
        expected_version: int | None = None,
    ) -> ReconciliationResult:
        if call.status in TERMINAL_CALL_STATES:
            return ReconciliationResult(None, False, "terminal")

        result = await self.telephony.execute(
            session,
            call=call,
            project=project,
            selection=selection,
            command_name=TelephonyCommandName.GET_CALL_STATE,
            correlation_id=correlation_id,
            actor_user_id=actor_user_id,
            idempotency_key=f"reconcile:{call.id}:{correlation_id}",
            expected_version=expected_version,
        )
        target = PROVIDER_STATE_TARGETS.get(result.state)
        if target is None:
            await self._record_check(
                session,
                call=call,
                correlation_id=correlation_id,
                provider_state=result.state,
                reason="unknown_provider_state",
                actor_user_id=actor_user_id,
            )
            return ReconciliationResult(result.state, False, "unknown_provider_state")
        if target == call.status:
            await self._record_check(
                session,
                call=call,
                correlation_id=correlation_id,
                provider_state=result.state,
                reason="already_consistent",
                actor_user_id=actor_user_id,
            )
            return ReconciliationResult(result.state, False, "already_consistent")
        if target not in ALLOWED_TRANSITIONS[call.status]:
            await self._record_check(
                session,
                call=call,
                correlation_id=correlation_id,
                provider_state=result.state,
                reason="transition_conflict",
                actor_user_id=actor_user_id,
            )
            return ReconciliationResult(result.state, False, "transition_conflict")

        await self.state_service.transition(
            session,
            call=call,
            target=target,
            event_type="call.reconciled",
            occurred_at=result.occurred_at,
            correlation_id=correlation_id,
            actor_user_id=actor_user_id,
            provider=result.provider,
            external_call_id=result.provider_call_id,
            safe_payload={"channelState": result.state.value},
        )
        return ReconciliationResult(result.state, True)

    async def _record_check(
        self,
        session: AsyncSession,
        *,
        call: Call,
        correlation_id: str,
        provider_state: TelephonyCallState,
        reason: str,
        actor_user_id: UUID,
    ) -> None:
        await append_call_event(
            session,
            tenant_id=call.tenant_id,
            call_id=call.id,
            event_type="call.reconciliation_checked",
            occurred_at=datetime.now(UTC),
            correlation_id=correlation_id,
            safe_payload={
                "call_state": call.status.value,
                "provider_state": provider_state.value,
                "reason": reason,
            },
        )
        await write_audit(
            session,
            tenant_id=call.tenant_id,
            actor_user_id=actor_user_id,
            action="call.reconciliation_checked",
            resource_type="call",
            resource_id=call.id,
            correlation_id=correlation_id,
            reason=reason,
            safe_metadata={
                "call_state": call.status.value,
                "provider_state": provider_state.value,
            },
        )
