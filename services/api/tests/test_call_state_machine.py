from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select

from teamora_api.audit import normalize_audit_correlation_id
from teamora_api.call_state import (
    ALLOWED_TRANSITIONS,
    CAPACITY_CALL_STATES,
    TERMINAL_CALL_STATES,
    CallStateService,
    allowed_actions,
    validate_transition,
)
from teamora_api.config import Settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import (
    CallChannel,
    CallDirection,
    CallerType,
    CallStatus,
    HangupCause,
    IntegrationStatus,
    TelephonyCallState,
    TelephonyCommandName,
)
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallEvent, Project, SipTrunk, TelephonyDiagnosticRun
from teamora_api.schemas.telephony import TelephonyCommand
from teamora_api.telephony.providers import (
    MockTelephonyProvider,
    ProviderCommandError,
)
from teamora_api.telephony.service import ProviderSelection, TelephonyService


def test_long_audit_correlation_id_is_stable_and_schema_safe() -> None:
    source = "simulator-finish:" + "x" * 160
    normalized = normalize_audit_correlation_id(source)
    assert len(normalized) == 80
    assert normalized == normalize_audit_correlation_id(source)
    assert normalized != normalize_audit_correlation_id(source + "other")


def test_transition_table_accepts_only_documented_edges() -> None:
    assert CallStatus.RINGING in ALLOWED_TRANSITIONS[CallStatus.QUEUED]
    for current in CallStatus:
        for target in CallStatus:
            if current == target:
                continue
            if target in ALLOWED_TRANSITIONS[current]:
                validate_transition(current, target)
            else:
                try:
                    validate_transition(current, target)
                except ApiError as exc:
                    assert exc.status_code == 409
                    assert exc.code == "call_state_transition_invalid"
                else:
                    raise AssertionError(f"unexpected transition {current} -> {target}")


def test_terminal_states_never_return_to_active_and_release_capacity() -> None:
    assert TERMINAL_CALL_STATES.isdisjoint(CAPACITY_CALL_STATES)
    for state in TERMINAL_CALL_STATES:
        assert not ALLOWED_TRANSITIONS[state]
        try:
            validate_transition(state, CallStatus.ACTIVE)
        except ApiError as exc:
            assert exc.code == "call_state_transition_invalid"
        else:
            raise AssertionError(f"terminal state {state} returned to active")


def test_allowed_actions_follow_canonical_state() -> None:
    assert allowed_actions(CallStatus.RINGING, recording_state="stopped") == [
        "answer",
        "hangup",
    ]
    assert {"hold", "transfer", "hangup", "start_recording"} == set(
        allowed_actions(CallStatus.ACTIVE, recording_state="stopped")
    )
    assert {
        "resume",
        "transfer",
        "hangup",
        "resume_recording",
        "stop_recording",
    } == set(allowed_actions(CallStatus.ON_HOLD, recording_state="paused"))
    for state in TERMINAL_CALL_STATES:
        assert allowed_actions(state, recording_state="stopped") == []


async def test_mock_provider_supports_deterministic_terminal_states() -> None:
    settings = Settings.model_construct(
        app_env="test",
        allow_mock_telephony_in_production=False,
    )
    provider = MockTelephonyProvider(settings)

    for simulation, expected in (
        ("busy", TelephonyCallState.BUSY),
        ("no_answer", TelephonyCallState.NO_ANSWER),
        ("failed", TelephonyCallState.FAILED),
    ):
        result = await provider.execute(_command(TelephonyCommandName.ORIGINATE, simulate=simulation))
        assert result.state == expected
    cancelled = await provider.execute(_command(TelephonyCommandName.HANGUP, reason="cancelled"))
    assert cancelled.state == TelephonyCallState.CANCELLED


