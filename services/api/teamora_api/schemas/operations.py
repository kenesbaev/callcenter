from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from teamora_api.enums import IntegrationStatus, LanguageCode, QueueStatus, RoleName


class TeamMemberRead(BaseModel):
    membership_id: UUID
    user_id: UUID
    display_name: str
    email: str
    role: RoleName
    is_active: bool
    operator_status: QueueStatus | None
    extension: str | None


class IntegrationRead(BaseModel):
    integration_id: UUID | None
    provider: str
    display_name: str
    status: IntegrationStatus
    capabilities: list[str]
    verified_at: datetime | None
    can_configure: bool
    note: str


class TenantSettingsRead(BaseModel):
    timezone: str
    default_language: LanguageCode
    recording_enabled: bool
    recording_disclosure_required: bool
    retention_days: int
    max_concurrent_calls: int
    updated_at: datetime


class TenantSettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    default_language: LanguageCode | None = None
    recording_enabled: bool | None = None
    recording_disclosure_required: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=100)
