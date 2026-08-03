from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from teamora_api.call_state import CAPACITY_CALL_STATES
from teamora_api.dependencies import Principal
from teamora_api.enums import (
    CallChannel,
    CallerType,
    CallResultCategory,
    CallStatus,
    RoleName,
    TaskStatus,
    TransferStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallbackTask,
    CallEvent,
    CallOutcome,
    Customer,
    HumanOperator,
    Membership,
    OperatorPresence,
    OperatorStatus,
    Project,
    ProjectUser,
    TenantSettings,
    TransferRequest,
    UsageRecord,
    User,
)
from teamora_api.project_access import accessible_project_ids, accessible_projects_statement
from teamora_api.schemas.analytics import (
    AnalyticsFilterOptionsRead,
    AnalyticsOperatorOptionRead,
    AnalyticsPeriodRead,
    AnalyticsProjectOptionRead,
    AnalyticsSummaryRead,
    DistributionItemRead,
    MetricRead,
    OperatorPerformancePage,
    OperatorPerformanceRead,
    OperatorStatusSummaryRead,
    ProjectPerformancePage,
    ProjectPerformanceRead,
    RecentCallRead,
    RecentCallsPage,
    TaskSummaryRead,
    TimeSeriesPointRead,
    TransferMetricsRead,
)
from teamora_api.team_service import PRESENCE_TTL_SECONDS

MAX_PERIOD_DAYS = 366
ANALYTICS_ACTIVE_STATES = frozenset(
    {
        CallStatus.INITIATED,
        CallStatus.RINGING,
        CallStatus.ACTIVE,
        CallStatus.ON_HOLD,
        CallStatus.TRANSFER_REQUESTED,
        CallStatus.TRANSFERRING,
    }
)
AI_USAGE_METRICS = frozenset({"ai_minutes", "ai_audio_minutes", "openai_realtime_audio_minutes"})
MANAGER_ROLES = frozenset({RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER, RoleName.ANALYST})


@dataclass(frozen=True)
class AnalyticsScope:
    principal: Principal
    project_ids: tuple[UUID, ...]
    project_id: UUID | None
    operator_id: UUID | None
    direction: str | None
    caller_type: str | None
    timezone: str
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    bucket: str
    financial_metrics_visible: bool


@dataclass(frozen=True)
class AggregateValues:
    attempted: int
    connected: int
    successful: int
    with_outcome: int
    average_duration: float | None
    ai_calls: int
    human_calls: int
    simulator_calls: int
    sip_calls: int
    invalid_duration: int
    test_calls: int
    incomplete_metadata: int


def _parse_boundary(raw: str | None, *, zone: ZoneInfo, fallback: date) -> datetime:
    if not raw:
        return datetime.combine(fallback, time.min, tzinfo=zone).astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError(422, "analytics_period_invalid", "Некорректная граница периода") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


async def resolve_scope(
    session: AsyncSession,
    principal: Principal,
    *,
    project_id: UUID | None,
    date_from: str | None,
    date_to: str | None,
    timezone: str | None,
    operator_id: UUID | None,
    direction: str | None,
    caller_type: str | None,
) -> AnalyticsScope:
    allowed_projects = tuple(await accessible_project_ids(session, principal))
    if project_id is not None and project_id not in allowed_projects:
        raise ApiError(404, "project_not_found", "Проект не найден или недоступен")
    selected_projects = (project_id,) if project_id else allowed_projects

    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == principal.tenant_id)
    )
    default_timezone = settings.timezone if settings else "Asia/Tashkent"
    if project_id is not None:
        project_timezone = await session.scalar(
            select(Project.timezone).where(
                Project.tenant_id == principal.tenant_id,
                Project.id == project_id,
            )
        )
        default_timezone = project_timezone or default_timezone
    timezone_name = timezone or default_timezone
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ApiError(422, "analytics_timezone_invalid", "Неизвестный часовой пояс") from exc

    local_today = datetime.now(UTC).astimezone(zone).date()
    start = _parse_boundary(date_from, zone=zone, fallback=local_today)
    end = _parse_boundary(date_to, zone=zone, fallback=local_today + timedelta(days=1))
    if start >= end:
        raise ApiError(422, "analytics_period_invalid", "Конец периода должен быть позже начала")
    if end - start > timedelta(days=MAX_PERIOD_DAYS):
        raise ApiError(422, "analytics_period_too_large", f"Период не может превышать {MAX_PERIOD_DAYS} дней")

    if direction not in {None, "inbound", "outbound"}:
        raise ApiError(422, "analytics_direction_invalid", "Некорректное направление звонка")
    if caller_type not in {None, "human_operator", "ai_agent"}:
        raise ApiError(422, "analytics_caller_type_invalid", "Некорректный тип оператора")

    effective_operator_id = operator_id
    if principal.role == RoleName.HUMAN_OPERATOR:
        if operator_id is not None and operator_id != principal.user_id:
            raise ApiError(
                403, "analytics_operator_scope_denied", "Оператор видит только собственные показатели"
            )
        effective_operator_id = principal.user_id
    elif operator_id is not None:
        exists = await session.scalar(
            select(Membership.id).where(
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id == operator_id,
            )
        )
        if exists is None:
            raise ApiError(404, "analytics_operator_not_found", "Сотрудник не найден")

    duration = end - start
    return AnalyticsScope(
        principal=principal,
        project_ids=selected_projects,
        project_id=project_id,
        operator_id=effective_operator_id,
        direction=direction,
        caller_type=caller_type,
        timezone=timezone_name,
        start=start,
        end=end,
        previous_start=start - duration,
        previous_end=start,
        bucket="hour" if duration <= timedelta(hours=48) else "day",
        financial_metrics_visible=principal.role in MANAGER_ROLES,
    )


