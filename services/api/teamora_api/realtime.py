from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from prometheus_client import Counter
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.dependencies import Principal
from teamora_api.enums import RoleName
from teamora_api.models import Call, CallbackTask, Membership, Project, ProjectUser, RealtimeEvent

REALTIME_SCHEMA_VERSION = 1
REALTIME_REDIS_CHANNEL = "kline:realtime:v1"
REALTIME_EVENTS_CREATED = Counter(
    "kline_realtime_events_created_total",
    "Durable realtime outbox events created",
    ["event_type"],
)

CALL_EVENT_TYPES = frozenset(
    {
        "call.created",
        "call.state_changed",
        "call.answered",
        "call.held",
        "call.resumed",
        "call.ended",
        "call.reconciled",
    }
)
TRANSFER_EVENT_TYPES = frozenset(
    {
        "transfer.requested",
        "transfer.started",
        "transfer.completed",
        "transfer.failed",
    }
)
DIALER_EVENT_TYPES = frozenset(
    {
        "dialer.assignment_created",
        "dialer.assignment_recovered",
        "dialer.assignment_released",
        "dialer.call_completed",
    }
)
TASK_EVENT_TYPES = frozenset(
    {"task.created", "task.updated", "task.completed", "task.cancelled", "callback.due"}
)
TEAM_EVENT_TYPES = frozenset(
    {
        "operator.presence_changed",
        "operator.status_changed",
        "team.member_blocked",
        "team.member_restored",
    }
)
ANALYTICS_EVENT_TYPES = frozenset({"analytics.invalidated"})
KNOWLEDGE_EVENT_TYPES = frozenset(
    {
        "knowledge.document_uploaded",
        "knowledge.document_processing",
        "knowledge.document_ready",
        "knowledge.document_failed",
        "knowledge.document_needs_ocr",
        "knowledge.revision_published",
        "knowledge.index_updated",
    }
)
BACKGROUND_EVENT_TYPES = frozenset(
    {
        "job.started",
        "job.progress",
        "job.completed",
        "job.failed",
        "job.cancelled",
        "import.progress",
        "import.completed",
        "retention.preview_ready",
        "retention.run_completed",
        "storage.issue_detected",
    }
)
REALTIME_EVENT_TYPES = (
    CALL_EVENT_TYPES
    | TRANSFER_EVENT_TYPES
    | DIALER_EVENT_TYPES
    | TASK_EVENT_TYPES
    | TEAM_EVENT_TYPES
    | ANALYTICS_EVENT_TYPES
    | KNOWLEDGE_EVENT_TYPES
    | BACKGROUND_EVENT_TYPES
)

_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "jwt",
        "token",
        "access_token",
        "refresh_token",
        "invitation_token",
        "sip_credentials",
        "openai_api_key",
        "transcript",
        "recording",
        "phone",
        "phone_number",
    }
)
_MANAGER_ROLES = frozenset({RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER, RoleName.ANALYST})


