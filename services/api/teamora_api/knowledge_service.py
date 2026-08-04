from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Float, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.config import Settings
from teamora_api.knowledge_embeddings import embedding_provider
from teamora_api.models import (
    Call,
    KnowledgeBaseRevision,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeRetrievalExecution,
    KnowledgeSource,
    Project,
)
from teamora_api.schemas.call_flows import normalize_language_code


@dataclass(frozen=True)
class RetrievalHit:
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

    def citation(self) -> dict[str, object]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_version_id": str(self.document_version_id),
            "document_version": self.document_version,
            "revision_id": str(self.revision_id),
            "title": self.title,
            "language": self.language,
            "page": self.page,
            "section": self.section,
        }


@dataclass(frozen=True)
class RetrievalResult:
    revision_id: UUID
    hits: list[RetrievalHit]
    provider: str
    model: str
    index_version: str
    provider_status: str
    no_match: bool
    usage: dict[str, int]


def fts_configuration(language: str) -> str:
    base = normalize_language_code(language).split("-", 1)[0]
    if base == "ru":
        return "russian"
    if base == "en":
        return "english"
    return "simple"


def mask_query(value: str) -> str:
    value = re.sub(r"\+?\d[\d\s().-]{6,}\d", "[phone]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", value)
    return value[:1000]


async def active_revision_for_project(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID
) -> KnowledgeBaseRevision | None:
    revision: KnowledgeBaseRevision | None = await session.scalar(
        select(KnowledgeBaseRevision)
        .join(
            KnowledgeSource,
            and_(
                KnowledgeSource.tenant_id == KnowledgeBaseRevision.tenant_id,
                KnowledgeSource.id == KnowledgeBaseRevision.knowledge_source_id,
            ),
        )
        .join(
            Project,
            and_(
                Project.tenant_id == KnowledgeSource.tenant_id,
                Project.id == KnowledgeSource.project_id,
            ),
        )
        .where(
            KnowledgeBaseRevision.tenant_id == tenant_id,
            KnowledgeBaseRevision.project_id == project_id,
            KnowledgeBaseRevision.status == "published",
            KnowledgeSource.active_revision_id == KnowledgeBaseRevision.id,
            KnowledgeSource.archived_at.is_(None),
            Project.knowledge_source_id == KnowledgeSource.id,
        )
    )
    return revision


