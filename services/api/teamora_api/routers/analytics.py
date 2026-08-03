from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from teamora_api.analytics_service import (
    AnalyticsScope,
    build_summary,
    distributions,
    filter_options,
    operator_performance,
    operator_status_summary,
    project_performance,
    recent_calls,
    resolve_scope,
    task_summary,
    timeseries,
)
from teamora_api.dependencies import PrincipalDep, SessionDep
from teamora_api.errors import ApiError
from teamora_api.rbac import role_has_permission
from teamora_api.schemas.analytics import (
    AnalyticsFilterOptionsRead,
    AnalyticsOverviewRead,
    AnalyticsSummaryRead,
    DistributionItemRead,
    OperatorPerformancePage,
    OperatorStatusSummaryRead,
    ProjectPerformancePage,
    RecentCallsPage,
    TaskSummaryRead,
    TimeSeriesPointRead,
)
from teamora_api.schemas.calls import DashboardResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def analytics_scope(
    session: SessionDep,
    principal: PrincipalDep,
    project_id: UUID | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    timezone: str | None = Query(default=None, max_length=64),
    operator_id: UUID | None = Query(default=None),
    direction: str | None = Query(default=None),
    caller_type: str | None = Query(default=None),
) -> AnalyticsScope:
    if not (
        role_has_permission(principal.role, "analytics:read")
        or role_has_permission(principal.role, "analytics:self")
    ):
        raise ApiError(403, "permission_denied", "Нет прав для просмотра аналитики")
    return await resolve_scope(
        session,
        principal,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        operator_id=operator_id,
        direction=direction,
        caller_type=caller_type,
    )


AnalyticsScopeDep = Annotated[AnalyticsScope, Depends(analytics_scope)]


@router.get("/summary", response_model=AnalyticsSummaryRead)
async def summary(session: SessionDep, scope: AnalyticsScopeDep) -> AnalyticsSummaryRead:
    return await build_summary(session, scope)


@router.get("/timeseries", response_model=list[TimeSeriesPointRead])
async def call_timeseries(session: SessionDep, scope: AnalyticsScopeDep) -> list[TimeSeriesPointRead]:
    return await timeseries(session, scope)


@router.get("/outcomes", response_model=list[DistributionItemRead])
async def outcome_distribution(session: SessionDep, scope: AnalyticsScopeDep) -> list[DistributionItemRead]:
    outcomes, _, _, _ = await distributions(session, scope)
    return outcomes


@router.get("/languages", response_model=list[DistributionItemRead])
async def language_distribution(session: SessionDep, scope: AnalyticsScopeDep) -> list[DistributionItemRead]:
    _, languages, _, _ = await distributions(session, scope)
    return languages


@router.get("/callers", response_model=dict[str, list[DistributionItemRead]])
async def caller_distribution(
    session: SessionDep, scope: AnalyticsScopeDep
) -> dict[str, list[DistributionItemRead]]:
    _, _, callers, channels = await distributions(session, scope)
    return {"caller_types": callers, "channels": channels}


@router.get("/tasks", response_model=TaskSummaryRead)
async def tasks(session: SessionDep, scope: AnalyticsScopeDep) -> TaskSummaryRead:
    return await task_summary(session, scope)


@router.get("/operator-statuses", response_model=OperatorStatusSummaryRead)
async def operator_statuses(session: SessionDep, scope: AnalyticsScopeDep) -> OperatorStatusSummaryRead:
    return await operator_status_summary(session, scope)


@router.get("/recent-calls", response_model=RecentCallsPage)
async def recent(
    session: SessionDep,
    scope: AnalyticsScopeDep,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RecentCallsPage:
    return await recent_calls(session, scope, limit=limit, offset=offset)


@router.get("/operators", response_model=OperatorPerformancePage)
async def operators(
    session: SessionDep,
    scope: AnalyticsScopeDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(
        default="attempted", pattern="^(attempted|connected|successful|answer_rate|success_rate|talk_time)$"
    ),
) -> OperatorPerformancePage:
    return await operator_performance(session, scope, limit=limit, offset=offset, sort=sort)


@router.get("/projects", response_model=ProjectPerformancePage)
async def projects(
    session: SessionDep,
    scope: AnalyticsScopeDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="attempted", pattern="^(attempted|connected|successful|ai_calls|callbacks)$"),
) -> ProjectPerformancePage:
    return await project_performance(session, scope, limit=limit, offset=offset, sort=sort)


@router.get("/filter-options", response_model=AnalyticsFilterOptionsRead)
async def filters(session: SessionDep, scope: AnalyticsScopeDep) -> AnalyticsFilterOptionsRead:
    return await filter_options(session, scope)


@router.get("/overview", response_model=AnalyticsOverviewRead)
async def overview(session: SessionDep, scope: AnalyticsScopeDep) -> AnalyticsOverviewRead:
    summary_value = await build_summary(session, scope)
    series = await timeseries(session, scope)
    outcomes, languages, callers, channels = await distributions(session, scope)
    task_value = await task_summary(session, scope)
    statuses = await operator_status_summary(session, scope)
    calls = await recent_calls(session, scope, limit=10, offset=0)
    return AnalyticsOverviewRead(
        summary=summary_value,
        timeseries=series,
        outcomes=outcomes,
        languages=languages,
        callers=callers,
        channels=channels,
        tasks=task_value,
        operator_statuses=statuses,
        recent_calls=calls,
    )


@router.get("/dashboard", response_model=DashboardResponse, deprecated=True)
async def dashboard_compatibility(session: SessionDep, scope: AnalyticsScopeDep) -> DashboardResponse:
    """Compatibility response for pre-Stage-11 clients."""

    summary_value = await build_summary(session, scope)
    _, languages, _, _ = await distributions(session, scope)
    return DashboardResponse(
        active_calls=int(summary_value.active_calls.value or 0),
        calls_today=int(summary_value.attempted_calls.value or 0),
        completed_calls=int(summary_value.successful_calls.value or 0),
        transfers=int(summary_value.transfers.requested.value or 0),
        average_duration_seconds=float(summary_value.average_duration_seconds.value or 0),
        used_ai_minutes=Decimal(str(summary_value.ai_minutes.value or 0)).quantize(Decimal("0.0000")),
        estimated_cost_usd=Decimal(str(summary_value.ai_cost_usd.value or 0)).quantize(Decimal("0.000000")),
        language_breakdown={item.key: item.value for item in languages},
        is_demo="test_data_present" in summary_value.data_quality_flags,
    )
