from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.models import CallEvent


async def append_call_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    call_id: UUID,
    event_type: str,
    safe_payload: dict[str, object] | None = None,
    provider: str | None = None,
    provider_event_id: str | None = None,
    external_call_id: str | None = None,
    occurred_at: datetime | None = None,
    provider_timestamp: datetime | None = None,
    correlation_id: str | None = None,
) -> CallEvent:
    persisted_sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(CallEvent.sequence), 0)).where(
                CallEvent.tenant_id == tenant_id,
                CallEvent.call_id == call_id,
            )
        )
        or 0
    )
    pending_sequence = max(
        (
            event.sequence
            for event in session.new
            if isinstance(event, CallEvent) and event.tenant_id == tenant_id and event.call_id == call_id
        ),
        default=0,
    )
    sequence = max(persisted_sequence, pending_sequence)
    event = CallEvent(
        tenant_id=tenant_id,
        call_id=call_id,
        event_type=event_type,
        sequence=sequence + 1,
        safe_payload=safe_payload or {},
        provider=provider,
        provider_event_id=provider_event_id,
        external_call_id=external_call_id,
        occurred_at=occurred_at or datetime.now(UTC),
        provider_timestamp=provider_timestamp,
        correlation_id=correlation_id,
    )
    session.add(event)
    return event
