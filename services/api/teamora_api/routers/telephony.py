from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import func, select

from teamora_api.audit import write_audit
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import CallChannel, CallDirection, CallerType, CallStatus, TelephonyCommandName
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallDetailRecord,
    CallEvent,
    PhoneNumber,
    Project,
    ProjectInboundPhoneNumber,
    SipTrunk,
    TelephonyChannelReservation,
    TelephonyDiagnosticRun,
)
from teamora_api.project_access import resolve_project
from teamora_api.schemas.telephony import (
    DidRoutingTestRead,
    DidRoutingTestRequest,
    LiveTelephonyTestRequest,
    LocalMediaTestRequest,
    TelephonyChannelUsageRead,
    TelephonyDiagnosticRead,
    TelephonyStatusRead,
)
from teamora_api.telephony.control import mask_number, number_hash
from teamora_api.telephony.service import TelephonyService

router = APIRouter(prefix="/telephony", tags=["telephony"])


def _serialize_diagnostic(run: TelephonyDiagnosticRun) -> TelephonyDiagnosticRead:
    return TelephonyDiagnosticRead(
        id=run.id,
        mode=run.mode,
        status=run.status,
        destination_masked=run.destination_masked,
        signaling_verified=run.signaling_verified,
        inbound_audio_verified=run.inbound_audio_verified,
        outbound_audio_verified=run.outbound_audio_verified,
        dtmf_verified=run.dtmf_verified,
        codec=run.codec,
        media_statistics=run.media_statistics,
        safe_error_code=run.safe_error_code,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/status", response_model=TelephonyStatusRead)
async def telephony_status(
    session: SessionDep,
    principal: Principal = require_permission("integrations:read"),
) -> TelephonyStatusRead:
    trunks = list(
        await session.scalars(
            select(SipTrunk).where(SipTrunk.tenant_id == principal.tenant_id).order_by(SipTrunk.name)
        )
    )
    usage: list[TelephonyChannelUsageRead] = []
    for trunk in trunks:
        rows = (
            await session.execute(
                select(TelephonyChannelReservation.direction, func.count())
                .where(
                    TelephonyChannelReservation.tenant_id == principal.tenant_id,
                    TelephonyChannelReservation.sip_trunk_id == trunk.id,
                    TelephonyChannelReservation.status == "reserved",
                )
                .group_by(TelephonyChannelReservation.direction)
            )
        ).all()
        counts = {str(direction): int(count) for direction, count in rows}
        occupied = sum(counts.values())
        usage.append(
            TelephonyChannelUsageRead(
                trunk_id=trunk.id,
                name=trunk.name,
                pool_mode=trunk.channel_pool_mode,
                limit=trunk.max_channels,
                inbound_limit=trunk.inbound_channel_limit,
                outbound_limit=trunk.outbound_channel_limit,
                occupied=occupied,
                inbound_occupied=counts.get("inbound", 0),
                outbound_occupied=counts.get("outbound", 0),
                available=max(0, trunk.max_channels - occupied),
            )
        )
    dids = list(
        await session.scalars(
            select(PhoneNumber.e164).where(
                PhoneNumber.tenant_id == principal.tenant_id,
                PhoneNumber.is_active.is_(True),
            )
        )
    )
    latest = await session.scalar(
        select(TelephonyDiagnosticRun)
        .where(TelephonyDiagnosticRun.tenant_id == principal.tenant_id)
        .order_by(TelephonyDiagnosticRun.started_at.desc())
        .limit(1)
    )
    live_signal = bool(latest and latest.signaling_verified and latest.mode == "live")
    live_audio = bool(
        latest and latest.mode == "live" and latest.inbound_audio_verified and latest.outbound_audio_verified
    )
    primary = trunks[0] if trunks else None
    status = "not_configured"
    if primary is not None:
        status = "configured"
    if latest is not None and latest.status == "local_test_passed":
        status = "local_test_passed"
    if live_signal:
        status = "live_signaling_verified"
    if live_audio:
        status = "live_audio_verified"
    return TelephonyStatusRead(
        status=status,
        asterisk="configured_live_verification_required" if primary else "not_configured",
        ari="configured_live_verification_required" if primary else "not_configured",
        sip_trunk="configured" if primary else "not_configured",
        registration=primary.registration_status if primary else "not_configured",
        reachability=primary.reachability_status if primary else "not_configured",
        external_media="local_test_passed" if latest and latest.inbound_audio_verified else "not_verified",
        recording="configured_not_live_verified" if primary else "not_configured",
        dids=[mask_number(did) or "" for did in dids],
        transport=primary.transport if primary else None,
        codecs=primary.codecs if primary else [],
        channel_usage=usage,
        last_checked_at=latest.completed_at if latest else (primary.last_status_at if primary else None),
        last_safe_error=latest.safe_error_code if latest else (primary.last_safe_error if primary else None),
        live_signaling_verified=live_signal,
        live_audio_verified=live_audio,
    )


@router.post("/did-routing-test", response_model=DidRoutingTestRead)
async def did_routing_test(
    payload: DidRoutingTestRequest,
    session: SessionDep,
    principal: Principal = require_permission("sip:manage"),
) -> DidRoutingTestRead:
    row = (
        await session.execute(
            select(ProjectInboundPhoneNumber, PhoneNumber)
            .join(
                PhoneNumber,
                (PhoneNumber.tenant_id == ProjectInboundPhoneNumber.tenant_id)
                & (PhoneNumber.id == ProjectInboundPhoneNumber.phone_number_id),
            )
            .where(
                ProjectInboundPhoneNumber.tenant_id == principal.tenant_id,
                PhoneNumber.e164 == payload.did,
                PhoneNumber.is_active.is_(True),
            )
        )
    ).first()
    return DidRoutingTestRead(
        matched=row is not None,
        project_id=row[0].project_id if row else None,
        phone_number_id=row[1].id if row else None,
        trunk_id=row[1].sip_trunk_id if row else None,
        did_masked=mask_number(payload.did) or "",
    )


@router.post("/diagnostics/local", response_model=TelephonyDiagnosticRead, status_code=201)
async def local_media_test(
    payload: LocalMediaTestRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("sip:manage"),
) -> TelephonyDiagnosticRead:
    project = await resolve_project(session, principal, payload.project_id)
    trunk = await _project_trunk(session, principal.tenant_id, project)
    run = TelephonyDiagnosticRun(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        sip_trunk_id=trunk.id,
        requested_by_user_id=principal.user_id,
        mode="local",
        status="running",
        correlation_id=request.state.correlation_id,
        codec=payload.codec.lower(),
    )
    session.add(run)
    await session.flush()
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "gateway_not_configured", "Voice Gateway is not configured")
    try:
        async with httpx.AsyncClient(
            base_url=settings.gateway_internal_url,
            timeout=httpx.Timeout(settings.gateway_command_timeout_seconds),
        ) as client:
            response = await client.post(
                "/internal/v1/telephony/diagnostics/local-media",
                headers={
                    "Authorization": f"Bearer {settings.gateway_service_token.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-Correlation-ID": request.state.correlation_id,
                },
                json={"codec": payload.codec.lower(), "packets": payload.packets},
            )
        response.raise_for_status()
        result = response.json()
        run.media_statistics = {
            key: value
            for key, value in result.items()
            if key in {"packetsReceived", "packetsSent", "bytesReceived", "bytesSent", "durationMs"}
            and isinstance(value, (int, float))
        }
        run.inbound_audio_verified = bool(result.get("inboundAudioVerified"))
        run.outbound_audio_verified = bool(result.get("outboundAudioVerified"))
        run.status = (
            "local_test_passed" if run.inbound_audio_verified and run.outbound_audio_verified else "failed"
        )
        run.safe_error_code = None if run.status == "local_test_passed" else "rtp_bidirectional_not_verified"
    except (httpx.HTTPError, ValueError):
        run.status = "failed"
        run.safe_error_code = "gateway_local_media_test_failed"
    run.completed_at = datetime.now(UTC)
    run.lock_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="telephony.local_media_test",
        resource_type="telephony_diagnostic_run",
        resource_id=run.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"status": run.status, "codec": run.codec},
    )
    await session.commit()
    return _serialize_diagnostic(run)