def _call_filters(scope: AnalyticsScope, *, start: datetime, end: datetime) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        Call.tenant_id == scope.principal.tenant_id,
        Call.project_id.in_(scope.project_ids),
        Call.started_at.is_not(None),
        Call.started_at >= start,
        Call.started_at < end,
    ]
    if scope.operator_id is not None:
        filters.append(Call.operator_user_id == scope.operator_id)
    if scope.direction is not None:
        filters.append(Call.direction == scope.direction)
    if scope.caller_type is not None:
        filters.append(Call.caller_type == scope.caller_type)
    return filters


async def _aggregate_calls(
    session: AsyncSession,
    scope: AnalyticsScope,
    *,
    start: datetime,
    end: datetime,
) -> AggregateValues:
    valid_duration = and_(
        Call.answered_at.is_not(None),
        Call.ended_at.is_not(None),
        Call.ended_at >= Call.answered_at,
    )
    row = (
        await session.execute(
            select(
                func.count(distinct(Call.id)),
                func.count(distinct(Call.id)).filter(Call.answered_at.is_not(None)),
                func.count(distinct(Call.id)).filter(CallOutcome.category == CallResultCategory.SUCCESSFUL),
                func.count(distinct(CallOutcome.call_id)),
                func.avg(func.extract("epoch", Call.ended_at - Call.answered_at)).filter(valid_duration),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.AI_AGENT),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.HUMAN_OPERATOR),
                func.count(distinct(Call.id)).filter(Call.channel == CallChannel.DEVELOPMENT_SIMULATOR),
                func.count(distinct(Call.id)).filter(Call.channel == CallChannel.SIP),
                func.count(distinct(Call.id)).filter(
                    Call.answered_at.is_not(None),
                    Call.ended_at.is_not(None),
                    Call.ended_at < Call.answered_at,
                ),
                func.count(distinct(Call.id)).filter(
                    or_(Call.is_demo.is_(True), Call.channel == CallChannel.DEVELOPMENT_SIMULATOR)
                ),
                func.count(distinct(Call.id)).filter(or_(Call.language.is_(None), Call.provider.is_(None))),
            )
            .select_from(Call)
            .outerjoin(
                CallOutcome,
                and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id),
            )
            .where(*_call_filters(scope, start=start, end=end))
        )
    ).one()
    return AggregateValues(
        attempted=int(row[0] or 0),
        connected=int(row[1] or 0),
        successful=int(row[2] or 0),
        with_outcome=int(row[3] or 0),
        average_duration=float(row[4]) if row[4] is not None else None,
        ai_calls=int(row[5] or 0),
        human_calls=int(row[6] or 0),
        simulator_calls=int(row[7] or 0),
        sip_calls=int(row[8] or 0),
        invalid_duration=int(row[9] or 0),
        test_calls=int(row[10] or 0),
        incomplete_metadata=int(row[11] or 0),
    )


def _change(current: float | None, previous: float | None) -> tuple[float | None, float | None]:
    if current is None or previous is None:
        return None, None
    absolute = current - previous
    percentage = None if previous == 0 else absolute / previous * 100
    return absolute, percentage


def metric(
    current: float | int | None,
    previous: float | int | None,
    *,
    numerator: float | int | None = None,
    denominator: float | int | None = None,
    available: bool = True,
    flags: list[str] | None = None,
) -> MetricRead:
    current_value = float(current) if current is not None else None
    previous_value = float(previous) if previous is not None else None
    absolute, percentage = _change(current_value, previous_value)
    return MetricRead(
        value=current_value,
        numerator=float(numerator) if numerator is not None else None,
        denominator=float(denominator) if denominator is not None else None,
        previous_value=previous_value,
        absolute_change=absolute,
        percentage_change=percentage,
        is_available=available,
        data_quality_flags=flags or [],
    )


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator * 100


