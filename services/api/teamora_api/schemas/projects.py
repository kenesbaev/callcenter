from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ProjectStatus = Literal["active", "paused", "archived"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=1000)
    status: ProjectStatus = "active"
    outbound_number: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    max_concurrent_calls: int = Field(default=1, ge=1, le=1000)
    working_hours: dict[str, object] = Field(default_factory=dict)
    operator_user_ids: list[UUID] = Field(default_factory=list, max_length=1000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    status: ProjectStatus | None = None
    outbound_number: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    max_concurrent_calls: int | None = Field(default=None, ge=1, le=1000)
    working_hours: dict[str, object] | None = None
    operator_user_ids: list[UUID] | None = Field(default=None, max_length=1000)


class ProjectRead(BaseModel):
    id: UUID
    name: str
    description: str
    status: ProjectStatus
    outbound_number: str | None
    max_concurrent_calls: int
    working_hours: dict[str, object]
    is_default: bool
    operator_user_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
