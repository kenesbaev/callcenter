from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from teamora_api.config import get_settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import (
    CallChannel,
    CallDirection,
    CallerType,
    CallStatus,
    IntegrationStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    PhoneNumber,
    Project,
    ProjectInboundPhoneNumber,
    SipTrunk,
    TelephonyChannelReservation,
)
from teamora_api.routers import webhooks as webhooks_router
from teamora_api.telephony.control import (
    finalize_cdr,
    mask_number,
    release_call_channel,
    release_transfer_channel,
    reserve_channel,
)


async def _default_project(tenant_id: UUID) -> Project:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.is_default.is_(True),
            )
        )
        assert project is not None
        session.expunge(project)
        return project


def _call(*, tenant_id: UUID, project_id: UUID, direction: CallDirection) -> Call:
    return Call(
        tenant_id=tenant_id,
        project_id=project_id,
        channel=CallChannel.SIP,
        status=CallStatus.RINGING,
        direction=direction,
        caller_type=CallerType.HUMAN_OPERATOR,
        provider="asterisk-ari",
        provider_state="ringing",
        from_number="+998711234567",
        to_number="+998901234567",
        is_demo=False,
    )


async def test_channel_limit_reservation_release_and_masked_cdr(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    project = await _default_project(tenant_id)
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Single local test channel",
            provider_host="sip-emulator",
            provider_port=5060,
            transport="udp",
            auth_mode="ip",
            allowed_ips=["127.0.0.0/8"],
            codecs=["ulaw"],
            dtmf_mode="rfc4733",
            max_channels=1,
            status=IntegrationStatus.CONFIGURED,
        )
        first = _call(
            tenant_id=tenant_id,
            project_id=project.id,
            direction=CallDirection.INBOUND,
        )
        second = _call(
            tenant_id=tenant_id,
            project_id=project.id,
            direction=CallDirection.OUTBOUND,
        )
        session.add_all([trunk, first, second])
        await session.flush()
        reservation = await reserve_channel(
            session,
            call=first,
            trunk_id=trunk.id,
            direction=CallDirection.INBOUND,
        )
        with pytest.raises(ApiError) as exhausted:
            await reserve_channel(
                session,
                call=second,
                trunk_id=trunk.id,
                direction=CallDirection.OUTBOUND,
            )
        assert exhausted.value.code == "sip_channel_limit"

        await release_call_channel(session, call=first, reason="normal")
        replacement = await reserve_channel(
            session,
            call=second,
            trunk_id=trunk.id,
            direction=CallDirection.OUTBOUND,
        )
        assert reservation.status == "released"
        assert replacement.status == "reserved"

        started = datetime.now(UTC) - timedelta(seconds=65)
        first.started_at = started
        first.answered_at = started + timedelta(seconds=5)
        first.ended_at = started + timedelta(seconds=65)
        first.status = CallStatus.COMPLETED
        first.provider_metadata = {"codec": "ulaw", "password": "must-not-copy"}
        first.sip_trunk_id = trunk.id
        cdr = await finalize_cdr(session, call=first)
        assert cdr is not None
        await session.flush()
        assert cdr.destination_masked == mask_number(first.from_number)
        assert cdr.destination_masked != first.from_number
        assert cdr.duration_seconds == 65
        assert cdr.billable_duration_seconds is None
        assert cdr.codec == "ulaw"
        assert cdr.safe_provider_metadata == {}


async def test_mobile_transfer_atomically_reserves_and_releases_second_channel(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    auth, _csrf = await register(client, f"transfer-channel-{unique_suffix}")  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    project = await _default_project(tenant_id)
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Two channel transfer trunk",
            provider_host="sip-emulator",
            provider_port=5060,
            transport="udp",
            auth_mode="ip",
            allowed_ips=["127.0.0.0/8"],
            codecs=["ulaw"],
            dtmf_mode="rfc4733",
            max_channels=2,
            status=IntegrationStatus.CONFIGURED,
        )
        call = _call(
            tenant_id=tenant_id,
            project_id=project.id,
            direction=CallDirection.INBOUND,
        )
        competing = _call(
            tenant_id=tenant_id,
            project_id=project.id,
            direction=CallDirection.OUTBOUND,
        )
        session.add_all([trunk, call, competing])
        await session.flush()

        customer_leg = await reserve_channel(
            session,
            call=call,
            trunk_id=trunk.id,
            direction=CallDirection.INBOUND,
        )
        operator_leg = await reserve_channel(
            session,
            call=call,
            trunk_id=trunk.id,
            direction=CallDirection.OUTBOUND,
            purpose="operator_transfer",
        )
        replay = await reserve_channel(
            session,
            call=call,
            trunk_id=trunk.id,
            direction=CallDirection.OUTBOUND,
            purpose="operator_transfer",
        )
        assert replay.id == operator_leg.id
        assert customer_leg.id != operator_leg.id
        with pytest.raises(ApiError) as exhausted:
            await reserve_channel(
                session,
                call=competing,
                trunk_id=trunk.id,
                direction=CallDirection.OUTBOUND,
            )
        assert exhausted.value.code == "sip_channel_limit"

        await release_transfer_channel(session, call=call, reason="operator_no_answer")
        replacement = await reserve_channel(
            session,
            call=competing,
            trunk_id=trunk.id,
            direction=CallDirection.OUTBOUND,
        )
        assert operator_leg.status == "released"
        assert customer_leg.status == "reserved"
        assert replacement.status == "reserved"


