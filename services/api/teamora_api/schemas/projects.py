from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from teamora_api.enums import LanguageCode, RoleName

ProjectStatus = Literal["active", "paused", "archived"]


class WorkingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    start: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")

    @model_validator(mode="after")
    def validate_interval(self) -> WorkingDay:
        if self.enabled and (self.start is None or self.end is None):
            raise ValueError("Для рабочего дня необходимо указать начало и окончание")
        if self.enabled and self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("Окончание рабочего дня должно быть позже начала")
        return self


class WorkingHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monday: WorkingDay | None = None
    tuesday: WorkingDay | None = None
    wednesday: WorkingDay | None = None
    thursday: WorkingDay | None = None
    friday: WorkingDay | None = None
    saturday: WorkingDay | None = None
    sunday: WorkingDay | None = None


class CallbackRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_delay_minutes: int = Field(default=60, ge=1, le=43_200)
    max_schedule_days: int = Field(default=30, ge=1, le=365)
    allow_operator_scheduling: bool = True
    require_assignee: bool = False
    overdue_first: bool = True


def validate_retry_policy(max_attempts: int, intervals: list[int]) -> None:
    if any(interval < 1 or interval > 10_080 for interval in intervals):
        raise ValueError("Интервалы повторных попыток должны быть от 1 до 10080 минут")
    if len(intervals) != max_attempts - 1:
        raise ValueError("Количество интервалов должно быть на один меньше числа попыток")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=1000)
    status: ProjectStatus = "active"
    default_language: LanguageCode | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    outbound_number: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    outbound_phone_number_id: UUID | None = None
    inbound_phone_number_ids: list[UUID] = Field(default_factory=list, max_length=100)
    ai_operator_id: UUID | None = None
    knowledge_source_id: UUID | None = None
    call_flow_id: UUID | None = None
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=1000)
    working_hours: WorkingHours = Field(default_factory=WorkingHours)
    recording_enabled: bool | None = None
    recording_disclosure_required: bool | None = None
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_intervals_minutes: list[int] = Field(default_factory=lambda: [15, 60], max_length=19)
    callback_rules: CallbackRules = Field(default_factory=CallbackRules)
    operator_user_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_configuration(self) -> ProjectCreate:
        validate_retry_policy(self.max_attempts, self.retry_intervals_minutes)
        if self.outbound_number is not None and self.outbound_phone_number_id is not None:
            raise ValueError("Укажите исходящий номер либо его идентификатор, но не оба значения")
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    status: ProjectStatus | None = None
    default_language: LanguageCode | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    outbound_number: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    outbound_phone_number_id: UUID | None = None
    inbound_phone_number_ids: list[UUID] | None = Field(default=None, max_length=100)
    ai_operator_id: UUID | None = None
    knowledge_source_id: UUID | None = None
    call_flow_id: UUID | None = None
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=1000)
    working_hours: WorkingHours | None = None
    recording_enabled: bool | None = None
    recording_disclosure_required: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    retry_intervals_minutes: list[int] | None = Field(default=None, max_length=19)
    callback_rules: CallbackRules | None = None
    operator_user_ids: list[UUID] | None = Field(default=None, max_length=1000)


class EffectiveProjectSettings(BaseModel):
    default_language: LanguageCode
    timezone: str
    max_concurrent_calls: int
    recording_enabled: bool
    recording_disclosure_required: bool


class ProjectRead(BaseModel):
    id: UUID
    name: str
    description: str
    status: ProjectStatus
    default_language: LanguageCode | None
    timezone: str | None
    outbound_number: str | None
    outbound_phone_number_id: UUID | None
    inbound_phone_number_ids: list[UUID]
    ai_operator_id: UUID | None
    knowledge_source_id: UUID | None
    call_flow_id: UUID | None
    call_result_catalog_id: UUID
    max_concurrent_calls: int | None
    effective_settings: EffectiveProjectSettings
    working_hours: WorkingHours
    recording_enabled: bool | None
    recording_disclosure_required: bool | None
    max_attempts: int
    retry_intervals_minutes: list[int]
    callback_rules: CallbackRules
    is_default: bool
    archived_at: datetime | None
    operator_user_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class ProjectOperatorOption(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    role: RoleName


class ProjectPhoneNumberOption(BaseModel):
    id: UUID
    e164: str
    label: str
    is_active: bool


class ProjectAiOperatorOption(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    is_active: bool


class ProjectKnowledgeSourceOption(BaseModel):
    id: UUID
    name: str
    source_type: str


class ProjectCallFlowOption(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    is_active: bool


class ProjectOptions(BaseModel):
    tenant_defaults: EffectiveProjectSettings
    operators: list[ProjectOperatorOption]
    phone_numbers: list[ProjectPhoneNumberOption]
    ai_operators: list[ProjectAiOperatorOption]
    knowledge_sources: list[ProjectKnowledgeSourceOption]
    call_flows: list[ProjectCallFlowOption]
