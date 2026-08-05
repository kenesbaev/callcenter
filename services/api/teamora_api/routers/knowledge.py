from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from sqlalchemy import and_, func, or_, select

from teamora_api.audit import write_audit
from teamora_api.background_service import (
    acquire_idempotency_lock,
    enqueue_background_job,
    serialize_background_job,
)
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import LanguageCode, RoleName
from teamora_api.errors import ApiError
from teamora_api.knowledge_chunking import ExtractedBlock, chunk_blocks, normalize_text
from teamora_api.knowledge_embeddings import embedding_provider
from teamora_api.knowledge_files import validate_upload
from teamora_api.knowledge_lifecycle import ALLOWED_DOCUMENT_TRANSITIONS, transition_document
from teamora_api.knowledge_service import hybrid_retrieve
from teamora_api.knowledge_storage import (
    KnowledgeObjectStorage,
    KnowledgeStorageUnavailable,
)
from teamora_api.models import (
    BackgroundJob,
    DocumentIngestionJob,
    KnowledgeBaseRevision,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeRetrievalExecution,
    KnowledgeSource,
    RetentionCandidate,
    StorageObject,
    TeamCommandSubmission,
)
from teamora_api.object_storage import controlled_object_key
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.retention_lock import acquire_retention_lock
from teamora_api.schemas.common import Page
from teamora_api.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    KnowledgeChunkRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentVersionRead,
    KnowledgeDownloadRead,
    KnowledgeIndexStatusRead,
    KnowledgePublishRequest,
    KnowledgeRetrievalHitRead,
    KnowledgeRetrievalRead,
    KnowledgeRetrievalRequest,
    KnowledgeRetryRequest,
    KnowledgeRevisionRead,
    KnowledgeTextCreate,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
MANAGER_ROLES = {RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER}


def _knowledge_command_fingerprint(operation: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _knowledge_command_replay(
    session: SessionDep,
    *,
    tenant_id: UUID,
    operation: str,
    idempotency_key: str,
    request_fingerprint: str,
    resource_id: UUID | None = None,
) -> TeamCommandSubmission | None:
    # TeamCommandSubmission is the existing durable, tenant-RLS protected domain
    # command journal. Its unique key is tenant-wide, so every user-facing domain
    # sharing it must also share this advisory-lock namespace.
    await acquire_idempotency_lock(
        session,
        namespace="team",
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
    )
    replay = await session.scalar(
        select(TeamCommandSubmission).where(
            TeamCommandSubmission.tenant_id == tenant_id,
            TeamCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is None:
        return None
    if (
        replay.operation != operation
        or replay.request_fingerprint != request_fingerprint
        or (resource_id is not None and replay.resource_id != resource_id)
    ):
        raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
    return replay


def _record_knowledge_command(
    session: SessionDep,
    *,
    tenant_id: UUID,
    operation: str,
    idempotency_key: str,
    request_fingerprint: str,
    resource_id: UUID,
    response: KnowledgeDocumentRead | KnowledgeDocumentVersionRead,
) -> None:
    response_payload = response.model_dump(mode="json")
    session.add(
        TeamCommandSubmission(
            tenant_id=tenant_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            resource_id=resource_id,
            response_payload=response_payload,
        )
    )


def _embedding_status(revision: KnowledgeBaseRevision) -> str:
    settings = get_settings()
    return embedding_provider(
        app_env=settings.app_env,
        name=revision.embedding_provider,
        model=revision.embedding_model,
        dimension=revision.embedding_dimension,
    ).status


def _revision_read(revision: KnowledgeBaseRevision) -> KnowledgeRevisionRead:
    return KnowledgeRevisionRead(
        id=revision.id,
        knowledge_base_id=revision.knowledge_source_id,
        project_id=revision.project_id,
        version=revision.version,
        status=revision.status,
        lock_version=revision.lock_version,
        created_from_revision_id=revision.created_from_revision_id,
        embedding_provider=revision.embedding_provider,
        embedding_model=revision.embedding_model,
        embedding_dimension=revision.embedding_dimension,
        embedding_status=_embedding_status(revision),
        index_version=revision.index_version,
        published_at=revision.published_at,
        created_at=revision.created_at,
    )


async def _base_read(
    session: SessionDep, source: KnowledgeSource, *, include_draft: bool = True
) -> KnowledgeBaseRead:
    revisions = list(
        await session.scalars(
            select(KnowledgeBaseRevision).where(
                KnowledgeBaseRevision.tenant_id == source.tenant_id,
                KnowledgeBaseRevision.knowledge_source_id == source.id,
            )
        )
    )
    count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == source.tenant_id,
                KnowledgeDocument.source_id == source.id,
                KnowledgeDocument.archived_at.is_(None),
            )
        )
        or 0
    )
    return KnowledgeBaseRead(
        id=source.id,
        project_id=source.project_id,
        name=source.name,
        description=source.description,
        default_language_code=source.default_language_code,
        active_revision_id=source.active_revision_id,
        archived_at=source.archived_at,
        lock_version=source.lock_version,
        draft_revision=(
            next((_revision_read(item) for item in revisions if item.status == "draft"), None)
            if include_draft
            else None
        ),
        published_revision=next(
            (_revision_read(item) for item in revisions if item.id == source.active_revision_id), None
        ),
        document_count=count,
        created_at=source.created_at,
    )


async def _resolve_base(
    session: SessionDep,
    principal: Principal,
    source_id: UUID,
    *,
    for_update: bool = False,
) -> KnowledgeSource:
    statement = select(KnowledgeSource).where(
        KnowledgeSource.tenant_id == principal.tenant_id,
        KnowledgeSource.id == source_id,
    )
    if for_update:
        statement = statement.with_for_update()
    source = await session.scalar(statement)
    if source is None or source.project_id is None:
        raise ApiError(404, "knowledge_base_not_found", "Knowledge base was not found")
    await resolve_project(session, principal, source.project_id, active_only=False)
    return source


async def _resolve_revision(
    session: SessionDep,
    principal: Principal,
    revision_id: UUID,
    *,
    draft_only: bool = False,
    for_update: bool = False,
) -> KnowledgeBaseRevision:
    statement = select(KnowledgeBaseRevision).where(
        KnowledgeBaseRevision.tenant_id == principal.tenant_id,
        KnowledgeBaseRevision.id == revision_id,
    )
    if draft_only:
        statement = statement.where(KnowledgeBaseRevision.status == "draft")
    if for_update:
        statement = statement.with_for_update()
    revision = await session.scalar(statement)
    if revision is None:
        raise ApiError(404, "knowledge_revision_not_found", "Knowledge revision was not found")
    await resolve_project(session, principal, revision.project_id, active_only=False)
    if principal.role not in MANAGER_ROLES and revision.status != "published":
        raise ApiError(404, "knowledge_revision_not_found", "Knowledge revision was not found")
    return revision


def _legacy_language(value: str) -> LanguageCode:
    base = value.split("-", 1)[0]
    return LanguageCode(base) if base in {item.value for item in LanguageCode} else LanguageCode.RU


