from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import (
    CallChannel,
    CallDirection,
    CallerType,
    CallResultCategory,
    CallStatus,
    RoleName,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)
from teamora_api.main import app
from teamora_api.models import (
    Call,
    CallbackTask,
    CallEvent,
    CallOutcome,
    CallResultDefinition,
    Customer,
    Membership,
    Project,
    ProjectUser,
    UsageRecord,
    User,
)
from teamora_api.security import hash_password

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def seed_known_analytics(auth: dict[str, object]) -> tuple[UUID, UUID]:
    tenant = auth["tenant"]
    user = auth["user"]
    assert isinstance(tenant, dict) and isinstance(user, dict)
    tenant_id = UUID(str(tenant["id"]))
    user_id = UUID(str(user["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        definition = await session.scalar(
            select(CallResultDefinition).where(
                CallResultDefinition.tenant_id == tenant_id,
                CallResultDefinition.project_id == project.id,
                CallResultDefinition.category == CallResultCategory.SUCCESSFUL,
            )
        )
        assert definition is not None
        now = datetime.now(UTC).replace(microsecond=0)
        customer = Customer(
            tenant_id=tenant_id,
            project_id=project.id,
            display_name="Analytics Customer",
            status="active",
        )
        session.add(customer)
        await session.flush()
        completed = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.SIP,
            status=CallStatus.COMPLETED,
            direction=CallDirection.OUTBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            customer_id=customer.id,
            operator_user_id=user_id,
            language="ru",
            provider="mock",
            provider_state="completed",
            started_at=now - timedelta(minutes=10),
            ringing_at=now - timedelta(minutes=9, seconds=50),
            answered_at=now - timedelta(minutes=9),
            ended_at=now - timedelta(minutes=7),
            duration_seconds=120,
            to_number="+998901234567",
        )
        no_answer = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.SIP,
            status=CallStatus.NO_ANSWER,
            direction=CallDirection.OUTBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            customer_id=customer.id,
            operator_user_id=user_id,
            language="uz",
            provider="mock",
            provider_state="no_answer",
            started_at=now - timedelta(minutes=6),
            ringing_at=now - timedelta(minutes=5, seconds=50),
            ended_at=now - timedelta(minutes=5),
            duration_seconds=0,
            to_number="+998909876543",
        )
        active_ai = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.DEVELOPMENT_SIMULATOR,
            status=CallStatus.ACTIVE,
            direction=CallDirection.INBOUND,
            caller_type=CallerType.AI_AGENT,
            customer_id=customer.id,
            language="kaa",
            provider="mock",
            provider_state="active",
            started_at=now - timedelta(minutes=4),
            answered_at=now - timedelta(minutes=3),
            duration_seconds=0,
            from_number="+998900000001",
            is_demo=True,
        )
        queued = Call(
            tenant_id=tenant_id,
            project_id=project.id,
            channel=CallChannel.SIP,
            status=CallStatus.QUEUED,
            direction=CallDirection.OUTBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            provider="mock",
            provider_state="queued",
        )
        session.add_all([completed, no_answer, active_ai, queued])
        await session.flush()
        session.add(
            CallOutcome(
                tenant_id=tenant_id,
                project_id=project.id,
                call_id=completed.id,
                result_definition_id=definition.id,
                code=definition.system_code,
                label="Snapshot success",
                category=CallResultCategory.SUCCESSFUL,
                color="#16A34A",
                label_translations={"ru": "Snapshot success"},
            )
        )
        session.add_all(
            [
                CallEvent(
                    tenant_id=tenant_id,
                    call_id=completed.id,
                    event_type="transfer.requested",
                    sequence=1,
                    occurred_at=now - timedelta(minutes=8),
                ),
                CallEvent(
                    tenant_id=tenant_id,
                    call_id=completed.id,
                    event_type="transfer.completed",
                    sequence=2,
                    occurred_at=now - timedelta(minutes=7),
                ),
                UsageRecord(
                    tenant_id=tenant_id,
                    call_id=active_ai.id,
                    metric="ai_minutes",
                    quantity=Decimal("2.5"),
                    unit="minute",
                    estimated_cost_usd=Decimal("0"),
                    idempotency_key=f"analytics-usage-{uuid4()}",
                    occurred_at=now - timedelta(minutes=2),
                ),
                CallbackTask(
                    tenant_id=tenant_id,
                    project_id=project.id,
                    customer_id=customer.id,
                    task_type=TaskType.CALLBACK,
                    title="Overdue callback",
                    priority=TaskPriority.NORMAL,
                    status=TaskStatus.PENDING,
                    source=TaskSource.MANUAL,
                    created_by_user_id=user_id,
                    assigned_user_id=user_id,
                    due_at=now - timedelta(hours=1),
                ),
            ]
        )
        return project.id, user_id


