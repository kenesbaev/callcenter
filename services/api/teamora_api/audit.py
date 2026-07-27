from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.models import AuditLog


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
        correlation_id=correlation_id,
        reason=reason,
        safe_metadata=safe_metadata or {},
    )
    session.add(record)
    return record