async def _active_count(session: AsyncSession, scope: AnalyticsScope) -> int:
    filters: list[ColumnElement[bool]] = [
        Call.tenant_id == scope.principal.tenant_id,
        Call.project_id.in_(scope.project_ids),
        Call.status.in_(ANALYTICS_ACTIVE_STATES),
    ]
    if scope.operator_id is not None:
        filters.append(Call.operator_user_id == scope.operator_id)
    if scope.direction is not None:
        filters.append(Call.direction == scope.direction)
    if scope.caller_type is not None:
        filters.append(Call.caller_type == scope.caller_type)
    return int(await session.scalar(select(func.count()).select_from(Call).where(*filters)) or 0)


async def _transfer_counts(
    session: AsyncSession, scope: AnalyticsScope, *, start: datetime, end: datetime
) -> tuple[int, int, int]:
    call_filters: list[ColumnElement[bool]] = [
        Call.tenant_id == scope.principal.tenant_id,
        Call.project_id.in_(scope.project_ids),
    ]
    if scope.operator_id is not None:
        call_filters.append(Call.operator_user_id == scope.operator_id)
    if scope.direction is not None:
        call_filters.append(Call.direction == scope.direction)
    if scope.caller_type is not None:
        call_filters.append(Call.caller_type == scope.caller_type)
    event_rows = (
        await session.execute(
            select(CallEvent.event_type, func.count(distinct(CallEvent.call_id)))
            .join(Call, and_(Call.tenant_id == CallEvent.tenant_id, Call.id == CallEvent.call_id))
            .where(
                *call_filters,
                CallEvent.event_type.in_(["transfer.requested", "transfer.completed"]),
                CallEvent.occurred_at >= start,
                CallEvent.occurred_at < end,
            )
            .group_by(CallEvent.event_type)
        )
    ).all()
    event_counts = {event_type: int(count) for event_type, count in event_rows}
    failed = int(
        await session.scalar(
            select(func.count(distinct(TransferRequest.call_id))).where(
                TransferRequest.tenant_id == scope.principal.tenant_id,
                TransferRequest.call_id.in_(select(Call.id).where(*call_filters)),
                TransferRequest.status == TransferStatus.FAILED,
                func.coalesce(TransferRequest.resolved_at, TransferRequest.requested_at) >= start,
                func.coalesce(TransferRequest.resolved_at, TransferRequest.requested_at) < end,
            )
        )
        or 0
    )
    return event_counts.get("transfer.requested", 0), event_counts.get("transfer.completed", 0), failed


async def _usage(
    session: AsyncSession, scope: AnalyticsScope, *, start: datetime, end: datetime
) -> tuple[float, float | None, list[str]]:
    filters: list[ColumnElement[bool]] = [
        UsageRecord.tenant_id == scope.principal.tenant_id,
        UsageRecord.metric.in_(AI_USAGE_METRICS),
        UsageRecord.occurred_at >= start,
        UsageRecord.occurred_at < end,
    ]
    if scope.project_ids:
        scoped_calls = select(Call.id).where(
            Call.tenant_id == scope.principal.tenant_id,
            Call.project_id.in_(scope.project_ids),
        )
        if scope.operator_id is not None:
            scoped_calls = scoped_calls.where(Call.operator_user_id == scope.operator_id)
        filters.append(UsageRecord.call_id.in_(scoped_calls))
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.quantity), 0),
                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
                func.count(UsageRecord.id),
                func.count(UsageRecord.id).filter(UsageRecord.estimated_cost_usd > 0),
            ).where(*filters)
        )
    ).one()
    quantity = float(row[0] or 0)
    cost = float(row[1] or 0)
    rows = int(row[2] or 0)
    priced = int(row[3] or 0)
    flags: list[str] = []
    if rows and priced < rows:
        flags.append("missing_usage_price" if priced == 0 else "partial_ai_usage")
        return quantity, None, flags
    return quantity, cost, flags


