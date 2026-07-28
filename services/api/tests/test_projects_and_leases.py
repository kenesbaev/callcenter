from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import RoleName
from teamora_api.main import app
from teamora_api.models import Customer, Membership, Project, ProjectUser, User
from teamora_api.security import hash_password


async def create_customer(
    client: AsyncClient,
    csrf: str,
    *,
    phone: str,
    project_id: str | None = None,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "display_name": "Клиент проекта",
            "phone": phone,
            "preferred_language": "ru",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_operator(
    *,
    tenant_id: UUID,
    suffix: str,
) -> tuple[str, str]:
    email = f"project-operator+{suffix}@example.com"
    password = "OperatorPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        user = User(
            email=email,
            display_name="Project Operator",
            password_hash=hash_password(password),
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
    return email, password


async def test_phone_is_unique_inside_project(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    auth, csrf = await register(client, unique_suffix)  # type: ignore[operator]
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    default_project = response.json()["items"][0]
    assert default_project["is_default"] is True
    assert str(auth["user"]["id"]) in default_project["operator_user_ids"]

    response = await client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Вторая кампания",
            "description": "Проверка проектной уникальности",
            "max_concurrent_calls": 2,
        },
    )
    assert response.status_code == 201, response.text
    second_project = response.json()

    phone = "+998941234567"
    first_customer = await create_customer(client, csrf, phone=phone)
    second_customer = await create_customer(
        client,
        csrf,
        phone=phone,
        project_id=second_project["id"],
    )
    assert first_customer["project_id"] == default_project["id"]
    assert second_customer["project_id"] == second_project["id"]


async def test_dialer_lease_rejects_stale_token(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    _auth, csrf = await register(client, unique_suffix)  # type: ignore[operator]
    customer = await create_customer(client, csrf, phone="+998951234567")
    response = await client.post(
        "/api/v1/dialer/next-client",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assignment = response.json()
    assert assignment["customer"]["id"] == customer["id"]

    response = await client.post(
        f"/api/v1/dialer/customers/{customer['id']}/heartbeat",
        headers={"X-CSRF-Token": csrf},
        json={"lock_token": assignment["lock_token"]},
    )
    assert response.status_code == 204, response.text
    response = await client.post(
        f"/api/v1/dialer/customers/{customer['id']}/heartbeat",
        headers={"X-CSRF-Token": csrf},
        json={"lock_token": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "dialer_lease_lost"

    response = await client.post(
        f"/api/v1/dialer/customers/{customer['id']}/release",
        headers={"X-CSRF-Token": csrf},
        json={"lock_token": assignment["lock_token"]},
    )
    assert response.status_code == 204, response.text
    response = await client.get("/api/v1/dialer/current")
    assert response.status_code == 200, response.text
    assert response.json() is None


async def test_operator_cannot_take_callback_assigned_to_another_user(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    auth, owner_csrf = await register(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    customer = await create_customer(client, owner_csrf, phone="+998961234567")
    response = await client.post(
        "/api/v1/callbacks",
        headers={"X-CSRF-Token": owner_csrf},
        json={
            "customer_id": customer["id"],
            "due_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            "note": "Персональный перезвон владельца",
        },
    )
    assert response.status_code == 201, response.text

    email, password = await add_operator(tenant_id=tenant_id, suffix=unique_suffix)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator_client:
        login = await operator_client.post(
            "/api/v1/auth/login",
            json={
                "company_slug": f"test-{unique_suffix}",
                "email": email,
                "password": password,
            },
        )
        assert login.status_code == 200, login.text
        operator_csrf = login.json()["csrf_token"]
        response = await operator_client.get("/api/v1/callbacks?status=pending")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        response = await operator_client.post(
            "/api/v1/dialer/next-client",
            headers={"X-CSRF-Token": operator_csrf},
        )
        assert response.status_code == 200, response.text
        assert response.json() is None

    response = await client.post(
        "/api/v1/dialer/next-client",
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["customer"]["id"] == customer["id"]
    assert response.json()["source"] == "callback"


async def test_project_concurrent_call_limit_is_enforced(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    auth, owner_csrf = await register(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    project_id = response.json()["items"][0]["id"]
    response = await client.patch(
        f"/api/v1/projects/{project_id}",
        headers={"X-CSRF-Token": owner_csrf},
        json={"max_concurrent_calls": 1},
    )
    assert response.status_code == 200, response.text

    first_customer = await create_customer(client, owner_csrf, phone="+998971234567")
    await create_customer(client, owner_csrf, phone="+998981234567")
    email, password = await add_operator(tenant_id=tenant_id, suffix=unique_suffix)

    owner_assignment_response = await client.post(
        "/api/v1/dialer/next-client",
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert owner_assignment_response.status_code == 200, owner_assignment_response.text
    owner_assignment = owner_assignment_response.json()
    assert owner_assignment["customer"]["id"] == first_customer["id"]
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": owner_csrf},
        json={
            "customer_id": owner_assignment["customer"]["id"],
            "lock_token": owner_assignment["lock_token"],
            "from_number": "MOCK",
        },
    )
    assert response.status_code == 201, response.text

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator_client:
        login = await operator_client.post(
            "/api/v1/auth/login",
            json={
                "company_slug": f"test-{unique_suffix}",
                "email": email,
                "password": password,
            },
        )
        assert login.status_code == 200, login.text
        operator_csrf = login.json()["csrf_token"]
        response = await operator_client.post(
            "/api/v1/dialer/next-client",
            headers={"X-CSRF-Token": operator_csrf},
        )
        assert response.status_code == 200, response.text
        operator_assignment = response.json()
        assert operator_assignment is not None
        response = await operator_client.post(
            "/api/v1/calls/start",
            headers={"X-CSRF-Token": operator_csrf},
            json={
                "customer_id": operator_assignment["customer"]["id"],
                "lock_token": operator_assignment["lock_token"],
                "from_number": "MOCK",
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "project_call_limit"


async def test_expired_new_customer_lease_can_be_reclaimed(
    client: AsyncClient, unique_suffix: str, register: object
) -> None:
    auth, owner_csrf = await register(client, unique_suffix)  # type: ignore[operator]
    tenant_id = UUID(str(auth["tenant"]["id"]))
    customer = await create_customer(client, owner_csrf, phone="+998991234567")
    response = await client.post(
        "/api/v1/dialer/next-client",
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert response.status_code == 200, response.text
    first_token = response.json()["lock_token"]

    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        stored = await session.scalar(
            select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.id == UUID(str(customer["id"])),
            )
        )
        assert stored is not None
        stored.locked_until = datetime.now(UTC) - timedelta(seconds=1)

    email, password = await add_operator(tenant_id=tenant_id, suffix=unique_suffix)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator_client:
        login = await operator_client.post(
            "/api/v1/auth/login",
            json={
                "company_slug": f"test-{unique_suffix}",
                "email": email,
                "password": password,
            },
        )
        assert login.status_code == 200, login.text
        response = await operator_client.post(
            "/api/v1/dialer/next-client",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert response.status_code == 200, response.text
        assignment = response.json()
        assert assignment["customer"]["id"] == customer["id"]
        assert assignment["lock_token"] != first_token
