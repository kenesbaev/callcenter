from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from teamora_api.enums import (
    CallChannel,
    CallDirection,
    CallerType,
    CallResultCategory,
    CallStatus,
    HangupCause,
    LanguageCode,
    TaskPriority,
    TranscriptSpeaker,
)


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
    language_code: str
    text: str
    provider_item_id: str | None
    is_final: bool
    interrupted: bool
    created_at: datetime


class CallSummaryRead(BaseModel):
    summary: str
    topics: list[str]
    result: str
    generated_by: str


class CallRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    project_id: UUID
    channel: CallChannel
    status: CallStatus
    language: LanguageCode | None
    customer_id: UUID | None
    operator_user_id: UUID | None
    ai_operator_id: UUID | None
    call_flow_version_id: UUID | None
    knowledge_base_revision_id: UUID | None
    direction: CallDirection
    caller_type: CallerType
    provider: str
    provider_call_id: str | None = Field(validation_alias="external_call_id")
    provider_state: str
    recording_state: str
    from_number: str | None
    to_number: str | None
    started_at: datetime | None
    ringing_at: datetime | None
    answered_at: datetime | None
    held_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int
    transfer_reason: str | None
    hangup_cause: HangupCause | None
    raw_provider_cause: str | None
    last_provider_event_at: datetime | None
    state_version: int
    is_demo: bool


class CallDetail(CallRead):
    transcript: list[TranscriptSegmentRead]
    summary: CallSummaryRead | None


class CallStartRequest(BaseModel):
    customer_id: UUID
    customer_contact_id: UUID | None = None
    lock_token: UUID
    callback_task_id: UUID | None = None
    from_number: str = Field(default="MOCK", min_length=2, max_length=32)


class CallResultTaskRequest(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=8000)
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime
    assigned_user_id: UUID | None = None

    @model_validator(mode="after")
    def require_aware_due_at(self) -> CallResultTaskRequest:
        if self.due_at.tzinfo is None:
            raise ValueError("Дата задачи должна содержать часовой пояс")
        return self


class CallResultRequest(BaseModel):
    result_definition_id: UUID | None = None
    result: (
        Literal[
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
        | None
    ) = Field(default=None, deprecated=True)
    comment: str = Field(default="", max_length=4000)
    callback_at: datetime | None = None
    task: CallResultTaskRequest | None = None

    def legacy_result_code(self) -> str | None:
        value = self.__dict__.get("result")
        return value if isinstance(value, str) else None

    @model_validator(mode="after")
    def require_one_result_reference(self) -> CallResultRequest:
        if (self.result_definition_id is None) == (self.legacy_result_code() is None):
            raise ValueError("Передайте result_definition_id или legacy result")
        return self


class CallOutcomeSnapshotRead(BaseModel):
    result_definition_id: UUID
    code: str
    label: str
    category: CallResultCategory
    color: str


class CallResultResponse(BaseModel):
    call: CallRead
    customer_status: str
    callback_task_id: UUID | None = None
    task_ids: list[UUID] = Field(default_factory=list)
    outcome: CallOutcomeSnapshotRead


class SimulatorMessageResponse(BaseModel):
    call: CallRead
    customer_segment: TranscriptSegmentRead
    assistant_segment: TranscriptSegmentRead
    tool_name: str
    tool_result: dict[str, object]
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