@router.get("/options", response_model=dict[str, object])
async def knowledge_options(
    principal: Principal = require_permission("knowledge:read"),
) -> dict[str, object]:
    del principal
    settings = get_settings()
    provider = embedding_provider(
        app_env=settings.app_env,
        name=settings.knowledge_embedding_provider,
        model=settings.knowledge_embedding_model,
        dimension=settings.knowledge_embedding_dimension,
    )
    return {
        "formats": ["txt", "md", "pdf", "docx"],
        "languages": ["ru", "uz", "en", "kaa", "kaa-latn", "kaa-cyrl", "ka"],
        "document_statuses": sorted(ALLOWED_DOCUMENT_TRANSITIONS),
        "max_file_bytes": settings.knowledge_max_file_bytes,
        "max_pdf_pages": settings.knowledge_max_pdf_pages,
        "max_extracted_chars": settings.knowledge_max_extracted_chars,
        "max_chunks": settings.knowledge_max_chunks,
        "embedding": {
            "provider": provider.name,
            "model": provider.model,
            "dimension": provider.dimension,
            "status": provider.status,
            "notice": (
                None
                if provider.status != "unavailable"
                else "Embedding provider unavailable — live verification required"
            ),
        },
    }


@router.get("/bases", response_model=Page[KnowledgeBaseRead])
async def list_bases(
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
    project_id: UUID | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Page[KnowledgeBaseRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [KnowledgeSource.tenant_id == principal.tenant_id]
    if project_id is None:
        filters.append(KnowledgeSource.project_id.in_(await accessible_project_ids(session, principal)))
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
        filters.append(KnowledgeSource.project_id == project_id)
    if not include_archived:
        filters.append(KnowledgeSource.archived_at.is_(None))
    if principal.role not in MANAGER_ROLES:
        filters.append(KnowledgeSource.active_revision_id.is_not(None))
    total = int(await session.scalar(select(func.count()).select_from(KnowledgeSource).where(*filters)) or 0)
    sources = list(
        await session.scalars(
            select(KnowledgeSource)
            .where(*filters)
            .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[
            await _base_read(session, source, include_draft=principal.role in MANAGER_ROLES)
            for source in sources
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/bases/{source_id}", response_model=KnowledgeBaseRead)
async def get_base(
    source_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> KnowledgeBaseRead:
    source = await _resolve_base(session, principal, source_id)
    if principal.role not in MANAGER_ROLES and source.active_revision_id is None:
        raise ApiError(404, "knowledge_base_not_found", "Knowledge base was not found")
    return await _base_read(session, source, include_draft=principal.role in MANAGER_ROLES)


@router.post("/bases", response_model=KnowledgeBaseRead, status_code=201)
async def create_base(
    payload: KnowledgeBaseCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeBaseRead:
    project = await resolve_project(session, principal, payload.project_id)
    source = KnowledgeSource(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        source_type="versioned",
        default_language_code=payload.default_language_code,
    )
    session.add(source)
    await session.flush()
    revision = KnowledgeBaseRevision(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        knowledge_source_id=source.id,
        version=1,
        status="draft",
        embedding_provider=get_settings().knowledge_embedding_provider,
        embedding_model=get_settings().knowledge_embedding_model,
        embedding_dimension=get_settings().knowledge_embedding_dimension,
        index_version="kb-index-v1",
        chunking_config={
            "size_chars": get_settings().knowledge_chunk_size_chars,
            "overlap_chars": get_settings().knowledge_chunk_overlap_chars,
        },
    )
    session.add(revision)
    if project.knowledge_source_id is None:
        project.knowledge_source_id = source.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.base_created",
        resource_type="knowledge_base",
        resource_id=source.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id)},
    )
    await session.flush()
    response = await _base_read(session, source)
    await session.commit()
    return response


@router.patch("/bases/{source_id}", response_model=KnowledgeBaseRead)
async def update_base(
    source_id: UUID,
    payload: KnowledgeBaseUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeBaseRead:
    source = await _resolve_base(session, principal, source_id, for_update=True)
    if source.lock_version != payload.expected_version:
        raise ApiError(409, "knowledge_base_conflict", "Knowledge base was changed by another request")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for key, value in changes.items():
        setattr(source, key, value.strip() if isinstance(value, str) else value)
    source.lock_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.base_updated",
        resource_type="knowledge_base",
        resource_id=source.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": ",".join(sorted(changes))},
    )
    await session.flush()
    response = await _base_read(session, source)
    await session.commit()
    return response


@router.post("/bases/{source_id}/archive", response_model=KnowledgeBaseRead)
async def archive_base(
    source_id: UUID,
    payload: KnowledgeRetryRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeBaseRead:
    source = await _resolve_base(session, principal, source_id, for_update=True)
    if source.lock_version != payload.expected_version:
        raise ApiError(409, "knowledge_base_conflict", "Knowledge base was changed by another request")
    if source.archived_at is None:
        source.archived_at = datetime.now(UTC)
        source.lock_version += 1
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="knowledge.base_archived",
            resource_type="knowledge_base",
            resource_id=source.id,
            correlation_id=request.state.correlation_id,
        )
    await session.flush()
    response = await _base_read(session, source)
    await session.commit()
    return response


@router.post("/bases/{source_id}/restore", response_model=KnowledgeBaseRead)
async def restore_base(
    source_id: UUID,
    payload: KnowledgeRetryRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeBaseRead:
    source = await _resolve_base(session, principal, source_id, for_update=True)
    if source.lock_version != payload.expected_version:
        raise ApiError(409, "knowledge_base_conflict", "Knowledge base was changed by another request")
    if source.archived_at is not None:
        source.archived_at = None
        source.lock_version += 1
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="knowledge.base_restored",
            resource_type="knowledge_base",
            resource_id=source.id,
            correlation_id=request.state.correlation_id,
        )
    await session.flush()
    response = await _base_read(session, source)
    await session.commit()
    return response


@router.get("/bases/{source_id}/revisions", response_model=list[KnowledgeRevisionRead])
async def list_revisions(
    source_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> list[KnowledgeRevisionRead]:
    await _resolve_base(session, principal, source_id)
    filters = [
        KnowledgeBaseRevision.tenant_id == principal.tenant_id,
        KnowledgeBaseRevision.knowledge_source_id == source_id,
    ]
    if principal.role not in MANAGER_ROLES:
        filters.append(KnowledgeBaseRevision.status == "published")
    revisions = list(
        await session.scalars(
            select(KnowledgeBaseRevision).where(*filters).order_by(KnowledgeBaseRevision.version.desc())
        )
    )
    return [_revision_read(item) for item in revisions]


@router.post("/bases/{source_id}/draft", response_model=KnowledgeRevisionRead, status_code=201)
async def create_draft(
    source_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeRevisionRead:
    source = await _resolve_base(session, principal, source_id, for_update=True)
    existing = await session.scalar(
        select(KnowledgeBaseRevision).where(
            KnowledgeBaseRevision.tenant_id == principal.tenant_id,
            KnowledgeBaseRevision.knowledge_source_id == source.id,
            KnowledgeBaseRevision.status == "draft",
        )
    )
    if existing is not None:
        return _revision_read(existing)
    published = await session.scalar(
        select(KnowledgeBaseRevision).where(
            KnowledgeBaseRevision.tenant_id == principal.tenant_id,
            KnowledgeBaseRevision.id == source.active_revision_id,
            KnowledgeBaseRevision.status == "published",
        )
    )
    next_version = (
        int(
            await session.scalar(
                select(func.max(KnowledgeBaseRevision.version)).where(
                    KnowledgeBaseRevision.knowledge_source_id == source.id
                )
            )
            or 0
        )
        + 1
    )
    settings = get_settings()
    draft = KnowledgeBaseRevision(
        tenant_id=principal.tenant_id,
        project_id=source.project_id,
        knowledge_source_id=source.id,
        version=next_version,
        status="draft",
        created_from_revision_id=published.id if published else None,
        embedding_provider=published.embedding_provider
        if published
        else settings.knowledge_embedding_provider,
        embedding_model=published.embedding_model if published else settings.knowledge_embedding_model,
        embedding_dimension=published.embedding_dimension
        if published
        else settings.knowledge_embedding_dimension,
        index_version=f"kb-index-v{next_version}",
        chunking_config=(
            published.chunking_config
            if published
            else {
                "size_chars": settings.knowledge_chunk_size_chars,
                "overlap_chars": settings.knowledge_chunk_overlap_chars,
            }
        ),
    )
    session.add(draft)
    await session.flush()
    if published:
        await _clone_revision_documents(
            session, source=source, source_revision=published, target_revision=draft
        )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.revision_draft_created",
        resource_type="knowledge_base_revision",
        resource_id=draft.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return _revision_read(draft)


async def _clone_revision_documents(
    session: SessionDep,
    *,
    source: KnowledgeSource,
    source_revision: KnowledgeBaseRevision,
    target_revision: KnowledgeBaseRevision,
) -> None:
    versions = list(
        await session.scalars(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.tenant_id == source.tenant_id,
                KnowledgeDocumentVersion.revision_id == source_revision.id,
                KnowledgeDocumentVersion.status == "ready",
            )
        )
    )
    for old in versions:
        next_version = (
            int(
                await session.scalar(
                    select(func.max(KnowledgeDocumentVersion.version)).where(
                        KnowledgeDocumentVersion.document_id == old.document_id
                    )
                )
                or 0
            )
            + 1
        )
        new = KnowledgeDocumentVersion(
            tenant_id=old.tenant_id,
            project_id=old.project_id,
            knowledge_source_id=old.knowledge_source_id,
            revision_id=target_revision.id,
            document_id=old.document_id,
            version=next_version,
            status="ready",
            title_snapshot=old.title_snapshot,
            language_code=old.language_code,
            original_filename=old.original_filename,
            file_type=old.file_type,
            content_type=old.content_type,
            object_key=old.object_key,
            storage_object_id=old.storage_object_id,
            checksum_sha256=old.checksum_sha256,
            content_length=old.content_length,
            extracted_text=old.extracted_text,
            normalized_text=old.normalized_text,
            page_count=old.page_count,
            extraction_metadata={**old.extraction_metadata, "copied_from_version_id": str(old.id)},
            ready_at=datetime.now(UTC),
        )
        session.add(new)
        await session.flush()
        old_chunks = list(
            await session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.tenant_id == source.tenant_id,
                    KnowledgeChunk.document_version_id == old.id,
                )
            )
        )
        for chunk in old_chunks:
            session.add(
                KnowledgeChunk(
                    tenant_id=chunk.tenant_id,
                    project_id=chunk.project_id,
                    revision_id=target_revision.id,
                    document_id=chunk.document_id,
                    document_version_id=new.id,
                    ordinal=chunk.ordinal,
                    content=chunk.content,
                    normalized_content=chunk.normalized_content,
                    token_count=chunk.token_count,
                    character_count=chunk.character_count,
                    content_checksum=chunk.content_checksum,
                    language_code=chunk.language_code,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    embedding=chunk.embedding,
                    embedding_status=chunk.embedding_status,
                    embedding_provider=chunk.embedding_provider,
                    embedding_model=chunk.embedding_model,
                    embedding_dimension=chunk.embedding_dimension,
                    index_version=target_revision.index_version,
                )
            )


@router.post("/revisions/{revision_id}/publish", response_model=KnowledgeRevisionRead)
async def publish_revision(
    revision_id: UUID,
    payload: KnowledgePublishRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeRevisionRead:
    await acquire_retention_lock(session, principal.tenant_id)
    revision = await _resolve_revision(session, principal, revision_id, draft_only=True, for_update=True)
    if revision.lock_version != payload.expected_version:
        raise ApiError(409, "knowledge_revision_conflict", "Revision was changed by another request")
    statuses = list(
        await session.execute(
            select(KnowledgeDocumentVersion.status, func.count())
            .where(
                KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
                KnowledgeDocumentVersion.revision_id == revision.id,
            )
            .group_by(KnowledgeDocumentVersion.status)
        )
    )
    status_counts = {status: int(count) for status, count in statuses}
    if not status_counts.get("ready"):
        raise ApiError(409, "knowledge_revision_empty", "At least one ready document is required")
    blockers = {
        status: count for status, count in status_counts.items() if status not in {"ready", "archived"}
    }
    if blockers:
        raise ApiError(409, "knowledge_revision_not_ready", "All active documents must finish processing")
    source = await _resolve_base(session, principal, revision.knowledge_source_id, for_update=True)
    now = datetime.now(UTC)
    revision.status = "published"
    revision.published_at = now
    revision.lock_version += 1
    source.active_revision_id = revision.id
    source.lock_version += 1
    project = await resolve_project(session, principal, revision.project_id, for_update=True)
    project.knowledge_source_id = source.id
    await _recompute_source_storage_lifecycle(
        session,
        source=source,
        published_revision_id=revision.id,
        occurred_at=now,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        event_type="knowledge.revision_published",
        aggregate_type="knowledge_revision",
        aggregate_id=revision.id,
        aggregate_version=revision.lock_version,
        payload={"knowledge_base_id": source.id, "version": revision.version},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.revision_published",
        resource_type="knowledge_base_revision",
        resource_id=revision.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return _revision_read(revision)


@router.get("/documents", response_model=Page[KnowledgeDocumentRead])
async def list_documents(
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
    knowledge_base_id: UUID | None = None,
    revision_id: UUID | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Page[KnowledgeDocumentRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [
        KnowledgeDocument.tenant_id == principal.tenant_id,
        KnowledgeDocument.project_id.in_(await accessible_project_ids(session, principal)),
    ]
    if not include_archived:
        filters.append(KnowledgeDocument.archived_at.is_(None))
    if knowledge_base_id is not None:
        await _resolve_base(session, principal, knowledge_base_id)
        filters.append(KnowledgeDocument.source_id == knowledge_base_id)
    version_join = and_(
        KnowledgeDocumentVersion.tenant_id == KnowledgeDocument.tenant_id,
        KnowledgeDocumentVersion.document_id == KnowledgeDocument.id,
    )
    if not include_archived:
        version_join = and_(version_join, KnowledgeDocumentVersion.status != "archived")
    if revision_id is not None:
        await _resolve_revision(session, principal, revision_id)
        version_join = and_(version_join, KnowledgeDocumentVersion.revision_id == revision_id)
    elif knowledge_base_id is not None and principal.role not in MANAGER_ROLES:
        source = await _resolve_base(session, principal, knowledge_base_id)
        if source.active_revision_id is None:
            raise ApiError(404, "knowledge_revision_not_found", "Knowledge revision was not found")
        version_join = and_(version_join, KnowledgeDocumentVersion.revision_id == source.active_revision_id)
    elif principal.role not in MANAGER_ROLES:
        version_join = and_(
            version_join,
            KnowledgeDocumentVersion.revision_id.in_(
                select(KnowledgeBaseRevision.id).where(
                    KnowledgeBaseRevision.tenant_id == principal.tenant_id,
                    KnowledgeBaseRevision.status == "published",
                )
            ),
        )
    total = int(
        await session.scalar(select(func.count()).select_from(KnowledgeDocument).where(*filters)) or 0
    )
    rows = (
        await session.execute(
            select(KnowledgeDocument, KnowledgeDocumentVersion)
            .outerjoin(KnowledgeDocumentVersion, version_join)
            .where(*filters)
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocumentVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[await _document_read(session, document, version) for document, version in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _version_read(
    session: SessionDep, version: KnowledgeDocumentVersion
) -> KnowledgeDocumentVersionRead:
    count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == version.tenant_id,
                KnowledgeChunk.document_version_id == version.id,
            )
        )
        or 0
    )
    background_job = await session.scalar(
        select(BackgroundJob)
        .join(
            DocumentIngestionJob,
            and_(
                DocumentIngestionJob.tenant_id == BackgroundJob.tenant_id,
                DocumentIngestionJob.background_job_id == BackgroundJob.id,
            ),
        )
        .where(
            DocumentIngestionJob.tenant_id == version.tenant_id,
            DocumentIngestionJob.document_version_id == version.id,
        )
    )
    return KnowledgeDocumentVersionRead(
        id=version.id,
        document_id=version.document_id,
        revision_id=version.revision_id,
        version=version.version,
        status=version.status,
        title_snapshot=version.title_snapshot,
        language_code=version.language_code,
        original_filename=version.original_filename,
        has_original=version.object_key is not None,
        file_type=version.file_type,
        content_type=version.content_type,
        checksum_sha256=version.checksum_sha256,
        content_length=version.content_length,
        page_count=version.page_count,
        chunk_count=count,
        safe_error_code=version.safe_error_code,
        safe_error_message=version.safe_error_message,
        lock_version=version.lock_version,
        background_job=(serialize_background_job(background_job) if background_job else None),
        created_at=version.created_at,
    )


async def _document_read(
    session: SessionDep, document: KnowledgeDocument, version: KnowledgeDocumentVersion | None
) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=document.id,
        source_id=document.source_id,
        project_id=document.project_id,
        title=document.title,
        language=version.language_code if version else document.language.value,
        content=(version.normalized_text[:4000] if version else document.content),
        archived_at=document.archived_at,
        created_at=document.created_at,
        version=await _version_read(session, version) if version else None,
    )


@router.post("/documents/upload", response_model=KnowledgeDocumentRead, status_code=201)
async def upload_document(
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
    revision_id: UUID = Form(),
    title: str = Form(min_length=2, max_length=240),
    language: str = Form(default="ru"),
    document_id: UUID | None = Form(default=None),
    document_expected_version: int | None = Form(default=None),
    file: UploadFile = File(),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> KnowledgeDocumentRead:
    from teamora_api.schemas.call_flows import normalize_language_code

    normalized_language = normalize_language_code(language)
    revision = await _resolve_revision(session, principal, revision_id)
    data = await file.read(get_settings().knowledge_max_file_bytes + 1)
    settings = get_settings()
    validated = validate_upload(
        file.filename,
        file.content_type,
        data,
        maximum=settings.knowledge_max_file_bytes,
        max_docx_expanded_bytes=settings.knowledge_max_docx_expanded_bytes,
        max_zip_ratio=settings.knowledge_max_zip_ratio,
    )
    checksum = hashlib.sha256(data).hexdigest()
    operation = "knowledge.document.upload"
    fingerprint = _knowledge_command_fingerprint(
        operation,
        {
            "revision_id": str(revision_id),
            "title": title.strip(),
            "language": normalized_language,
            "document_id": str(document_id) if document_id else None,
            "document_expected_version": document_expected_version,
            "filename": validated.safe_filename,
            "file_type": validated.file_type,
            "content_type": validated.content_type,
            "content_length": len(data),
            "checksum_sha256": checksum,
        },
    )
    replay = await _knowledge_command_replay(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if replay is not None:
        return KnowledgeDocumentRead.model_validate(replay.response_payload)
    revision = await _resolve_revision(
        session,
        principal,
        revision_id,
        draft_only=True,
        for_update=True,
    )
    existing_job = await session.scalar(
        select(DocumentIngestionJob).where(
            DocumentIngestionJob.tenant_id == principal.tenant_id,
            DocumentIngestionJob.idempotency_key == idempotency_key,
        )
    )
    if existing_job is not None:
        raise ApiError(
            409,
            "knowledge_upload_legacy_replay_unavailable",
            "The previous upload predates durable request fingerprints",
        )
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"knowledge-content:{principal.tenant_id}:{revision.id}:{checksum}",
                    0,
                )
            )
        )
    )
    duplicate = await session.scalar(
        select(KnowledgeDocumentVersion.id).where(
            KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
            KnowledgeDocumentVersion.knowledge_source_id == revision.knowledge_source_id,
            KnowledgeDocumentVersion.revision_id == revision.id,
            KnowledgeDocumentVersion.checksum_sha256 == checksum,
            KnowledgeDocumentVersion.status != "archived",
        )
    )
    if duplicate:
        raise ApiError(409, "knowledge_document_duplicate", "This file already exists in the draft revision")
    if document_id is None:
        document = KnowledgeDocument(
            tenant_id=principal.tenant_id,
            project_id=revision.project_id,
            source_id=revision.knowledge_source_id,
            title=title.strip(),
            language=_legacy_language(normalized_language),
            content="",
            content_hash=checksum,
            is_active=True,
        )
        session.add(document)
        await session.flush()
        next_version = 1
    else:
        existing_document = await session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == principal.tenant_id,
                KnowledgeDocument.project_id == revision.project_id,
                KnowledgeDocument.source_id == revision.knowledge_source_id,
                KnowledgeDocument.id == document_id,
            )
            .with_for_update()
        )
        if existing_document is None:
            raise ApiError(404, "knowledge_document_not_found", "Knowledge document was not found")
        document = existing_document
        current = await session.scalar(
            select(KnowledgeDocumentVersion)
            .where(
                KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
                KnowledgeDocumentVersion.revision_id == revision.id,
                KnowledgeDocumentVersion.document_id == document.id,
                KnowledgeDocumentVersion.status != "archived",
            )
            .with_for_update()
        )
        if current is not None:
            if document_expected_version is None or current.lock_version != document_expected_version:
                raise ApiError(
                    409,
                    "knowledge_version_conflict",
                    "Document was changed by another request",
                )
            transition_document(current, "archived", expected_version=document_expected_version)
        next_version = (
            int(
                await session.scalar(
                    select(func.max(KnowledgeDocumentVersion.version)).where(
                        KnowledgeDocumentVersion.document_id == document.id
                    )
                )
                or 0
            )
            + 1
        )
        document.title = title.strip()
        document.language = _legacy_language(normalized_language)
        document.archived_at = None
        document.is_active = True
        document.lock_version += 1
    version = KnowledgeDocumentVersion(
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        knowledge_source_id=revision.knowledge_source_id,
        revision_id=revision.id,
        document_id=document.id,
        version=next_version,
        status="uploaded",
        title_snapshot=title.strip(),
        language_code=normalized_language,
        original_filename=validated.safe_filename,
        file_type=validated.file_type,
        content_type=validated.content_type,
        checksum_sha256=checksum,
        content_length=len(data),
    )
    session.add(version)
    await session.flush()
    key = controlled_object_key(
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        category="knowledge_original",
        object_id=version.id,
    )
    try:
        storage = KnowledgeObjectStorage(get_settings())
        await storage.put(key=key, data=data, content_type=validated.content_type)
    except KnowledgeStorageUnavailable as exc:
        await session.rollback()
        raise ApiError(503, "knowledge_storage_unavailable", str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise ApiError(503, "knowledge_storage_failed", "Could not store the private original") from exc
    version.object_key = key
    storage_object = StorageObject(
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        bucket=settings.minio_bucket,
        object_key=key,
        category="knowledge_original",
        owner_aggregate_type="knowledge_document_version",
        owner_aggregate_id=version.id,
        checksum_sha256=checksum,
        size_bytes=len(data),
        content_type=validated.content_type,
        status="active",
        retention_state="retained",
        lock_version=1,
    )
    session.add(storage_object)
    await session.flush()
    version.storage_object_id = storage_object.id
    transition_document(version, "queued")
    background_job, _ = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        created_by_user_id=principal.user_id,
        job_type="knowledge.ingest_document",
        queue="knowledge",
        priority=60,
        safe_payload={
            "document_version_id": version.id,
            "knowledge_base_id": revision.knowledge_source_id,
            "storage_object_id": storage_object.id,
        },
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    job = DocumentIngestionJob(
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        document_version_id=version.id,
        status="pending",
        stage="queued",
        idempotency_key=idempotency_key,
        background_job_id=background_job.id,
    )
    session.add(job)
    revision.lock_version += 1
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=revision.project_id,
        event_type="knowledge.document_uploaded",
        aggregate_type="knowledge_document_version",
        aggregate_id=version.id,
        aggregate_version=version.lock_version,
        payload={"status": version.status, "knowledge_base_id": revision.knowledge_source_id},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.document_uploaded",
        resource_type="knowledge_document_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"file_type": version.file_type, "content_length": len(data)},
    )
    await session.flush()
    response = await _document_read(session, document, version)
    _record_knowledge_command(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        resource_id=version.id,
        response=response,
    )
    await session.commit()
    return response


@router.post("/text", response_model=KnowledgeDocumentRead, status_code=201)
async def create_text_document(
    payload: KnowledgeTextCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> KnowledgeDocumentRead:
    legacy_auto_publish = payload.project_id is None and payload.knowledge_base_id is None
    project = await resolve_project(session, principal, payload.project_id)
    operation = "knowledge.text.create"
    fingerprint = _knowledge_command_fingerprint(operation, payload.model_dump(mode="json"))
    replay = await _knowledge_command_replay(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if replay is not None:
        return KnowledgeDocumentRead.model_validate(replay.response_payload)
    # Serialise initial base/draft creation and duplicate detection for a project.
    # This lock is transaction-scoped and is acquired before any source mutation or
    # embedding call, so concurrent logical retries cannot duplicate embedding cost.
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"knowledge-text-project:{principal.tenant_id}:{project.id}",
                    0,
                )
            )
        )
    )
    source = (
        await _resolve_base(session, principal, payload.knowledge_base_id)
        if payload.knowledge_base_id
        else await session.scalar(
            select(KnowledgeSource)
            .where(
                KnowledgeSource.tenant_id == principal.tenant_id,
                KnowledgeSource.project_id == project.id,
                KnowledgeSource.archived_at.is_(None),
            )
            .order_by(KnowledgeSource.created_at)
        )
    )
    if source is None:
        source = KnowledgeSource(
            tenant_id=principal.tenant_id,
            project_id=project.id,
            name="Project knowledge",
            description="Migrated text knowledge",
            source_type="versioned",
            default_language_code=payload.language,
        )
        session.add(source)
        await session.flush()
    revision = await session.scalar(
        select(KnowledgeBaseRevision).where(
            KnowledgeBaseRevision.tenant_id == principal.tenant_id,
            KnowledgeBaseRevision.knowledge_source_id == source.id,
            KnowledgeBaseRevision.status == "draft",
        )
    )
    if revision is None:
        revision = KnowledgeBaseRevision(
            tenant_id=principal.tenant_id,
            project_id=project.id,
            knowledge_source_id=source.id,
            version=1,
            status="draft",
            embedding_provider=get_settings().knowledge_embedding_provider,
            embedding_model=get_settings().knowledge_embedding_model,
            embedding_dimension=get_settings().knowledge_embedding_dimension,
            index_version="kb-index-v1",
            chunking_config={
                "size_chars": get_settings().knowledge_chunk_size_chars,
                "overlap_chars": get_settings().knowledge_chunk_overlap_chars,
            },
        )
        session.add(revision)
        await session.flush()
    content_hash = hashlib.sha256(payload.content.encode()).hexdigest()
    existing = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant_id,
            KnowledgeDocument.source_id == source.id,
            KnowledgeDocument.content_hash == content_hash,
        )
    )
    if existing:
        raise ApiError(409, "knowledge_duplicate", "The same knowledge content already exists")
    normalized = normalize_text(payload.content)
    provider = embedding_provider(
        app_env=get_settings().app_env,
        name=revision.embedding_provider,
        model=revision.embedding_model,
        dimension=revision.embedding_dimension,
    )
    chunks = chunk_blocks(
        [ExtractedBlock(normalized, page=1)],
        size_chars=int(str(revision.chunking_config.get("size_chars", 1800))),
        overlap_chars=int(str(revision.chunking_config.get("overlap_chars", 220))),
    )
    chunk_texts = [chunk.text for chunk in chunks]
    vectors = await provider.embed(chunk_texts)
    embedding_usage = provider.usage(chunk_texts)
    document = KnowledgeDocument(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        source_id=source.id,
        title=payload.title.strip(),
        language=_legacy_language(payload.language),
        content=payload.content,
        content_hash=content_hash,
        is_active=True,
    )
    session.add(document)
    await session.flush()
    version = KnowledgeDocumentVersion(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        knowledge_source_id=source.id,
        revision_id=revision.id,
        document_id=document.id,
        version=1,
        status="ready",
        title_snapshot=payload.title.strip(),
        language_code=payload.language,
        original_filename=f"{payload.title[:220]}.txt",
        file_type="txt",
        content_type="text/plain; charset=utf-8",
        checksum_sha256=content_hash,
        content_length=len(payload.content.encode()),
        extracted_text=payload.content,
        normalized_text=normalized,
        page_count=1,
        extraction_metadata={
            "source": "text_editor",
            "embedding_usage": embedding_usage,
        },
        ready_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            KnowledgeChunk(
                tenant_id=principal.tenant_id,
                project_id=project.id,
                revision_id=revision.id,
                document_id=document.id,
                document_version_id=version.id,
                ordinal=ordinal,
                content=chunk.text,
                normalized_content=chunk.text,
                token_count=chunk.token_count,
                character_count=chunk.character_count,
                content_checksum=chunk.checksum,
                language_code=payload.language,
                page_number=chunk.page,
                section=chunk.section,
                embedding=vector,
                embedding_status="ready",
                embedding_provider=provider.name,
                embedding_model=provider.model,
                embedding_dimension=provider.dimension,
                index_version=revision.index_version,
            )
        )
    revision.lock_version += 1
    if legacy_auto_publish and source.active_revision_id is None:
        revision.status = "published"
        revision.published_at = datetime.now(UTC)
        source.active_revision_id = revision.id
        project.knowledge_source_id = source.id
        await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            project_id=project.id,
            event_type="knowledge.revision_published",
            aggregate_type="knowledge_revision",
            aggregate_id=revision.id,
            aggregate_version=revision.lock_version,
            payload={"knowledge_base_id": source.id, "version": revision.version},
            correlation_id=request.state.correlation_id,
        )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        event_type="knowledge.document_ready",
        aggregate_type="knowledge_document_version",
        aggregate_id=version.id,
        aggregate_version=version.lock_version,
        payload={"status": "ready", "knowledge_base_id": source.id},
        correlation_id=request.state.correlation_id,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        event_type="knowledge.index_updated",
        aggregate_type="knowledge_revision",
        aggregate_id=revision.id,
        aggregate_version=revision.lock_version,
        payload={"status": "ready", "knowledge_base_id": source.id},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.text_created",
        resource_type="knowledge_document_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"language": payload.language, "chunks": len(chunks)},
    )
    await session.flush()
    response = await _document_read(session, document, version)
    _record_knowledge_command(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        resource_id=version.id,
        response=response,
    )
    await session.commit()
    return response