def _validate_payload(payload: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in payload.items():
        normalized_key = key.lower()
        if normalized_key in _SENSITIVE_PAYLOAD_KEYS or any(
            marker in normalized_key for marker in ("secret", "credential")
        ):
            raise ValueError(f"Sensitive realtime payload key is forbidden: {key}")
        if isinstance(value, str):
            safe[key] = value[:500]
        elif isinstance(value, (bool, int, float)) or value is None:
            safe[key] = value
        elif isinstance(value, UUID):
            safe[key] = str(value)
        else:
            raise ValueError(f"Realtime payload values must be scalar: {key}")
    return safe


async def enqueue_realtime_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    project_id: UUID | None = None,
    target_membership_id: UUID | None = None,
    aggregate_version: int | None = None,
    payload: Mapping[str, object] | None = None,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
    causation_id: UUID | None = None,
    retention_hours: int = 24,
) -> RealtimeEvent:
    if event_type not in REALTIME_EVENT_TYPES:
        raise ValueError(f"Unsupported realtime event type: {event_type}")
    timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC)
    event = RealtimeEvent(
        tenant_id=tenant_id,
        project_id=project_id,
        target_membership_id=target_membership_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        safe_payload=_validate_payload(payload or {}),
        occurred_at=timestamp,
        publish_status="pending",
        publish_attempts=0,
        expires_at=timestamp + timedelta(hours=retention_hours),
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    session.add(event)
    REALTIME_EVENTS_CREATED.labels(event_type=event_type).inc()
    return event


async def enqueue_analytics_invalidation(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    correlation_id: str | None,
    reason: str,
) -> RealtimeEvent:
    return await enqueue_realtime_event(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        event_type="analytics.invalidated",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload={"reason": reason},
        correlation_id=correlation_id,
    )


async def allowed_project_ids(session: AsyncSession, principal: Principal) -> set[UUID]:
    statement = select(Project.id).where(Project.tenant_id == principal.tenant_id)
    if principal.role not in _MANAGER_ROLES:
        statement = statement.join(
            ProjectUser,
            and_(
                ProjectUser.tenant_id == Project.tenant_id,
                ProjectUser.project_id == Project.id,
            ),
        ).where(
            ProjectUser.user_id == principal.user_id,
            ProjectUser.is_active.is_(True),
        )
    return set(await session.scalars(statement))


def event_envelope(event: RealtimeEvent) -> dict[str, object]:
    return {
        "schema_version": REALTIME_SCHEMA_VERSION,
        "event_id": str(event.id),
        "cursor": event.cursor,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "project_id": str(event.project_id) if event.project_id else None,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": str(event.aggregate_id),
        "aggregate_version": event.aggregate_version,
        "payload": event.safe_payload,
    }


async def replay_events(
    session: AsyncSession,
    *,
    principal: Principal,
    cursor: int,
    project_ids: set[UUID],
    limit: int,
) -> tuple[list[RealtimeEvent], bool, int]:
    visible_projects = await allowed_project_ids(session, principal)
    requested_projects = visible_projects & project_ids if project_ids else visible_projects
    oldest = await session.scalar(
        select(func.min(RealtimeEvent.cursor)).where(
            RealtimeEvent.tenant_id == principal.tenant_id,
            RealtimeEvent.expires_at > datetime.now(UTC),
        )
    )
    resync_required = cursor > 0 and (oldest is None or cursor < oldest)
    if resync_required:
        return [], True, cursor

    visibility = [RealtimeEvent.project_id.is_(None)]
    if requested_projects:
        visibility.append(RealtimeEvent.project_id.in_(requested_projects))
    events = list(
        await session.scalars(
            select(RealtimeEvent)
            .where(
                RealtimeEvent.tenant_id == principal.tenant_id,
                RealtimeEvent.cursor > cursor,
                RealtimeEvent.expires_at > datetime.now(UTC),
                or_(*visibility),
                or_(
                    RealtimeEvent.target_membership_id.is_(None),
                    RealtimeEvent.target_membership_id == principal.membership_id,
                ),
            )
            .order_by(RealtimeEvent.cursor)
            .limit(limit)
        )
    )
    scanned_cursor = max((event.cursor for event in events), default=cursor)
    if principal.role == RoleName.HUMAN_OPERATOR and events:
        events = await _filter_operator_events(
            session,
            principal=principal,
            events=events,
            project_ids=requested_projects,
        )
    return events, False, scanned_cursor


async def _filter_operator_events(
    session: AsyncSession,
    *,
    principal: Principal,
    events: list[RealtimeEvent],
    project_ids: set[UUID],
) -> list[RealtimeEvent]:
    call_ids = {event.aggregate_id for event in events if event.aggregate_type == "call"}
    own_calls = set()
    if call_ids:
        own_calls = set(
            await session.scalars(
                select(Call.id).where(
                    Call.tenant_id == principal.tenant_id,
                    Call.id.in_(call_ids),
                    Call.operator_user_id == principal.user_id,
                )
            )
        )
    task_ids = {event.aggregate_id for event in events if event.aggregate_type == "task"}
    visible_tasks = set()
    if task_ids:
        visible_tasks = set(
            await session.scalars(
                select(CallbackTask.id).where(
                    CallbackTask.tenant_id == principal.tenant_id,
                    CallbackTask.id.in_(task_ids),
                    or_(
                        CallbackTask.assigned_user_id.is_(None),
                        CallbackTask.assigned_user_id == principal.user_id,
                    ),
                )
            )
        )
    membership_ids = {event.aggregate_id for event in events if event.aggregate_type == "membership"}
    visible_memberships = {principal.membership_id}
    if membership_ids and project_ids:
        visible_memberships.update(
            await session.scalars(
                select(Membership.id)
                .join(
                    ProjectUser,
                    and_(
                        ProjectUser.tenant_id == Membership.tenant_id,
                        ProjectUser.user_id == Membership.user_id,
                    ),
                )
                .where(
                    Membership.tenant_id == principal.tenant_id,
                    Membership.id.in_(membership_ids),
                    ProjectUser.project_id.in_(project_ids),
                    ProjectUser.is_active.is_(True),
                )
            )
        )
    visible: list[RealtimeEvent] = []
    for event in events:
        if event.aggregate_type == "call" and event.aggregate_id not in own_calls:
            continue
        if event.aggregate_type == "task" and event.aggregate_id not in visible_tasks:
            continue
        if event.aggregate_type == "membership" and event.aggregate_id not in visible_memberships:
            continue
        visible.append(event)
    return visible


def normalize_requested_projects(values: Sequence[str], *, maximum: int) -> set[UUID]:
    if len(values) > maximum:
        raise ValueError("Too many realtime project subscriptions")
    return {UUID(value) for value in values}
