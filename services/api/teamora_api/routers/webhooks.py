from __future__ import annotations

import json
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from teamora_api.call_flow_service import active_version_for_project
from teamora_api.call_state import CAPACITY_CALL_STATES, CallStateService
from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import SessionDep
from teamora_api.enums import (
    CallChannel,
    CallDirection,
    CallerType,
    CallStatus,
    OperatorVersionStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    AiOperator,
    AiOperatorVersion,
    Call,
    Customer,
    CustomerContact,
    PhoneNumber,
    Project,
    SipTrunk,
    TelephonyCommandSubmission,
    TenantSettings,
)
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.schemas.telephony import (
    InboundTelephonyAccepted,
    InboundTelephonyEvent,
    TelephonyProviderEvent,
)
from teamora_api.telephony.control import reserve_channel
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
        select(Call)
        .where(
            Call.tenant_id == event.tenant_id,
            Call.id == event.call_id,
        )
        .with_for_update()
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
    command_exists = await session.scalar(
        select(TelephonyCommandSubmission.id).where(
            TelephonyCommandSubmission.tenant_id == event.tenant_id,
            TelephonyCommandSubmission.call_id == call.id,
            TelephonyCommandSubmission.provider == event.provider,
            TelephonyCommandSubmission.status == "succeeded",
        )
    )
    if call.direction == CallDirection.OUTBOUND and command_exists is None:
        raise ApiError(
            409,
            "provider_event_command_unknown",
            "Provider event has no matching telephony command",
        )
    state_service = CallStateService()
    if event.event_type in {
        "call.dtmf",
        "bridge.created",
        "bridge.destroyed",
        "external_media.created",
        "external_media.destroyed",
        "recording.started",
        "recording.finished",
        "recording.failed",
    }:
        transition = await state_service.record_provider_information(
            session,
            call=call,
            event_type=event.event_type,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            external_call_id=event.external_call_id,
            occurred_at=event.occurred_at,
            provider_timestamp=event.provider_timestamp,
            correlation_id=event.correlation_id,
            safe_payload=event.safe_payload,
        )
    else:
        transition = await state_service.ingest_provider_event(
            session,
            call=call,
            event_type=event.event_type,
            provider=event.provider,
            provider_event_id=event.provider_event_id,
            external_call_id=event.external_call_id,
            occurred_at=event.occurred_at,
            provider_timestamp=event.provider_timestamp,
            correlation_id=event.correlation_id,
            safe_payload=dict(event.safe_payload),
        )
    await session.commit()
    return {
        "accepted": True,
        "duplicate": transition.ignored_reason == "duplicate",
        "ignored_reason": transition.ignored_reason,
        "call_id": str(call.id),
        "status": call.status.value,
        "state_version": call.state_version,
    }


