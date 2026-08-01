from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.main import app
from teamora_api.models import AuditLog
from tests.test_vertical_slice import create_published_operator

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def test_team_integrations_and_settings_are_real_tenant_scoped_views(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)

    response = await client.get("/api/v1/team")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["display_name"] == "Test Owner"
    assert response.json()["items"][0]["role"] == "tenant_owner"

    response = await client.get("/api/v1/integrations")
    assert response.status_code == 200, response.text
    providers = {item["provider"]: item for item in response.json()}
    assert set(providers) >= {"generic_webhook", "bitrix24", "amocrm", "google_sheets"}
    assert all(item["can_configure"] is False for item in providers.values())
    assert all(item["status"] == "unavailable" for item in providers.values())

    response = await client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    assert response.json()["default_language"] == "ru"

    response = await client.patch(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"retention_days": 45, "max_concurrent_calls": 8},
    )
    assert response.status_code == 200, response.text
    assert response.json()["retention_days"] == 45
    assert response.json()["max_concurrent_calls"] == 8

    tenant_id = UUID(auth["tenant"]["id"])
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == "tenant.settings.updated",
            )
        )
        assert audit is not None
        assert audit.safe_metadata == {"changed_fields": ["max_concurrent_calls", "retention_days"]}


async def test_live_calls_do_not_leak_between_tenants(unique_suffix: str, register: Register) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_a:
        _auth_a, csrf_a = await register(tenant_a, f"live-a-{unique_suffix}")
        operator_id = await create_published_operator(tenant_a, csrf_a, "Live operator A")
        response = await tenant_a.post(
            "/api/v1/simulator/calls",
            headers={
                "X-CSRF-Token": csrf_a,
                "Idempotency-Key": f"live-start-a-{unique_suffix}",
            },
            json={
                "ai_operator_id": operator_id,
                "language": "ru",
                "customer_name": "Private tenant A customer",
                "customer_phone": "+998901112233",
            },
        )
        assert response.status_code == 201, response.text
        call_id = response.json()["id"]

        response = await tenant_a.get("/api/v1/live-calls")
        assert response.status_code == 200
        assert [call["id"] for call in response.json()] == [call_id]

    async with AsyncClient(transport=transport, base_url="http://testserver") as tenant_b:
        await register(tenant_b, f"live-b-{unique_suffix}")
        response = await tenant_b.get("/api/v1/live-calls")
        assert response.status_code == 200
        assert call_id not in response.text
        assert "Private tenant A customer" not in response.text


async def test_recording_requires_customer_disclosure(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    response = await client.patch(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"recording_enabled": True, "recording_disclosure_required": False},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "recording_disclosure_required"
