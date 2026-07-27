from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from teamora_api.errors import ApiError
from teamora_api.webhooks import verify_standard_webhook


def signature(secret_bytes: bytes, webhook_id: str, timestamp: str, payload: bytes) -> str:
    signed = f"{webhook_id}.{timestamp}.".encode() + payload
    digest = hmac.new(secret_bytes, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def test_standard_webhook_signature_verifies() -> None:
    secret_bytes = b"test-signing-secret"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    payload = b'{"type":"realtime.call.incoming"}'
    verify_standard_webhook(
        payload=payload,
        webhook_id="wh_test",
        timestamp="1000",
        signature_header=signature(secret_bytes, "wh_test", "1000", payload),
        secret=secret,
        now=1000,
    )


def test_standard_webhook_signature_rejects_tampering() -> None:
    secret_bytes = b"test-signing-secret"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    with pytest.raises(ApiError, match="signature"):
        verify_standard_webhook(
            payload=b'{"changed":true}',
            webhook_id="wh_test",
            timestamp="1000",
            signature_header=signature(secret_bytes, "wh_test", "1000", b'{"changed":false}'),
            secret=secret,
            now=1000,
        )
