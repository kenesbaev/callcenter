from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AnalyticsPeriodRead(BaseModel):
    date_from: datetime
    date_to: datetime
    timezone: str
    bucket: Literal["hour", "day"]


class MetricRead(BaseModel):
    value: float | None
    numerator: float | None = None
    denominator: float | None = None
    previous_value: float | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    is_available: bool = True
    data_quality_flags: list[str] = Field(default_factory=list)


class TransferMetricsRead(BaseModel):
    requested: MetricRead
    successful: MetricRead
    failed: MetricRead
    success_rate: MetricRead


class AnalyticsSummaryRead(BaseModel):
    period: AnalyticsPeriodRead
    active_calls: MetricRead
    attempted_calls: MetricRead
    connected_calls: MetricRead
    answer_rate: MetricRead
    successful_calls: MetricRead
    success_rate: MetricRead
    success_rate_connected: MetricRead
    average_duration_seconds: MetricRead
    ai_calls: MetricRead
    human_calls: MetricRead
    simulator_calls: MetricRead
    sip_calls: MetricRead
    ai_minutes: MetricRead
    ai_cost_usd: MetricRead
    callbacks: MetricRead
    transfers: TransferMetricsRead
    data_quality_flags: list[str] = Field(default_factory=list)
    telephony_cost_included: bool = False


class TimeSeriesPointRead(BaseModel):
    bucket_start: datetime
    attempted: int
    connected: int
    successful: int
    ai_calls: int
    human_calls: int


class DistributionItemRead(BaseModel):
    key: str
    label: str
    value: int
    color: str | None = None


class TaskSummaryRead(BaseModel):
    overdue: int
    today: int
    future: int
    completed: int
    by_type: list[DistributionItemRead]


class OperatorStatusSummaryRead(BaseModel):
    available: int
    busy: int
    on_hold: int
    away: int
    on_break: int
    offline: int
    active_members: int
    blocked_members: int


class RecentCallRead(BaseModel):
    id: UUID
    occurred_at: datetime
    project_id: UUID
    project_name: str
    customer_name: str | None
    phone_masked: str | None
    direction: str
    caller_type: str
    channel: str
    language: str | None
    duration_seconds: int
    result_label: str | None
    result_category: str | None
    status: str
    transferred: bool
    operator_name: str | None
    is_test: bool


class RecentCallsPage(BaseModel):
    items: list[RecentCallRead]
    total: int
    limit: int
    offset: int


class OperatorPerformanceRead(BaseModel):
    operator_id: UUID
    operator_name: str
    project_names: list[str]
    attempted: int
    connected: int
    successful: int
    answer_rate: float | None
    success_rate: float | None
    average_duration_seconds: float | None
    transfers: int
    callbacks: int
    talk_time_seconds: int
    is_active: bool


class OperatorPerformancePage(BaseModel):
    items: list[OperatorPerformanceRead]
    total: int
    limit: int
    offset: int


class ProjectPerformanceRead(BaseModel):
    project_id: UUID
    project_name: str
    project_status: str
    attempted: int
    connected: int
    successful: int
    ai_calls: int
    human_calls: int
    callbacks: int
    ai_minutes: float
    ai_cost_usd: float | None
    cost_is_available: bool


class ProjectPerformancePage(BaseModel):
    items: list[ProjectPerformanceRead]
    total: int
    limit: int
    offset: int


class AnalyticsProjectOptionRead(BaseModel):
    id: UUID
    name: str
    status: str
    timezone: str


class AnalyticsOperatorOptionRead(BaseModel):
    id: UUID
    name: str


class AnalyticsFilterOptionsRead(BaseModel):
    projects: list[AnalyticsProjectOptionRead]
    operators: list[AnalyticsOperatorOptionRead]
    default_timezone: str
    max_period_days: int
    financial_metrics_visible: bool


class AnalyticsOverviewRead(BaseModel):
    summary: AnalyticsSummaryRead
    timeseries: list[TimeSeriesPointRead]
    outcomes: list[DistributionItemRead]
    languages: list[DistributionItemRead]
    callers: list[DistributionItemRead]
    channels: list[DistributionItemRead]
    tasks: TaskSummaryRead
    operator_statuses: OperatorStatusSummaryRead
    recent_calls: RecentCallsPage