async def build_summary(session: AsyncSession, scope: AnalyticsScope) -> AnalyticsSummaryRead:
    current = await _aggregate_calls(session, scope, start=scope.start, end=scope.end)
    previous = await _aggregate_calls(session, scope, start=scope.previous_start, end=scope.previous_end)
    active = await _active_count(session, scope)
    transfer_current = await _transfer_counts(session, scope, start=scope.start, end=scope.end)
    transfer_previous = await _transfer_counts(
        session, scope, start=scope.previous_start, end=scope.previous_end
    )
    usage_current = await _usage(session, scope, start=scope.start, end=scope.end)
    usage_previous = await _usage(session, scope, start=scope.previous_start, end=scope.previous_end)
    callback_filters: list[ColumnElement[bool]] = [
        CallbackTask.tenant_id == scope.principal.tenant_id,
        CallbackTask.project_id.in_(scope.project_ids),
        CallbackTask.task_type == "callback",
        CallbackTask.created_at >= scope.start,
        CallbackTask.created_at < scope.end,
    ]
    previous_callback_filters = callback_filters[:-2] + [
        CallbackTask.created_at >= scope.previous_start,
        CallbackTask.created_at < scope.previous_end,
    ]
    if scope.operator_id is not None:
        callback_filters.append(CallbackTask.assigned_user_id == scope.operator_id)
        previous_callback_filters.append(CallbackTask.assigned_user_id == scope.operator_id)
    callbacks = int(
        await session.scalar(select(func.count()).select_from(CallbackTask).where(*callback_filters)) or 0
    )
    previous_callbacks = int(
        await session.scalar(select(func.count()).select_from(CallbackTask).where(*previous_callback_filters))
        or 0
    )

    flags: list[str] = []
    if current.test_calls:
        flags.append("test_data_present")
    if current.with_outcome < current.attempted:
        flags.append("missing_outcome")
    if current.invalid_duration:
        flags.append("invalid_duration_excluded")
    if current.incomplete_metadata:
        flags.append("incomplete_historical_metadata")
    flags.extend(flag for flag in usage_current[2] if flag not in flags)
    answer = rate(current.connected, current.attempted)
    previous_answer = rate(previous.connected, previous.attempted)
    success = rate(current.successful, current.with_outcome)
    previous_success = rate(previous.successful, previous.with_outcome)
    success_connected = rate(current.successful, current.connected)
    previous_success_connected = rate(previous.successful, previous.connected)
    transfer_rate = rate(transfer_current[1], transfer_current[0])
    previous_transfer_rate = rate(transfer_previous[1], transfer_previous[0])
    cost_visible = scope.financial_metrics_visible
    cost_available = cost_visible and usage_current[1] is not None
    cost_flags = list(usage_current[2])
    if not cost_visible:
        cost_flags.append("permission_restricted")

    return AnalyticsSummaryRead(
        period=AnalyticsPeriodRead(
            date_from=scope.start,
            date_to=scope.end,
            timezone=scope.timezone,
            bucket=scope.bucket,  # type: ignore[arg-type]
        ),
        active_calls=metric(active, None),
        attempted_calls=metric(current.attempted, previous.attempted),
        connected_calls=metric(current.connected, previous.connected),
        answer_rate=metric(
            answer,
            previous_answer,
            numerator=current.connected,
            denominator=current.attempted,
            available=answer is not None,
            flags=[] if answer is not None else ["zero_denominator"],
        ),
        successful_calls=metric(current.successful, previous.successful),
        success_rate=metric(
            success,
            previous_success,
            numerator=current.successful,
            denominator=current.with_outcome,
            available=success is not None,
            flags=[] if success is not None else ["zero_denominator"],
        ),
        success_rate_connected=metric(
            success_connected,
            previous_success_connected,
            numerator=current.successful,
            denominator=current.connected,
            available=success_connected is not None,
            flags=[] if success_connected is not None else ["zero_denominator"],
        ),
        average_duration_seconds=metric(current.average_duration, previous.average_duration),
        ai_calls=metric(current.ai_calls, previous.ai_calls),
        human_calls=metric(current.human_calls, previous.human_calls),
        simulator_calls=metric(current.simulator_calls, previous.simulator_calls),
        sip_calls=metric(current.sip_calls, previous.sip_calls),
        ai_minutes=metric(usage_current[0], usage_previous[0], flags=usage_current[2]),
        ai_cost_usd=metric(
            usage_current[1] if cost_visible else None,
            usage_previous[1] if cost_visible else None,
            available=cost_available,
            flags=cost_flags,
        ),
        callbacks=metric(callbacks, previous_callbacks),
        transfers=TransferMetricsRead(
            requested=metric(transfer_current[0], transfer_previous[0]),
            successful=metric(transfer_current[1], transfer_previous[1]),
            failed=metric(transfer_current[2], transfer_previous[2]),
            success_rate=metric(
                transfer_rate,
                previous_transfer_rate,
                numerator=transfer_current[1],
                denominator=transfer_current[0],
                available=transfer_rate is not None,
                flags=[] if transfer_rate is not None else ["zero_denominator"],
            ),
        ),
        data_quality_flags=flags,
    )


async def timeseries(session: AsyncSession, scope: AnalyticsScope) -> list[TimeSeriesPointRead]:
    local_started = func.timezone(scope.timezone, Call.started_at)
    bucket_expression = func.date_trunc(scope.bucket, local_started).label("bucket_start")
    rows = (
        await session.execute(
            select(
                bucket_expression,
                func.count(distinct(Call.id)),
                func.count(distinct(Call.id)).filter(Call.answered_at.is_not(None)),
                func.count(distinct(Call.id)).filter(CallOutcome.category == CallResultCategory.SUCCESSFUL),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.AI_AGENT),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.HUMAN_OPERATOR),
            )
            .select_from(Call)
            .outerjoin(
                CallOutcome,
                and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id),
            )
            .where(*_call_filters(scope, start=scope.start, end=scope.end))
            .group_by(bucket_expression)
            .order_by(bucket_expression)
        )
    ).all()
    zone = ZoneInfo(scope.timezone)
    return [
        TimeSeriesPointRead(
            bucket_start=bucket_start.replace(tzinfo=zone).astimezone(UTC),
            attempted=int(attempted),
            connected=int(connected),
            successful=int(successful),
            ai_calls=int(ai_calls),
            human_calls=int(human_calls),
        )
        for bucket_start, attempted, connected, successful, ai_calls, human_calls in rows
    ]


