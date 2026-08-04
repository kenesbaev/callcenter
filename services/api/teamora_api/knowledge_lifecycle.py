from __future__ import annotations

from datetime import UTC, datetime

from teamora_api.errors import ApiError
from teamora_api.models import KnowledgeDocumentVersion

ALLOWED_DOCUMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "uploaded": frozenset({"queued", "archived"}),
    "queued": frozenset({"extracting", "failed", "archived"}),
    "extracting": frozenset({"chunking", "needs_ocr", "failed"}),
    "chunking": frozenset({"embedding", "failed"}),
    "embedding": frozenset({"ready", "failed"}),
    "ready": frozenset({"archived"}),
    "failed": frozenset({"queued", "archived"}),
    "needs_ocr": frozenset({"archived"}),
    "archived": frozenset({"ready", "failed"}),
}


def transition_document(
    version: KnowledgeDocumentVersion,
    target: str,
    *,
    expected_version: int | None = None,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    if expected_version is not None and version.lock_version != expected_version:
        raise ApiError(409, "knowledge_version_conflict", "Document was changed by another request")
    if target == version.status:
        return
    if target not in ALLOWED_DOCUMENT_TRANSITIONS.get(version.status, frozenset()):
        raise ApiError(
            409,
            "knowledge_transition_invalid",
            f"Document cannot transition from {version.status} to {target}",
        )
    now = occurred_at or datetime.now(UTC)
    version.status = target
    version.lock_version += 1
    if target == "queued":
        version.queued_at = now
        version.safe_error_code = None
        version.safe_error_message = None
    elif target == "extracting":
        version.extraction_started_at = now
    elif target == "chunking":
        version.chunking_started_at = now
    elif target == "embedding":
        version.embedding_started_at = now
    elif target == "ready":
        version.ready_at = now
    elif target in {"failed", "needs_ocr"}:
        version.failed_at = now
        version.safe_error_code = safe_error_code or target
        version.safe_error_message = (safe_error_message or "Document processing did not complete")[:500]