@router.post(
    "/telephony/inbound",
    status_code=202,
    response_model=InboundTelephonyAccepted,
    response_model_by_alias=True,
)
async def inbound_telephony_webhook(request: Request, session: SessionDep) -> InboundTelephonyAccepted:
    """Accept an ARI StasisStart before tenant context is known.

    The SECURITY DEFINER resolver returns only exact DID routing identifiers.
    Every subsequent read/write runs under the resolved tenant RLS context and
    the event is accepted only from the trunk allowlist.
    """

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
        tolerance_seconds=settings.telephony_provider_event_tolerance_seconds,
    )
    try:
        event = InboundTelephonyEvent.model_validate_json(payload)
    except ValueError as exc:
        raise ApiError(422, "provider_event_invalid", "Inbound provider event is invalid") from exc

    route = (
        (
            await session.execute(
                text("SELECT * FROM kline_resolve_inbound_did(:did)"),
                {"did": event.did},
            )
        )
        .mappings()
        .one_or_none()
    )
    if route is None:
        raise ApiError(404, "inbound_did_unknown", "Inbound DID is not configured")
    tenant_id = route["tenant_id"]
    project_id = route["project_id"]
    phone_number_id = route["phone_number_id"]
    trunk_id = route["sip_trunk_id"]
    if trunk_id is None:
        raise ApiError(409, "inbound_trunk_missing", "Inbound DID has no SIP trunk")
    await set_tenant_context(session, tenant_id)
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"inbound:{tenant_id}:{project_id}", 0)))
    )

    project = await session.scalar(
        select(Project).where(
            Project.tenant_id == tenant_id,
            Project.id == project_id,
            Project.status == "active",
            Project.archived_at.is_(None),
        )
    )
    phone = await session.scalar(
        select(PhoneNumber).where(
            PhoneNumber.tenant_id == tenant_id,
            PhoneNumber.id == phone_number_id,
            PhoneNumber.is_active.is_(True),
        )
    )
    trunk = await session.scalar(
        select(SipTrunk).where(SipTrunk.tenant_id == tenant_id, SipTrunk.id == trunk_id)
    )
    if project is None or phone is None or trunk is None:
        raise ApiError(404, "inbound_route_unavailable", "Inbound route is unavailable")
    if not _source_allowed(event.source_ip, trunk.allowed_ips):
        raise ApiError(403, "provider_source_rejected", "Provider source is not allowed")
    tenant_settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id).with_for_update()
    )
    if not _project_is_open(project, tenant_settings, event.occurred_at):
        raise ApiError(409, "project_outside_working_hours", "Project is outside working hours")
    tenant_limit = (
        tenant_settings.max_concurrent_calls
        if tenant_settings is not None
        else settings.default_max_concurrent_calls
    )
    tenant_active = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == tenant_id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
        )
        or 0
    )
    project_active = int(
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.tenant_id == tenant_id,
                Call.project_id == project_id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
        )
        or 0
    )
    if tenant_active >= tenant_limit:
        raise ApiError(409, "tenant_call_limit", "Tenant concurrent call limit is exhausted")
    if project_active >= (project.max_concurrent_calls or tenant_limit):
        raise ApiError(409, "project_call_limit", "Project concurrent call limit is exhausted")

    duplicate = await session.scalar(
        select(Call).where(
            Call.tenant_id == tenant_id,
            Call.external_call_id == event.channel_id,
        )
    )
    if duplicate is not None:
        return _inbound_response(duplicate, project, tenant_settings, duplicate=True)

    from teamora_api.knowledge_service import active_revision_for_project

    call_flow_version = await active_version_for_project(session, tenant_id=tenant_id, project_id=project_id)
    knowledge_revision = await active_revision_for_project(
        session, tenant_id=tenant_id, project_id=project_id
    )
    ai_version_id = None
    if project.ai_operator_id is not None:
        ai_version_id = await session.scalar(
            select(AiOperatorVersion.id)
            .join(AiOperator, AiOperator.id == AiOperatorVersion.ai_operator_id)
            .where(
                AiOperator.tenant_id == tenant_id,
                AiOperator.id == project.ai_operator_id,
                AiOperator.is_active.is_(True),
                AiOperator.active_version_id == AiOperatorVersion.id,
                AiOperatorVersion.status == OperatorVersionStatus.PUBLISHED,
            )
        )
    customer = None
    if event.caller_number:
        customer_contact = await session.scalar(
            select(CustomerContact).where(
                CustomerContact.tenant_id == tenant_id,
                CustomerContact.project_id == project_id,
                CustomerContact.kind == "phone",
                CustomerContact.normalized_value == event.caller_number,
            )
        )
        if customer_contact is not None:
            customer = await session.scalar(
                select(Customer).where(
                    Customer.tenant_id == tenant_id,
                    Customer.project_id == project_id,
                    Customer.id == customer_contact.customer_id,
                    Customer.archived_at.is_(None),
                )
            )
    now = datetime.now(UTC)
    call = Call(
        tenant_id=tenant_id,
        project_id=project_id,
        external_call_id=event.channel_id,
        channel=CallChannel.SIP,
        status=CallStatus.QUEUED,
        direction=CallDirection.INBOUND,
        caller_type=CallerType.AI_AGENT if ai_version_id else CallerType.HUMAN_OPERATOR,
        customer_id=customer.id if customer else None,
        phone_number_id=phone.id,
        sip_trunk_id=trunk.id,
        ai_operator_id=project.ai_operator_id if ai_version_id else None,
        ai_operator_version_id=ai_version_id,
        call_flow_version_id=call_flow_version.id if call_flow_version else None,
        knowledge_base_revision_id=knowledge_revision.id if knowledge_revision else None,
        language=(
            customer.preferred_language
            if customer is not None and customer.preferred_language is not None
            else project.default_language or (tenant_settings.default_language if tenant_settings else None)
        ),
        provider=event.provider,
        provider_state=CallStatus.QUEUED.value,
        from_number=event.caller_number,
        to_number=event.did,
        provider_metadata={key: value for key, value in event.safe_payload.items() if key in {"codec"}},
        is_demo=False,
    )
    session.add(call)
    await session.flush()
    await reserve_channel(
        session,
        call=call,
        trunk_id=trunk.id,
        direction=CallDirection.INBOUND,
    )
    from teamora_api.telephony.control import record_resource

    await record_resource(
        session,
        call=call,
        resource_type="channel",
        provider_resource_id=event.channel_id,
        status="active",
        safe_metadata={"direction": "inbound"},
    )
    await enqueue_realtime_event(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        event_type="call.created",
        aggregate_type="call",
        aggregate_id=call.id,
        aggregate_version=call.state_version,
        payload={"status": "queued", "direction": "inbound"},
        occurred_at=now,
        correlation_id=event.correlation_id,
    )
    await CallStateService().transition(
        session,
        call=call,
        target=CallStatus.RINGING,
        event_type="call.ringing",
        occurred_at=event.occurred_at,
        correlation_id=event.correlation_id,
        actor_user_id=None,
        provider=event.provider,
        provider_event_id=event.provider_event_id,
        external_call_id=event.channel_id,
        provider_timestamp=event.occurred_at,
        safe_payload=event.safe_payload,
    )
    await session.commit()
    return _inbound_response(call, project, tenant_settings, duplicate=False)