async def hybrid_retrieve(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: UUID,
    project_id: UUID,
    query: str,
    language: str,
    revision_id: UUID | None = None,
    call_id: UUID | None = None,
    top_k: int = 5,
    threshold: float = 0.08,
    idempotency_key: str,
    allowed_document_ids: set[UUID] | None = None,
) -> RetrievalResult | None:
    normalized_language = normalize_language_code(language)
    revision = (
        await session.scalar(
            select(KnowledgeBaseRevision).where(
                KnowledgeBaseRevision.tenant_id == tenant_id,
                KnowledgeBaseRevision.project_id == project_id,
                KnowledgeBaseRevision.id == revision_id,
                KnowledgeBaseRevision.status == "published",
            )
        )
        if revision_id
        else await active_revision_for_project(session, tenant_id=tenant_id, project_id=project_id)
    )
    if revision is None:
        return None
    provider = embedding_provider(
        app_env=settings.app_env,
        name=revision.embedding_provider,
        model=revision.embedding_model,
        dimension=revision.embedding_dimension,
    )
    query_vector = (await provider.embed([query]))[0]
    usage = provider.usage([query])
    config = fts_configuration(normalized_language)
    ts_query = func.plainto_tsquery(config, query)
    lexical_rank = func.ts_rank_cd(func.to_tsvector(config, KnowledgeChunk.normalized_content), ts_query)
    exact_phrase = func.strpos(func.lower(KnowledgeChunk.normalized_content), query.casefold()) > 0
    lexical = cast(func.greatest(lexical_rank, case((exact_phrase, 1.0), else_=0.0)), Float)
    vector = cast(1.0 - KnowledgeChunk.embedding.cosine_distance(query_vector), Float)
    # Weighted late fusion: deterministic exact cosine + PostgreSQL language-aware FTS.
    combined = cast(0.60 * func.greatest(vector, 0.0) + 0.40 * func.least(lexical, 1.0), Float)
    minimum_vector_match = 0.30 if provider.status == "development" else 0.55
    base_language = normalized_language.split("-", 1)[0]
    language_match = (
        or_(
            KnowledgeChunk.language_code == normalized_language,
            KnowledgeChunk.language_code == base_language,
        )
        if "-" in normalized_language
        else or_(
            KnowledgeChunk.language_code == base_language,
            KnowledgeChunk.language_code.like(f"{base_language}-%"),
        )
    )
    statement = (
        select(
            KnowledgeChunk,
            KnowledgeDocument,
            KnowledgeDocumentVersion,
            lexical.label("lexical_score"),
            vector.label("vector_score"),
            combined.label("combined_score"),
        )
        .join(
            KnowledgeDocumentVersion,
            KnowledgeDocumentVersion.id == KnowledgeChunk.document_version_id,
        )
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.project_id == project_id,
            KnowledgeChunk.revision_id == revision.id,
            KnowledgeChunk.embedding_status == "ready",
            KnowledgeChunk.embedding.is_not(None),
            KnowledgeChunk.embedding_provider == revision.embedding_provider,
            KnowledgeChunk.embedding_model == revision.embedding_model,
            KnowledgeChunk.embedding_dimension == revision.embedding_dimension,
            KnowledgeChunk.index_version == revision.index_version,
            KnowledgeDocumentVersion.status == "ready",
            language_match,
            or_(lexical > 0.0, vector >= minimum_vector_match),
        )
        .order_by(combined.desc(), KnowledgeChunk.ordinal, KnowledgeChunk.id)
        .limit(min(max(top_k, 1) * 4, 80))
    )
    if allowed_document_ids is not None:
        statement = statement.where(KnowledgeDocument.id.in_(allowed_document_ids))
    started = time.perf_counter()
    rows = (await session.execute(statement)).all()
    hits: list[RetrievalHit] = []
    seen_checksums: set[str] = set()
    for chunk, document, version, lexical_score, vector_score, combined_score in rows:
        if chunk.content_checksum in seen_checksums:
            continue
        if float(combined_score or 0.0) < threshold:
            continue
        seen_checksums.add(chunk.content_checksum)
        hits.append(
            RetrievalHit(
                chunk_id=chunk.id,
                document_id=document.id,
                document_version_id=version.id,
                document_version=version.version,
                title=version.title_snapshot,
                language=chunk.language_code,
                page=chunk.page_number,
                section=chunk.section,
                excerpt=chunk.normalized_content[:1200],
                lexical_score=max(0.0, float(lexical_score or 0.0)),
                vector_score=max(-1.0, min(1.0, float(vector_score or 0.0))),
                combined_score=float(combined_score or 0.0),
                revision_id=revision.id,
            )
        )
        if len(hits) >= min(max(top_k, 1), 20):
            break
    execution = KnowledgeRetrievalExecution(
        tenant_id=tenant_id,
        project_id=project_id,
        revision_id=revision.id,
        call_id=call_id,
        query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        masked_query=mask_query(query),
        language_code=normalized_language,
        chunk_ids=[str(hit.chunk_id) for hit in hits],
        citations=[hit.citation() for hit in hits],
        scores=[
            {
                "chunk_id": str(hit.chunk_id),
                "lexical": round(hit.lexical_score, 6),
                "vector": round(hit.vector_score, 6),
                "combined": round(hit.combined_score, 6),
            }
            for hit in hits
        ],
        usage={"embedding": usage},
        latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        provider=provider.name,
        model=provider.model,
        index_version=revision.index_version,
        no_match=not hits,
        idempotency_key=idempotency_key,
    )
    session.add(execution)
    return RetrievalResult(
        revision_id=revision.id,
        hits=hits,
        provider=provider.name,
        model=provider.model,
        index_version=revision.index_version,
        provider_status=provider.status,
        no_match=not hits,
        usage=usage,
    )


async def pin_call_knowledge_revision(session: AsyncSession, call: Call) -> None:
    if call.knowledge_base_revision_id is not None:
        return
    revision = await active_revision_for_project(
        session, tenant_id=call.tenant_id, project_id=call.project_id
    )
    if revision is not None:
        call.knowledge_base_revision_id = revision.id
