from __future__ import annotations

import os
from collections.abc import AsyncIterator
from importlib import import_module
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url


def require_isolated_test_database() -> None:
    """Fail before importing the API if pytest could target a non-test database."""

    database_url = os.environ.get("DATABASE_URL", "")
    test_database_url = os.environ.get("TEST_DATABASE_URL", "")
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("API tests require APP_ENV=test")
    if not database_url or database_url != test_database_url:
        raise RuntimeError("API tests require DATABASE_URL to match TEST_DATABASE_URL")

    parsed = make_url(database_url)
    database_name = parsed.database or ""
    if not database_name.endswith(("_test", "_pytest")):
        raise RuntimeError("API tests require a database name ending in _test or _pytest")
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("API tests may only use an explicitly local test database")


require_isolated_test_database()

engine = import_module("teamora_api.db").engine
app = import_module("teamora_api.main").app


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