async def distributions(
    session: AsyncSession, scope: AnalyticsScope
) -> tuple[
    list[DistributionItemRead],
    list[DistributionItemRead],
    list[DistributionItemRead],
    list[DistributionItemRead],
]:
    base = _call_filters(scope, start=scope.start, end=scope.end)
    outcome_rows = (
        await session.execute(
            select(
                CallOutcome.code,
                CallOutcome.label,
                CallOutcome.color,
                func.count(distinct(CallOutcome.call_id)),
            )
            .join(Call, and_(Call.id == CallOutcome.call_id, Call.tenant_id == CallOutcome.tenant_id))
            .where(*base)
            .group_by(CallOutcome.code, CallOutcome.label, CallOutcome.color)
            .order_by(func.count(distinct(CallOutcome.call_id)).desc(), CallOutcome.code)
        )
    ).all()
    language_expression = func.coalesce(Call.language, "unknown").label("language_key")
    language_rows = (
        await session.execute(
            select(language_expression, func.count(Call.id))
            .where(*base)
            .group_by(language_expression)
            .order_by(func.count(Call.id).desc())
        )
    ).all()
    caller_rows = (
        await session.execute(
            select(Call.caller_type, func.count(Call.id)).where(*base).group_by(Call.caller_type)
        )
    ).all()
    channel_rows = (
        await session.execute(select(Call.channel, func.count(Call.id)).where(*base).group_by(Call.channel))
    ).all()
    return (
        [
            DistributionItemRead(key=code, label=label, color=color, value=int(count))
            for code, label, color, count in outcome_rows
        ],
        [
            DistributionItemRead(key=str(key), label=str(key).upper(), value=int(count))
            for key, count in language_rows
        ],
        [
            DistributionItemRead(key=key.value, label=key.value, value=int(count))
            for key, count in caller_rows
        ],
        [
            DistributionItemRead(key=key.value, label=key.value, value=int(count))
            for key, count in channel_rows
        ],
    )


async def task_summary(session: AsyncSession, scope: AnalyticsScope) -> TaskSummaryRead:
    zone = ZoneInfo(scope.timezone)
    now = datetime.now(UTC)
    today = now.astimezone(zone).date()
    tomorrow_start = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    base: list[ColumnElement[bool]] = [
        CallbackTask.tenant_id == scope.principal.tenant_id,
        CallbackTask.project_id.in_(scope.project_ids),
    ]
    if scope.operator_id is not None:
        base.append(CallbackTask.assigned_user_id == scope.operator_id)
    open_status = CallbackTask.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
    row = (
        await session.execute(
            select(
                func.count().filter(open_status, CallbackTask.due_at < now),
                func.count().filter(
                    open_status,
                    CallbackTask.due_at >= now,
                    CallbackTask.due_at < tomorrow_start,
                ),
                func.count().filter(open_status, CallbackTask.due_at >= tomorrow_start),
                func.count().filter(
                    CallbackTask.status == TaskStatus.COMPLETED,
                    CallbackTask.completed_at >= scope.start,
                    CallbackTask.completed_at < scope.end,
                ),
            )
            .select_from(CallbackTask)
            .where(*base)
        )
    ).one()
    type_rows = (
        await session.execute(
            select(CallbackTask.task_type, func.count())
            .where(*base, CallbackTask.created_at >= scope.start, CallbackTask.created_at < scope.end)
            .group_by(CallbackTask.task_type)
        )
    ).all()
    return TaskSummaryRead(
        overdue=int(row[0] or 0),
        today=int(row[1] or 0),
        future=int(row[2] or 0),
        completed=int(row[3] or 0),
        by_type=[
            DistributionItemRead(key=key.value, label=key.value, value=int(count)) for key, count in type_rows
        ],
    )


