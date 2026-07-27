from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.enums import LanguageCode
from teamora_api.models import KnowledgeDocument


def terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[\w'-]+", text.lower(), flags=re.UNICODE) if len(term) >= 3}


async def search_knowledge(
    session: AsyncSession, *, tenant_id: UUID, language: LanguageCode, query: str, limit: int = 3
) -> list[KnowledgeDocument]:
    documents = list(
        await session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.language == language,
                KnowledgeDocument.is_active.is_(True),
            )
        )
    )
    query_terms = terms(query)
    ranked = sorted(
        (
            (len(query_terms & terms(f"{document.title} {document.content}")), document)
            for document in documents
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return [document for score, document in ranked if score > 0][:limit]