@router.get("/documents/{version_id}/text", response_model=dict[str, object])
async def preview_text(
    version_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> dict[str, object]:
    version = await _visible_version(session, principal, version_id)
    return {
        "id": str(version.id),
        "status": version.status,
        "language": version.language_code,
        "text": version.normalized_text,
        "pages": version.page_count,
        "metadata": version.extraction_metadata,
    }


@router.get("/documents/{version_id}/chunks", response_model=list[KnowledgeChunkRead])
async def preview_chunks(
    version_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> list[KnowledgeChunkRead]:
    version = await _visible_version(session, principal, version_id)
    chunks = list(
        await session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == principal.tenant_id,
                KnowledgeChunk.document_version_id == version.id,
            )
            .order_by(KnowledgeChunk.ordinal)
        )
    )
    return [
        KnowledgeChunkRead(
            id=chunk.id,
            document_version_id=version.id,
            ordinal=chunk.ordinal,
            language=chunk.language_code,
            page=chunk.page_number,
            section=chunk.section,
            text=chunk.normalized_content,
            character_count=chunk.character_count,
            token_count=chunk.token_count,
            embedding_status=chunk.embedding_status,
        )
        for chunk in chunks
    ]


@router.get("/citations/{chunk_id}", response_model=dict[str, object])
async def citation_preview(
    chunk_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> dict[str, object]:
    chunk = await session.scalar(
        select(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == principal.tenant_id,
            KnowledgeChunk.id == chunk_id,
        )
    )
    if chunk is None or chunk.document_version_id is None:
        raise ApiError(404, "knowledge_citation_not_found", "Knowledge citation was not found")
    version = await _visible_version(session, principal, chunk.document_version_id)
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(version.document_id),
        "document_version_id": str(version.id),
        "document_version": version.version,
        "revision_id": str(version.revision_id),
        "title": version.title_snapshot,
        "language": chunk.language_code,
        "page": chunk.page_number,
        "section": chunk.section,
        "text": chunk.normalized_content,
    }