async def operator_status_summary(session: AsyncSession, scope: AnalyticsScope) -> OperatorStatusSummaryRead:
    memberships = list(
        await session.scalars(
            select(Membership).where(
                Membership.tenant_id == scope.principal.tenant_id,
                Membership.role.in_(
                    [RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER, RoleName.HUMAN_OPERATOR]
                ),
            )
        )
    )
    if scope.project_id is not None:
        assigned_user_ids = set(
            await session.scalars(
                select(ProjectUser.user_id).where(
                    ProjectUser.tenant_id == scope.principal.tenant_id,
                    ProjectUser.project_id == scope.project_id,
                    ProjectUser.is_active.is_(True),
                )
            )
        )
        memberships = [item for item in memberships if item.user_id in assigned_user_ids]
    if scope.operator_id is not None:
        memberships = [item for item in memberships if item.user_id == scope.operator_id]
    user_ids = [item.user_id for item in memberships]
    membership_ids = [item.id for item in memberships]
    status_rows = (
        (
            await session.execute(
                select(Membership.id, OperatorStatus.status)
                .join(
                    HumanOperator,
                    and_(
                        HumanOperator.tenant_id == Membership.tenant_id,
                        HumanOperator.membership_id == Membership.id,
                    ),
                )
                .join(
                    OperatorStatus,
                    and_(
                        OperatorStatus.tenant_id == Membership.tenant_id,
                        OperatorStatus.human_operator_id == HumanOperator.id,
                    ),
                )
                .where(Membership.id.in_(membership_ids))
            )
        ).all()
        if membership_ids
        else []
    )
    manual = {membership_id: status.value for membership_id, status in status_rows}
    cutoff = datetime.now(UTC) - timedelta(seconds=PRESENCE_TTL_SECONDS)
    heartbeat_rows = (
        (
            await session.execute(
                select(OperatorPresence.membership_id, func.max(OperatorPresence.heartbeat_at))
                .where(
                    OperatorPresence.tenant_id == scope.principal.tenant_id,
                    OperatorPresence.membership_id.in_(membership_ids),
                    OperatorPresence.ended_at.is_(None),
                    OperatorPresence.heartbeat_at >= cutoff,
                )
                .group_by(OperatorPresence.membership_id)
            )
        ).all()
        if membership_ids
        else []
    )
    heartbeat_ids = {membership_id for membership_id, _ in heartbeat_rows}
    active_rows = (
        (
            await session.execute(
                select(Call.operator_user_id, Call.status)
                .where(
                    Call.tenant_id == scope.principal.tenant_id,
                    Call.operator_user_id.in_(user_ids),
                    Call.status.in_(CAPACITY_CALL_STATES),
                )
                .order_by(Call.last_provider_event_at.desc().nullslast())
            )
        ).all()
        if user_ids
        else []
    )
    active_by_user: dict[UUID, CallStatus] = {}
    for user_id, status in active_rows:
        if user_id is not None and user_id not in active_by_user:
            active_by_user[user_id] = status
    counts = {key: 0 for key in ("available", "busy", "on_hold", "away", "on_break", "offline")}
    for membership in memberships:
        if membership.user_id in active_by_user:
            effective = "on_hold" if active_by_user[membership.user_id] == CallStatus.ON_HOLD else "busy"
        elif (
            not membership.is_active
            or membership.id not in heartbeat_ids
            or manual.get(membership.id) == "offline"
        ):
            effective = "offline"
        else:
            effective = manual.get(membership.id, "offline")
        counts[effective] = counts.get(effective, 0) + 1
    return OperatorStatusSummaryRead(
        **counts,
        active_members=sum(1 for item in memberships if item.is_active),
        blocked_members=sum(1 for item in memberships if not item.is_active),
    )


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) <= 4:
        return "•" * len(digits)
    prefix = "+" if value.startswith("+") else ""
    return f"{prefix}{'•' * min(7, len(digits) - 4)}{digits[-4:]}"


async def recent_calls(
    session: AsyncSession, scope: AnalyticsScope, *, limit: int, offset: int
) -> RecentCallsPage:
    filters = _call_filters(scope, start=scope.start, end=scope.end)
    total = int(await session.scalar(select(func.count()).select_from(Call).where(*filters)) or 0)
    operator = aliased(User)
    transferred = (
        select(CallEvent.id)
        .where(
            CallEvent.tenant_id == Call.tenant_id,
            CallEvent.call_id == Call.id,
            CallEvent.event_type == "transfer.completed",
        )
        .exists()
    )
    rows = (
        await session.execute(
            select(Call, Project.name, Customer.display_name, CallOutcome, operator.display_name, transferred)
            .join(Project, and_(Project.tenant_id == Call.tenant_id, Project.id == Call.project_id))
            .outerjoin(Customer, and_(Customer.tenant_id == Call.tenant_id, Customer.id == Call.customer_id))
            .outerjoin(
                CallOutcome, and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id)
            )
            .outerjoin(operator, operator.id == Call.operator_user_id)
            .where(*filters)
            .order_by(Call.started_at.desc(), Call.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        RecentCallRead(
            id=call.id,
            occurred_at=call.started_at or call.created_at,
            project_id=call.project_id,
            project_name=project_name,
            customer_name=customer_name,
            phone_masked=_mask_phone(
                call.to_number if call.direction.value == "outbound" else call.from_number
            ),
            direction=call.direction.value,
            caller_type=call.caller_type.value,
            channel=call.channel.value,
            language=call.language.value if call.language else None,
            duration_seconds=call.duration_seconds,
            result_label=outcome.label if outcome else None,
            result_category=outcome.category.value if outcome else None,
            status=call.status.value,
            transferred=bool(was_transferred),
            operator_name=operator_name,
            is_test=call.is_demo or call.channel == CallChannel.DEVELOPMENT_SIMULATOR,
        )
        for call, project_name, customer_name, outcome, operator_name, was_transferred in rows
    ]
    return RecentCallsPage(items=items, total=total, limit=limit, offset=offset)


async def filter_options(session: AsyncSession, scope: AnalyticsScope) -> AnalyticsFilterOptionsRead:
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == scope.principal.tenant_id)
    )
    default_timezone = settings.timezone if settings else "Asia/Tashkent"
    projects = list(
        await session.scalars(accessible_projects_statement(scope.principal).order_by(Project.name))
    )
    operator_statement = (
        select(User.id, User.display_name)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == scope.principal.tenant_id)
        .order_by(User.display_name)
    )
    if scope.principal.role == RoleName.HUMAN_OPERATOR:
        operator_statement = operator_statement.where(User.id == scope.principal.user_id)
    operator_rows = (await session.execute(operator_statement)).all()
    return AnalyticsFilterOptionsRead(
        projects=[
            AnalyticsProjectOptionRead(
                id=project.id,
                name=project.name,
                status=project.status,
                timezone=project.timezone or default_timezone,
            )
            for project in projects
        ],
        operators=[AnalyticsOperatorOptionRead(id=user_id, name=name) for user_id, name in operator_rows],
        default_timezone=default_timezone,
        max_period_days=MAX_PERIOD_DAYS,
        financial_metrics_visible=scope.financial_metrics_visible,
    )


