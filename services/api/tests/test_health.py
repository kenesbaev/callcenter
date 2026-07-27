from __future__ import annotations

import pytest
from httpx import AsyncClient

from teamora_api.routers import health


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


async def test_readiness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok"}


async def test_readiness_fails_fast_when_postgres_is_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(_: float) -> str:
        return "unavailable"

    async def available(_: float) -> str:
        return "ok"

    monkeypatch.setattr(health, "_postgres_status", unavailable)
    monkeypatch.setattr(health, "_redis_status", available)

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"postgres": "unavailable", "redis": "ok"},
    }
