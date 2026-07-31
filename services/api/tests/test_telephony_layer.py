from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from teamora_api.config import Settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import (
    IntegrationStatus,
    RoleName,
    TelephonyCallState,
    TelephonyCommandName,
)
from teamora_api.main import app
from teamora_api.models import (
    Call,
    CallEvent,
    Membership,
    PhoneNumber,
    Project,
    ProjectUser,
    SipTrunk,
    TelephonyCommandSubmission,
    User,
)
from teamora_api.routers import calls as calls_router
from teamora_api.routers import webhooks as webhooks_router
from teamora_api.schemas.telephony import TelephonyCommand
from teamora_api.security import hash_password
from teamora_api.telephony.providers import (
    GatewayTelephonyProvider,
    MockTelephonyProvider,
    ProviderCommandError,
)
from teamora_api.telephony.service import TelephonyService


def mock_settings(*, app_env: str = "test", allow_production: bool = False) -> Settings:
    return Settings.model_construct(
        app_env=app_env,
        allow_mock_telephony_in_production=allow_production,
        gateway_service_token=None,
        gateway_internal_url="http://gateway.invalid",
        gateway_command_timeout_seconds=0.25,
    )


def command(name: TelephonyCommandName, **parameters: str | int | float | bool) -> TelephonyCommand:
    return TelephonyCommand(
        command=name,
        tenantId=uuid4(),
        projectId=uuid4(),
        callId=uuid4(),
        providerCallId="mock:channel",
        commandId=uuid4(),
        idempotencyKey=f"test-{name.value}-key",
        timestamp=datetime.now(UTC),
        correlationId="telephony-test",
        parameters=parameters,
    )