async def test_timestamps_versions_and_provider_event_ordering(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    actor_id = UUID(str(auth["user"]["id"]))
    now = datetime.now(UTC)

    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.is_default.is_(True),
            )
        )
        assert project is not None
        call = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.DEVELOPMENT_SIMULATOR,
            status=CallStatus.QUEUED,
            direction=CallDirection.INBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            provider="mock",
            provider_state=CallStatus.QUEUED.value,
            is_demo=True,
        )
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Live diagnostic test trunk",
            provider_host="sip.invalid.test",
            auth_mode="ip",
            max_channels=1,
            status=IntegrationStatus.CONFIGURED,
        )
        session.add_all([call, trunk])
        await session.flush()
        diagnostic = TelephonyDiagnosticRun(
            tenant_id=tenant_id,
            project_id=project.id,
            sip_trunk_id=trunk.id,
            call_id=call.id,
            requested_by_user_id=actor_id,
            mode="live",
            status="pending",
            correlation_id=f"live-state-test-{unique_suffix}",
        )
        session.add(diagnostic)
        await session.flush()
        service = CallStateService()
        await service.transition(
            session,
            call=call,
            target=CallStatus.RINGING,
            event_type="call.ringing",
            occurred_at=now,
            correlation_id="state-test-ringing",
            actor_user_id=actor_id,
        )
        ringing_at = call.ringing_at
        assert diagnostic.signaling_verified is False
        assert diagnostic.status == "pending"
        await service.ingest_provider_event(
            session,
            call=call,
            event_type="call.answered",
            provider="mock",
            provider_event_id="state-test-answer",
            occurred_at=now + timedelta(seconds=1),
            provider_timestamp=now + timedelta(seconds=1),
            external_call_id="mock:state-test",
            correlation_id="state-test-answer",
            safe_payload={"state": "active"},
        )
        assert call.status == CallStatus.ACTIVE
        assert call.ringing_at == ringing_at
        assert call.answered_at == now + timedelta(seconds=1)
        assert call.state_version == 2
        assert diagnostic.signaling_verified is True
        assert diagnostic.status == "live_signaling_verified"
        assert diagnostic.completed_at == now + timedelta(seconds=1)

        duplicate = await service.ingest_provider_event(
            session,
            call=call,
            event_type="call.answered",
            provider="mock",
            provider_event_id="state-test-answer",
            occurred_at=now + timedelta(seconds=1),
            provider_timestamp=now + timedelta(seconds=1),
            external_call_id="mock:state-test",
            correlation_id="state-test-answer-replay",
            safe_payload={"state": "active"},
        )
        assert duplicate.ignored_reason == "duplicate"
        assert call.state_version == 2

        stale = await service.ingest_provider_event(
            session,
            call=call,
            event_type="call.ringing",
            provider="mock",
            provider_event_id="state-test-stale",
            occurred_at=now,
            provider_timestamp=now,
            external_call_id="mock:state-test",
            correlation_id="state-test-stale",
            safe_payload={"state": "ringing"},
        )
        assert stale.ignored_reason == "out_of_order"
        assert call.status == CallStatus.ACTIVE

        unknown = await service.ingest_provider_event(
            session,
            call=call,
            event_type="vendor.unknown",
            provider="mock",
            provider_event_id="state-test-unknown",
            occurred_at=now + timedelta(seconds=2),
            provider_timestamp=now + timedelta(seconds=2),
            external_call_id="mock:state-test",
            correlation_id="state-test-unknown",
            safe_payload={},
        )
        assert unknown.ignored_reason == "unknown_event"

        await service.transition(
            session,
            call=call,
            target=CallStatus.COMPLETED,
            event_type="call.hangup",
            occurred_at=now + timedelta(seconds=5),
            correlation_id="state-test-complete",
            actor_user_id=actor_id,
            hangup_cause=HangupCause.CALLER_HANGUP,
            raw_provider_cause="NORMAL_CLEARING",
        )
        assert call.ended_at == now + timedelta(seconds=5)
        assert call.hangup_cause == HangupCause.CALLER_HANGUP
        assert call.raw_provider_cause == "NORMAL_CLEARING"
        assert call.state_version == 3
        late_answer = await service.ingest_provider_event(
            session,
            call=call,
            event_type="call.answered",
            provider="mock",
            provider_event_id="state-test-late-answer",
            occurred_at=now + timedelta(seconds=6),
            provider_timestamp=now + timedelta(seconds=6),
            external_call_id="mock:state-test",
            correlation_id="state-test-late-answer",
            safe_payload={"state": "active"},
        )
        assert late_answer.ignored_reason == "transition_conflict"
        assert call.status == CallStatus.COMPLETED
        assert call.state_version == 3
        await session.flush()
        event_count = int(
            await session.scalar(
                select(func.count())
                .select_from(CallEvent)
                .where(CallEvent.tenant_id == tenant_id, CallEvent.call_id == call.id)
            )
            or 0
        )
        assert event_count == 6


