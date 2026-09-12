from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from teamora_api.ai_realtime_service import configure_session, execute_tool, ingest_event
from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import RoleName
from teamora_api.errors import ApiError
from teamora_api.models import (
    AIRealtimeSession,
    AIRealtimeSessionEvent,
    Call,
    KnowledgeRetrievalExecution,
    Project,
    TenantSettings,
    ToolExecution,
    UsageRecord,
)
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.schemas.ai_realtime import (
    AIRealtimeCitationRead,
    AIRealtimeDiagnosticRead,
    AIRealtimeLatencyRead,
    AIRealtimeProviderEvent,
    AIRealtimeSessionConfiguration,
    AIRealtimeSessionDetailRead,
    AIRealtimeSessionEventRead,
    AIRealtimeSessionRead,
    AIRealtimeSessionRequest,
    AIRealtimeStatusRead,
    AIRealtimeToolExecutionRead,
    AIRealtimeToolRequest,
    AIRealtimeUsageRead,
    VoiceLabTicketRead,
    VoiceLabTicketRequest,
)
from teamora_api.voice_lab_tokens import create_voice_lab_ticket
from teamora_api.webhooks import verify_standard_webhook

router = APIRouter(tags=["ai-realtime"])


def _read(row: AIRealtimeSession) -> AIRealtimeSessionRead:
    return AIRealtimeSessionRead(
        id=row.id,
        call_id=row.call_id,
        project_id=row.project_id,
        state=row.state,
        state_version=row.state_version,
        provider=row.provider,
        model=row.model,
        voice=row.voice,
        language_code=row.language_code,
        provider_session_id=row.provider_session_id,
        interruption_count=row.interruption_count,
        usage=dict(row.usage_snapshot),
        latency=dict(row.latency_snapshot),
        safe_error_code=row.safe_error_code,
        created_at=row.created_at,
        connected_at=row.connected_at,
        closed_at=row.closed_at,
    )


async def _verified_payload(request: Request) -> bytes:
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "ai_gateway_unavailable", "Gateway service authentication is not configured")
    payload = await request.body()
    if len(payload) > 65_536:
        raise ApiError(413, "ai_payload_too_large", "AI Gateway payload is too large")
    verify_standard_webhook(
        payload=payload,
        webhook_id=request.headers.get("x-teamora-id", ""),
        timestamp=request.headers.get("x-teamora-timestamp", ""),
        signature_header=request.headers.get("x-teamora-signature", ""),
        secret=settings.gateway_service_token.get_secret_value(),
    )
    return payload


@router.post("/webhooks/ai-realtime/session", response_model=AIRealtimeSessionConfiguration)
async def create_ai_session(request: Request, session: SessionDep) -> AIRealtimeSessionConfiguration:
    payload = await _verified_payload(request)
    try:
        data = AIRealtimeSessionRequest.model_validate_json(payload)
    except ValueError as exc:
        raise ApiError(422, "ai_session_request_invalid", "AI session request is invalid") from exc
    await set_tenant_context(session, data.tenant_id)
    project = await session.scalar(
        select(Project).where(Project.tenant_id == data.tenant_id, Project.id == data.project_id)
    )
    if project is None:
        raise ApiError(404, "ai_project_not_found", "AI project was not found")
    tenant_settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == data.tenant_id)
    )
    recording_allowed = (
        project.recording_enabled
        if project.recording_enabled is not None
        else bool(tenant_settings and tenant_settings.recording_enabled)
    )
    disclosure_required = (
        project.recording_disclosure_required
        if project.recording_disclosure_required is not None
        else bool(tenant_settings and tenant_settings.recording_disclosure_required)
    )
    result = await configure_session(
        session,
        settings=get_settings(),
        request=data,
        recording_allowed=recording_allowed,
        disclosure_required=disclosure_required,
    )
    await session.commit()
    return result


@router.post("/webhooks/ai-realtime/events", status_code=202)
async def ai_session_event(request: Request, session: SessionDep) -> dict[str, object]:
    payload = await _verified_payload(request)
    try:
        data = AIRealtimeProviderEvent.model_validate_json(payload)
    except ValueError as exc:
        raise ApiError(422, "ai_event_invalid", "AI provider event is invalid") from exc
    await set_tenant_context(session, data.tenant_id)
    realtime, duplicate = await ingest_event(session, event=data)
    await session.commit()
    return {
        "accepted": True,
        "duplicate": duplicate,
        "session_id": str(realtime.id),
        "state": realtime.state,
        "state_version": realtime.state_version,
    }


@router.post("/webhooks/ai-realtime/tools")
async def ai_tool(request: Request, session: SessionDep) -> dict[str, object]:
    payload = await _verified_payload(request)
    try:
        data = AIRealtimeToolRequest.model_validate_json(payload)
    except ValueError as exc:
        raise ApiError(422, "ai_tool_request_invalid", "AI tool request is invalid") from exc
    await set_tenant_context(session, data.tenant_id)
    result = await execute_tool(session, request=data, settings=get_settings())
    await session.commit()
    return result