async def _visible_version(
    session: SessionDep, principal: Principal, version_id: UUID, *, for_update: bool = False
) -> KnowledgeDocumentVersion:
    statement = select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
        KnowledgeDocumentVersion.id == version_id,
    )
    if for_update:
        statement = statement.with_for_update()
    version = await session.scalar(statement)
    if version is None:
        raise ApiError(404, "knowledge_document_not_found", "Knowledge document was not found")
    await _resolve_revision(session, principal, version.revision_id)
    return version


@router.post("/documents/{version_id}/retry", response_model=KnowledgeDocumentVersionRead)
async def retry_document(
    version_id: UUID,
    payload: KnowledgeRetryRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> KnowledgeDocumentVersionRead:
    visible_version = await _visible_version(session, principal, version_id)
    operation = "knowledge.document.retry"
    fingerprint = _knowledge_command_fingerprint(
        operation,
        {"version_id": str(version_id), "expected_version": payload.expected_version},
    )
    replay = await _knowledge_command_replay(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        resource_id=visible_version.id,
    )
    if replay is not None:
        return KnowledgeDocumentVersionRead.model_validate(replay.response_payload)
    version = await _visible_version(session, principal, version_id, for_update=True)
    revision = await _resolve_revision(session, principal, version.revision_id, draft_only=True)
    del revision
    if version.status != "failed":
        raise ApiError(409, "knowledge_retry_invalid", "Only a failed document can be retried")
    transition_document(version, "queued", expected_version=payload.expected_version)
    job = await session.scalar(
        select(DocumentIngestionJob)
        .where(
            DocumentIngestionJob.tenant_id == principal.tenant_id,
            DocumentIngestionJob.document_version_id == version.id,
        )
        .with_for_update()
    )
    background_job, _ = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        project_id=version.project_id,
        created_by_user_id=principal.user_id,
        job_type="knowledge.ingest_document",
        queue="knowledge",
        priority=60,
        safe_payload={
            "document_version_id": version.id,
            "knowledge_base_id": version.knowledge_source_id,
            "storage_object_id": version.storage_object_id,
        },
        idempotency_key=f"retry:{idempotency_key}",
        correlation_id=request.state.correlation_id,
    )
    if job is None:
        job = DocumentIngestionJob(
            tenant_id=principal.tenant_id,
            project_id=version.project_id,
            document_version_id=version.id,
            idempotency_key=idempotency_key,
            background_job_id=background_job.id,
        )
        session.add(job)
    else:
        job.status = "pending"
        job.stage = "queued"
        job.next_attempt_at = datetime.now(UTC)
        job.lease_token = None
        job.lease_expires_at = None
        job.safe_error_code = None
        job.background_job_id = background_job.id
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=version.project_id,
        event_type="knowledge.document_processing",
        aggregate_type="knowledge_document_version",
        aggregate_id=version.id,
        aggregate_version=version.lock_version,
        payload={"status": "queued"},
        correlation_id=request.state.correlation_id,
    )
    await session.flush()
    response = await _version_read(session, version)
    _record_knowledge_command(
        session,
        tenant_id=principal.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        resource_id=version.id,
        response=response,
    )
    await session.commit()
    return response


