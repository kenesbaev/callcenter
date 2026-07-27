from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import AsyncClient

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def test_registration_sets_http_only_session_and_me(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    payload, csrf = await register(client, unique_suffix)

    assert payload["user"]["role"] == "tenant_owner"
    assert payload["tenant"]["slug"] == f"test-{unique_suffix}"
    assert client.cookies.get("tv_access")
    assert client.cookies.get("tv_refresh")
    assert client.cookies.get("tv_csrf") == csrf

    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["tenant"]["id"] == payload["tenant"]["id"]


async def test_mutation_rejects_missing_csrf(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    await register(client, unique_suffix)
    response = await client.post(
        "/api/v1/ai-operators",
        json={
            "name": "No CSRF",
            "system_instructions": (
                "Only answer from verified tenant knowledge and transfer unknown requests."
            ),
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


async def test_refresh_rotates_refresh_token(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _, csrf = await register(client, unique_suffix)
    previous = client.cookies.get("tv_refresh")
    response = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert client.cookies.get("tv_refresh") != previous


async def test_registration_rejects_duplicate_workspace_slug(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    await register(client, unique_suffix)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "company_name": "Duplicate Company",
            "company_slug": f"test-{unique_suffix}",
            "display_name": "Another Owner",
            "email": f"another+{unique_suffix}@example.com",
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tenant_slug_taken"
