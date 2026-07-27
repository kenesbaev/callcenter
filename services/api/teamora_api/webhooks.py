from __future__ import annotations

import base64
import hashlib
import hmac
import time

from teamora_api.errors import ApiError


def _decode_secret(secret: str) -> bytes:
    value = secret.removeprefix("whsec_")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        return value.encode("utf-8")


def verify_standard_webhook(
    *,
    payload: bytes,
    webhook_id: str,
    timestamp: str,
    signature_header: str,
    secret: str,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> None:
    if not webhook_id or not timestamp or not signature_header:
        raise ApiError(400, "webhook_headers_missing", "Required webhook headers are missing")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise ApiError(400, "webhook_timestamp_invalid", "Webhook timestamp is invalid") from exc
    if abs((now or int(time.time())) - timestamp_value) > tolerance_seconds:
        raise ApiError(400, "webhook_timestamp_expired", "Webhook timestamp is outside the allowed window")

    signed = f"{webhook_id}.{timestamp}.".encode() + payload
    expected = base64.b64encode(hmac.new(_decode_secret(secret), signed, hashlib.sha256).digest()).decode()
    signatures = [part.split(",", 1)[1] for part in signature_header.split() if part.startswith("v1,")]
    if not signatures:
        signatures = [
            part.split(",", 1)[1] for part in signature_header.split(" ") if part.strip().startswith("v1,")
        ]
    if not any(hmac.compare_digest(expected, candidate.strip()) for candidate in signatures):
        raise ApiError(400, "webhook_signature_invalid", "Webhook signature is invalid")
