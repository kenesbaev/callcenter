from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
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
    project_id: UUID
    channel: CallChannel
    status: CallStatus
    language: LanguageCode | None
    customer_id: UUID | None
    operator_user_id: UUID | None
    ai_operator_id: UUID | None
    direction: str
    provider: str
    from_number: str | None
    to_number: str | None
    started_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int
    transfer_reason: str | None
    is_demo: bool


class CallDetail(CallRead):
    transcript: list[TranscriptSegmentRead]
    summary: CallSummaryRead | None


class CallStartRequest(BaseModel):
    customer_id: UUID
    lock_token: UUID
    callback_task_id: UUID | None = None
    from_number: str = Field(default="MOCK", min_length=2, max_length=32)


class CallResultRequest(BaseModel):
    result: Literal[
        "success",
        "no_answer",
        "busy",
        "callback",
        "wrong_number",
        "do_not_call",
        "not_interested",
        "failed",
        "other",
    ]
    comment: str = Field(default="", max_length=4000)
    callback_at: datetime | None = None


class CallResultResponse(BaseModel):
    call: CallRead
    customer_status: str
    callback_task_id: UUID | None = None


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
