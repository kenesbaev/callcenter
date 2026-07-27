from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from teamora_api.db import engine
from teamora_api.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as value:
        yield value


@pytest.fixture(scope="session", autouse=True)
async def close_database_pool() -> AsyncIterator[None]:
    yield
    await engine.dispose()


@pytest.fixture
def unique_suffix() -> str:
    return uuid4().hex[:12]


async def register_tenant(client: AsyncClient, suffix: str) -> tuple[dict[str, object], str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "company_name": f"Test Company {suffix}",
            "company_slug": f"test-{suffix}",
            "display_name": "Test Owner",
            "email": f"owner+{suffix}@example.com",
            "password": "SecurePass123!",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, payload["csrf_token"]


@pytest.fixture
def register() -> object:
    return register_tenant
