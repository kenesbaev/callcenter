from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import RoleName
from teamora_api.main import app
from teamora_api.models import (
    CallbackTask,
    CallOutcome,
    Membership,
    Project,
    ProjectUser,
    TaskEvent,
    TenantSettings,
    User,
)
from teamora_api.security import hash_password

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def default_project(client: AsyncClient) -> str:
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    return str(response.json()["items"][0]["id"])


async def create_customer(client: AsyncClient, csrf: str, phone: str, name: str = "Клиент") -> dict:
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": name, "phone": phone, "preferred_language": "ru"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_task(
    client: AsyncClient,
    csrf: str,
    *,
    project_id: str,
    customer_id: str,
    task_type: str = "manual",
    title: str = "Связаться с клиентом",
    assigned_user_id: str | None = None,
    due_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> dict:
    headers = {"X-CSRF-Token": csrf}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = await client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "project_id": project_id,
            "customer_id": customer_id,
            "task_type": task_type,
            "title": title,
            "description": "Проверить следующий шаг",
            "priority": "high",
            "assigned_user_id": assigned_user_id,
            "due_at": (due_at or datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "comment": "Без персональных данных в журнале",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_project_member(
    *,
    tenant_id: UUID,
    project_id: UUID,
    suffix: str,
    role: RoleName,
) -> tuple[UUID, str, str]:
    email = f"{role.value}+tasks-{suffix}@example.com"
    password = "TaskMemberPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(
            email=email,
            display_name=f"Task {role.value}",
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Membership(tenant_id=tenant_id, user_id=user.id, role=role),
                ProjectUser(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=user.id,
                    is_active=True,
                ),
            ]
        )
        return user.id, email, password