async def operator_performance(
    session: AsyncSession,
    scope: AnalyticsScope,
    *,
    limit: int,
    offset: int,
    sort: str,
) -> OperatorPerformancePage:
    filters = _call_filters(scope, start=scope.start, end=scope.end)
    duration = func.extract("epoch", Call.ended_at - Call.answered_at)
    valid_duration = and_(Call.answered_at.is_not(None), Call.ended_at >= Call.answered_at)
    rows = (
        await session.execute(
            select(
                Call.operator_user_id,
                User.display_name,
                Membership.is_active,
                func.count(distinct(Call.id)).label("attempted"),
                func.count(distinct(Call.id)).filter(Call.answered_at.is_not(None)).label("connected"),
                func.count(distinct(Call.id))
                .filter(CallOutcome.category == CallResultCategory.SUCCESSFUL)
                .label("successful"),
                func.count(distinct(CallOutcome.call_id)).label("with_outcome"),
                func.avg(duration).filter(valid_duration).label("average_duration"),
                func.coalesce(func.sum(duration).filter(valid_duration), 0).label("talk_time"),
            )
            .select_from(Call)
            .join(User, User.id == Call.operator_user_id)
            .outerjoin(
                Membership, and_(Membership.tenant_id == Call.tenant_id, Membership.user_id == User.id)
            )
            .outerjoin(
                CallOutcome, and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id)
            )
            .where(*filters, Call.operator_user_id.is_not(None))
            .group_by(Call.operator_user_id, User.display_name, Membership.is_active)
        )
    ).all()
    ids = [row[0] for row in rows]
    project_rows = (
        (
            await session.execute(
                select(ProjectUser.user_id, func.array_agg(distinct(Project.name)))
                .join(
                    Project,
                    and_(Project.tenant_id == ProjectUser.tenant_id, Project.id == ProjectUser.project_id),
                )
                .where(ProjectUser.tenant_id == scope.principal.tenant_id, ProjectUser.user_id.in_(ids))
                .group_by(ProjectUser.user_id)
            )
        ).all()
        if ids
        else []
    )
    project_map = {user_id: sorted(names or []) for user_id, names in project_rows}
    transfer_rows = (
        (
            await session.execute(
                select(Call.operator_user_id, func.count(distinct(CallEvent.call_id)))
                .join(CallEvent, and_(CallEvent.tenant_id == Call.tenant_id, CallEvent.call_id == Call.id))
                .where(*filters, CallEvent.event_type == "transfer.completed", Call.operator_user_id.in_(ids))
                .group_by(Call.operator_user_id)
            )
        ).all()
        if ids
        else []
    )
    callback_rows = (
        (
            await session.execute(
                select(CallbackTask.assigned_user_id, func.count())
                .where(
                    CallbackTask.tenant_id == scope.principal.tenant_id,
                    CallbackTask.project_id.in_(scope.project_ids),
                    CallbackTask.assigned_user_id.in_(ids),
                    CallbackTask.task_type == "callback",
                    CallbackTask.created_at >= scope.start,
                    CallbackTask.created_at < scope.end,
                )
                .group_by(CallbackTask.assigned_user_id)
            )
        ).all()
        if ids
        else []
    )
    transfers = {key: int(value) for key, value in transfer_rows}
    callbacks = {key: int(value) for key, value in callback_rows}
    items = [
        OperatorPerformanceRead(
            operator_id=user_id,
            operator_name=name,
            project_names=project_map.get(user_id, []),
            attempted=int(attempted),
            connected=int(connected),
            successful=int(successful),
            answer_rate=rate(int(connected), int(attempted)),
            success_rate=rate(int(successful), int(with_outcome)),
            average_duration_seconds=float(average) if average is not None else None,
            transfers=transfers.get(user_id, 0),
            callbacks=callbacks.get(user_id, 0),
            talk_time_seconds=int(talk_time or 0),
            is_active=bool(is_active),
        )
        for (
            user_id,
            name,
            is_active,
            attempted,
            connected,
            successful,
            with_outcome,
            average,
            talk_time,
        ) in rows
    ]
    sort_key = {
        "attempted": lambda item: item.attempted,
        "connected": lambda item: item.connected,
        "successful": lambda item: item.successful,
        "answer_rate": lambda item: item.answer_rate or -1,
        "success_rate": lambda item: item.success_rate or -1,
        "talk_time": lambda item: item.talk_time_seconds,
    }.get(sort, lambda item: item.attempted)
    items.sort(key=sort_key, reverse=True)
    return OperatorPerformancePage(
        items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset
    )


