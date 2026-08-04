from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from teamora_api.errors import ApiError

SUPPORTED_TYPES = {
    ".txt": ("txt", {"text/plain", "application/octet-stream"}),
    ".md": ("md", {"text/markdown", "text/plain", "application/octet-stream"}),
    ".pdf": ("pdf", {"application/pdf", "application/octet-stream"}),
    ".docx": (
        "docx",
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
            "application/zip",
        },
    ),
}


@dataclass(frozen=True)
class ValidatedUpload:
    safe_filename: str
    file_type: str
    content_type: str


def validate_upload(
    filename: str | None,
    content_type: str | None,
    data: bytes,
    *,
    maximum: int,
    max_docx_expanded_bytes: int = 200 * 1024 * 1024,
    max_zip_ratio: int = 100,
) -> ValidatedUpload:
    if not data:
        raise ApiError(422, "knowledge_file_empty", "The uploaded file is empty")
    if len(data) > maximum:
        raise ApiError(413, "knowledge_file_too_large", "The uploaded file exceeds the configured limit")
    safe_name = PurePosixPath((filename or "document").replace("\\", "/")).name[:255]
    suffix = PurePosixPath(safe_name.lower()).suffix
    definition = SUPPORTED_TYPES.get(suffix)
    if definition is None:
        raise ApiError(
            415, "knowledge_file_type_unsupported", "Only TXT, Markdown, PDF and DOCX are supported"
        )
    file_type, allowed_mimes = definition
    normalized_mime = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if normalized_mime not in allowed_mimes:
        raise ApiError(415, "knowledge_mime_mismatch", "File MIME type does not match its extension")
    if file_type == "pdf" and not data.startswith(b"%PDF-"):
        raise ApiError(422, "knowledge_file_signature_invalid", "PDF signature is invalid")
    if file_type == "docx":
        _validate_docx_container(
            data,
            max_expanded_bytes=max_docx_expanded_bytes,
            max_zip_ratio=max_zip_ratio,
        )
    if file_type in {"txt", "md"}:
        if b"\x00" in data:
            raise ApiError(422, "knowledge_text_binary", "Text document contains binary data")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError(422, "knowledge_text_encoding", "Text documents must use UTF-8") from exc
    return ValidatedUpload(safe_name, file_type, normalized_mime)


def _validate_docx_container(data: bytes, *, max_expanded_bytes: int, max_zip_ratio: int) -> None:
    if not data.startswith(b"PK"):
        raise ApiError(422, "knowledge_file_signature_invalid", "DOCX ZIP signature is invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 10_000:
                raise ApiError(422, "knowledge_zip_bomb", "DOCX contains too many entries")
            expanded = sum(entry.file_size for entry in entries)
            compressed = max(1, sum(entry.compress_size for entry in entries))
            if expanded > max_expanded_bytes or expanded / compressed > max_zip_ratio:
                raise ApiError(422, "knowledge_zip_bomb", "DOCX expansion ratio is unsafe")
            names = {entry.filename.replace("\\", "/") for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ApiError(422, "knowledge_docx_structure", "DOCX structure is incomplete")
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ApiError(422, "knowledge_path_traversal", "DOCX contains an unsafe path")
                lowered = name.lower()
                if lowered.endswith(("vbaproject.bin", ".exe", ".dll", ".js")):
                    raise ApiError(
                        422, "knowledge_docx_macro", "Macro-enabled or executable DOCX content is forbidden"
                    )
            for relation in (name for name in names if name.lower().endswith(".rels")):
                content = archive.read(relation)
                if b'TargetMode="External"' in content or b"TargetMode='External'" in content:
                    raise ApiError(
                        422,
                        "knowledge_docx_external_relationship",
                        "External DOCX relationships are forbidden",
                    )
    except zipfile.BadZipFile as exc:
        raise ApiError(422, "knowledge_docx_corrupt", "DOCX archive is corrupted") from exc
