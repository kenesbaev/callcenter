from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator

from teamora_api.enums import InvitationStatus, QueueStatus, RoleName

TeamRole = Literal["tenant_owner", "tenant_manager", "human_operator", "analyst"]
EffectiveOperatorStatus = Literal["available", "away", "on_break", "offline", "busy", "on_hold"]


def normalize_language_code(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", normalized):
        raise ValueError("Укажите корректный BCP 47 language code")
    return normalized


class TeamProjectRead(BaseModel):
    id: UUID
    name: str


class TeamMemberRead(BaseModel):
    membership_id: UUID
    user_id: UUID
    display_name: str
    email: EmailStr
    phone: str | None
    job_title: str | None
    role: RoleName
    is_active: bool
    extension: str | None
    interface_language: str
    timezone: str | None
    invited_at: datetime | None
    activated_at: datetime | None
    last_login_at: datetime | None
    last_heartbeat_at: datetime | None
    blocked_at: datetime | None
    blocked_reason: str | None
    manual_status: QueueStatus
    effective_status: EffectiveOperatorStatus
    is_transfer_available: bool
    current_call_id: UUID | None
    projects: list[TeamProjectRead]
    state_version: int


class TeamProfileUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    job_title: str | None = Field(default=None, max_length=120)
    extension: str | None = Field(default=None, max_length=32, pattern=r"^[0-9*#-]{1,32}$")
    interface_language: str | None = Field(default=None, min_length=2, max_length=32)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    is_transfer_available: bool | None = None

    @field_validator("interface_language")
    @classmethod
    def language_code(cls, value: str | None) -> str | None:
        return normalize_language_code(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def timezone_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Укажите корректный часовой пояс IANA") from exc
        return value

    @field_validator("phone", "job_title", "extension")
    @classmethod
    def empty_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TeamRoleUpdate(BaseModel):
    role: TeamRole
    expected_version: int = Field(ge=1)


class TeamProjectsUpdate(BaseModel):
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    expected_version: int = Field(ge=1)


class TeamBlockRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class TeamVersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class OwnershipTransferRequest(BaseModel):
    current_owner_expected_version: int = Field(ge=1)
    target_expected_version: int = Field(ge=1)


class InvitationCreate(BaseModel):
    email: EmailStr
    role: TeamRole
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    expires_in_days: int = Field(default=7, ge=1, le=30)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class InvitationAccept(BaseModel):
    tenant_slug: str = Field(min_length=3, max_length=80)
    token: str = Field(min_length=32, max_length=512)
    display_name: str | None = Field(default=None, min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, value: str) -> str:
        if not any(ch.isupper() for ch in value) or not any(ch.islower() for ch in value):
            raise ValueError("Password must contain uppercase and lowercase letters")
        if not any(ch.isdigit() for ch in value):
            raise ValueError("Password must contain a number")
        return value


class InvitationRead(BaseModel):
    id: UUID
    email: EmailStr
    role: RoleName
    status: InvitationStatus
    project_ids: list[UUID]
    expires_at: datetime
    issued_at: datetime
    accepted_at: datetime | None
    cancelled_at: datetime | None
    state_version: int
    created_at: datetime
    acceptance_url: str | None = None
    acceptance_token: str | None = None


class InvitationAcceptResponse(BaseModel):
    membership_id: UUID
    tenant_id: UUID
    tenant_slug: str
    email: EmailStr
    role: RoleName
    already_accepted: bool = False


class OperatorStatusUpdate(BaseModel):
    status: Literal["available", "away", "on_break", "offline"]
    session_key: str = Field(min_length=8, max_length=80)


class OperatorHeartbeat(BaseModel):
    session_key: str = Field(min_length=8, max_length=80)


class OperatorPresenceRead(BaseModel):
    manual_status: QueueStatus
    effective_status: EffectiveOperatorStatus
    last_heartbeat_at: datetime | None
    heartbeat_ttl_seconds: int
    current_call_id: UUID | None


class TransferCandidateRead(BaseModel):
    membership_id: UUID
    user_id: UUID
    display_name: str
    extension: str | None
    project_id: UUID
    effective_status: EffectiveOperatorStatus
    is_transfer_available: bool


class TeamAuditRead(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: str
    reason: str | None
    safe_metadata: dict[str, object]
    created_at: datetime