def _signature(secret: str, webhook_id: str, timestamp: str, payload: bytes) -> str:
    digest = hmac.new(
        secret.encode(),
        f"{webhook_id}.{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).digest()
    return "v1," + base64.b64encode(digest).decode()


async def test_inbound_did_routing_source_allowlist_and_idempotency(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    project = await _default_project(tenant_id)
    did = f"+99871{int(unique_suffix[:8], 16) % 10000000:07d}"
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Inbound local emulator",
            provider_host="sip-emulator",
            provider_port=5060,
            transport="udp",
            auth_mode="ip",
            allowed_ips=["127.0.0.0/8"],
            codecs=["ulaw"],
            dtmf_mode="rfc4733",
            max_channels=2,
            status=IntegrationStatus.CONFIGURED,
        )
        session.add(trunk)
        await session.flush()
        phone = PhoneNumber(
            tenant_id=tenant_id,
            sip_trunk_id=trunk.id,
            e164=did,
            label="Local inbound DID",
            is_active=True,
        )
        session.add(phone)
        await session.flush()
        session.add(
            ProjectInboundPhoneNumber(
                tenant_id=tenant_id,
                project_id=project.id,
                phone_number_id=phone.id,
            )
        )

    secret = "local-sip-webhook-secret-for-tests"  # noqa: S105
    settings = get_settings().model_copy(update={"gateway_service_token": SecretStr(secret)})
    monkeypatch.setattr(webhooks_router, "get_settings", lambda: settings)

    async def send(*, event_id: str, source_ip: str, channel_id: str) -> object:
        event = {
            "version": "1",
            "provider": "asterisk-ari",
            "providerEventId": event_id,
            "channelId": channel_id,
            "did": did,
            "callerNumber": "+998901234567",
            "sourceIp": source_ip,
            "occurredAt": datetime.now(UTC).isoformat(),
            "correlationId": f"correlation-{event_id}",
            "safePayload": {"codec": "ulaw"},
        }
        raw = json.dumps(event, separators=(",", ":")).encode()
        webhook_id = f"webhook-{event_id}"
        timestamp = str(int(time.time()))
        return await client.post(
            "/api/v1/webhooks/telephony/inbound",
            content=raw,
            headers={
                "content-type": "application/json",
                "x-teamora-id": webhook_id,
                "x-teamora-timestamp": timestamp,
                "x-teamora-signature": _signature(secret, webhook_id, timestamp, raw),
            },
        )

    rejected = await send(
        event_id=f"spoof-{unique_suffix}",
        source_ip="203.0.113.55",
        channel_id=f"spoof-channel-{unique_suffix}",
    )
    assert rejected.status_code == 403  # type: ignore[attr-defined]

    first = await send(
        event_id=f"inbound-{unique_suffix}",
        source_ip="127.0.0.1",
        channel_id=f"channel-{unique_suffix}",
    )
    assert first.status_code == 202, first.text  # type: ignore[attr-defined]
    assert first.json()["duplicate"] is False  # type: ignore[attr-defined]
    repeated = await send(
        event_id=f"inbound-{unique_suffix}",
        source_ip="127.0.0.1",
        channel_id=f"channel-{unique_suffix}",
    )
    assert repeated.status_code == 202  # type: ignore[attr-defined]
    assert repeated.json()["duplicate"] is True  # type: ignore[attr-defined]

    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        created = await session.scalar(
            select(Call).where(
                Call.tenant_id == tenant_id,
                Call.external_call_id == f"channel-{unique_suffix}",
            )
        )
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(TelephonyChannelReservation)
                .where(
                    TelephonyChannelReservation.tenant_id == tenant_id,
                    TelephonyChannelReservation.call_id == created.id,
                )
            )
            or 0
        )
    assert created is not None
    assert created.status == CallStatus.RINGING
    assert created.channel == CallChannel.SIP
    assert count == 1


async def test_controlled_live_call_is_disabled_without_explicit_configuration(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    project = await _default_project(tenant_id)
    response = await client.post(
        "/api/v1/telephony/diagnostics/live",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"live-{unique_suffix}"},
        json={
            "project_id": str(project.id),
            "destination": "+998901234567",
            "confirmation": "CALL_ALLOWED_TEST_NUMBER",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "live_test_disabled"


async def test_telephony_status_is_tenant_scoped(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        session.add(
            SipTrunk(
                tenant_id=tenant_id,
                name="Visible trunk",
                provider_host="sip-emulator",
                provider_port=5060,
                transport="udp",
                auth_mode="ip",
                allowed_ips=["127.0.0.1/32"],
                codecs=["ulaw"],
                max_channels=3,
                status=IntegrationStatus.CONFIGURED,
            )
        )
    response = await client.get("/api/v1/telephony/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "configured"
    assert body["deployment_mode"] == "direct"
    assert body["media_gateway_placement"] == "platform"
    assert body["edge_connectivity"] == "not_applicable"
    assert body["browser_webrtc"] == "configured_live_verification_required"
    assert body["live_signaling_verified"] is False
    assert body["live_audio_verified"] is False
    assert body["channel_usage"][0]["occupied"] == 0
    assert body["channel_usage"][0]["available"] == 3
