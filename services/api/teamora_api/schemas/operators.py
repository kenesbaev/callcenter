from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from teamora_api.enums import LanguageCode, OperatorVersionStatus


class AiOperatorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    system_instructions: str = Field(min_length=20, max_length=12_000)
    allowed_languages: list[LanguageCode] = Field(default_factory=lambda: [LanguageCode.RU], min_length=1)
    greeting_by_language: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["search_knowledge", "request_human_operator", "end_call"]
    )


class AiOperatorVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: OperatorVersionStatus
    system_instructions: str
    allowed_languages: list[str]
    allowed_tools: list[str]
    published_at: datetime | None


class AiOperatorRead(BaseModel):
    id: UUID
    name: str
    description: str
    is_active: bool
    active_version_id: UUID | None
    created_at: datetime
    version: AiOperatorVersionRead