def _source_allowed(source_ip: str, allowed: list[str]) -> bool:
    try:
        source = ip_address(source_ip)
    except ValueError:
        return False
    for candidate in allowed:
        try:
            if source in ip_network(candidate, strict=False):
                return True
        except ValueError:
            continue
    return False


def _project_is_open(project: Project, tenant_settings: TenantSettings | None, occurred_at: datetime) -> bool:
    if not project.working_hours:
        return True
    timezone = ZoneInfo(project.timezone or (tenant_settings.timezone if tenant_settings else "UTC"))
    local = occurred_at.astimezone(timezone)
    day = project.working_hours.get(local.strftime("%A").lower())
    if not isinstance(day, dict):
        return True
    if not day.get("enabled", False):
        return False
    start = day.get("start")
    end = day.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    current = local.strftime("%H:%M")
    return start <= current < end


def _inbound_response(
    call: Call,
    project: Project,
    tenant_settings: TenantSettings | None,
    *,
    duplicate: bool,
) -> InboundTelephonyAccepted:
    recording_allowed = (
        project.recording_enabled
        if project.recording_enabled is not None
        else bool(tenant_settings and tenant_settings.recording_enabled)
    )
    disclosure = (
        project.recording_disclosure_required
        if project.recording_disclosure_required is not None
        else bool(tenant_settings and tenant_settings.recording_disclosure_required)
    )
    return InboundTelephonyAccepted.model_validate(
        {
            "callId": call.id,
            "tenantId": call.tenant_id,
            "projectId": call.project_id,
            "stateVersion": call.state_version,
            "recordingAllowed": recording_allowed,
            "disclosureRequired": disclosure,
            "aiSessionAvailable": call.ai_operator_version_id is not None,
            "duplicate": duplicate,
        }
    )