async def _activate_version_storage(
    session: SessionDep,
    version: KnowledgeDocumentVersion,
    storage: StorageObject,
    *,
    require_available: bool,
) -> None:
    if storage.status == "purged" or storage.retention_state == "purged":
        if require_available:
            raise ApiError(
                409,
                "knowledge_restore_original_purged",
                "The archived original has already been purged",
            )
        return
    if storage.status == "missing":
        if require_available:
            raise ApiError(
                409,
                "knowledge_restore_original_missing",
                "The archived original is missing from private storage",
            )
        return

    changed = False
    if storage.status != "active":
        storage.status = "active"
        changed = True
    if storage.archived_at is not None:
        storage.archived_at = None
        changed = True
    if storage.retention_state != "retained":
        storage.retention_state = "retained"
        changed = True
    if storage.pending_purge_at is not None:
        storage.pending_purge_at = None
        changed = True
    if changed:
        storage.lock_version += 1

    candidates = list(
        await session.scalars(
            select(RetentionCandidate)
            .where(
                RetentionCandidate.tenant_id == version.tenant_id,
                RetentionCandidate.storage_object_id == storage.id,
                RetentionCandidate.status.in_(("eligible", "pending_purge")),
            )
            .with_for_update(of=RetentionCandidate)
        )
    )
    for candidate in candidates:
        candidate.status = "blocked"
        candidate.pending_purge_at = None
        candidate.grace_until = None
        candidate.safe_metadata = {
            **candidate.safe_metadata,
            "blocked_reason": "knowledge_reference_active",
            "document_version_id": str(version.id),
        }
        candidate.lock_version += 1