async def project_performance(
    session: AsyncSession,
    scope: AnalyticsScope,
    *,
    limit: int,
    offset: int,
    sort: str,
) -> ProjectPerformancePage:
    rows = (
        await session.execute(
            select(
                Project.id,
                Project.name,
                Project.status,
                func.count(distinct(Call.id)),
                func.count(distinct(Call.id)).filter(Call.answered_at.is_not(None)),
                func.count(distinct(Call.id)).filter(CallOutcome.category == CallResultCategory.SUCCESSFUL),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.AI_AGENT),
                func.count(distinct(Call.id)).filter(Call.caller_type == CallerType.HUMAN_OPERATOR),
            )
            .select_from(Project)
            .outerjoin(
                Call,
                and_(
                    Call.tenant_id == Project.tenant_id,
                    Call.project_id == Project.id,
                    *_call_filters(scope, start=scope.start, end=scope.end),
                ),
            )
            .outerjoin(
                CallOutcome, and_(CallOutcome.tenant_id == Call.tenant_id, CallOutcome.call_id == Call.id)
            )
            .where(Project.tenant_id == scope.principal.tenant_id, Project.id.in_(scope.project_ids))
            .group_by(Project.id, Project.name, Project.status)
        )
    ).all()
    project_ids = [row[0] for row in rows]
    usage_rows = (
        (
            await session.execute(
                select(
                    Call.project_id,
                    func.coalesce(func.sum(UsageRecord.quantity), 0),
                    func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
                    func.count(UsageRecord.id),
                    func.count(UsageRecord.id).filter(UsageRecord.estimated_cost_usd > 0),
                )
                .join(Call, and_(Call.tenant_id == UsageRecord.tenant_id, Call.id == UsageRecord.call_id))
                .where(
                    UsageRecord.tenant_id == scope.principal.tenant_id,
                    UsageRecord.metric.in_(AI_USAGE_METRICS),
                    UsageRecord.occurred_at >= scope.start,
                    UsageRecord.occurred_at < scope.end,
                    Call.project_id.in_(project_ids),
                )
                .group_by(Call.project_id)
            )
        ).all()
        if project_ids
        else []
    )
    usage_map = {
        project_id: (
            float(quantity or 0),
            float(cost or 0) if int(row_count or 0) == int(priced_count or 0) else None,
        )
        for project_id, quantity, cost, row_count, priced_count in usage_rows
    }
    callback_rows = (
        (
            await session.execute(
                select(CallbackTask.project_id, func.count())
                .where(
                    CallbackTask.tenant_id == scope.principal.tenant_id,
                    CallbackTask.project_id.in_(project_ids),
                    CallbackTask.task_type == "callback",
                    CallbackTask.created_at >= scope.start,
                    CallbackTask.created_at < scope.end,
                )
                .group_by(CallbackTask.project_id)
            )
        ).all()
        if project_ids
        else []
    )
    callback_map = {project_id: int(count) for project_id, count in callback_rows}
    items: list[ProjectPerformanceRead] = []
    for project_id, name, status, attempted, connected, successful, ai_calls, human_calls in rows:
        usage = usage_map.get(project_id, (0.0, 0.0))
        items.append(
            ProjectPerformanceRead(
                project_id=project_id,
                project_name=name,
                project_status=status,
                attempted=int(attempted or 0),
                connected=int(connected or 0),
                successful=int(successful or 0),
                ai_calls=int(ai_calls or 0),
                human_calls=int(human_calls or 0),
                callbacks=callback_map.get(project_id, 0),
                ai_minutes=usage[0],
                ai_cost_usd=usage[1] if scope.financial_metrics_visible else None,
                cost_is_available=scope.financial_metrics_visible and usage[1] is not None,
            )
        )
    sort_key = {
        "attempted": lambda item: item.attempted,
        "connected": lambda item: item.connected,
        "successful": lambda item: item.successful,
        "ai_calls": lambda item: item.ai_calls,
        "callbacks": lambda item: item.callbacks,
    }.get(sort, lambda item: item.attempted)
    items.sort(key=sort_key, reverse=True)
    return ProjectPerformancePage(
        items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset
    )
