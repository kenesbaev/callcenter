from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Request
from sqlalchemy import select

from teamora_api.audit import write_audit
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, PrincipalDep, SessionDep, require_permission
from teamora_api.enums import CallDirection, RoleName, TransferStatus
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    Membership,
    OperatorTransferEndpoint,
    ProjectUser,
    TransferAttempt,
    TransferRequest,
)
from teamora_api.project_access import resolve_project
from teamora_api.rbac import role_has_permission
from teamora_api.schemas.transfers import (
    OperatorEndpointAuthorize,
    OperatorEndpointRead,
    OperatorEndpointUpsert,
    TransferAnswerRequest,
    TransferAttemptRead,
    TransferClaimRequest,
    TransferDeclineRequest,
    TransferRequestCreate,
    TransferRequestRead,
    WebRtcConfigurationRead,
)
from teamora_api.telephony.control import reserve_channel
from teamora_api.transfer_service import (
    claim_transfer,
    decline_transfer,
    request_transfer,
)

router = APIRouter(prefix="/transfers", tags=["live-transfers"])
MANAGER_ROLES = {RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER}
READ_ALL_ROLES = MANAGER_ROLES | {RoleName.ANALYST}


def _require_transfer_read(principal: Principal) -> None:
    if not (
        role_has_permission(principal.role, "calls:read") or role_has_permission(principal.role, "dialer:use")
    ):
        raise ApiError(403, "permission_denied", "You do not have permission for this action")


async def _attempts(session: SessionDep, transfer_id: UUID, tenant_id: UUID) -> list[TransferAttempt]:
    return list(
        await session.scalars(
            select(TransferAttempt)
            .where(
                TransferAttempt.tenant_id == tenant_id,
                TransferAttempt.transfer_request_id == transfer_id,
            )
            .order_by(TransferAttempt.attempt_number)
        )
    )


async def _serialize(session: SessionDep, row: TransferRequest) -> TransferRequestRead:
    attempts = await _attempts(session, row.id, row.tenant_id)
    return TransferRequestRead(
        id=row.id,
        call_id=row.call_id,
        project_id=row.project_id,
        status=row.status,
        reason=row.reason,
        summary=row.summary,
        language_code=row.language_code,
        routing_strategy=row.routing_strategy,
        destination_type=cast(Literal["browser", "sip", "mobile"], row.destination_type),
        claimed_membership_id=row.claimed_membership_id,
        context=dict(row.context_snapshot),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        lock_version=row.lock_version,
        requested_at=row.requested_at,
        offer_expires_at=row.offer_expires_at,
        claimed_at=row.claimed_at,
        connected_at=row.connected_at,
        resolved_at=row.resolved_at,
        last_error_code=row.last_error_code,
        attempts=[
            TransferAttemptRead(
                id=item.id,
                membership_id=item.membership_id,
                attempt_number=item.attempt_number,
                destination_type=cast(Literal["browser", "sip", "mobile"], item.destination_type),
                status=item.status,
                offered_at=item.offered_at,
                expires_at=item.expires_at,
                claimed_at=item.claimed_at,
                answered_at=item.answered_at,
                safe_error_code=item.safe_error_code,
            )
            for item in attempts
        ],
    )


