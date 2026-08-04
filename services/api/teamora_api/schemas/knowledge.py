from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from teamora_api.schemas.call_flows import normalize_language_code


class KnowledgeTextCreate(BaseModel):
    project_id: UUID | None = None
    knowledge_base_id: UUID | None = None
    title: str = Field(min_length=2, max_length=240)
    language: str
    content: str = Field(min_length=20, max_length=100_000)

    @field_validator("language")
    @classmethod
    def language_is_bcp47(cls, value: str) -> str:
        return normalize_language_code(value)


class KnowledgeBaseCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=1000)
    default_language_code: str = "ru"

    @field_validator("default_language_code")
    @classmethod
    def language_is_bcp47(cls, value: str) -> str:
        return normalize_language_code(value)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    default_language_code: str | None = None
    expected_version: int = Field(ge=1)

    @field_validator("default_language_code")
    @classmethod
    def language_is_bcp47(cls, value: str | None) -> str | None:
        return normalize_language_code(value) if value else None


class KnowledgeRevisionRead(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    project_id: UUID
    version: int
    status: str
    lock_version: int
    created_from_revision_id: UUID | None
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_status: str
    index_version: str
    published_at: datetime | None
    created_at: datetime


class KnowledgeBaseRead(BaseModel):
    id: UUID
    project_id: UUID | None
    name: str
    description: str
    default_language_code: str
    active_revision_id: UUID | None
    archived_at: datetime | None
    lock_version: int
    draft_revision: KnowledgeRevisionRead | None = None
    published_revision: KnowledgeRevisionRead | None = None
    document_count: int = 0
    created_at: datetime


class KnowledgeDocumentVersionRead(BaseModel):
    id: UUID
    document_id: UUID
    revision_id: UUID
    version: int
    status: str
    title_snapshot: str
    language_code: str
    original_filename: str
    has_original: bool
    file_type: str
    content_type: str
    checksum_sha256: str
    content_length: int
    page_count: int | None
    chunk_count: int = 0
    safe_error_code: str | None
    safe_error_message: str | None
    lock_version: int
    created_at: datetime


class KnowledgeDocumentRead(BaseModel):
    id: UUID
    source_id: UUID
    project_id: UUID | None
    title: str
    language: str
    content: str = ""
    archived_at: datetime | None = None
    created_at: datetime
    version: KnowledgeDocumentVersionRead | None = None


class KnowledgeChunkRead(BaseModel):
    id: UUID
    document_version_id: UUID
    ordinal: int
    language: str
    page: int | None
    section: str | None
    text: str
    character_count: int
    token_count: int
    embedding_status: str


class KnowledgePublishRequest(BaseModel):
    expected_version: int = Field(ge=1)


class KnowledgeRetryRequest(BaseModel):
    expected_version: int = Field(ge=1)


class KnowledgeRetrievalRequest(BaseModel):
    project_id: UUID
    knowledge_base_id: UUID | None = None
    revision_id: UUID | None = None
    query: str = Field(min_length=2, max_length=2000)
    language: str = "ru"
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0.08, ge=0, le=1)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)

    @field_validator("language")
    @classmethod
    def language_is_bcp47(cls, value: str) -> str:
        return normalize_language_code(value)


class KnowledgeRetrievalHitRead(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    document_version: int
    title: str
    language: str
    page: int | None
    section: str | None
    excerpt: str
    lexical_score: float
    vector_score: float
    combined_score: float
    revision_id: UUID


class KnowledgeRetrievalRead(BaseModel):
    revision_id: UUID
    hits: list[KnowledgeRetrievalHitRead]
    no_match: bool
    provider: str
    model: str
    provider_status: str
    index_version: str
    notice: str
    usage: dict[str, int]


class KnowledgeDownloadRead(BaseModel):
    url: str
    expires_in_seconds: int = 300


class KnowledgeIndexStatusRead(BaseModel):
    revision_id: UUID
    provider: str
    model: str
    dimension: int
    index_version: str
    provider_status: str
    ready_chunks: int
    pending_chunks: int
    notice: str | None = None
