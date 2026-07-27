from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.main import app
from teamora_api.models import Call
from tests.test_vertical_slice import create_published_operator

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def test_other_tenant_cannot_read_call(unique_suffix: str, register: Register) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_a:
        auth_a, csrf_a = await register(tenant_a, f"a-{unique_suffix}")
        operator_id = await create_published_operator(tenant_a, csrf_a, "Tenant A operator")
        response = await tenant_a.post(
            "/api/v1/simulator/calls",
            headers={"X-CSRF-Token": csrf_a, "Idempotency-Key": f"tenant-a-{unique_suffix}"},
            json={
                "ai_operator_id": operator_id,
                "language": "ru",
                "customer_name": "Tenant A secret customer",
                "customer_phone": "+998907654321",
            },
        )
        assert response.status_code == 201, response.text
        call_id = response.json()["id"]
        tenant_a_id = UUID(auth_a["tenant"]["id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as tenant_b:
        auth_b, _csrf_b = await register(tenant_b, f"b-{unique_suffix}")
        response = await tenant_b.get(f"/api/v1/calls/{call_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "call_not_found"
        assert "Tenant A" not in response.text
        tenant_b_id = UUID(auth_b["tenant"]["id"])

    async with SessionFactory.begin() as session_a:
        await set_tenant_context(session_a, tenant_a_id)
        visible_to_a = await session_a.scalar(select(Call).where(Call.id == UUID(call_id)))
        assert visible_to_a is not None

    async with SessionFactory.begin() as session_b:
        await set_tenant_context(session_b, tenant_b_id)
        visible_to_b = await session_b.scalar(select(Call).where(Call.id == UUID(call_id)))
        assert visible_to_b is None
