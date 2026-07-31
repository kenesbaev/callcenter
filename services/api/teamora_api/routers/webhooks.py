from __future__ import annotations

import json

from fastapi import APIRouter, Request
from sqlalchemy import select

from teamora_api.audit import write_audit
from teamora_api.call_events import append_call_event
from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import SessionDep
from teamora_api.enums import CallStatus, TelephonyCallState
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallEvent, TelephonyCommandSubmission
from teamora_api.schemas.telephony import TelephonyProviderEvent
from teamora_api.webhooks import verify_standard_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/openai", status_code=202)
async def openai_webhook(request: Request) -> dict[str, object]:
    settings = get_settings()
    if settings.openai_webhook_secret is None:
        raise ApiError(503, "webhook_unavailable", "OpenAI webhook verification is not configured")
    payload = await request.body()
    verify_standard_webhook(
        payload=payload,
        webhook_id=request.headers.get("webhook-id", ""),
        timestamp=request.headers.get("webhook-timestamp", ""),
        signature_header=request.headers.get("webhook-signature", ""),
        secret=settings.openai_webhook_secret.get_secret_value(),
    )
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "webhook_payload_invalid", "Webhook payload is not valid JSON") from exc
    return {"accepted": True, "event_type": event.get("type", "unknown")}


@router.post("/telephony", status_code=202)
async def telephony_webhook(request: Request, session: SessionDep) -> dict[str, object]:
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "webhook_unavailable", "Telephony webhook verification is not configured")
    payload = await request.body()
    verify_standard_webhook(
        payload=payload,
        webhook_id=request.headers.get("x-teamora-id", ""),
        timestamp=request.headers.get("x-teamora-timestamp", ""),
        signature_header=request.headers.get("x-teamora-signature", ""),
        secret=settings.gateway_service_token.get_secret_value(),
    )
    try:
        event = TelephonyProviderEvent.model_validate_json(payload)
    except ValueError as exc:
        raise ApiError(422, "provider_event_invalid", "Provider event is invalid") from exc
    await set_tenant_context(session, event.tenant_id)
    call = await session.scalar(
        select(Call).where(
            Call.tenant_id == event.tenant_id,
            Call.id == event.call_id,
        )
    )
    if call is None:
        raise ApiError(404, "provider_event_call_unknown", "Provider event call is unknown")
    if call.project_id != event.project_id or call.provider != event.provider:
        raise ApiError(
            409,
            "provider_event_scope_mismatch",
            "Provider event does not match the stored call scope",
        )
    if event.external_call_id and call.external_call_id not in (None, event.external_call_id):
        raise ApiError(
            409,
            "provider_event_call_mismatch",
            "Provider event external call ID does not match",
        )
    existing = await session.scalar(
        select(CallEvent).where(
            CallEvent.tenant_id == event.tenant_id,
            CallEvent.provider == event.provider,
            CallEvent.provider_event_id == event.provider_event_id,
        )
    )
    if existing is not None:
        return {"accepted": True, "duplicate": True, "call_id": str(call.id)}
    command_exists = await session.scalar(
        select(TelephonyCommandSubmission.id).where(
            TelephonyCommandSubmission.tenant_id == event.tenant_id,
            TelephonyCommandSubmission.call_id == call.id,
            TelephonyCommandSubmission.provider == event.provider,
            TelephonyCommandSubmission.status == "succeeded",
        )
    )
    if call.direction == "outbound" and command_exists is None:
        raise ApiError(
            409,
            "provider_event_command_unknown",
            "Provider event has no matching telephony command",
        )
    state_value = event.safe_payload.get("state")
    if isinstance(state_value, str):
        try:
            state = TelephonyCallState(state_value)
        except ValueError:
            state = TelephonyCallState.UNKNOWN
        call.provider_state = state.value
        if state == TelephonyCallState.RINGING:
            call.status = CallStatus.RINGING
        elif state == TelephonyCallState.ACTIVE:
            call.status = CallStatus.ACTIVE
            call.answered_at = call.answered_at or event.occurred_at
        elif state == TelephonyCallState.COMPLETED:
            call.status = CallStatus.COMPLETED
            call.ended_at = call.ended_at or event.occurred_at
        elif state in {
            TelephonyCallState.BUSY,
            TelephonyCallState.NO_ANSWER,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
        }:
            call.status = CallStatus.FAILED
            call.ended_at = call.ended_at or event.occurred_at
    if event.external_call_id and call.external_call_id is None:
        call.external_call_id = event.external_call_id
    await append_call_event(
        session,
        tenant_id=event.tenant_id,
        call_id=call.id,
        event_type=event.event_type,
        safe_payload=dict(event.safe_payload),
        provider=event.provider,
        provider_event_id=event.provider_event_id,
        external_call_id=event.external_call_id,
        occurred_at=event.occurred_at,
        provider_timestamp=event.provider_timestamp,
        correlation_id=event.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=event.tenant_id,
        actor_user_id=None,
        action="telephony.provider_event",
        resource_type="call",
        resource_id=call.id,
        correlation_id=event.correlation_id,
        safe_metadata={
            "provider": event.provider,
            "event_type": event.event_type,
            "provider_event_id": event.provider_event_id,
        },
    )
    await session.commit()
    return {"accepted": True, "duplicate": False, "call_id": str(call.id)}
