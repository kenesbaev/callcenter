from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import RoleName
from teamora_api.main import app
from teamora_api.models import (
    AiOperator,
    CallFlow,
    CallResultCatalog,
    KnowledgeSource,
    Membership,
    PhoneNumber,
    Project,
    ProjectInboundPhoneNumber,
    ProjectUser,
    User,
)
from teamora_api.security import hash_password

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]
TEST_PASSWORD = "ProjectPass123!"  # noqa: S105 - isolated test credential


def make_test_phone(suffix: str, offset: int = 0) -> str:
    value = (int(suffix[-8:], 16) + offset) % 1_000_000_000
    return f"+998{value:09d}"


async def default_project_id(client: AsyncClient) -> str:
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    project = next(item for item in response.json()["items"] if item["is_default"])
    return str(project["id"])


async def create_member(
    *,
    tenant_id: UUID,
    suffix: str,
    role: RoleName,
    project_id: UUID | None = None,
) -> tuple[UUID, str]:
    email = f"{role.value}+{suffix}@example.com"
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        user = User(
            email=email,
            display_name=f"Test {role.value}",
            password_hash=hash_password(TEST_PASSWORD),
        )
        session.add(user)
        await session.flush()
        session.add(
            Membership(
                tenant_id=tenant_id,
                user_id=user.id,
                role=role,
                is_active=True,
            )
        )
        if project_id is not None:
            session.add(
                ProjectUser(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=user.id,
                    is_active=True,
                )
            )
    return user.id, email


