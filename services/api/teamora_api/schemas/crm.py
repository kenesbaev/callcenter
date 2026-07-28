from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from teamora_api.enums import LanguageCode


def normalize_phone(value: str) -> str:
    compact = re.sub(r"[\s()\-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", compact):
        raise ValueError("Укажите телефон в международном формате, например +998901234567")
    return compact


class CustomerCreate(BaseModel):
    project_id: UUID | None = None
    display_name: str = Field(min_length=2, max_length=160)
    phone: str
    alternate_phone: str | None = None
    email: EmailStr | None = None
    preferred_language: LanguageCode = LanguageCode.RU
    external_reference: str | None = Field(default=None, max_length=160)
    custom_fields: dict[str, object] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("phone", "alternate_phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else None


class CustomerContactRead(BaseModel):
    kind: str
    value: str
    is_primary: bool


class CustomerRead(BaseModel):
    id: UUID
    project_id: UUID
    display_name: str | None
    external_reference: str | None
    preferred_language: LanguageCode | None
    status: str
    custom_fields: dict[str, object]
    contacts: list[CustomerContactRead]
    locked_by_user_id: UUID | None
    locked_until: datetime | None
    last_call_at: datetime | None
    next_call_at: datetime | None
    created_at: datetime


class CallbackCreate(BaseModel):
    customer_id: UUID
    due_at: datetime
    note: str = Field(default="", max_length=4000)


class CallbackRead(BaseModel):
    id: UUID
    project_id: UUID
    customer_id: UUID
    customer_name: str | None
    customer_phone: str | None
    call_id: UUID | None
    assigned_user_id: UUID | None
    due_at: datetime
    status: str
    note: str
    completed_at: datetime | None
    created_at: datetime


class DialerAssignment(BaseModel):
    customer: CustomerRead
    source: Literal["callback", "new"]
    callback_task_id: UUID | None = None
    lock_token: UUID


class DialerLeaseRequest(BaseModel):
    lock_token: UUID