async def _live_storage_reference(
    session: SessionDep,
    *,
    tenant_id: UUID,
    storage_object_id: UUID,
    excluded_version_id: UUID | None = None,
    published_revision_id: UUID | None = None,
) -> KnowledgeDocumentVersion | None:
    active_revision = (
        KnowledgeBaseRevision.id == published_revision_id
        if published_revision_id is not None
        else KnowledgeSource.active_revision_id == KnowledgeBaseRevision.id
    )
    statement = (
        select(KnowledgeDocumentVersion)
        .join(
            KnowledgeBaseRevision,
            and_(
                KnowledgeBaseRevision.tenant_id == KnowledgeDocumentVersion.tenant_id,
                KnowledgeBaseRevision.id == KnowledgeDocumentVersion.revision_id,
            ),
        )
        .join(
            KnowledgeSource,
            and_(
                KnowledgeSource.tenant_id == KnowledgeBaseRevision.tenant_id,
                KnowledgeSource.id == KnowledgeBaseRevision.knowledge_source_id,
            ),
        )
        .where(
            KnowledgeDocumentVersion.tenant_id == tenant_id,
            KnowledgeDocumentVersion.storage_object_id == storage_object_id,
            KnowledgeDocumentVersion.status != "archived",
            or_(KnowledgeBaseRevision.status == "draft", active_revision),
        )
        .order_by(
            (KnowledgeBaseRevision.id == published_revision_id).desc()
            if published_revision_id is not None
            else KnowledgeBaseRevision.status.desc(),
            KnowledgeDocumentVersion.created_at.desc(),
        )
        .limit(1)
    )
    if excluded_version_id is not None:
        statement = statement.where(KnowledgeDocumentVersion.id != excluded_version_id)
    reference: KnowledgeDocumentVersion | None = await session.scalar(statement)
    return reference


