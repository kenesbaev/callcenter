from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import RoleName
from teamora_api.main import app
from teamora_api.models import Membership, Project, ProjectUser, User
from teamora_api.security import hash_password

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def create_customer(
    client: AsyncClient, csrf: str, *, phone: str, name: str = "Клиент банка"
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={
            "display_name": name,
            "phone": phone,
            "preferred_language": "ru",
            "custom_fields": {"segment": "retail"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_complete_operator_call_and_callback_flow(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(client, csrf, phone="+998901234567")

    response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assignment = response.json()
    assert assignment["customer"]["id"] == customer["id"]
    assert assignment["source"] == "new"

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
    assert call["status"] == "ringing"
    assert call["provider"] == "mock"
    assert call["to_number"] == customer["contacts"][0]["value"]

    response = await client.post(f"/api/v1/calls/{call['id']}/answer", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert response.json()["answered_at"] is not None

    response = await client.post(f"/api/v1/calls/{call['id']}/hangup", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"

    callback_at = datetime.now(UTC) + timedelta(hours=2)
    response = await client.post(
        f"/api/v1/calls/{call['id']}/result",
        headers={"X-CSRF-Token": csrf},
        json={
            "result": "callback",
            "comment": "Клиент попросил позвонить после обеда",
            "callback_at": callback_at.isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["customer_status"] == "callback"
    assert result["callback_task_id"] is not None

    response = await client.get("/api/v1/callbacks?status=pending")
    assert response.status_code == 200, response.text
    tasks = response.json()["items"]
    assert len(tasks) == 1
    assert tasks[0]["customer_id"] == customer["id"]
    assert tasks[0]["customer_phone"] == customer["contacts"][0]["value"]


async def test_due_callback_is_prioritized_before_new_customer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    callback_customer = await create_customer(client, csrf, phone="+998911234567", name="Перезвон")
    await create_customer(client, csrf, phone="+998921234567", name="Новый клиент")
    response = await client.post(
        "/api/v1/callbacks",
        headers={"X-CSRF-Token": csrf},
        json={
            "customer_id": callback_customer["id"],
            "due_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            "note": "Приоритетный перезвон",
        },
    )
    assert response.status_code == 201, response.text

    response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assignment = response.json()
    assert assignment["source"] == "callback"
    assert assignment["customer"]["id"] == callback_customer["id"]


async def test_customer_cannot_be_claimed_by_two_operators(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, owner_csrf = await register(client, unique_suffix)
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    await create_customer(client, owner_csrf, phone="+998931234567")

    operator_email = f"operator+{unique_suffix}@example.com"
    operator_password = "OperatorPass123!"  # noqa: S105 - isolated test credential
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert project is not None
        user = User(
            email=operator_email,
            display_name="Second Operator",
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as operator_client:
        login = await operator_client.post(
            "/api/v1/auth/login",
            json={
                "company_slug": f"test-{unique_suffix}",
                "email": operator_email,
                "password": operator_password,
            },
        )
        assert login.status_code == 200, login.text
        operator_csrf = login.json()["csrf_token"]
        owner_response, operator_response = await asyncio.gather(
            client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": owner_csrf}),
            operator_client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": operator_csrf}),
        )

    assert owner_response.status_code == 200, owner_response.text
    assert operator_response.status_code == 200, operator_response.text
    assignments = [owner_response.json(), operator_response.json()]
    assert sum(value is not None for value in assignments) == 1
