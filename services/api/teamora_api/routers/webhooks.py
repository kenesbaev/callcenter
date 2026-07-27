from __future__ import annotations

import json

from fastapi import APIRouter, Request

from teamora_api.config import get_settings
from teamora_api.errors import ApiError
from teamora_api.webhooks import verify_standard_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/openai", status_code=202)
async def openai_webhook(request: Request) -> dict[str, object]:
    settings = get_settings()
    if settings.openai_webhook_secret is None:
        raise ApiError(503, "webhook_unavailable", "OpenAI webhook verification is not configured")
    payload = await request.body()
    verify_standard_webhook(
        payload=payload,
        webhook_id=request.headers.get("webhook-id", ""),
        timestamp=request.headers.get("webhook-timestamp", ""),
        signature_header=request.headers.get("webhook-signature", ""),
        secret=settings.openai_webhook_secret.get_secret_value(),
    )
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "webhook_payload_invalid", "Webhook payload is not valid JSON") from exc
    return {"accepted": True, "event_type": event.get("type", "unknown")}


@router.post("/telephony", status_code=202)
async def telephony_webhook(request: Request) -> dict[str, object]:
    settings = get_settings()
    if settings.gateway_service_token is None:
        raise ApiError(503, "webhook_unavailable", "Telephony webhook verification is not configured")
    payload = await request.body()
    verify_standard_webhook(
        payload=payload,
        webhook_id=request.headers.get("x-teamora-id", ""),
        timestamp=request.headers.get("x-teamora-timestamp", ""),
        signature_header=request.headers.get("x-teamora-signature", ""),
        secret=settings.gateway_service_token.get_secret_value(),
    )
    return {"accepted": True, "status": "development_contract_only"}
