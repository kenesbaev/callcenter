from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from teamora_api.enums import LanguageCode


class KnowledgeTextCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    language: LanguageCode
    content: str = Field(min_length=20, max_length=100_000)


class KnowledgeDocumentRead(BaseModel):
    id: UUID
    source_id: UUID
    title: str
    language: LanguageCode
    content: str
    created_at: datetime