async def authenticated_client(slug: str, email: str) -> tuple[AsyncClient, str]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    response = await client.post(
        "/api/v1/auth/login",
        json={"company_slug": slug, "email": email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return client, str(response.json()["csrf_token"])


async def create_project_resource_options(
    *, tenant_id: UUID, project_id: UUID, suffix: str
) -> dict[str, UUID]:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        outbound = PhoneNumber(
            tenant_id=tenant_id,
            e164=make_test_phone(suffix),
            label="Project outbound",
            is_active=True,
        )
        inbound = PhoneNumber(
            tenant_id=tenant_id,
            e164=make_test_phone(suffix, 1),
            label="Project DID",
            is_active=True,
        )
        source = KnowledgeSource(
            tenant_id=tenant_id,
            name="Project knowledge",
            source_type="text",
        )
        ai_operator = AiOperator(
            tenant_id=tenant_id,
            project_id=project_id,
            name="Project AI",
            description="",
            is_active=True,
        )
        call_flow = CallFlow(
            tenant_id=tenant_id,
            project_id=project_id,
            name="Project script",
            is_active=True,
        )
        session.add_all([outbound, inbound, source, ai_operator, call_flow])
        await session.flush()
        return {
            "outbound": outbound.id,
            "inbound": inbound.id,
            "source": source.id,
            "ai_operator": ai_operator.id,
            "call_flow": call_flow.id,
        }


async def test_owner_creates_and_updates_complete_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    owner_id = str(auth["user"]["id"])  # type: ignore[index]

    response = await client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Retail retention",
            "description": "Retention campaign",
            "default_language": "uz",
            "timezone": "Asia/Tashkent",
            "max_concurrent_calls": 3,
            "working_hours": {
                "monday": {"enabled": True, "start": "09:00", "end": "18:00"},
                "saturday": {"enabled": False},
            },
            "recording_enabled": True,
            "recording_disclosure_required": True,
            "max_attempts": 3,
            "retry_intervals_minutes": [30, 120],
            "callback_rules": {
                "default_delay_minutes": 90,
                "max_schedule_days": 14,
                "allow_operator_scheduling": True,
                "require_assignee": False,
                "overdue_first": True,
            },
            "operator_user_ids": [owner_id],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    project_id = UUID(created["id"])
    assert created["effective_settings"]["default_language"] == "uz"
    assert created["call_result_catalog_id"]

    options = await create_project_resource_options(
        tenant_id=tenant_id, project_id=project_id, suffix=unique_suffix
    )
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Retail retention 2026",
            "outbound_phone_number_id": str(options["outbound"]),
            "inbound_phone_number_ids": [str(options["inbound"])],
            "ai_operator_id": str(options["ai_operator"]),
            "knowledge_source_id": str(options["source"]),
            "call_flow_id": str(options["call_flow"]),
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "Retail retention 2026"
    assert updated["outbound_number"] == make_test_phone(unique_suffix)
    assert updated["inbound_phone_number_ids"] == [str(options["inbound"])]
    assert updated["ai_operator_id"] == str(options["ai_operator"])
    assert updated["knowledge_source_id"] == str(options["source"])
    assert updated["call_flow_id"] == str(options["call_flow"])
    assert updated["operator_user_ids"] == [owner_id]


async def test_manager_can_manage_projects(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    _manager_id, manager_email = await create_member(
        tenant_id=tenant_id,
        suffix=unique_suffix,
        role=RoleName.TENANT_MANAGER,
    )
    manager, manager_csrf = await authenticated_client(f"test-{unique_suffix}", manager_email)
    try:
        response = await manager.post(
            "/api/v1/projects",
            headers={"X-CSRF-Token": manager_csrf},
            json={"name": "Manager project"},
        )
        assert response.status_code == 201, response.text
        project_id = response.json()["id"]
        response = await manager.patch(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": manager_csrf},
            json={"description": "Managed by tenant manager", "status": "paused"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "paused"
        assert response.json()["description"] == "Managed by tenant manager"
    finally:
        await manager.aclose()


async def test_operator_reads_assigned_project_but_cannot_change_settings(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, _csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = UUID(await default_project_id(client))
    _operator_id, operator_email = await create_member(
        tenant_id=tenant_id,
        suffix=unique_suffix,
        role=RoleName.HUMAN_OPERATOR,
        project_id=project_id,
    )
    operator, operator_csrf = await authenticated_client(f"test-{unique_suffix}", operator_email)
    try:
        response = await operator.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200, response.text
        response = await operator.patch(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": operator_csrf},
            json={"name": "Forbidden change"},
        )
        assert response.status_code == 403, response.text
        response = await operator.get("/api/v1/projects/options")
        assert response.status_code == 403, response.text
    finally:
        await operator.aclose()


async def test_tenant_cannot_read_or_update_foreign_project(unique_suffix: str, register: Register) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_a:
        auth_a, _csrf_a = await register(tenant_a, f"a-{unique_suffix}")
        tenant_a_id = UUID(str(auth_a["tenant"]["id"]))  # type: ignore[index]
        project_id = UUID(await default_project_id(tenant_a))

    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_b:
        auth_b, csrf_b = await register(tenant_b, f"b-{unique_suffix}")
        tenant_b_id = UUID(str(auth_b["tenant"]["id"]))  # type: ignore[index]
        response = await tenant_b.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 404, response.text
        response = await tenant_b.patch(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": csrf_b},
            json={"name": "Foreign project"},
        )
        assert response.status_code == 404, response.text

    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_a_id)
        assert await session.scalar(select(Project).where(Project.id == project_id)) is not None
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_b_id)
        assert await session.scalar(select(Project).where(Project.id == project_id)) is None
        assert (
            await session.scalar(select(CallResultCatalog).where(CallResultCatalog.project_id == project_id))
            is None
        )


async def test_foreign_ai_operator_and_phone_number_are_rejected(
    unique_suffix: str, register: Register
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_a:
        _auth_a, csrf_a = await register(tenant_a, f"a-{unique_suffix}")
        project_a = await default_project_id(tenant_a)

        async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_b:
            auth_b, _csrf_b = await register(tenant_b, f"b-{unique_suffix}")
            tenant_b_id = UUID(str(auth_b["tenant"]["id"]))  # type: ignore[index]
            project_b = UUID(await default_project_id(tenant_b))
            async with SessionFactory.begin() as session:
                await set_tenant_context(session, tenant_b_id)
                ai_operator = AiOperator(
                    tenant_id=tenant_b_id,
                    project_id=project_b,
                    name="Foreign AI",
                    description="",
                    is_active=True,
                )
                phone = PhoneNumber(
                    tenant_id=tenant_b_id,
                    e164=make_test_phone(unique_suffix),
                    label="Foreign phone",
                    is_active=True,
                )
                session.add_all([ai_operator, phone])
                await session.flush()
                ai_operator_id = ai_operator.id
                phone_id = phone.id

        response = await tenant_a.patch(
            f"/api/v1/projects/{project_a}",
            headers={"X-CSRF-Token": csrf_a},
            json={"ai_operator_id": str(ai_operator_id)},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "project_ai_operator_invalid"
        response = await tenant_a.patch(
            f"/api/v1/projects/{project_a}",
            headers={"X-CSRF-Token": csrf_a},
            json={"outbound_phone_number_id": str(phone_id)},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "project_phone_number_invalid"
        response = await tenant_a.post(
            "/api/v1/projects",
            headers={"X-CSRF-Token": csrf_a},
            json={"name": "Foreign AI create", "ai_operator_id": str(ai_operator_id)},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "project_ai_operator_invalid"
        response = await tenant_a.post(
            "/api/v1/projects",
            headers={"X-CSRF-Token": csrf_a},
            json={"name": "Foreign phone create", "outbound_phone_number_id": str(phone_id)},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "project_phone_number_invalid"
        response = await tenant_a.patch(
            f"/api/v1/projects/{project_a}",
            headers={"X-CSRF-Token": csrf_a},
            json={"inbound_phone_number_ids": [str(phone_id)]},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "project_phone_number_invalid"


async def test_unassignable_employee_is_rejected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    project_id = await default_project_id(client)
    analyst_id, _email = await create_member(
        tenant_id=tenant_id,
        suffix=unique_suffix,
        role=RoleName.ANALYST,
    )
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={"operator_user_ids": [str(analyst_id)]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "project_operator_invalid"


async def test_working_hours_and_concurrent_call_limit_are_validated(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={"working_hours": {"monday": {"enabled": True, "start": "18:00", "end": "09:00"}}},
    )
    assert response.status_code == 422, response.text
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={"max_concurrent_calls": 6},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "project_call_limit_invalid"
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": csrf},
        json={"max_concurrent_calls": 0},
    )
    assert response.status_code == 422, response.text


async def test_default_project_inherits_settings_and_cannot_be_archived(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    response = await client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200, response.text
    project = response.json()
    assert project["is_default"] is True
    assert project["max_concurrent_calls"] is None
    assert project["effective_settings"]["max_concurrent_calls"] == 5
    assert project["effective_settings"]["timezone"] == "Asia/Tashkent"
    assert project["call_result_catalog_id"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "default_project_required"


async def test_archived_project_remains_readable_and_is_not_deleted(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    response = await client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Archive candidate"},
    )
    assert response.status_code == 201, response.text
    project_id = UUID(response.json()["id"])

    response = await client.post(
        f"/api/v1/projects/{project_id}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    assert response.json()["archived_at"] is not None
    response = await client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "archived"
    response = await client.get("/api/v1/projects?status=archived")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(project_id)]

    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        stored = await session.scalar(select(Project).where(Project.id == project_id))
        assert stored is not None
        inbound = await session.scalars(
            select(ProjectInboundPhoneNumber).where(ProjectInboundPhoneNumber.project_id == project_id)
        )
        assert list(inbound) == []
