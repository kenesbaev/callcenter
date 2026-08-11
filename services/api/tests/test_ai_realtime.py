from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient

from teamora_api.config import get_settings
from teamora_api.schemas.call_flows import normalize_language_code
from teamora_api.schemas.operators import AiOperatorCreate

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def create_published_operator(client: AsyncClient, csrf: str, name: str) -> str:
    response = await client.post(
        "/api/v1/ai-operators",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": name,
            "description": "Realtime test operator",
            "system_instructions": "Use verified knowledge and escalate safely when information is missing.",
            "allowed_languages": ["ru"],
            "allowed_tools": ["search_knowledge", "request_human_operator", "end_call"],
        },
    )
    assert response.status_code == 201, response.text
    operator_id = response.json()["id"]
    response = await client.post(
        f"/api/v1/ai-operators/{operator_id}/publish",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return operator_id


def signed_headers(payload: bytes) -> dict[str, str]:
    secret = get_settings().gateway_service_token
    assert secret is not None
    raw_secret = secret.get_secret_value()
    normalized_secret = raw_secret.removeprefix("whsec_")
    try:
        secret_bytes = base64.b64decode(normalized_secret, validate=True)
    except (ValueError, binascii.Error):
        secret_bytes = normalized_secret.encode()
    webhook_id = str(uuid4())
    timestamp = str(int(time.time()))
    signed = f"{webhook_id}.{timestamp}.".encode() + payload
    signature = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    return {
        "Content-Type": "application/json",
        "X-Teamora-Id": webhook_id,
        "X-Teamora-Timestamp": timestamp,
        "X-Teamora-Signature": f"v1,{signature}",
    }


async def signed_post(client: AsyncClient, path: str, body: dict[str, object]):
    payload = json.dumps(body, separators=(",", ":"), default=str).encode()
    return await client.post(path, headers=signed_headers(payload), content=payload)


async def create_ai_call(client: AsyncClient, csrf: str, suffix: str) -> tuple[dict[str, object], str]:
    operator_id = await create_published_operator(client, csrf, f"Voice {suffix}")
    response = await client.post(
        "/api/v1/simulator/calls",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"voice-call-{suffix}"},
        json={
            "ai_operator_id": operator_id,
            "language": "ru",
            "customer_name": "Voice Test",
            "customer_phone": "+998901234567",
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), operator_id


async def test_ai_realtime_session_event_transcript_usage_and_idempotency(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    call, _operator_id = await create_ai_call(client, csrf, unique_suffix)
    tenant_id = str(auth["tenant"]["id"])
    correlation_id = str(uuid4())
    response = await signed_post(
        client,
        "/api/v1/webhooks/ai-realtime/session",
        {
            "tenantId": tenant_id,
            "projectId": call["project_id"],
            "callId": call["id"],
            "requestedLanguage": "RU",
            "correlationId": correlation_id,
        },
    )
    assert response.status_code == 200, response.text
    configuration = response.json()
    assert configuration["provider"] == "mock"
    assert configuration["language"] == "ru"
    assert configuration["model"] == "mock-realtime-deterministic"
    assert configuration["vad"]["type"] == "server_vad"
    assert "request_human_transfer" in [item["name"] for item in configuration["tools"]]
    session_id = configuration["sessionId"]

    event_id = str(uuid4())
    event = {
        "tenantId": tenant_id,
        "projectId": call["project_id"],
        "callId": call["id"],
        "sessionId": session_id,
        "providerEventId": event_id,
        "eventType": "ai.session_started",
        "occurredAt": datetime.now(UTC).isoformat(),
        "providerTimestamp": datetime.now(UTC).isoformat(),
        "correlationId": correlation_id,
        "safePayload": {"provider_session_id": "mock-session-safe"},
    }
    first = await signed_post(client, "/api/v1/webhooks/ai-realtime/events", event)
    assert first.status_code == 202, first.text
    duplicate = await signed_post(client, "/api/v1/webhooks/ai-realtime/events", event)
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["state_version"] == first.json()["state_version"]

    transcript_event = {
        **event,
        "providerEventId": str(uuid4()),
        "eventType": "ai.transcript_updated",
        "safePayload": {
            "speaker": "customer",
            "text": "Karakalpak language is kaa, not Georgian ka.",
            "language": "kaa-Latn",
            "item_id": "customer-item-1",
            "final": True,
            "interrupted": False,
        },
    }
    response = await signed_post(client, "/api/v1/webhooks/ai-realtime/events", transcript_event)
    assert response.status_code == 202, response.text
    usage_event = {
        **event,
        "providerEventId": str(uuid4()),
        "eventType": "ai.usage_updated",
        "safePayload": {
            "input_audio_tokens": 12,
            "output_audio_tokens": 8,
            "input_text_tokens": 3,
        },
    }
    response = await signed_post(client, "/api/v1/webhooks/ai-realtime/events", usage_event)
    assert response.status_code == 202, response.text
    latency_event = {
        **event,
        "providerEventId": str(uuid4()),
        "eventType": "ai.response_completed",
        "safePayload": {
            "first_audio_latency_ms": 240,
            "response_latency_ms": 470,
        },
    }
    response = await signed_post(client, "/api/v1/webhooks/ai-realtime/events", latency_event)
    assert response.status_code == 202, response.text

    response = await client.get("/api/v1/ai-realtime/status")
    assert response.status_code == 200, response.text
    latency = response.json()["latency"]
    assert latency == {
        "metric": "first_audio_latency_ms",
        "samples": 1,
        "p50_ms": 240.0,
        "p95_ms": 240.0,
        "p99_ms": 240.0,
        "is_available": True,
    }

    response = await client.get(f"/api/v1/calls/{call['id']}")
    assert response.status_code == 200, response.text
    stored = response.json()["transcript"][-1]
    assert stored["language_code"] == "kaa-latn"
    assert stored["provider_item_id"] == "customer-item-1"
    assert stored["is_final"] is True

    response = await client.get(f"/api/v1/calls/{call['id']}/ai-session/details")
    assert response.status_code == 200, response.text
    details = response.json()
    assert details["session"]["state"] == "active"
    assert len(details["events"]) == 4
    assert len(details["usage"]) == 3
    assert all(item["pricing_available"] is False for item in details["usage"])


async def test_ai_realtime_tenant_scope_and_language_codes_are_distinct(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    call, _operator_id = await create_ai_call(client, csrf, unique_suffix)
    response = await signed_post(
        client,
        "/api/v1/webhooks/ai-realtime/session",
        {
            "tenantId": str(uuid4()),
            "projectId": call["project_id"],
            "callId": call["id"],
            "requestedLanguage": "ru",
            "correlationId": str(uuid4()),
        },
    )
    assert response.status_code == 404
    assert normalize_language_code("KA") == "ka"
    assert normalize_language_code("KAA-Latn") == "kaa-latn"
    assert normalize_language_code("KA") != normalize_language_code("KAA")
    payload = AiOperatorCreate(
        name="Language operator",
        system_instructions="Use the requested language and verified information only.",
        allowed_languages=["ka", "kaa-Latn", "kaa-Cyrl"],
    )
    assert payload.allowed_languages == ["ka", "kaa-latn", "kaa-cyrl"]
    assert auth["tenant"]["id"]
