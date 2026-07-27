from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Response

from teamora_api.config import Settings
from teamora_api.enums import RoleName

ACCESS_COOKIE = "tv_access"
REFRESH_COOKIE = "tv_refresh"
CSRF_COOKIE = "tv_csrf"
JWT_ALGORITHM = "HS256"

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: RoleName
    expires_at: datetime


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(
    *, user_id: UUID, tenant_id: UUID, membership_id: UUID, role: RoleName, settings: Settings
) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "membership_id": str(membership_id),
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": "teamora-voice",
        "aud": "teamora-web",
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.jwt_signing_key.get_secret_value(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> AccessClaims | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience="teamora-web",
            issuer="teamora-voice",
        )
        return AccessClaims(
            user_id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tenant_id"]),
            membership_id=UUID(payload["membership_id"]),
            role=RoleName(payload["role"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None


def new_refresh_token(tenant_id: UUID) -> str:
    return f"{tenant_id}.{secrets.token_urlsafe(48)}"


def refresh_tenant_id(token: str) -> UUID | None:
    try:
        prefix, opaque = token.split(".", 1)
        if len(opaque) < 32:
            return None
        return UUID(prefix)
    except (ValueError, AttributeError):
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, *, access: str, refresh: str, csrf: str, settings: Settings) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        httponly=True,
        max_age=settings.access_token_ttl_minutes * 60,
        secure=settings.csrf_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        httponly=True,
        max_age=settings.refresh_token_ttl_days * 86400,
        secure=settings.csrf_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        max_age=settings.refresh_token_ttl_days * 86400,
        secure=settings.csrf_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for cookie in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            cookie,
            path="/",
            secure=settings.csrf_cookie_secure,
            httponly=cookie != CSRF_COOKIE,
            samesite="lax",
        )