async def create_customer(client: AsyncClient, csrf: str, phone: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Telephony Customer", "phone": phone},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def assigned_customer(client: AsyncClient, csrf: str, phone: str) -> tuple[dict, dict]:
    customer = await create_customer(client, csrf, phone)
    assignment_response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert assignment_response.status_code == 200, assignment_response.text
    return customer, assignment_response.json()


async def start_call(
    client: AsyncClient,
    csrf: str,
    customer: dict,
    assignment: dict,
    *,
    key: str,
) -> dict:
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        json={
            "customer_id": customer["id"],
            "lock_token": assignment["lock_token"],
            "callback_task_id": assignment.get("callback_task_id"),
            "from_number": "MOCK",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_mock_provider_implements_the_full_contract() -> None:
    provider = MockTelephonyProvider(mock_settings())
    originate = await provider.execute(
        command(
            TelephonyCommandName.ORIGINATE,
            fromNumber="MOCK",
            toNumber="+998901234567",
        )
    )
    assert originate.state == TelephonyCallState.RINGING
    assert originate.provider_call_id is not None
    expected = {
        TelephonyCommandName.ANSWER: TelephonyCallState.ACTIVE,
        TelephonyCommandName.HOLD: TelephonyCallState.ON_HOLD,
        TelephonyCommandName.RESUME: TelephonyCallState.ACTIVE,
        TelephonyCommandName.TRANSFER: TelephonyCallState.TRANSFERRED,
        TelephonyCommandName.HANGUP: TelephonyCallState.COMPLETED,
    }
    for name, state in expected.items():
        result = await provider.execute(command(name, destination="operator-queue", reason="test"))
        assert result.state == state
    for name, recording_state in (
        (TelephonyCommandName.START_RECORDING, "recording"),
        (TelephonyCommandName.PAUSE_RECORDING, "paused"),
        (TelephonyCommandName.RESUME_RECORDING, "recording"),
        (TelephonyCommandName.STOP_RECORDING, "stopped"),
    ):
        result = await provider.execute(command(name, currentState="active"))
        assert result.safe_metadata["recordingState"] == recording_state
    media = await provider.execute(command(TelephonyCommandName.CREATE_EXTERNAL_MEDIA, currentState="active"))
    assert str(media.safe_metadata["mediaId"]).startswith("mock-media:")


async def test_production_rejects_mock_without_explicit_flag() -> None:
    try:
        MockTelephonyProvider(mock_settings(app_env="production"))
    except ProviderCommandError as exc:
        assert exc.code == "mock_telephony_forbidden"
    else:
        raise AssertionError("production Mock must be disabled")
    assert MockTelephonyProvider(mock_settings(app_env="production", allow_production=True)).name == "mock"


async def test_router_contains_no_embedded_mock_call_implementation() -> None:
    source = inspect.getsource(calls_router.start_call)
    assert "mock:" not in source
    assert 'provider="mock"' not in source
    assert "MockTelephonyProvider" not in source


async def test_mock_call_commands_and_idempotency(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer, assignment = await assigned_customer(client, csrf, "+998901234561")
    call = await start_call(
        client,
        csrf,
        customer,
        assignment,
        key=f"start-{unique_suffix}",
    )
    assert call["provider"] == "mock"
    assert call["provider_state"] == "ringing"
    repeated = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"start-{unique_suffix}"},
        json={
            "customer_id": customer["id"],
            "lock_token": assignment["lock_token"],
            "from_number": "MOCK",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == call["id"]

    async def action(name: str, *, body: dict | None = None) -> dict:
        response = await client.post(
            f"/api/v1/calls/{call['id']}/{name}",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"{name}-{unique_suffix}",
            },
            json=body,
        )
        assert response.status_code == 200, response.text
        return response.json()

    answered = await action("answer")
    assert answered["provider_state"] == "active"
    held = await action("hold")
    assert held["provider_state"] == "on_hold"
    assert held["status"] == "on_hold"
    held_again = await action("hold")
    assert held_again["provider_state"] == "on_hold"
    resumed = await action("resume")
    assert resumed["provider_state"] == "active"
    assert resumed["status"] == "active"
    transferred = await action(
        "transfer",
        body={"destination": "operator-queue", "reason": "test_transfer"},
    )
    assert transferred["provider_state"] == "transferred"
    assert transferred["status"] == "transferred"
    state = await client.get(f"/api/v1/calls/{call['id']}/state")
    assert state.status_code == 200, state.text
    assert state.json()["state"] == "transferred"
    completed = await action("hangup")
    assert completed["status"] == "completed"

    tenant_id = UUID(str(auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        command_count = int(
            await session.scalar(
                select(func.count())
                .select_from(TelephonyCommandSubmission)
                .where(
                    TelephonyCommandSubmission.tenant_id == tenant_id,
                    TelephonyCommandSubmission.call_id == UUID(call["id"]),
                )
            )
            or 0
        )
        hold_count = int(
            await session.scalar(
                select(func.count())
                .select_from(TelephonyCommandSubmission)
                .where(
                    TelephonyCommandSubmission.tenant_id == tenant_id,
                    TelephonyCommandSubmission.call_id == UUID(call["id"]),
                    TelephonyCommandSubmission.command_name == "hold",
                )
            )
            or 0
        )
    assert command_count == 6
    assert hold_count == 1


async def test_hold_resume_can_repeat_without_explicit_idempotency_key(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    _auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer, assignment = await assigned_customer(client, csrf, "+998901234562")
    call = await start_call(
        client,
        csrf,
        customer,
        assignment,
        key=f"start-repeat-{unique_suffix}",
    )
    answered = await client.post(
        f"/api/v1/calls/{call['id']}/answer",
        headers={"X-CSRF-Token": csrf},
    )
    assert answered.status_code == 200, answered.text

    for _ in range(2):
        held = await client.post(
            f"/api/v1/calls/{call['id']}/hold",
            headers={"X-CSRF-Token": csrf},
        )
        assert held.status_code == 200, held.text
        assert held.json()["provider_state"] == "on_hold"

        resumed = await client.post(
            f"/api/v1/calls/{call['id']}/resume",
            headers={"X-CSRF-Token": csrf},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["provider_state"] == "active"


async def test_provider_is_selected_from_project_telephony_configuration(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Test trunk",
            provider_host="ari.invalid",
            max_channels=2,
            status=IntegrationStatus.CONFIGURED,
        )
        session.add(trunk)
        await session.flush()
        number = PhoneNumber(
            tenant_id=tenant_id,
            sip_trunk_id=trunk.id,
            e164="+998799999991",
            label="Outbound",
            is_active=True,
        )
        session.add(number)
        await session.flush()
        project.outbound_phone_number_id = number.id
        selection = await TelephonyService(mock_settings()).select_provider(
            session,
            tenant_id=tenant_id,
            project=project,
        )
        assert selection.provider.name == "asterisk-ari"
        assert selection.from_number == number.e164


async def test_operator_and_other_tenant_cannot_control_foreign_call(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer, assignment = await assigned_customer(client, csrf, "+998931234561")
    call = await start_call(
        client,
        csrf,
        customer,
        assignment,
        key=f"ownership-start-{unique_suffix}",
    )
    tenant_id = UUID(str(auth["tenant"]["id"]))
    operator_email = f"telephony-operator+{unique_suffix}@example.com"
    operator_password = "OperatorPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        user = User(
            email=operator_email,
            display_name="Other operator",
            password_hash=hash_password(operator_password),
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Membership(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=RoleName.HUMAN_OPERATOR,
                ),
                ProjectUser(
                    tenant_id=tenant_id,
                    project_id=project.id,
                    user_id=user.id,
                    is_active=True,
                ),
            ]
        )
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "company_slug": auth["tenant"]["slug"],
            "email": operator_email,
            "password": operator_password,
        },
    )
    assert login.status_code == 200, login.text
    operator_csrf = login.json()["csrf_token"]
    forbidden = await client.post(
        f"/api/v1/calls/{call['id']}/answer",
        headers={"X-CSRF-Token": operator_csrf},
    )
    assert forbidden.status_code == 404

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other_tenant:
        other_auth, other_csrf = await register_tenant(  # type: ignore[operator]
            other_tenant, f"other-{unique_suffix}"
        )
        assert other_auth["tenant"]["id"] != auth["tenant"]["id"]
        hidden = await other_tenant.post(
            f"/api/v1/calls/{call['id']}/answer",
            headers={"X-CSRF-Token": other_csrf},
        )
        assert hidden.status_code == 404


async def test_project_without_telephony_is_rejected_outside_dev(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        try:
            await TelephonyService(mock_settings(app_env="staging")).select_provider(
                session,
                tenant_id=tenant_id,
                project=project,
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "telephony_not_configured"
        else:
            raise AssertionError("project without telephony must be rejected")


async def test_gateway_errors_are_stable_and_timeout_is_bounded() -> None:
    settings = Settings.model_construct(
        gateway_service_token=SecretStr("gateway-service-token-for-tests"),
        gateway_internal_url="http://gateway.test",
        gateway_command_timeout_seconds=0.25,
    )
    unavailable = GatewayTelephonyProvider(
        settings,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"error": {"code": "asterisk_not_configured"}})
        ),
    )
    try:
        await unavailable.execute(command(TelephonyCommandName.ANSWER))
    except ProviderCommandError as exc:
        assert exc.code == "asterisk_not_configured"
    else:
        raise AssertionError("gateway failure must be mapped")

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("bounded timeout")

    timed_out = GatewayTelephonyProvider(settings, transport=httpx.MockTransport(timeout))
    try:
        await timed_out.execute(command(TelephonyCommandName.ANSWER))
    except ProviderCommandError as exc:
        assert exc.code == "gateway_timeout"
    else:
        raise AssertionError("gateway timeout must be mapped")


def webhook_signature(secret: str, webhook_id: str, timestamp: str, payload: bytes) -> str:
    signed = f"{webhook_id}.{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


async def test_provider_event_idempotency_and_tenant_scope(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
    monkeypatch: object,
) -> None:
    register_tenant = register
    auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer, assignment = await assigned_customer(client, csrf, "+998911234561")
    call = await start_call(
        client,
        csrf,
        customer,
        assignment,
        key=f"event-start-{unique_suffix}",
    )
    secret = "telephony-webhook-secret-for-tests"  # noqa: S105 - isolated test credential
    settings = Settings.model_construct(gateway_service_token=SecretStr(secret))
    monkeypatch.setattr(webhooks_router, "get_settings", lambda: settings)  # type: ignore[attr-defined]
    event = {
        "version": "1",
        "provider": "mock",
        "providerEventId": f"provider-event-{unique_suffix}",
        "eventType": "telephony.active",
        "tenantId": auth["tenant"]["id"],
        "projectId": call["project_id"],
        "callId": call["id"],
        "externalCallId": call["provider_call_id"],
        "occurredAt": datetime.now(UTC).isoformat(),
        "providerTimestamp": datetime.now(UTC).isoformat(),
        "correlationId": f"provider-{unique_suffix}",
        "safePayload": {"state": "active"},
    }

    async def send(value: dict) -> httpx.Response:
        payload = json.dumps(value, separators=(",", ":")).encode()
        webhook_id = f"evt-{unique_suffix}"
        timestamp = str(int(time.time()))
        return await client.post(
            "/api/v1/webhooks/telephony",
            content=payload,
            headers={
                "content-type": "application/json",
                "x-teamora-id": webhook_id,
                "x-teamora-timestamp": timestamp,
                "x-teamora-signature": webhook_signature(secret, webhook_id, timestamp, payload),
            },
        )

    first = await send(event)
    assert first.status_code == 202, first.text
    assert first.json()["duplicate"] is False
    repeated = await send(event)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["duplicate"] is True

    forged = {**event, "providerEventId": f"forged-{unique_suffix}", "tenantId": str(uuid4())}
    rejected = await send(forged)
    assert rejected.status_code == 404

    tenant_id = UUID(str(auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(CallEvent)
                .where(
                    CallEvent.tenant_id == tenant_id,
                    CallEvent.provider_event_id == event["providerEventId"],
                )
            )
            or 0
        )
        stored_call = await session.scalar(
            select(Call).where(Call.tenant_id == tenant_id, Call.id == UUID(call["id"]))
        )
    assert count == 1
    assert stored_call is not None and stored_call.provider_state == "active"