@router.post("/diagnostics/live", response_model=TelephonyDiagnosticRead, status_code=201)
async def controlled_live_test(
    payload: LiveTelephonyTestRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("sip:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> TelephonyDiagnosticRead:
    settings = get_settings()
    if not settings.telephony_live_diagnostic_enabled:
        raise ApiError(409, "live_test_disabled", "Controlled live telephony test is disabled")
    if payload.destination not in settings.live_test_number_allowlist:
        raise ApiError(403, "live_test_number_not_allowed", "Destination is not in the live test allowlist")
    if settings.safe_dial_prefixes and not payload.destination.startswith(settings.safe_dial_prefixes):
        raise ApiError(403, "dial_pattern_rejected", "Destination is rejected by the dial allowlist")
    project = await resolve_project(session, principal, payload.project_id, for_update=True)
    telephony = TelephonyService(settings)
    selection = await telephony.select_provider(
        session,
        tenant_id=principal.tenant_id,
        project=project,
        allow_live_diagnostic=True,
    )
    if selection.sip_trunk_id is None:
        raise ApiError(409, "live_sip_not_configured", "Project has no live SIP trunk")
    existing = await session.scalar(
        select(TelephonyDiagnosticRun).where(
            TelephonyDiagnosticRun.tenant_id == principal.tenant_id,
            TelephonyDiagnosticRun.correlation_id == f"live:{idempotency_key}",
        )
    )
    if existing is not None:
        return _serialize_diagnostic(existing)
    run = TelephonyDiagnosticRun(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        sip_trunk_id=selection.sip_trunk_id,
        requested_by_user_id=principal.user_id,
        mode="live",
        status="pending",
        destination_hash=number_hash(payload.destination),
        destination_masked=mask_number(payload.destination),
        correlation_id=f"live:{idempotency_key}",
    )
    call = Call(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        channel=CallChannel.SIP,
        status=CallStatus.QUEUED,
        direction=CallDirection.OUTBOUND,
        caller_type=CallerType.HUMAN_OPERATOR,
        operator_user_id=principal.user_id,
        provider=selection.provider.name,
        provider_state="queued",
        from_number=selection.from_number,
        to_number=payload.destination,
        sip_trunk_id=selection.sip_trunk_id,
        is_demo=False,
    )
    session.add_all([run, call])
    await session.flush()
    run.call_id = call.id
    try:
        result = await telephony.execute(
            session,
            call=call,
            project=project,
            selection=selection,
            command_name=TelephonyCommandName.ORIGINATE,
            correlation_id=request.state.correlation_id,
            actor_user_id=principal.user_id,
            idempotency_key=f"live-test:{idempotency_key}",
            parameters={
                "toNumber": payload.destination,
                "fromNumber": selection.from_number,
                "diagnostic": True,
            },
        )
    except ApiError as exc:
        # TelephonyService durably records command failures before raising. Keep
        # the diagnostic linked to that Call, but never claim provider signaling.
        run.status = "provider_unreachable" if exc.status_code >= 500 else "failed"
        run.safe_error_code = exc.code
        run.completed_at = datetime.now(UTC)
        run.lock_version += 1
        await session.commit()
        raise
    if not result.accepted:
        run.status = "failed"
        run.safe_error_code = "provider_command_rejected"
        run.completed_at = datetime.now(UTC)
    # ARI command acceptance only proves that the internal Gateway accepted the
    # command. A provider event will promote this run after a real SIP response.
    run.lock_version += 1
    await session.commit()
    return _serialize_diagnostic(run)


@router.get("/diagnostics", response_model=list[TelephonyDiagnosticRead])
async def recent_diagnostics(
    session: SessionDep,
    principal: Principal = require_permission("integrations:read"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TelephonyDiagnosticRead]:
    rows = list(
        await session.scalars(
            select(TelephonyDiagnosticRun)
            .where(TelephonyDiagnosticRun.tenant_id == principal.tenant_id)
            .order_by(TelephonyDiagnosticRun.started_at.desc())
            .limit(limit)
        )
    )
    return [_serialize_diagnostic(row) for row in rows]


@router.get("/provider-events")
async def recent_provider_events(
    session: SessionDep,
    principal: Principal = require_permission("integrations:read"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, object]]:
    rows = list(
        await session.scalars(
            select(CallEvent)
            .where(
                CallEvent.tenant_id == principal.tenant_id,
                CallEvent.provider.is_not(None),
            )
            .order_by(CallEvent.occurred_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": str(row.id),
            "call_id": str(row.call_id),
            "event_type": row.event_type,
            "provider": row.provider,
            "occurred_at": row.occurred_at,
            "correlation_id": row.correlation_id,
        }
        for row in rows
    ]


@router.get("/cdr")
async def recent_cdr(
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, object]]:
    rows = list(
        await session.scalars(
            select(CallDetailRecord)
            .where(CallDetailRecord.tenant_id == principal.tenant_id)
            .order_by(CallDetailRecord.started_at.desc().nullslast())
            .limit(limit)
        )
    )
    return [
        {
            "id": str(row.id),
            "call_id": str(row.call_id),
            "direction": row.direction,
            "did": row.did_masked,
            "destination": row.destination_masked,
            "started_at": row.started_at,
            "answered_at": row.answered_at,
            "ended_at": row.ended_at,
            "disposition": row.disposition,
            "hangup_cause": row.hangup_cause,
            "codec": row.codec,
            "duration_seconds": row.duration_seconds,
            "billable_duration_seconds": row.billable_duration_seconds,
            "channels_used": row.channels_used,
        }
        for row in rows
    ]


async def _project_trunk(session: SessionDep, tenant_id: UUID, project: Project) -> SipTrunk:
    if project.outbound_phone_number_id is None:
        raise ApiError(409, "project_trunk_not_configured", "Project has no telephony number")
    trunk = await session.scalar(
        select(SipTrunk)
        .join(PhoneNumber, PhoneNumber.sip_trunk_id == SipTrunk.id)
        .where(
            SipTrunk.tenant_id == tenant_id,
            PhoneNumber.tenant_id == tenant_id,
            PhoneNumber.id == project.outbound_phone_number_id,
        )
    )
    if trunk is None:
        raise ApiError(409, "project_trunk_not_configured", "Project SIP trunk is not configured")
    return trunk
