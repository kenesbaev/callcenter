from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.models import AuditLog


def normalize_audit_correlation_id(value: str) -> str:
    """Fit a correlation ID into the persistent audit schema without losing identity."""
    if len(value) <= 80:
        return value
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[:67]}:{digest}"


async def write_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    correlation_id: str,
    reason: str | None = None,
    safe_metadata: dict[str, object] | None = None,
) -> AuditLog:
    record = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=normalize_audit_correlation_id(correlation_id),
        reason=reason,
        safe_metadata=safe_metadata or {},
    )
    session.add(record)
    return record