async def test_api_state_version_conflict_and_full_mock_flow(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
) -> None:
    register_tenant = register
    _auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer_response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "State Customer", "phone": "+998901110022"},
    )
    customer = customer_response.json()
    assignment_response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assignment = assignment_response.json()
    started_response = await client.post(
        "/api/v1/calls/start",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"state-start-{unique_suffix}",
        },
        json={
            "customer_id": customer["id"],
            "lock_token": assignment["lock_token"],
            "from_number": "MOCK",
        },
    )
    assert started_response.status_code == 201, started_response.text
    call = started_response.json()
    assert call["status"] == "ringing"
    assert call["ringing_at"] is not None

    reconciled = await client.post(
        f"/api/v1/calls/{call['id']}/reconcile",
        headers={
            "X-CSRF-Token": csrf,
            "X-Call-State-Version": str(call["state_version"]),
        },
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["provider_state"] == "ringing"
    assert reconciled.json()["reconciled"] is False
    assert reconciled.json()["ignored_reason"] == "already_consistent"

    conflict = await client.post(
        f"/api/v1/calls/{call['id']}/answer",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"state-answer-conflict-{unique_suffix}",
            "X-Call-State-Version": "0",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "call_state_version_conflict"

    async def action(name: str, current: dict, body: dict | None = None) -> dict:
        response = await client.post(
            f"/api/v1/calls/{current['id']}/{name}",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"state-{name}-{unique_suffix}",
                "X-Call-State-Version": str(current["state_version"]),
            },
            json=body,
        )
        assert response.status_code == 200, response.text
        return response.json()

    call = await action("answer", call)
    assert call["status"] == "active"
    assert call["answered_at"] is not None
    call = await action("hold", call)
    assert call["status"] == "on_hold"
    call = await action("resume", call)
    assert call["status"] == "active"
    call = await action(
        "transfer",
        call,
        {"destination": "operator-queue", "reason": "state_test"},
    )
    assert call["status"] == "transferred"

    state = await client.get(f"/api/v1/calls/{call['id']}/state")
    assert state.status_code == 200, state.text
    state_body = state.json()
    assert state_body["state"] == "transferred"
    assert state_body["terminal"] is False
    assert "hold" in state_body["allowed_actions"]
    assert state_body["last_event"]["event_type"] == "transfer.completed"

    call = await action("hangup", call)
    assert call["status"] == "completed"
    assert call["hangup_cause"] == "operator_hangup"
    assert call["ended_at"] is not None
    rejected = await client.post(
        f"/api/v1/calls/{call['id']}/answer",
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 409


async def test_mock_call_can_be_cancelled_before_answer(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
) -> None:
    register_tenant = register
    _auth, csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    customer_response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Cancelled Customer", "phone": "+998901110033"},
    )
    assignment_response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    started_response = await client.post(
        "/api/v1/calls/start",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"cancel-start-{unique_suffix}",
        },
        json={
            "customer_id": customer_response.json()["id"],
            "lock_token": assignment_response.json()["lock_token"],
            "from_number": "MOCK",
        },
    )
    call = started_response.json()
    cancelled = await client.post(
        f"/api/v1/calls/{call['id']}/cancel",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"cancel-command-{unique_suffix}",
            "X-Call-State-Version": str(call["state_version"]),
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["hangup_cause"] == "cancelled"


class FailingProvider:
    name = "mock"

    async def execute(self, _command: TelephonyCommand):
        raise ProviderCommandError(
            "provider_test_failure",
            "Provider failed in an isolated test",
        )


async def test_failed_provider_command_does_not_fake_state_change(
    client: AsyncClient,
    unique_suffix: str,
    register: object,
) -> None:
    register_tenant = register
    auth, _csrf = await register_tenant(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    actor_id = UUID(str(auth["user"]["id"]))
    settings = Settings.model_construct(
        app_env="test",
        allow_mock_telephony_in_production=False,
        gateway_command_timeout_seconds=0.25,
    )
    async with SessionFactory() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.is_default.is_(True),
            )
        )
        assert project is not None
        call = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.DEVELOPMENT_SIMULATOR,
            status=CallStatus.ACTIVE,
            direction=CallDirection.OUTBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            provider="mock",
            provider_state=CallStatus.ACTIVE.value,
            is_demo=True,
        )
        session.add(call)
        await session.flush()
        provider = FailingProvider()
        telephony = TelephonyService(settings, mock_provider=provider)
        try:
            await telephony.execute(
                session,
                call=call,
                project=project,
                selection=ProviderSelection(
                    provider=provider,
                    from_number="MOCK",
                    channel=CallChannel.DEVELOPMENT_SIMULATOR,
                    is_demo=True,
                ),
                command_name=TelephonyCommandName.HOLD,
                correlation_id=f"provider-failure-{unique_suffix}",
                actor_user_id=actor_id,
            )
        except ApiError as exc:
            assert exc.code == "provider_test_failure"
        else:
            raise AssertionError("failing provider unexpectedly succeeded")
        assert call.status == CallStatus.ACTIVE


def _command(
    name: TelephonyCommandName,
    **parameters: str | int | float | bool,
) -> TelephonyCommand:
    return TelephonyCommand(
        command=name,
        tenantId=uuid4(),
        projectId=uuid4(),
        callId=uuid4(),
        providerCallId="mock:state-machine",
        commandId=uuid4(),
        idempotencyKey=f"state-machine-{name.value}",
        timestamp=datetime.now(UTC),
        correlationId="state-machine-test",
        parameters=parameters,
    )