def _archive_version_storage(storage: StorageObject, *, occurred_at: datetime) -> None:
    changed = False
    if storage.retention_state != "pending_purge" and storage.status not in {
        "archived",
        "missing",
        "purged",
    }:
        storage.status = "archived"
        changed = True
    if storage.archived_at is None:
        storage.archived_at = occurred_at
        changed = True
    if changed:
        storage.lock_version += 1


async def _recompute_source_storage_lifecycle(
    session: SessionDep,
    *,
    source: KnowledgeSource,
    published_revision_id: UUID,
    occurred_at: datetime,
) -> None:
    storage_ids = list(
        await session.scalars(
            select(KnowledgeDocumentVersion.storage_object_id)
            .where(
                KnowledgeDocumentVersion.tenant_id == source.tenant_id,
                KnowledgeDocumentVersion.knowledge_source_id == source.id,
                KnowledgeDocumentVersion.storage_object_id.is_not(None),
            )
            .distinct()
        )
    )
    for storage_id in storage_ids:
        if storage_id is None:
            continue
        storage = await session.scalar(
            select(StorageObject)
            .where(
                StorageObject.tenant_id == source.tenant_id,
                StorageObject.id == storage_id,
            )
            .with_for_update(of=StorageObject)
        )
        if storage is None:
            continue
        live_reference = await _live_storage_reference(
            session,
            tenant_id=source.tenant_id,
            storage_object_id=storage.id,
            published_revision_id=published_revision_id,
        )
        if live_reference is None:
            _archive_version_storage(storage, occurred_at=occurred_at)
        else:
            await _activate_version_storage(
                session,
                live_reference,
                storage,
                require_available=False,
            )


async def _sync_version_storage_lifecycle(
    session: SessionDep,
    version: KnowledgeDocumentVersion,
    *,
    restoring: bool,
    occurred_at: datetime,
) -> None:
    if version.storage_object_id is None:
        return
    storage = await session.scalar(
        select(StorageObject)
        .where(
            StorageObject.tenant_id == version.tenant_id,
            StorageObject.id == version.storage_object_id,
        )
        .with_for_update(of=StorageObject)
    )
    if storage is None:
        return
    if restoring:
        await _activate_version_storage(session, version, storage, require_available=True)
        return

    live_reference = await _live_storage_reference(
        session,
        tenant_id=version.tenant_id,
        storage_object_id=storage.id,
        excluded_version_id=version.id,
    )
    if live_reference is not None:
        await _activate_version_storage(
            session,
            live_reference,
            storage,
            require_available=False,
        )
        return

    _archive_version_storage(storage, occurred_at=occurred_at)


@router.post("/documents/{version_id}/archive", response_model=KnowledgeDocumentVersionRead)
async def archive_document(
    version_id: UUID,
    payload: KnowledgeRetryRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeDocumentVersionRead:
    await acquire_retention_lock(session, principal.tenant_id)
    version = await _visible_version(session, principal, version_id, for_update=True)
    await _resolve_revision(session, principal, version.revision_id, draft_only=True)
    now = datetime.now(UTC)
    transition_document(
        version,
        "archived",
        expected_version=payload.expected_version,
        occurred_at=now,
    )
    await _sync_version_storage_lifecycle(
        session,
        version,
        restoring=False,
        occurred_at=now,
    )
    document = await session.get(KnowledgeDocument, version.document_id)
    if document:
        published_copy = await session.scalar(
            select(KnowledgeDocumentVersion.id)
            .join(
                KnowledgeBaseRevision,
                KnowledgeBaseRevision.id == KnowledgeDocumentVersion.revision_id,
            )
            .where(
                KnowledgeDocumentVersion.tenant_id == principal.tenant_id,
                KnowledgeDocumentVersion.document_id == document.id,
                KnowledgeDocumentVersion.status == "ready",
                KnowledgeBaseRevision.status == "published",
            )
            .limit(1)
        )
        if published_copy is None:
            document.archived_at = now
            document.is_active = False
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.document_archived",
        resource_type="knowledge_document_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
    )
    await session.flush()
    response = await _version_read(session, version)
    await session.commit()
    return response


