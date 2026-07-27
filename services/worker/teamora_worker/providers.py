from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Literal, Protocol

import httpx

ProviderStatus = Literal["unavailable", "development", "configured", "verified"]


class CrmProvider(Protocol):
    name: str
    status: ProviderStatus

    async def execute(
        self, operation: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]: ...


class NotificationProvider(Protocol):
    name: str
    status: ProviderStatus

    async def send(
        self, template: str, recipient: str, safe_data: dict[str, Any], idempotency_key: str
    ) -> None: ...


class ObjectStorageProvider(Protocol):
    async def delete(self, tenant_id: str, key: str) -> None: ...


class ProviderUnavailableError(RuntimeError):
    pass


class UnavailableCrmAdapter:
    status: ProviderStatus = "unavailable"

    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(
        self, operation: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        del operation, payload, idempotency_key
        raise ProviderUnavailableError(
            f"{self.name} is unavailable until credentials and mapping validation are complete"
        )


class GenericWebhookCrmProvider:
    """Calls one server-configured destination; the model can never select a URL."""

    name = "generic-webhook"
    status: ProviderStatus = "configured"
    allowed_operations = {"create_lead", "create_support_ticket", "sync_call_summary"}

    def __init__(self, *, endpoint: str, signing_secret: str, client: httpx.AsyncClient) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Generic CRM webhooks require HTTPS")
        self._endpoint = endpoint
        self._secret = signing_secret.encode()
        self._client = client

    async def execute(
        self, operation: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if operation not in self.allowed_operations:
            raise ValueError("Operation is not allowed by the CRM adapter")
        body = json.dumps(
            {"operation": operation, "data": payload}, separators=(",", ":"), sort_keys=True
        ).encode()
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        response = await self._client.post(
            self._endpoint,
            content=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
                "X-Teamora-Signature": f"sha256={signature}",
            },
            timeout=8,
        )
        response.raise_for_status()
        return {"accepted": True, "status_code": response.status_code}


BITRIX24 = UnavailableCrmAdapter("bitrix24")
AMOCRM = UnavailableCrmAdapter("amocrm")
GOOGLE_SHEETS = UnavailableCrmAdapter("google-sheets")