async def _visible_transfer(
    session: SessionDep,
    principal: Principal,
    transfer_id: UUID,
    *,
    lock: bool = False,
) -> TransferRequest:
    statement = select(TransferRequest).where(
        TransferRequest.tenant_id == principal.tenant_id,
        TransferRequest.id == transfer_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise ApiError(404, "transfer_not_found", "Transfer request was not found")
    await resolve_project(session, principal, row.project_id, active_only=False)
    if principal.role not in READ_ALL_ROLES:
        visible = await session.scalar(
            select(TransferAttempt.id).where(
                TransferAttempt.tenant_id == principal.tenant_id,
                TransferAttempt.transfer_request_id == row.id,
                TransferAttempt.membership_id == principal.membership_id,
            )
        )
        if visible is None and row.claimed_membership_id != principal.membership_id:
            raise ApiError(404, "transfer_not_found", "Transfer request was not found")
    return row


@router.post("/calls/{call_id}/request", response_model=TransferRequestRead)
async def create_transfer_request(
    call_id: UUID,
    payload: TransferRequestCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> TransferRequestRead:
    call = await session.scalar(
        select(Call).where(Call.tenant_id == principal.tenant_id, Call.id == call_id).with_for_update()
    )
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    await resolve_project(session, principal, call.project_id)
    if call.operator_user_id not in {None, principal.user_id} and principal.role not in MANAGER_ROLES:
        raise ApiError(404, "call_not_found", "Call was not found")
    row = await request_transfer(
        session,
        call=call,
        reason=payload.reason,
        summary=payload.summary,
        destination_type=payload.destination_type,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
        actor_user_id=principal.user_id,
        settings=get_settings(),
        expected_call_version=payload.expected_call_version,
        routing_strategy=payload.routing_strategy,
        max_attempts=payload.max_attempts,
    )
    response = await _serialize(session, row)
    await session.commit()
    return response


@router.get("", response_model=list[TransferRequestRead])
async def list_transfers(
    session: SessionDep,
    principal: PrincipalDep,
    project_id: UUID | None = None,
    status: TransferStatus | None = None,
) -> list[TransferRequestRead]:
    _require_transfer_read(principal)
    statement = select(TransferRequest).where(TransferRequest.tenant_id == principal.tenant_id)
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
        statement = statement.where(TransferRequest.project_id == project_id)
    elif principal.role not in READ_ALL_ROLES:
        project_ids = select(ProjectUser.project_id).where(
            ProjectUser.tenant_id == principal.tenant_id,
            ProjectUser.user_id == principal.user_id,
            ProjectUser.is_active.is_(True),
        )
        statement = statement.where(TransferRequest.project_id.in_(project_ids))
    if status is not None:
        statement = statement.where(TransferRequest.status == status)
    if principal.role not in READ_ALL_ROLES:
        attempt_ids = select(TransferAttempt.transfer_request_id).where(
            TransferAttempt.tenant_id == principal.tenant_id,
            TransferAttempt.membership_id == principal.membership_id,
        )
        statement = statement.where(TransferRequest.id.in_(attempt_ids))
    rows = list(await session.scalars(statement.order_by(TransferRequest.requested_at.desc()).limit(200)))
    return [await _serialize(session, row) for row in rows]


@router.get("/requests/{transfer_id}", response_model=TransferRequestRead)
async def get_transfer(
    transfer_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> TransferRequestRead:
    _require_transfer_read(principal)
    return await _serialize(session, await _visible_transfer(session, principal, transfer_id))


@router.post("/requests/{transfer_id}/claim", response_model=TransferRequestRead)
async def claim(
    transfer_id: UUID,
    payload: TransferClaimRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> TransferRequestRead:
    row = await claim_transfer(
        session,
        transfer_id=transfer_id,
        principal=principal,
        expected_version=payload.expected_version,
        correlation_id=request.state.correlation_id,
    )
    response = await _serialize(session, row)
    await session.commit()
    return response


@router.post("/requests/{transfer_id}/decline", response_model=TransferRequestRead)
async def decline(
    transfer_id: UUID,
    payload: TransferDeclineRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> TransferRequestRead:
    row = await _visible_transfer(session, principal, transfer_id, lock=True)
    await decline_transfer(
        session,
        transfer=row,
        principal=principal,
        expected_version=payload.expected_version,
        reason=payload.reason,
        settings=get_settings(),
        correlation_id=request.state.correlation_id,
    )
    response = await _serialize(session, row)
    await session.commit()
    return response


async def _gateway_handoff(action: str, payload: dict[str, object], correlation_id: str) -> dict[str, object]:
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "transfer_gateway_unavailable", "Voice Gateway is not configured")
    try:
        async with httpx.AsyncClient(
            base_url=settings.gateway_internal_url,
            timeout=httpx.Timeout(settings.gateway_command_timeout_seconds),
        ) as client:
            response = await client.post(
                f"/internal/v1/transfers/{action}",
                headers={
                    "Authorization": f"Bearer {settings.gateway_service_token.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-Correlation-ID": correlation_id,
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise ApiError(503, "transfer_gateway_failed", "Voice Gateway rejected the transfer")
        result = response.json()
        return result if isinstance(result, dict) else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiError(503, "transfer_gateway_unavailable", "Voice Gateway is unavailable") from exc


@router.post("/requests/{transfer_id}/answer", response_model=TransferRequestRead)
async def answer(
    transfer_id: UUID,
    payload: TransferAnswerRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> TransferRequestRead:
    row = await _visible_transfer(session, principal, transfer_id, lock=True)
    if row.lock_version != payload.expected_version:
        raise ApiError(409, "transfer_version_conflict", "Transfer request was changed")
    if row.claimed_membership_id != principal.membership_id or row.status != TransferStatus.CLAIMED:
        raise ApiError(409, "transfer_not_claimed", "Claim this transfer before answering")
    attempt = await session.scalar(
        select(TransferAttempt)
        .where(
            TransferAttempt.tenant_id == row.tenant_id,
            TransferAttempt.transfer_request_id == row.id,
            TransferAttempt.membership_id == principal.membership_id,
            TransferAttempt.status == "claimed",
        )
        .with_for_update()
    )
    if attempt is None:
        raise ApiError(409, "transfer_endpoint_invalid", "Claimed transfer endpoint is unavailable")
    destination = f"webrtc:{principal.membership_id}"
    if payload.endpoint_type != "browser":
        endpoint = await session.scalar(
            select(OperatorTransferEndpoint).where(
                OperatorTransferEndpoint.tenant_id == principal.tenant_id,
                OperatorTransferEndpoint.membership_id == principal.membership_id,
                OperatorTransferEndpoint.endpoint_type == payload.endpoint_type,
                OperatorTransferEndpoint.is_enabled.is_(True),
                OperatorTransferEndpoint.is_verified.is_(True),
            )
        )
        if endpoint is None:
            raise ApiError(
                409, "transfer_destination_not_allowlisted", "Transfer destination is not allowlisted"
            )
        destination = endpoint.destination
    if payload.endpoint_type == "mobile":
        call = await session.scalar(
            select(Call).where(Call.tenant_id == row.tenant_id, Call.id == row.call_id).with_for_update()
        )
        if call is None or call.sip_trunk_id is None:
            raise ApiError(
                409,
                "transfer_channel_unavailable",
                "The call has no SIP trunk available for a mobile transfer",
            )
        await reserve_channel(
            session,
            call=call,
            trunk_id=call.sip_trunk_id,
            direction=CallDirection.OUTBOUND,
            purpose="operator_transfer",
        )
    result = await _gateway_handoff(
        "dial",
        {
            "tenantId": str(row.tenant_id),
            "projectId": str(row.project_id),
            "callId": str(row.call_id),
            "transferRequestId": str(row.id),
            "destinationType": payload.endpoint_type,
            "destination": destination,
            "idempotencyKey": attempt.idempotency_key,
        },
        request.state.correlation_id,
    )
    row.status = TransferStatus.CONNECTING
    row.destination_type = payload.endpoint_type
    row.lock_version += 1
    attempt.status = "connecting"
    attempt.destination_type = payload.endpoint_type
    attempt.destination_ref = destination
    provider_channel = result.get("operatorChannelId")
    if isinstance(provider_channel, str):
        attempt.provider_channel_id = provider_channel[:200]
        row.operator_channel_id = provider_channel[:200]
    response = await _serialize(session, row)
    await session.commit()
    return response


@router.post("/operator/webrtc-config", response_model=WebRtcConfigurationRead)
async def webrtc_configuration(
    request: Request,
    principal: Principal = require_permission("dialer:use"),
) -> WebRtcConfigurationRead:
    settings = get_settings()
    username = f"operator-{principal.membership_id}"
    authorization = secrets.token_urlsafe(36)
    ttl_seconds = settings.operator_webrtc_credential_ttl_seconds
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    await _gateway_handoff(
        "webrtc/provision",
        {
            "membershipId": str(principal.membership_id),
            "username": username,
            "password": authorization,
            "ttlSeconds": ttl_seconds,
        },
        request.state.correlation_id,
    )
    return WebRtcConfigurationRead(
        websocket_url=settings.operator_webrtc_wss_url,
        sip_uri=f"sip:{username}@kline.invalid",
        authorization=authorization,
        expires_at=expires_at,
        ice_servers=[],
        live_verification="ephemeral_endpoint_provisioned_live_browser_verification_required",
    )


@router.get("/operator/endpoints", response_model=list[OperatorEndpointRead])
async def list_operator_endpoints(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> list[OperatorEndpointRead]:
    rows = (
        await session.scalars(
            select(OperatorTransferEndpoint)
            .where(
                OperatorTransferEndpoint.tenant_id == principal.tenant_id,
                OperatorTransferEndpoint.membership_id == principal.membership_id,
                OperatorTransferEndpoint.is_verified.is_(True),
                OperatorTransferEndpoint.is_enabled.is_(True),
            )
            .order_by(OperatorTransferEndpoint.endpoint_type, OperatorTransferEndpoint.created_at)
        )
    ).all()
    return [
        OperatorEndpointRead(
            id=row.id,
            endpoint_type=cast(Literal["browser", "sip", "mobile"], row.endpoint_type),
            display_hint=row.display_hint,
            is_verified=row.is_verified,
            is_enabled=row.is_enabled,
            lock_version=row.lock_version,
        )
        for row in rows
    ]


@router.put("/operator/endpoints/{membership_id}", response_model=OperatorEndpointRead)
async def upsert_endpoint(
    membership_id: UUID,
    payload: OperatorEndpointUpsert,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> OperatorEndpointRead:
    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == principal.tenant_id,
            Membership.id == membership_id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None:
        raise ApiError(404, "team_member_not_found", "Team member was not found")
    destination = payload.destination.strip()
    if payload.endpoint_type == "mobile" and not re.fullmatch(r"\+[1-9][0-9]{7,14}", destination):
        raise ApiError(422, "mobile_destination_invalid", "Mobile destination must use E.164")
    if payload.endpoint_type == "sip" and not re.fullmatch(r"sip:[0-9*#-]{1,32}", destination):
        raise ApiError(422, "sip_destination_invalid", "SIP destination must use an allowed extension")
    row = await session.scalar(
        select(OperatorTransferEndpoint).where(
            OperatorTransferEndpoint.tenant_id == principal.tenant_id,
            OperatorTransferEndpoint.membership_id == membership.id,
            OperatorTransferEndpoint.endpoint_type == payload.endpoint_type,
        )
    )
    if row is None:
        row = OperatorTransferEndpoint(
            tenant_id=principal.tenant_id,
            membership_id=membership.id,
            endpoint_type=payload.endpoint_type,
            destination=destination,
            display_hint=payload.display_hint,
            is_verified=False,
            is_enabled=payload.is_enabled,
            lock_version=1,
        )
        session.add(row)
    else:
        if row.destination != destination:
            row.is_verified = False
        row.destination = destination
        row.display_hint = payload.display_hint
        row.is_enabled = payload.is_enabled
        row.lock_version += 1
    await session.commit()
    return OperatorEndpointRead(
        id=row.id,
        endpoint_type=cast(Literal["browser", "sip", "mobile"], row.endpoint_type),
        display_hint=row.display_hint,
        is_verified=row.is_verified,
        is_enabled=row.is_enabled,
        lock_version=row.lock_version,
    )


@router.post("/operator/endpoints/{endpoint_id}/authorize", response_model=OperatorEndpointRead)
async def authorize_endpoint(
    endpoint_id: UUID,
    payload: OperatorEndpointAuthorize,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> OperatorEndpointRead:
    if principal.role != RoleName.TENANT_OWNER:
        raise ApiError(403, "owner_required", "Only the tenant owner can authorize a destination")
    if not payload.confirm_controlled_destination:
        raise ApiError(422, "destination_confirmation_required", "Destination control must be confirmed")
    row = await session.scalar(
        select(OperatorTransferEndpoint)
        .where(
            OperatorTransferEndpoint.tenant_id == principal.tenant_id,
            OperatorTransferEndpoint.id == endpoint_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiError(404, "transfer_endpoint_not_found", "Transfer endpoint was not found")
    if row.lock_version != payload.expected_version:
        raise ApiError(409, "transfer_endpoint_version_conflict", "Transfer endpoint was changed")
    row.is_verified = True
    row.lock_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="transfer.endpoint_authorized",
        resource_type="operator_transfer_endpoint",
        resource_id=row.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"endpoint_type": row.endpoint_type, "membership_id": str(row.membership_id)},
    )
    response = OperatorEndpointRead(
        id=row.id,
        endpoint_type=cast(Literal["browser", "sip", "mobile"], row.endpoint_type),
        display_hint=row.display_hint,
        is_verified=row.is_verified,
        is_enabled=row.is_enabled,
        lock_version=row.lock_version,
    )
    await session.commit()
    return response