@router.post("/documents/{version_id}/restore", response_model=KnowledgeDocumentVersionRead)
async def restore_document(
    version_id: UUID,
    payload: KnowledgeRetryRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeDocumentVersionRead:
    await acquire_retention_lock(session, principal.tenant_id)
    version = await _visible_version(session, principal, version_id, for_update=True)
    await _resolve_revision(session, principal, version.revision_id, draft_only=True)
    if version.status != "archived":
        raise ApiError(409, "knowledge_restore_invalid", "Only an archived document can be restored")
    chunk_count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.tenant_id == principal.tenant_id,
                KnowledgeChunk.document_version_id == version.id,
            )
        )
        or 0
    )
    target = "ready" if chunk_count and version.normalized_text else "failed"
    transition_document(
        version,
        target,
        expected_version=payload.expected_version,
        safe_error_code="restore_requires_retry" if target == "failed" else None,
        safe_error_message=(
            "Restored document requires a new processing retry" if target == "failed" else None
        ),
    )
    await _sync_version_storage_lifecycle(
        session,
        version,
        restoring=True,
        occurred_at=datetime.now(UTC),
    )
    document = await session.get(KnowledgeDocument, version.document_id)
    if document:
        document.archived_at = None
        document.is_active = True
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=version.project_id,
        event_type=("knowledge.document_ready" if target == "ready" else "knowledge.document_failed"),
        aggregate_type="knowledge_document_version",
        aggregate_id=version.id,
        aggregate_version=version.lock_version,
        payload={"status": target},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.document_restored",
        resource_type="knowledge_document_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"status": target},
    )
    await session.flush()
    response = await _version_read(session, version)
    await session.commit()
    return response


@router.get("/documents/{version_id}/download", response_model=KnowledgeDownloadRead)
async def download_document(
    version_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeDownloadRead:
    version = await _visible_version(session, principal, version_id)
    if not version.object_key:
        raise ApiError(404, "knowledge_original_missing", "This text entry has no original file")
    try:
        url = await KnowledgeObjectStorage(get_settings()).presigned_download(
            key=version.object_key, filename=version.original_filename
        )
    except KnowledgeStorageUnavailable as exc:
        raise ApiError(503, "knowledge_storage_unavailable", str(exc)) from exc
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="knowledge.document_downloaded",
        resource_type="knowledge_document_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return KnowledgeDownloadRead(url=url)


@router.post("/retrieval/test", response_model=KnowledgeRetrievalRead)
async def retrieval_test(
    payload: KnowledgeRetrievalRequest,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> KnowledgeRetrievalRead:
    project = await resolve_project(session, principal, payload.project_id, active_only=False)
    revision_id = payload.revision_id
    if payload.knowledge_base_id is not None:
        source = await _resolve_base(session, principal, payload.knowledge_base_id)
        if source.project_id != project.id:
            raise ApiError(404, "knowledge_base_not_found", "Knowledge base was not found")
        revision_id = revision_id or source.active_revision_id
        if revision_id is not None:
            revision = await _resolve_revision(session, principal, revision_id)
            if revision.knowledge_source_id != source.id or revision.status != "published":
                raise ApiError(404, "knowledge_revision_not_found", "Knowledge revision was not found")
    result = await hybrid_retrieve(
        session,
        settings=get_settings(),
        tenant_id=principal.tenant_id,
        project_id=project.id,
        query=payload.query,
        language=payload.language,
        revision_id=revision_id,
        top_k=payload.top_k,
        threshold=payload.threshold,
        idempotency_key=idempotency_key,
        allowed_document_ids=set(payload.document_ids) if payload.document_ids else None,
    )
    if result is None:
        raise ApiError(409, "knowledge_revision_unpublished", "Project has no published knowledge revision")
    await session.commit()
    return KnowledgeRetrievalRead(
        revision_id=result.revision_id,
        hits=[KnowledgeRetrievalHitRead(**hit.__dict__) for hit in result.hits],
        no_match=result.no_match,
        provider=result.provider,
        model=result.model,
        provider_status=result.provider_status,
        index_version=result.index_version,
        notice=(
            "Deterministic retrieval mode — no generative LLM was used"
            if result.provider_status == "development"
            else "Retrieved document evidence only; document text is untrusted data"
        ),
        usage=result.usage,
    )


@router.get("/retrieval/history", response_model=Page[dict[str, object]])
async def retrieval_history(
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
    revision_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[dict[str, object]]:
    filters = [
        KnowledgeRetrievalExecution.tenant_id == principal.tenant_id,
        KnowledgeRetrievalExecution.project_id.in_(await accessible_project_ids(session, principal)),
    ]
    if revision_id:
        await _resolve_revision(session, principal, revision_id)
        filters.append(KnowledgeRetrievalExecution.revision_id == revision_id)
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    total = int(
        await session.scalar(select(func.count()).select_from(KnowledgeRetrievalExecution).where(*filters))
        or 0
    )
    items = list(
        await session.scalars(
            select(KnowledgeRetrievalExecution)
            .where(*filters)
            .order_by(KnowledgeRetrievalExecution.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[
            {
                "id": item.id,
                "revision_id": item.revision_id,
                "language": item.language_code,
                "query": item.masked_query,
                "no_match": item.no_match,
                "citations": item.citations,
                "latency_ms": item.latency_ms,
                "created_at": item.created_at,
            }
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/revisions/{revision_id}/index", response_model=KnowledgeIndexStatusRead)
async def index_status(
    revision_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
) -> KnowledgeIndexStatusRead:
    revision = await _resolve_revision(session, principal, revision_id)
    count_rows = (
        await session.execute(
            select(KnowledgeChunk.embedding_status, func.count())
            .where(
                KnowledgeChunk.tenant_id == principal.tenant_id,
                KnowledgeChunk.revision_id == revision.id,
            )
            .group_by(KnowledgeChunk.embedding_status)
        )
    ).all()
    counts: dict[str, int] = {key: int(value) for key, value in count_rows}
    status = _embedding_status(revision)
    return KnowledgeIndexStatusRead(
        revision_id=revision.id,
        provider=revision.embedding_provider,
        model=revision.embedding_model,
        dimension=revision.embedding_dimension,
        index_version=revision.index_version,
        provider_status=status,
        ready_chunks=int(counts.get("ready", 0)),
        pending_chunks=sum(value for key, value in counts.items() if key != "ready"),
        notice=(
            "Embedding provider unavailable — live verification required"
            if status == "unavailable"
            else "Mock embeddings are deterministic development data, not production semantic quality"
        ),
    )


# Compatibility route retained for the old frontend and integrations.
@router.get("", response_model=Page[KnowledgeDocumentRead])
async def legacy_list_documents(
    session: SessionDep,
    principal: Principal = require_permission("knowledge:read"),
    limit: int = 50,
    offset: int = 0,
) -> Page[KnowledgeDocumentRead]:
    return await list_documents(session, principal, limit=limit, offset=offset)