@router.get("/ai-realtime/status", response_model=AIRealtimeStatusRead)
async def ai_status(
    session: SessionDep,
    principal: Principal = require_permission("integrations:read"),
) -> AIRealtimeStatusRead:
    settings = get_settings()
    active = int(
        await session.scalar(
            select(func.count())
            .select_from(AIRealtimeSession)
            .where(
                AIRealtimeSession.tenant_id == principal.tenant_id,
                AIRealtimeSession.state.in_({"pending", "connecting", "active", "reconnecting", "degraded"}),
            )
        )
        or 0
    )
    latest = await session.scalar(
        select(AIRealtimeSession)
        .where(AIRealtimeSession.tenant_id == principal.tenant_id)
        .order_by(AIRealtimeSession.created_at.desc())
        .limit(1)
    )
    first_audio_latency = AIRealtimeSession.latency_snapshot["first_audio_latency_ms"].as_float()
    latency_row = (
        await session.execute(
            select(
                func.count(first_audio_latency),
                func.percentile_cont(0.5).within_group(first_audio_latency),
                func.percentile_cont(0.95).within_group(first_audio_latency),
                func.percentile_cont(0.99).within_group(first_audio_latency),
            ).where(
                AIRealtimeSession.tenant_id == principal.tenant_id,
                first_audio_latency.is_not(None),
                first_audio_latency >= 0,
            )
        )
    ).one()
    samples = int(latency_row[0] or 0)
    paid_provider = settings.openai_realtime_provider == "openai"
    live_status = (
        "live_not_requested"
        if paid_provider and settings.openai_realtime_enabled
        else "not_applicable"
        if not paid_provider
        else "not_configured"
    )
    return AIRealtimeStatusRead(
        configured=not paid_provider or settings.openai_api_key is not None,
        enabled=settings.openai_realtime_enabled,
        provider=settings.openai_realtime_provider,
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
        voice_lab_enabled=settings.openai_voice_lab_enabled,
        voice_lab_max_seconds=settings.openai_voice_lab_max_seconds,
        live_verification=live_status,
        last_session_state=latest.state if latest else None,
        last_safe_error=latest.safe_error_code if latest else None,
        active_sessions=active,
        latency=AIRealtimeLatencyRead(
            samples=samples,
            p50_ms=float(latency_row[1]) if latency_row[1] is not None else None,
            p95_ms=float(latency_row[2]) if latency_row[2] is not None else None,
            p99_ms=float(latency_row[3]) if latency_row[3] is not None else None,
            is_available=samples > 0,
        ),
    )


@router.post("/ai-realtime/lab/ticket", response_model=VoiceLabTicketRead)
async def create_voice_lab_access(
    payload: VoiceLabTicketRequest,
    principal: Principal = require_permission("integrations:manage"),
) -> VoiceLabTicketRead:
    settings = get_settings()
    if not settings.openai_voice_lab_enabled or not settings.openai_realtime_enabled:
        raise ApiError(503, "voice_lab_disabled", "Voice Lab is not enabled")
    if settings.gateway_service_token is None:
        raise ApiError(503, "ai_gateway_unavailable", "Voice Gateway is not configured")
    paid_provider = settings.openai_realtime_provider == "openai"
    if paid_provider and settings.openai_api_key is None:
        raise ApiError(503, "openai_not_configured", "OpenAI Realtime is not configured")
    if paid_provider and payload.confirmation != "I_APPROVE_PAID_VOICE_LAB":
        raise ApiError(
            422,
            "voice_lab_confirmation_required",
            "Explicit confirmation is required before a paid Voice Lab session",
        )
    token, expires_at = create_voice_lab_ticket(
        secret=settings.gateway_service_token.get_secret_value(),
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        language=payload.language,
    )
    return VoiceLabTicketRead(
        token=token,
        websocket_url="/gateway/voice-lab/ws",
        expires_at=expires_at,
        max_seconds=settings.openai_voice_lab_max_seconds,
        provider=settings.openai_realtime_provider,
        model=(settings.openai_realtime_model if paid_provider else "mock-realtime-deterministic"),
        voice=settings.openai_realtime_voice if paid_provider else "mock",
        paid_provider=paid_provider,
    )


@router.post("/ai-realtime/diagnostics/local", response_model=AIRealtimeDiagnosticRead)
async def local_ai_diagnostic(
    request: Request,
    principal: Principal = require_permission("integrations:manage"),
) -> AIRealtimeDiagnosticRead:
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "ai_gateway_unavailable", "Voice Gateway is not configured")
    try:
        async with httpx.AsyncClient(
            base_url=settings.gateway_internal_url,
            timeout=httpx.Timeout(settings.gateway_command_timeout_seconds),
        ) as client:
            response = await client.post(
                "/internal/v1/ai/diagnostics/local",
                headers={
                    "Authorization": f"Bearer {settings.gateway_service_token.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-Correlation-ID": request.state.correlation_id,
                },
                json={},
            )
        response.raise_for_status()
        return AIRealtimeDiagnosticRead.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiError(
            503,
            "ai_gateway_diagnostic_failed",
            "Local AI Realtime diagnostic did not complete",
        ) from exc


