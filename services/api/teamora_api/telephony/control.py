from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.enums import CallDirection
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallDetailRecord,
    CallRecording,
    SipTrunk,
    TelephonyChannelReservation,
    TelephonyResource,
    TenantSettings,
)


def mask_number(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:3]}{'*' * max(2, len(value) - 6)}{value[-3:]}"


def number_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def reserve_channel(
    session: AsyncSession,
    *,
    call: Call,
    trunk_id: UUID,
    direction: CallDirection,
) -> TelephonyChannelReservation:
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"sip-trunk:{trunk_id}", 0)))
    )
    existing = await session.scalar(
        select(TelephonyChannelReservation).where(
            TelephonyChannelReservation.tenant_id == call.tenant_id,
            TelephonyChannelReservation.call_id == call.id,
        )
    )
    if existing is not None:
        if existing.status == "reserved":
            return existing
        raise ApiError(409, "telephony_channel_already_released", "Call channel was already released")
    trunk = await session.scalar(
        select(SipTrunk)
        .where(SipTrunk.tenant_id == call.tenant_id, SipTrunk.id == trunk_id)
        .with_for_update()
    )
    if trunk is None:
        raise ApiError(404, "sip_trunk_not_found", "SIP trunk was not found")
    pool_key = "shared" if trunk.channel_pool_mode == "shared" else direction.value
    filters = [
        TelephonyChannelReservation.tenant_id == call.tenant_id,
        TelephonyChannelReservation.sip_trunk_id == trunk.id,
        TelephonyChannelReservation.status == "reserved",
    ]
    if trunk.channel_pool_mode == "separate":
        filters.append(TelephonyChannelReservation.direction == direction.value)
    used = int(
        await session.scalar(select(func.count()).select_from(TelephonyChannelReservation).where(*filters))
        or 0
    )
    limit = trunk.max_channels
    if trunk.channel_pool_mode == "separate":
        scoped = (
            trunk.inbound_channel_limit
            if direction == CallDirection.INBOUND
            else trunk.outbound_channel_limit
        )
        limit = scoped or trunk.max_channels
    if used >= limit:
        raise ApiError(409, "sip_channel_limit", "SIP trunk channel limit is exhausted")
    reservation = TelephonyChannelReservation(
        tenant_id=call.tenant_id,
        project_id=call.project_id,
        sip_trunk_id=trunk.id,
        call_id=call.id,
        direction=direction.value,
        pool_key=pool_key,
        status="reserved",
        heartbeat_at=datetime.now(UTC),
    )
    call.sip_trunk_id = trunk.id
    session.add(reservation)
    await session.flush()
    return reservation


async def bind_provider_channel(session: AsyncSession, *, call: Call, provider_channel_id: str) -> None:
    reservation = await session.scalar(
        select(TelephonyChannelReservation).where(
            TelephonyChannelReservation.tenant_id == call.tenant_id,
            TelephonyChannelReservation.call_id == call.id,
            TelephonyChannelReservation.status == "reserved",
        )
    )
    if reservation is not None:
        reservation.provider_channel_id = provider_channel_id[:200]
        reservation.heartbeat_at = datetime.now(UTC)


async def release_call_channel(session: AsyncSession, *, call: Call, reason: str) -> None:
    reservation = await session.scalar(
        select(TelephonyChannelReservation)
        .where(
            TelephonyChannelReservation.tenant_id == call.tenant_id,
            TelephonyChannelReservation.call_id == call.id,
        )
        .with_for_update()
    )
    if reservation is None or reservation.status != "reserved":
        return
    reservation.status = "released"
    reservation.released_at = datetime.now(UTC)
    reservation.release_reason = reason[:80]
    reservation.lock_version += 1
    # SessionFactory intentionally disables autoflush. Make released capacity
    # visible to a same-transaction replacement reservation.
    await session.flush()


async def release_call_resources(session: AsyncSession, *, call: Call) -> None:
    resources = list(
        await session.scalars(
            select(TelephonyResource)
            .where(
                TelephonyResource.tenant_id == call.tenant_id,
                TelephonyResource.call_id == call.id,
                TelephonyResource.status.in_({"creating", "active", "stopping"}),
            )
            .with_for_update()
        )
    )
    now = datetime.now(UTC)
    for resource in resources:
        resource.status = "released"
        resource.released_at = resource.released_at or now
        resource.last_seen_at = now
        resource.lock_version += 1
    await session.flush()


