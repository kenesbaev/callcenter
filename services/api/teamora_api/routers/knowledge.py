from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from teamora_api.audit import write_audit
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import LanguageCode
from teamora_api.errors import ApiError
from teamora_api.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from teamora_api.schemas.common import Page
from teamora_api.schemas.knowledge import KnowledgeDocumentRead, KnowledgeTextCreate

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def serialize(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=document.id,
        source_id=document.source_id,
        title=document.title,
        language=document.language,
        content=document.content,
        created_at=document.created_at,
    )


@router.get("", response_model=Page[KnowledgeDocumentRead])
async def list_documents(
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
    limit: int = 50,
    offset: int = 0,
) -> Page[KnowledgeDocumentRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    where = KnowledgeDocument.tenant_id == principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(KnowledgeDocument).where(where)) or 0)
    documents = list(
        await session.scalars(
            select(KnowledgeDocument)
            .where(where)
            .order_by(KnowledgeDocument.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[serialize(document) for document in documents], total=total, limit=limit, offset=offset
    )


@router.post("/text", response_model=KnowledgeDocumentRead, status_code=201)
async def create_text_document(
    payload: KnowledgeTextCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("knowledge:manage"),
) -> KnowledgeDocumentRead:
    if payload.language == LanguageCode.KAA and not get_settings().karakalpak_experimental:
        raise ApiError(
            422, "kaa_feature_disabled", "Karakalpak is experimental and the feature flag is disabled"
        )
    content_hash = hashlib.sha256(payload.content.encode()).hexdigest()
    duplicate = await session.scalar(
        select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == principal.tenant_id,
            KnowledgeDocument.content_hash == content_hash,
        )
    )
    if duplicate:
        raise ApiError(409, "knowledge_duplicate", "The same knowledge content already exists")
    async with session.begin_nested():
        source = KnowledgeSource(
            tenant_id=principal.tenant_id,
            name=f"Text: {payload.title}",
            source_type="text",
        )
        session.add(source)
        await session.flush()
        document = KnowledgeDocument(
            tenant_id=principal.tenant_id,
            source_id=source.id,
            title=payload.title,
            language=payload.language,
            content=payload.content,
            content_hash=content_hash,
            is_active=True,
        )
        session.add(document)
        await session.flush()
        session.add(
            KnowledgeChunk(
                tenant_id=principal.tenant_id,
                document_id=document.id,
                ordinal=0,
                content=payload.content,
                token_count=max(1, len(payload.content.split())),
            )
        )
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="knowledge.text_created",
            resource_type="knowledge_document",
            resource_id=document.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"language": payload.language.value},
        )
    await session.commit()
    return serialize(document)