async def test_exact_metrics_snapshots_usage_and_masking(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _csrf = await register(client, unique_suffix)
    project_id, _ = await seed_known_analytics(auth)
    today = datetime.now(UTC).date().isoformat()
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    response = await client.get(
        f"/api/v1/analytics/overview?project_id={project_id}&date_from={today}&date_to={tomorrow}&timezone=UTC"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    summary = payload["summary"]
    assert summary["attempted_calls"]["value"] == 3
    assert summary["connected_calls"]["value"] == 2
    assert round(summary["answer_rate"]["percentage_change"] or 0, 2) == 0
    assert round(summary["answer_rate"]["value"], 2) == 66.67
    assert summary["answer_rate"]["numerator"] == 2
    assert summary["answer_rate"]["denominator"] == 3
    assert summary["successful_calls"]["value"] == 1
    assert summary["success_rate"]["value"] == 100
    assert summary["average_duration_seconds"]["value"] == 120
    assert summary["active_calls"]["value"] == 1
    assert summary["ai_calls"]["value"] == 1
    assert summary["human_calls"]["value"] == 2
    assert summary["simulator_calls"]["value"] == 1
    assert summary["sip_calls"]["value"] == 2
    assert summary["transfers"]["requested"]["value"] == 1
    assert summary["transfers"]["successful"]["value"] == 1
    assert summary["ai_minutes"]["value"] == 2.5
    assert summary["ai_cost_usd"]["value"] is None
    assert "missing_usage_price" in summary["ai_cost_usd"]["data_quality_flags"]
    assert "test_data_present" in summary["data_quality_flags"]
    assert payload["tasks"]["overdue"] == 1
    assert payload["outcomes"][0]["label"] == "Snapshot success"
    assert payload["recent_calls"]["items"][0]["phone_masked"].endswith("0001")
    assert "+998900000001" not in response.text
    operators = await client.get(
        f"/api/v1/analytics/operators?project_id={project_id}&date_from={today}&date_to={tomorrow}&timezone=UTC"
    )
    projects = await client.get(
        f"/api/v1/analytics/projects?date_from={today}&date_to={tomorrow}&timezone=UTC"
    )
    assert operators.status_code == 200 and operators.json()["total"] == 1
    assert projects.status_code == 200 and projects.json()["items"][0]["attempted"] == 3


async def test_period_timezone_dst_validation_and_filter_options(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    await register(client, unique_suffix)
    response = await client.get(
        "/api/v1/analytics/summary?date_from=2026-03-08&date_to=2026-03-09&timezone=America%2FNew_York"
    )
    assert response.status_code == 200, response.text
    period = response.json()["period"]
    start = datetime.fromisoformat(period["date_from"])
    end = datetime.fromisoformat(period["date_to"])
    assert end - start == timedelta(hours=23)
    invalid = await client.get(
        "/api/v1/analytics/summary?date_from=2024-01-01&date_to=2026-01-02&timezone=UTC"
    )
    assert invalid.status_code == 422
    options = await client.get("/api/v1/analytics/filter-options")
    assert options.status_code == 200
    assert options.json()["default_timezone"] == "Asia/Tashkent"
    assert options.json()["max_period_days"] == 366


async def test_tenant_and_operator_scope(client: AsyncClient, unique_suffix: str, register: Register) -> None:
    first, _ = await register(client, f"first-{unique_suffix}")
    project_id, user_id = await seed_known_analytics(first)
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        await register(other, f"other-{unique_suffix}")
        hidden = await other.get(f"/api/v1/analytics/summary?project_id={project_id}")
        assert hidden.status_code == 404
        summary = await client.get(f"/api/v1/analytics/summary?operator_id={user_id}")
        assert summary.status_code == 200
        assert summary.json()["attempted_calls"]["value"] == 2
    finally:
        await other.aclose()


async def test_operator_receives_only_personal_scope_and_no_finance(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _ = await register(client, unique_suffix)
    project_id, owner_id = await seed_known_analytics(auth)
    tenant = auth["tenant"]
    assert isinstance(tenant, dict)
    tenant_id = UUID(str(tenant["id"]))
    email = f"analytics-operator-{unique_suffix}@example.com"
    password = "AnalyticsOperator123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(
            email=email,
            display_name="Analytics Operator",
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Membership(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=RoleName.HUMAN_OPERATOR,
                    is_active=True,
                    activated_at=datetime.now(UTC),
                ),
                ProjectUser(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=user.id,
                    is_active=True,
                ),
            ]
        )
        call = await session.scalar(
            select(Call).where(
                Call.tenant_id == tenant_id,
                Call.operator_user_id == owner_id,
                Call.status == CallStatus.NO_ANSWER,
            )
        )
        assert call is not None
        call.operator_user_id = user.id
        operator_id = user.id
    operator = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        login = await operator.post(
            "/api/v1/auth/login",
            json={
                "company_slug": tenant["slug"],
                "email": email,
                "password": password,
            },
        )
        assert login.status_code == 200, login.text
        personal = await operator.get("/api/v1/analytics/summary?timezone=UTC")
        assert personal.status_code == 200, personal.text
        assert personal.json()["attempted_calls"]["value"] == 1
        assert personal.json()["ai_cost_usd"]["value"] is None
        assert "permission_restricted" in personal.json()["ai_cost_usd"]["data_quality_flags"]
        forbidden = await operator.get(f"/api/v1/analytics/summary?operator_id={owner_id}")
        assert forbidden.status_code == 403
        own = await operator.get(f"/api/v1/analytics/summary?operator_id={operator_id}")
        assert own.status_code == 200
    finally:
        await operator.aclose()