async def record_resource(
    session: AsyncSession,
    *,
    call: Call,
    resource_type: str,
    provider_resource_id: str,
    status: str,
    parent_provider_resource_id: str | None = None,
    safe_metadata: dict[str, object] | None = None,
) -> TelephonyResource:
    resource = await session.scalar(
        select(TelephonyResource).where(
            TelephonyResource.tenant_id == call.tenant_id,
            TelephonyResource.resource_type == resource_type,
            TelephonyResource.provider_resource_id == provider_resource_id,
        )
    )
    if resource is None:
        resource = TelephonyResource(
            tenant_id=call.tenant_id,
            project_id=call.project_id,
            call_id=call.id,
            resource_type=resource_type,
            provider_resource_id=provider_resource_id[:200],
            lock_version=1,
        )
        session.add(resource)
    elif resource.call_id != call.id:
        raise ApiError(409, "telephony_resource_scope_mismatch", "Provider resource belongs to another call")
    resource.status = status
    resource.parent_provider_resource_id = (
        parent_provider_resource_id[:200] if parent_provider_resource_id else None
    )
    resource.safe_metadata = safe_metadata or {}
    resource.last_seen_at = datetime.now(UTC)
    if status in {"released", "orphaned", "failed"}:
        resource.released_at = datetime.now(UTC)
    if resource.id is not None:
        resource.lock_version += 1
    await session.flush()
    return resource


async def finalize_cdr(session: AsyncSession, *, call: Call) -> CallDetailRecord | None:
    if call.sip_trunk_id is None:
        return None
    cdr = await session.scalar(
        select(CallDetailRecord).where(
            CallDetailRecord.tenant_id == call.tenant_id,
            CallDetailRecord.call_id == call.id,
        )
    )
    duration = 0
    if call.started_at and call.ended_at and call.ended_at >= call.started_at:
        duration = int((call.ended_at - call.started_at).total_seconds())
    if cdr is None:
        cdr = CallDetailRecord(
            tenant_id=call.tenant_id,
            project_id=call.project_id,
            call_id=call.id,
            sip_trunk_id=call.sip_trunk_id,
            direction=call.direction.value,
            disposition=call.status.value,
        )
        session.add(cdr)
    cdr.external_call_id = call.external_call_id
    cdr.did_masked = mask_number(
        call.to_number if call.direction == CallDirection.INBOUND else call.from_number
    )
    cdr.destination_masked = mask_number(
        call.from_number if call.direction == CallDirection.INBOUND else call.to_number
    )
    cdr.started_at = call.started_at
    cdr.answered_at = call.answered_at
    cdr.ended_at = call.ended_at
    cdr.disposition = call.status.value
    cdr.hangup_cause = call.hangup_cause.value if call.hangup_cause else None
    codec = (call.provider_metadata or {}).get("codec")
    cdr.codec = codec if isinstance(codec, str) else None
    cdr.duration_seconds = duration
    # Billable duration remains null until a trusted provider CDR supplies it.
    cdr.billable_duration_seconds = None
    return cdr


async def schedule_recording_upload(
    session: AsyncSession,
    *,
    call: Call,
    provider_recording_id: str,
    correlation_id: str,
) -> CallRecording:
    from teamora_api.background_service import enqueue_background_job

    recording = await session.scalar(
        select(CallRecording).where(
            CallRecording.tenant_id == call.tenant_id,
            CallRecording.provider_recording_id == provider_recording_id,
        )
    )
    if recording is not None:
        return recording
    settings = await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == call.tenant_id))
    retention_days = settings.retention_days if settings is not None else 90
    recording_id = uuid4()
    object_key = f"tenants/{call.tenant_id}/projects/{call.project_id}/recordings/{recording_id}.wav"
    recording = CallRecording(
        id=recording_id,
        tenant_id=call.tenant_id,
        call_id=call.id,
        storage_key=object_key,
        provider_recording_id=provider_recording_id[:200],
        encryption_key_ref="minio-sse",
        content_type="audio/wav",
        size_bytes=0,
        status="pending_upload",
        delete_after=datetime.now(UTC) + timedelta(days=retention_days),
        retention_state="retained",
    )
    session.add(recording)
    await session.flush()
    await enqueue_background_job(
        session,
        tenant_id=call.tenant_id,
        project_id=call.project_id,
        job_type="recording.upload",
        queue="media",
        priority=80,
        safe_payload={"recording_id": str(recording.id)},
        correlation_id=correlation_id,
        idempotency_key=f"recording-upload:{recording.id}",
        max_attempts=8,
    )
    return recording