async def login_client(company_slug: str, email: str, password: str) -> tuple[AsyncClient, str]:
    value = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    response = await value.post(
        "/api/v1/auth/login",
        json={"company_slug": company_slug, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return value, str(response.json()["csrf_token"])


async def make_task_due(tenant_id: UUID, task_id: UUID) -> None:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        task = await session.scalar(select(CallbackTask).where(CallbackTask.id == task_id))
        assert task is not None
        task.due_at = datetime.now(UTC) - timedelta(hours=1)


async def start_mock_call(client: AsyncClient, csrf: str, project_id: str, customer_id: str) -> dict:
    assignment = await client.post(
        f"/api/v1/dialer/next-client?project_id={project_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert assignment.status_code == 200, assignment.text
    value = assignment.json()
    assert value["customer"]["id"] == customer_id
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "customer_id": customer_id,
            "lock_token": value["lock_token"],
            "callback_task_id": value.get("callback_task_id"),
            "from_number": "MOCK",
        },
    )
    assert response.status_code == 201, response.text
    call = response.json()
    response = await client.post(f"/api/v1/calls/{call['id']}/hangup", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    return response.json()


async def test_create_manual_task_callback_and_events(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100001")
    manual = await create_task(client, csrf, project_id=project_id, customer_id=customer["id"])
    callback = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        task_type="callback",
        title="Перезвонить клиенту",
    )
    assert manual["task_type"] == "manual"
    assert callback["task_type"] == "callback"
    assert callback["status"] == "pending"
    assert callback["project_timezone"] == "Asia/Tashkent"
    events = await client.get(f"/api/v1/tasks/{manual['id']}/events")
    assert events.status_code == 200, events.text
    assert [event["event_type"] for event in events.json()] == ["created"]


async def test_task_idempotency_prevents_duplicate_creation_and_completion(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100002")
    key = f"task-create-{unique_suffix}"
    due_at = datetime.now(UTC) + timedelta(hours=2)
    first = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
        due_at=due_at,
        idempotency_key=key,
    )
    replay = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
        due_at=due_at,
        idempotency_key=key,
    )
    assert replay["id"] == first["id"]
    response = await client.post(
        f"/api/v1/tasks/{first['id']}/start",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"task-start-{unique_suffix}"},
    )
    assert response.status_code == 200, response.text
    complete_key = f"task-complete-{unique_suffix}"
    first_complete = await client.post(
        f"/api/v1/tasks/{first['id']}/complete",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": complete_key},
    )
    replay_complete = await client.post(
        f"/api/v1/tasks/{first['id']}/complete",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": complete_key},
    )
    assert first_complete.status_code == replay_complete.status_code == 200
    events = await client.get(f"/api/v1/tasks/{first['id']}/events")
    assert [event["event_type"] for event in events.json()].count("completed") == 1
    response = await client.post(f"/api/v1/tasks/{first['id']}/complete", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409


async def test_task_transitions_reschedule_restore_and_immutable_history(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100003")
    task = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        task_type="callback",
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
    )
    response = await client.post(
        f"/api/v1/tasks/{task['id']}/reschedule",
        headers={"X-CSRF-Token": csrf},
        json={"due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        f"/api/v1/tasks/{task['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Клиент попросил отменить"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"priority": "urgent"},
    )
    assert response.status_code == 409
    response = await client.post(f"/api/v1/tasks/{task['id']}/restore", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    events = await client.get(f"/api/v1/tasks/{task['id']}/events")
    event_types = [event["event_type"] for event in events.json()]
    assert "rescheduled" in event_types
    assert "cancelled" in event_types
    assert "restored" in event_types

    event_id = UUID(events.json()[0]["id"])
    async with SessionFactory() as session:
        await set_tenant_context(session, tenant_id)
        with pytest.raises(DBAPIError):
            await session.execute(
                update(TaskEvent).where(TaskEvent.id == event_id).values(safe_snapshot={"tampered": True})
            )
            await session.flush()
        await session.rollback()


async def test_assignee_project_validation_and_role_visibility(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = UUID(await default_project(client))
    customer = await create_customer(client, csrf, "+998900100004")
    operator_id, operator_email, operator_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"operator-{unique_suffix}",
        role=RoleName.HUMAN_OPERATOR,
    )
    other_id, other_email, other_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"other-{unique_suffix}",
        role=RoleName.HUMAN_OPERATOR,
    )
    analyst_id, analyst_email, analyst_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"analyst-{unique_suffix}",
        role=RoleName.ANALYST,
    )
    manager_id, manager_email, manager_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"manager-{unique_suffix}",
        role=RoleName.TENANT_MANAGER,
    )
    assigned = await create_task(
        client,
        csrf,
        project_id=str(project_id),
        customer_id=customer["id"],
        assigned_user_id=str(operator_id),
    )
    await create_task(
        client,
        csrf,
        project_id=str(project_id),
        customer_id=customer["id"],
        title="Чужая задача",
        assigned_user_id=str(other_id),
    )
    operator_client, operator_csrf = await login_client(
        f"test-{unique_suffix}", operator_email, operator_password
    )
    try:
        response = await operator_client.get("/api/v1/tasks")
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [assigned["id"]]
        response = await operator_client.post(
            "/api/v1/tasks",
            headers={"X-CSRF-Token": operator_csrf},
            json={
                "project_id": str(project_id),
                "customer_id": customer["id"],
                "task_type": "manual",
                "title": "Назначить другому",
                "assigned_user_id": str(other_id),
                "due_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 403
    finally:
        await operator_client.aclose()

    manager_client, manager_csrf = await login_client(
        f"test-{unique_suffix}", manager_email, manager_password
    )
    try:
        response = await manager_client.get("/api/v1/tasks")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        response = await manager_client.patch(
            f"/api/v1/tasks/{assigned['id']}",
            headers={"X-CSRF-Token": manager_csrf},
            json={"priority": "urgent"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["priority"] == "urgent"
    finally:
        await manager_client.aclose()

    analyst_client, analyst_csrf = await login_client(
        f"test-{unique_suffix}", analyst_email, analyst_password
    )
    try:
        response = await analyst_client.get("/api/v1/tasks")
        assert response.status_code == 200
        assert response.json()["total"] == 2
        response = await analyst_client.patch(
            f"/api/v1/tasks/{assigned['id']}",
            headers={"X-CSRF-Token": analyst_csrf},
            json={"priority": "urgent"},
        )
        assert response.status_code == 403
    finally:
        await analyst_client.aclose()
    assert len({analyst_id, manager_id, operator_id}) == 3


async def test_cannot_assign_user_without_project_access(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100005")
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(
            email=f"no-project-{unique_suffix}@example.com",
            display_name="Без проекта",
            password_hash=hash_password("NoProjectPass123!"),
        )
        session.add(user)
        await session.flush()
        session.add(
            Membership(
                tenant_id=tenant_id,
                user_id=user.id,
                role=RoleName.HUMAN_OPERATOR,
            )
        )
        user_id = user.id
    response = await client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "customer_id": customer["id"],
            "task_type": "manual",
            "title": "Некорректное назначение",
            "assigned_user_id": str(user_id),
            "due_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "task_assignee_project_access_required"


async def test_call_outcome_must_belong_to_the_same_customer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    first_customer = await create_customer(client, csrf, "+998900100016", "Первый клиент")
    second_customer = await create_customer(client, csrf, "+998900100017", "Второй клиент")
    call = await start_mock_call(client, csrf, project_id, first_customer["id"])
    definitions = await client.get(f"/api/v1/call-results/available?project_id={project_id}")
    other = next(item for item in definitions.json()["definitions"] if item["system_code"] == "other")
    response = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={"X-CSRF-Token": csrf},
        json={"result_definition_id": other["id"], "comment": "Проверка связи"},
    )
    assert response.status_code == 200, response.text
    async with SessionFactory() as session:
        await set_tenant_context(session, tenant_id)
        outcome_id = await session.scalar(
            select(CallOutcome.id).where(CallOutcome.call_id == UUID(call["id"]))
        )
    assert outcome_id is not None
    response = await client.post(
        "/api/v1/tasks",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "customer_id": second_customer["id"],
            "task_type": "manual",
            "title": "Несовместимая связь",
            "call_outcome_id": str(outcome_id),
            "due_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "task_outcome_mismatch"


async def test_tenant_isolation_and_task_event_rls(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100006")
    task = await create_task(client, csrf, project_id=project_id, customer_id=customer["id"])
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        other_auth, _other_csrf = await register(other, f"other-{unique_suffix}")
        response = await other.get(f"/api/v1/tasks/{task['id']}")
        assert response.status_code == 404
        other_tenant_id = UUID(str(other_auth["tenant"]["id"]))  # type: ignore[index]
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, other_tenant_id)
            assert (
                await session.scalar(
                    select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == UUID(task["id"]))
                )
            ) == 0
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            assert (
                await session.scalar(
                    select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == UUID(task["id"]))
                )
            ) == 1
    finally:
        await other.aclose()


async def test_overdue_filters_and_timezone_are_computed(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100007")
    task = await create_task(client, csrf, project_id=project_id, customer_id=customer["id"])
    await make_task_due(tenant_id, UUID(task["id"]))
    response = await client.get(f"/api/v1/tasks?project_id={project_id}&period=overdue")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == task["id"]
    assert response.json()["items"][0]["is_overdue"] is True
    assert response.json()["items"][0]["project_timezone"] == "Asia/Tashkent"

    now = datetime.now(UTC)
    zones = [
        "Pacific/Kiritimati",
        "Pacific/Honolulu",
        "America/New_York",
        "Europe/London",
        "Asia/Tashkent",
        "Asia/Tokyo",
    ]

    def until_midnight(zone_name: str) -> timedelta:
        zone = ZoneInfo(zone_name)
        local = now.astimezone(zone)
        midnight = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
        return midnight.astimezone(UTC) - now

    project_zone = max(zones, key=until_midnight)
    tenant_zone = min(zones, key=until_midnight)
    project_horizon = until_midnight(project_zone)
    tenant_horizon = until_midnight(tenant_zone)
    assert project_horizon - tenant_horizon > timedelta(hours=4)
    due_at = now + tenant_horizon + ((project_horizon - tenant_horizon) / 2)
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(select(Project).where(Project.id == UUID(project_id)))
        settings = await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
        assert project is not None and settings is not None
        project.timezone = project_zone
        settings.timezone = tenant_zone
    today_task = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        title="Сегодня по timezone проекта",
        due_at=due_at,
    )
    response = await client.get("/api/v1/tasks?period=today")
    assert response.status_code == 200, response.text
    assert today_task["id"] in {item["id"] for item in response.json()["items"]}
    response = await client.get("/api/v1/tasks?period=future")
    assert response.status_code == 200, response.text
    assert today_task["id"] not in {item["id"] for item in response.json()["items"]}


async def test_future_callback_is_not_issued_and_due_callback_precedes_new_customer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project(client)
    callback_customer = await create_customer(client, csrf, "+998900100008", "Перезвон")
    new_customer = await create_customer(client, csrf, "+998900100009", "Новый")
    task = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=callback_customer["id"],
        task_type="callback",
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    response = await client.post(
        f"/api/v1/dialer/next-client?project_id={project_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["customer"]["id"] == new_customer["id"]
    await client.post(
        f"/api/v1/dialer/customers/{new_customer['id']}/release",
        headers={"X-CSRF-Token": csrf},
        json={"lock_token": response.json()["lock_token"]},
    )
    await make_task_due(tenant_id, UUID(task["id"]))
    response = await client.post(
        f"/api/v1/dialer/next-client?project_id={project_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assignment = response.json()
    assert assignment["source"] == "callback"
    assert assignment["customer"]["id"] == callback_customer["id"]
    assert assignment["task"]["id"] == task["id"]
    assert assignment["task"]["comment"] == "Без персональных данных в журнале"


async def test_two_operators_cannot_claim_same_task_or_customer_and_recovery_works(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = UUID(await default_project(client))
    customer = await create_customer(client, csrf, "+998900100010")
    task = await create_task(
        client,
        csrf,
        project_id=str(project_id),
        customer_id=customer["id"],
        task_type="callback",
    )
    await make_task_due(tenant_id, UUID(task["id"]))
    first_id, first_email, first_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"first-{unique_suffix}",
        role=RoleName.HUMAN_OPERATOR,
    )
    second_id, second_email, second_password = await add_project_member(
        tenant_id=tenant_id,
        project_id=project_id,
        suffix=f"second-{unique_suffix}",
        role=RoleName.HUMAN_OPERATOR,
    )
    first_client, first_csrf = await login_client(f"test-{unique_suffix}", first_email, first_password)
    second_client, second_csrf = await login_client(f"test-{unique_suffix}", second_email, second_password)
    try:
        first_response, second_response = await asyncio.gather(
            first_client.post(
                f"/api/v1/dialer/next-client?project_id={project_id}",
                headers={"X-CSRF-Token": first_csrf},
            ),
            second_client.post(
                f"/api/v1/dialer/next-client?project_id={project_id}",
                headers={"X-CSRF-Token": second_csrf},
            ),
        )
        assignments = [first_response.json(), second_response.json()]
        assert sum(value is not None for value in assignments) == 1
        winner_client = first_client if first_response.json() else second_client
        winner = first_response.json() or second_response.json()
        current = await winner_client.get("/api/v1/dialer/current")
        assert current.status_code == 200, current.text
        assert current.json()["task"]["id"] == task["id"]
        assert current.json()["lock_token"] == winner["lock_token"]
    finally:
        await first_client.aclose()
        await second_client.aclose()
    assert first_id != second_id


async def test_call_result_creates_one_general_task_and_cancels_callback(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100011")
    call = await start_mock_call(client, csrf, project_id, customer["id"])
    definitions = await client.get(f"/api/v1/call-results/available?project_id={project_id}")
    assert definitions.status_code == 200, definitions.text
    other = next(item for item in definitions.json()["definitions"] if item["system_code"] == "other")
    task_due_at = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    result_payload = {
        "result_definition_id": other["id"],
        "comment": "Нужен последующий контакт",
        "task": {
            "title": "Подготовить предложение",
            "description": "Связаться по согласованному вопросу",
            "priority": "urgent",
            "due_at": task_due_at,
        },
    }
    response = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"call-task-{unique_suffix}",
        },
        json=result_payload,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["task_ids"]) == 1
    replay = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"call-task-{unique_suffix}",
        },
        json=result_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["task_ids"] == response.json()["task_ids"]
    tasks = await client.get(f"/api/v1/tasks?customer_id={customer['id']}")
    assert tasks.status_code == 200
    assert [item["title"] for item in tasks.json()["items"]] == ["Подготовить предложение"]


async def test_do_not_call_cancels_future_callback_but_keeps_completed_task(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project(client)
    customer = await create_customer(client, csrf, "+998900100012")
    call = await start_mock_call(client, csrf, project_id, customer["id"])
    callback = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        task_type="callback",
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    completed = await create_task(
        client,
        csrf,
        project_id=project_id,
        customer_id=customer["id"],
        assigned_user_id=str(auth["user"]["id"]),  # type: ignore[index]
        title="Уже выполненная задача",
    )
    await client.post(f"/api/v1/tasks/{completed['id']}/start", headers={"X-CSRF-Token": csrf})
    await client.post(f"/api/v1/tasks/{completed['id']}/complete", headers={"X-CSRF-Token": csrf})
    definitions = await client.get(f"/api/v1/call-results/available?project_id={project_id}")
    do_not_call = next(
        item for item in definitions.json()["definitions"] if item["system_code"] == "do_not_call"
    )
    response = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={"X-CSRF-Token": csrf},
        json={"result_definition_id": do_not_call["id"], "comment": "Не звонить"},
    )
    assert response.status_code == 200, response.text
    callback_response = await client.get(f"/api/v1/tasks/{callback['id']}")
    completed_response = await client.get(f"/api/v1/tasks/{completed['id']}")
    assert callback_response.json()["status"] == "cancelled"
    assert callback_response.json()["cancellation_reason"] == "Клиент отмечен как do-not-call"
    assert completed_response.json()["status"] == "completed"


async def test_legacy_callbacks_api_uses_unified_tasks(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(client, csrf, "+998900100013")
    response = await client.post(
        "/api/v1/callbacks",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"legacy-callback-{unique_suffix}",
        },
        json={
            "customer_id": customer["id"],
            "due_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "note": "Совместимый перезвон",
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    unified = await client.get(f"/api/v1/tasks/{task_id}")
    assert unified.status_code == 200, unified.text
    assert unified.json()["task_type"] == "callback"
    assert unified.json()["comment"] == "Совместимый перезвон"
    response = await client.post(
        f"/api/v1/callbacks/{task_id}/complete",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