@router.get("/ai-realtime/sessions/active", response_model=list[AIRealtimeSessionRead])
async def active_ai_sessions(
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> list[AIRealtimeSessionRead]:
    project_ids = await accessible_project_ids(session, principal)
    if not project_ids:
        return []
    rows = list(
        await session.scalars(
            select(AIRealtimeSession)
            .where(
                AIRealtimeSession.tenant_id == principal.tenant_id,
                AIRealtimeSession.project_id.in_(project_ids),
                AIRealtimeSession.state.in_(
                    {"pending", "connecting", "active", "reconnecting", "degraded", "closing"}
                ),
            )
            .order_by(AIRealtimeSession.created_at.desc())
            .limit(200)
        )
    )
    if principal.role == RoleName.HUMAN_OPERATOR and rows:
        call_ids = set(
            await session.scalars(
                select(Call.id).where(
                    Call.tenant_id == principal.tenant_id,
                    Call.id.in_([row.call_id for row in rows]),
                    or_(Call.operator_user_id.is_(None), Call.operator_user_id == principal.user_id),
                )
            )
        )
        rows = [row for row in rows if row.call_id in call_ids]
    return [_read(row) for row in rows]


@router.get("/calls/{call_id}/ai-session", response_model=AIRealtimeSessionRead)
async def call_ai_session(
    call_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> AIRealtimeSessionRead:
    call = await session.scalar(select(Call).where(Call.tenant_id == principal.tenant_id, Call.id == call_id))
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    await resolve_project(session, principal, call.project_id, active_only=False)
    if principal.role == RoleName.HUMAN_OPERATOR and call.operator_user_id not in {None, principal.user_id}:
        raise ApiError(404, "call_not_found", "Call was not found")
    row = await session.scalar(
        select(AIRealtimeSession).where(
            AIRealtimeSession.tenant_id == principal.tenant_id,
            AIRealtimeSession.call_id == call.id,
        )
    )
    if row is None:
        raise ApiError(404, "ai_session_not_found", "AI session was not found")
    return _read(row)


@router.get("/calls/{call_id}/ai-session/details", response_model=AIRealtimeSessionDetailRead)
async def call_ai_session_details(
    call_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> AIRealtimeSessionDetailRead:
    row = await session.scalar(
        select(AIRealtimeSession).where(
            AIRealtimeSession.tenant_id == principal.tenant_id,
            AIRealtimeSession.call_id == call_id,
        )
    )
    if row is None:
        raise ApiError(404, "ai_session_not_found", "AI session was not found")
    await resolve_project(session, principal, row.project_id, active_only=False)
    call = await session.scalar(select(Call).where(Call.tenant_id == principal.tenant_id, Call.id == call_id))
    if call is None or (
        principal.role == RoleName.HUMAN_OPERATOR and call.operator_user_id not in {None, principal.user_id}
    ):
        raise ApiError(404, "call_not_found", "Call was not found")
    events = list(
        await session.scalars(
            select(AIRealtimeSessionEvent)
            .where(
                AIRealtimeSessionEvent.tenant_id == principal.tenant_id,
                AIRealtimeSessionEvent.session_id == row.id,
            )
            .order_by(AIRealtimeSessionEvent.occurred_at, AIRealtimeSessionEvent.id)
        )
    )
    tools = list(
        await session.scalars(
            select(ToolExecution)
            .where(
                ToolExecution.tenant_id == principal.tenant_id,
                ToolExecution.call_id == call_id,
            )
            .order_by(ToolExecution.created_at)
        )
    )
    retrievals = list(
        await session.scalars(
            select(KnowledgeRetrievalExecution)
            .where(
                KnowledgeRetrievalExecution.tenant_id == principal.tenant_id,
                KnowledgeRetrievalExecution.call_id == call_id,
            )
            .order_by(KnowledgeRetrievalExecution.created_at)
        )
    )
    usage = list(
        await session.scalars(
            select(UsageRecord)
            .where(UsageRecord.tenant_id == principal.tenant_id, UsageRecord.call_id == call_id)
            .order_by(UsageRecord.occurred_at)
        )
    )
    return AIRealtimeSessionDetailRead(
        session=_read(row),
        events=[
            AIRealtimeSessionEventRead(
                id=item.id,
                event_type=item.event_type,
                aggregate_version=item.aggregate_version,
                occurred_at=item.occurred_at,
            )
            for item in events
        ],
        tools=[
            AIRealtimeToolExecutionRead(
                id=item.id,
                tool_name=item.tool_name,
                status=item.status.value,
                safe_result=item.safe_result,
                duration_ms=item.duration_ms,
                created_at=item.created_at,
            )
            for item in tools
        ],
        citations=[
            AIRealtimeCitationRead(
                revision_id=item.revision_id,
                citations=item.citations,
                no_match=item.no_match,
                latency_ms=item.latency_ms,
                created_at=item.created_at,
            )
            for item in retrievals
        ],
        usage=[
            AIRealtimeUsageRead(
                metric=item.metric,
                quantity=str(item.quantity),
                unit=item.unit,
                provider=item.provider,
                model=item.model,
                pricing_available=item.pricing_available,
                occurred_at=item.occurred_at,
            )
            for item in usage
        ],
    )
