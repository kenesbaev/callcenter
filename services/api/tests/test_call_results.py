from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import CallResultCategory, RoleName
from teamora_api.main import app
from teamora_api.models import (
    CallbackTask,
    CallEvent,
    CallOutcome,
    CallResultDefinition,
    CustomerNote,
    Membership,
    ProjectUser,
    User,
)
from teamora_api.security import hash_password

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def default_project(client: AsyncClient) -> dict[str, object]:
    response = await client.get("/api/v1/projects?limit=100")
    assert response.status_code == 200, response.text
    return next(project for project in response.json()["items"] if project["is_default"])


async def available_results(client: AsyncClient, project_id: str) -> list[dict[str, object]]:
    response = await client.get(f"/api/v1/call-results/available?project_id={project_id}")
    assert response.status_code == 200, response.text
    return response.json()["definitions"]


def translated_name(name: str) -> dict[str, str]:
    return {"ru": name, "uz": f"{name} uz", "en": f"{name} en", "kaa": f"{name} kaa"}


async def create_result(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    code: str,
    **overrides: object,
) -> dict[str, object]:
    name = str(overrides.pop("name", f"Результат {code}"))
    payload: dict[str, object] = {
        "project_id": project_id,
        "system_code": code,
        "category": "intermediate",
        "name": name,
        "name_translations": translated_name(name),
        "description": "",
        "color": "#2563EB",
        "sort_order": 500,
        "is_active": True,
        "requires_comment": False,
        "requires_callback": False,
        "requires_callback_at": False,
        "creates_task": False,
        "next_customer_status": "completed",
        "return_to_queue": False,
        "completes_customer": True,
        "do_not_call": False,
        "counts_as_success": False,
    }
    payload.update(overrides)
    response = await client.post(
        "/api/v1/call-results",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def phone_for_test(suffix: str, offset: int = 0) -> str:
    digits = "".join(character for character in suffix if character.isdigit())
    value = (int(digits[-7:] or "1") + offset) % 10_000_000
    return f"+99890{value:07d}"


async def completed_mock_call(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    suffix: str,
    *,
    offset: int = 0,
) -> tuple[dict[str, object], dict[str, object]]:
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "display_name": f"Клиент {suffix} {offset}",
            "phone": phone_for_test(suffix, offset),
            "preferred_language": "ru",
        },
    )
    assert response.status_code == 201, response.text
    customer = response.json()
    response = await client.post(
        f"/api/v1/dialer/next-client?project_id={project_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assignment = response.json()
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "customer_id": customer["id"],
            "lock_token": assignment["lock_token"],
            "from_number": "MOCK",
        },
    )
    assert response.status_code == 201, response.text
    call = response.json()
    response = await client.post(
        f"/api/v1/calls/{call['id']}/hangup",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return customer, response.json()


async def save_result(
    client: AsyncClient,
    csrf: str,
    call_id: str,
    definition_id: str,
    *,
    comment: str = "",
    callback_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> Response:
    headers = {"X-CSRF-Token": csrf}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        f"/api/v1/calls/{call_id}/result",
        headers=headers,
        json={
            "result_definition_id": definition_id,
            "comment": comment,
            "callback_at": callback_at.isoformat() if callback_at else None,
        },
    )


async def test_create_result_inside_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    created = await create_result(client, csrf, str(project["id"]), f"sale_{unique_suffix}")
    assert created["project_id"] == project["id"]
    assert created["category"] == "intermediate"


async def test_system_code_is_unique_inside_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    code = f"unique_{unique_suffix}"
    await create_result(client, csrf, str(project["id"]), code)
    response = await client.post(
        "/api/v1/call-results",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project["id"],
            "system_code": code,
            "category": "intermediate",
            "name": "Дубликат",
            "name_translations": translated_name("Дубликат"),
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "call_result_code_taken"


async def test_tenant_isolation_and_rls(client: AsyncClient, unique_suffix: str, register: Register) -> None:
    _auth, _csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = (await available_results(client, str(project["id"])))[0]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as other:
        other_auth, _other_csrf = await register(other, f"other-{unique_suffix}")
        response = await other.get(f"/api/v1/call-results/{definition['id']}")
        assert response.status_code == 404
    other_tenant = other_auth["tenant"]
    assert isinstance(other_tenant, dict)
    other_tenant_id = UUID(str(other_tenant["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, other_tenant_id)
        visible = await session.scalar(
            select(CallResultDefinition).where(CallResultDefinition.id == UUID(str(definition["id"])))
        )
        assert visible is None


async def test_operator_reads_but_cannot_edit_catalog(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project = await default_project(client)
    definition = (await available_results(client, str(project["id"])))[0]
    email = f"results-operator-{unique_suffix}@example.com"
    password = "OperatorPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(email=email, display_name="Operator", password_hash=hash_password(password))
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Membership(tenant_id=tenant_id, user_id=user.id, role=RoleName.HUMAN_OPERATOR),
                ProjectUser(
                    tenant_id=tenant_id,
                    project_id=UUID(str(project["id"])),
                    user_id=user.id,
                    is_active=True,
                ),
            ]
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator:
        login = await operator.post(
            "/api/v1/auth/login",
            json={"company_slug": f"test-{unique_suffix}", "email": email, "password": password},
        )
        csrf = login.json()["csrf_token"]
        assert (await operator.get(f"/api/v1/call-results?project_id={project['id']}")).status_code == 200
        response = await operator.patch(
            f"/api/v1/call-results/{definition['id']}",
            headers={"X-CSRF-Token": csrf},
            json={"name": "Нельзя"},
        )
        assert response.status_code == 403


async def test_manager_can_manage_project_catalog(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project = await default_project(client)
    email = f"results-manager-{unique_suffix}@example.com"
    password = "ManagerPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(email=email, display_name="Manager", password_hash=hash_password(password))
        session.add(user)
        await session.flush()
        session.add(
            Membership(
                tenant_id=tenant_id,
                user_id=user.id,
                role=RoleName.TENANT_MANAGER,
            )
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as manager:
        login = await manager.post(
            "/api/v1/auth/login",
            json={
                "company_slug": f"test-{unique_suffix}",
                "email": email,
                "password": password,
            },
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        created = await create_result(
            manager,
            csrf,
            str(project["id"]),
            f"manager_{unique_suffix}",
        )
        assert created["system_code"] == f"manager_{unique_suffix}"


async def test_result_from_another_project_cannot_be_saved(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    default = await default_project(client)
    foreign_definition = (await available_results(client, str(default["id"])))[0]
    response = await client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Второй проект", "operator_user_ids": [auth["user"]["id"]]},  # type: ignore[index]
    )
    assert response.status_code == 201, response.text
    other_project = response.json()
    _customer, call = await completed_mock_call(client, csrf, other_project["id"], unique_suffix, offset=5)
    response = await save_result(client, csrf, call["id"], str(foreign_definition["id"]))
    assert response.status_code == 422


async def test_archived_result_cannot_be_selected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = await create_result(client, csrf, str(project["id"]), f"archive_{unique_suffix}")
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await client.post(
        f"/api/v1/call-results/{definition['id']}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 409


async def test_historical_label_survives_definition_rename(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "success"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 200
    original_label = response.json()["outcome"]["label"]
    response = await client.patch(
        f"/api/v1/call-results/{definition['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Новое название", "name_translations": translated_name("Новое название")},
    )
    assert response.status_code == 200
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        outcome = await session.scalar(select(CallOutcome).where(CallOutcome.call_id == UUID(call["id"])))
        assert outcome is not None
        assert outcome.label == original_label


async def test_historical_category_survives_definition_change(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "success"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 200
    await client.patch(
        f"/api/v1/call-results/{definition['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"category": "unsuccessful"},
    )
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        outcome = await session.scalar(select(CallOutcome).where(CallOutcome.call_id == UUID(call["id"])))
        assert outcome is not None
        assert outcome.category == CallResultCategory.SUCCESSFUL


async def test_required_comment_is_validated(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = await create_result(
        client, csrf, str(project["id"]), f"comment_{unique_suffix}", requires_comment=True
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 422


async def test_required_callback_datetime_is_validated(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = await create_result(
        client,
        csrf,
        str(project["id"]),
        f"callback_{unique_suffix}",
        requires_callback=True,
        requires_callback_at=True,
        completes_customer=False,
        next_customer_status="callback",
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 422


async def test_callback_task_is_created_once(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "callback"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    due = datetime.now(UTC) + timedelta(hours=1)
    key = f"callback-{unique_suffix}"
    first = await save_result(
        client, csrf, call["id"], definition["id"], callback_at=due, idempotency_key=key
    )
    second = await save_result(
        client, csrf, call["id"], definition["id"], callback_at=due, idempotency_key=key
    )
    assert first.status_code == second.status_code == 200
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        count = await session.scalar(
            select(func.count()).select_from(CallbackTask).where(CallbackTask.call_id == UUID(call["id"]))
        )
        assert count == 1


async def test_pending_callback_is_cancelled_when_result_changes(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    results = await available_results(client, str(project["id"]))
    callback = next(item for item in results if item["system_code"] == "callback")
    success = next(item for item in results if item["system_code"] == "success")
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    await save_result(
        client,
        csrf,
        call["id"],
        callback["id"],
        callback_at=datetime.now(UTC) + timedelta(hours=1),
        idempotency_key=f"callback-first-{unique_suffix}",
    )
    response = await save_result(
        client, csrf, call["id"], success["id"], idempotency_key=f"callback-change-{unique_suffix}"
    )
    assert response.status_code == 200
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        task = await session.scalar(select(CallbackTask).where(CallbackTask.call_id == UUID(call["id"])))
        assert task is not None
        assert task.status == "cancelled"


async def test_idempotency_prevents_duplicate_notes_events_and_tasks(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "callback"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    key = f"idempotent-{unique_suffix}"
    due = datetime.now(UTC) + timedelta(hours=1)
    for _ in range(2):
        response = await save_result(
            client,
            csrf,
            call["id"],
            definition["id"],
            comment="Один комментарий",
            callback_at=due,
            idempotency_key=key,
        )
        assert response.status_code == 200
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        task_count = await session.scalar(
            select(func.count()).select_from(CallbackTask).where(CallbackTask.call_id == UUID(call["id"]))
        )
        note_count = await session.scalar(
            select(func.count()).select_from(CustomerNote).where(CustomerNote.content == "Один комментарий")
        )
        event_count = await session.scalar(
            select(func.count())
            .select_from(CallEvent)
            .where(CallEvent.call_id == UUID(call["id"]), CallEvent.event_type == "call.result_saved")
        )
        assert (task_count, note_count, event_count) == (1, 1, 1)


async def test_idempotency_history_survives_a_later_result_change(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    results = await available_results(client, str(project["id"]))
    callback = next(item for item in results if item["system_code"] == "callback")
    success = next(item for item in results if item["system_code"] == "success")
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    due = datetime.now(UTC) + timedelta(hours=1)
    original_key = f"original-{unique_suffix}"
    original = await save_result(
        client,
        csrf,
        call["id"],
        callback["id"],
        callback_at=due,
        idempotency_key=original_key,
    )
    changed = await save_result(
        client,
        csrf,
        call["id"],
        success["id"],
        idempotency_key=f"changed-{unique_suffix}",
    )
    replay = await save_result(
        client,
        csrf,
        call["id"],
        callback["id"],
        callback_at=due,
        idempotency_key=original_key,
    )
    assert changed.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == original.json()
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))  # type: ignore[index]
        event_count = await session.scalar(
            select(func.count())
            .select_from(CallEvent)
            .where(
                CallEvent.call_id == UUID(call["id"]),
                CallEvent.event_type == "call.result_saved",
            )
        )
        assert event_count == 2


async def test_do_not_call_updates_customer_status(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "do_not_call"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 200
    assert response.json()["customer_status"] == "do_not_call"


async def test_legacy_result_code_resolves_to_project_definition(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    response = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={"X-CSRF-Token": csrf},
        json={"result": "success", "comment": "legacy"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"]["code"] == "success"


async def test_default_catalog_localizes_ru_uz_en_and_kaa(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, _csrf = await register(client, unique_suffix)
    project = await default_project(client)
    results = await available_results(client, str(project["id"]))
    assert len(results) == 9
    for definition in results:
        assert {"ru", "uz", "en", "kaa"}.issubset(definition["name_translations"])
        assert "ka" not in definition["name_translations"]


async def test_mock_dialer_continues_with_project_catalog(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "success"
    )
    response = await save_result(client, csrf, call["id"], definition["id"])
    assert response.status_code == 200
    assert response.json()["call"]["provider"] == "mock"


async def test_analytics_uses_snapshot_category(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project = await default_project(client)
    definition = next(
        item
        for item in await available_results(client, str(project["id"]))
        if item["system_code"] == "success"
    )
    _customer, call = await completed_mock_call(client, csrf, str(project["id"]), unique_suffix)
    await save_result(client, csrf, call["id"], definition["id"])
    response = await client.patch(
        f"/api/v1/call-results/{definition['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"category": "unsuccessful"},
    )
    assert response.status_code == 200
    response = await client.get(f"/api/v1/call-results/analytics?project_id={project['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["category_counts"]["successful"] == 1
    assert response.json()["category_counts"]["unsuccessful"] == 0
