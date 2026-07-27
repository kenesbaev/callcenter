from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from teamora_api.enums import CallChannel, CallStatus, LanguageCode, TranscriptSpeaker


class SimulatorCallCreate(BaseModel):
    ai_operator_id: UUID
    language: LanguageCode = LanguageCode.RU
    customer_name: str | None = Field(default=None, max_length=160)
    customer_phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


class SimulatorMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TranscriptSegmentRead(BaseModel):
    id: UUID
    sequence: int
    speaker: TranscriptSpeaker
    language: LanguageCode
    text: str
    created_at: datetime


class CallSummaryRead(BaseModel):
    summary: str
    topics: list[str]
    result: str
    generated_by: str


class CallRead(BaseModel):
    id: UUID
    channel: CallChannel
    status: CallStatus
    language: LanguageCode | None
    customer_id: UUID | None
    ai_operator_id: UUID | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int
    transfer_reason: str | None
    is_demo: bool


class CallDetail(CallRead):
    transcript: list[TranscriptSegmentRead]
    summary: CallSummaryRead | None


class SimulatorMessageResponse(BaseModel):
    call: CallRead
    customer_segment: TranscriptSegmentRead
    assistant_segment: TranscriptSegmentRead
    tool_name: str
    transfer_requested: bool


class DashboardMetric(BaseModel):
    value: int | float | Decimal
    label: str


class DashboardResponse(BaseModel):
    active_calls: int
    calls_today: int
    completed_calls: int
    transfers: int
    average_duration_seconds: float
    used_ai_minutes: Decimal
    estimated_cost_usd: Decimal
    language_breakdown: dict[str, int]
    is_demo: bool
