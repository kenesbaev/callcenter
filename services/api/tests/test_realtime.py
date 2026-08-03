from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from teamora_api.db import SessionFactory, set_tenant_context, tenant_transaction
from teamora_api.dependencies import Principal
from teamora_api.models import Membership, Project, RealtimeEvent
from teamora_api.realtime import enqueue_realtime_event, event_envelope, replay_events

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def _context(
    client: AsyncClient,
    suffix: str,
    register: Register,
) -> tuple[Principal, UUID]:
    auth, _csrf = await register(client, suffix)
    tenant = auth["tenant"]
    user = auth["user"]
    tenant_id = UUID(str(tenant["id"]))  # type: ignore[index]
    user_id = UUID(str(user["id"]))  # type: ignore[index]
    async with tenant_transaction(tenant_id) as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
        )
        project_id = await session.scalar(
            select(Project.id).where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
        )
        assert membership is not None and project_id is not None
        principal = Principal(
            user_id=user_id,
            tenant_id=tenant_id,
            membership_id=membership.id,
            role=membership.role,
            display_name=str(user["display_name"]),  # type: ignore[index]
            email=str(user["email"]),  # type: ignore[index]
            tenant_name=str(tenant["name"]),  # type: ignore[index]
            tenant_slug=str(tenant["slug"]),  # type: ignore[index]
        )
    return principal, project_id


async def test_outbox_event_commits_with_business_transaction(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    principal, project_id = await _context(client, unique_suffix, register)
    aggregate_id = uuid4()
    async with tenant_transaction(principal.tenant_id) as session:
        await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            project_id=project_id,
            event_type="analytics.invalidated",
            aggregate_type="project",
            aggregate_id=aggregate_id,
            payload={"reason": "test"},
        )
    async with tenant_transaction(principal.tenant_id) as session:
        event = await session.scalar(
            select(RealtimeEvent).where(
                RealtimeEvent.tenant_id == principal.tenant_id,
                RealtimeEvent.aggregate_id == aggregate_id,
            )
        )
        assert event is not None
        assert event.publish_status == "pending"
        assert event_envelope(event)["cursor"] == event.cursor


async def test_rollback_does_not_leave_realtime_event(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    principal, project_id = await _context(client, unique_suffix, register)
    aggregate_id = uuid4()
    with pytest.raises(RuntimeError, match="rollback"):
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, principal.tenant_id)
            await enqueue_realtime_event(
                session,
                tenant_id=principal.tenant_id,
                project_id=project_id,
                event_type="analytics.invalidated",
                aggregate_type="project",
                aggregate_id=aggregate_id,
                payload={"reason": "rollback"},
            )
            await session.flush()
            raise RuntimeError("rollback")
    async with tenant_transaction(principal.tenant_id) as session:
        assert (
            await session.scalar(select(RealtimeEvent.id).where(RealtimeEvent.aggregate_id == aggregate_id))
            is None
        )


async def test_payload_rejects_secrets_and_full_phone_data(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    principal, project_id = await _context(client, unique_suffix, register)
    async with tenant_transaction(principal.tenant_id) as session:
        with pytest.raises(ValueError, match="Sensitive realtime payload"):
            await enqueue_realtime_event(
                session,
                tenant_id=principal.tenant_id,
                project_id=project_id,
                event_type="analytics.invalidated",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={"access_token": "forbidden"},
            )
        with pytest.raises(ValueError, match="Sensitive realtime payload"):
            await enqueue_realtime_event(
                session,
                tenant_id=principal.tenant_id,
                project_id=project_id,
                event_type="analytics.invalidated",
                aggregate_type="project",
                aggregate_id=project_id,
                payload={"phone_number": "+998000000000"},
            )


async def test_replay_is_ordered_targeted_and_requires_resync_after_expiry(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    principal, project_id = await _context(client, unique_suffix, register)
    now = datetime.now(UTC)
    async with tenant_transaction(principal.tenant_id) as session:
        first = await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            project_id=project_id,
            event_type="dialer.assignment_created",
            aggregate_type="customer",
            aggregate_id=uuid4(),
            target_membership_id=principal.membership_id,
            payload={"source": "new"},
            occurred_at=now,
        )
        second = await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            project_id=project_id,
            event_type="analytics.invalidated",
            aggregate_type="project",
            aggregate_id=project_id,
            payload={"reason": "call_state_changed"},
            occurred_at=now + timedelta(milliseconds=1),
        )
        await session.flush()
        first_cursor = first.cursor
        second_cursor = second.cursor
    async with tenant_transaction(principal.tenant_id) as session:
        events, resync, scanned = await replay_events(
            session,
            principal=principal,
            cursor=0,
            project_ids={project_id},
            limit=20,
        )
        assert resync is False
        assert [event.cursor for event in events] == [first_cursor, second_cursor]
        assert scanned == second_cursor
        first = await session.scalar(select(RealtimeEvent).where(RealtimeEvent.cursor == first_cursor))
        assert first is not None
        first.expires_at = now - timedelta(seconds=1)
    async with tenant_transaction(principal.tenant_id) as session:
        events, resync, scanned = await replay_events(
            session,
            principal=principal,
            cursor=first_cursor,
            project_ids={project_id},
            limit=20,
        )
        assert events == []
        assert resync is True
        assert scanned == first_cursor


async def test_rls_hides_other_tenant_events(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    first, first_project = await _context(client, f"{unique_suffix}a", register)
    second, _second_project = await _context(client, f"{unique_suffix}b", register)
    aggregate_id = uuid4()
    async with tenant_transaction(first.tenant_id) as session:
        await enqueue_realtime_event(
            session,
            tenant_id=first.tenant_id,
            project_id=first_project,
            event_type="analytics.invalidated",
            aggregate_type="project",
            aggregate_id=aggregate_id,
            payload={"reason": "tenant_test"},
        )
    async with tenant_transaction(second.tenant_id) as session:
        assert (
            await session.scalar(select(RealtimeEvent.id).where(RealtimeEvent.aggregate_id == aggregate_id))
            is None
        )
