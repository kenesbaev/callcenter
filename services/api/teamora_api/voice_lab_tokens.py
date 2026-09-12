from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

VOICE_LAB_AUDIENCE = "teamora-voice-lab"
VOICE_LAB_SIGNATURE_PREFIX = b"teamora-voice-lab:v1."


def create_voice_lab_ticket(
    *,
    secret: str,
    tenant_id: UUID,
    user_id: UUID,
    language: str,
    ttl_seconds: int = 60,
) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    payload = {
        "aud": VOICE_LAB_AUDIENCE,
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(18),
        "language": language,
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "v": 1,
    }
    encoded = _base64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(
        secret.encode(),
        VOICE_LAB_SIGNATURE_PREFIX + encoded.encode(),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_base64url(signature)}", expires_at


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
