from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import AsyncClient

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def create_published_operator(client: AsyncClient, csrf: str, name: str = "Mira") -> str:
    response = await client.post(
        "/api/v1/ai-operators",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": name,
            "description": "Russian support operator",
            "system_instructions": (
                "Disclose that you are a virtual assistant, use only verified knowledge, "
                "and request a human operator when an answer is unknown."
            ),
            "allowed_languages": ["ru"],
            "allowed_tools": ["search_knowledge", "request_human_operator", "end_call"],
        },
    )
    assert response.status_code == 201, response.text
    operator_id = response.json()["id"]
    response = await client.post(
        f"/api/v1/ai-operators/{operator_id}/publish", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"]["status"] == "published"
    return operator_id


async def test_vertical_development_slice(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    operator_id = await create_published_operator(client, csrf)

    response = await client.post(
        "/api/v1/knowledge/text",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"vertical-knowledge-{unique_suffix}",
        },
        json={
            "title": "Working hours",
            "language": "ru",
            "content": "Служба поддержки работает с понедельника по пятницу с девяти до восемнадцати.",
        },
    )
    assert response.status_code == 201, response.text

    response = await client.post(
        "/api/v1/simulator/calls",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"start-{unique_suffix}"},
        json={
            "ai_operator_id": operator_id,
            "language": "ru",
            "customer_name": "Тестовый клиент",
            "customer_phone": "+998901234567",
        },
    )
    assert response.status_code == 201, response.text
    call = response.json()
    assert call["channel"] == "development_simulator"
    assert call["is_demo"] is True
    assert "виртуальным помощником" in call["transcript"][0]["text"]
    call_id = call["id"]

    response = await client.post(
        f"/api/v1/simulator/calls/{call_id}/messages",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"message-{unique_suffix}"},
        json={"text": "Когда работает служба поддержки?"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tool_name"] == "search_knowledge"
    assert "понедельника" in response.json()["assistant_segment"]["text"]

    response = await client.post(
        f"/api/v1/simulator/calls/{call_id}/finish",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"finish-{unique_suffix}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["generated_by"] == "development_deterministic"

    response = await client.get(f"/api/v1/calls/{call_id}")
    assert response.status_code == 200
    assert len(response.json()["transcript"]) == 3

    response = await client.get("/api/v1/analytics/dashboard")
    assert response.status_code == 200
    assert response.json()["calls_today"] >= 1
    assert response.json()["used_ai_minutes"] >= "1.0000"
    assert response.json()["is_demo"] is True
