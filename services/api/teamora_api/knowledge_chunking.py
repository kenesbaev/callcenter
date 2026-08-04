from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    page: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    page: int | None
    section: str | None
    checksum: str
    character_count: int
    token_count: int


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(lines).strip()


def chunk_blocks(
    blocks: list[ExtractedBlock], *, size_chars: int = 1800, overlap_chars: int = 220
) -> list[ChunkDraft]:
    if size_chars < 200 or overlap_chars < 0 or overlap_chars >= size_chars:
        raise ValueError("Invalid chunking configuration")
    chunks: list[ChunkDraft] = []
    for block in blocks:
        normalized = normalize_text(block.text)
        if not normalized:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= size_chars:
                buffer = candidate
                continue
            if buffer:
                _append(chunks, buffer, block.page, block.section)
                prefix = buffer[-overlap_chars:] if overlap_chars else ""
                candidate = f"{prefix}\n{paragraph}".strip()
            while len(candidate) > size_chars:
                split = _sentence_boundary(candidate, size_chars)
                piece = candidate[:split].strip()
                _append(chunks, piece, block.page, block.section)
                prefix = piece[-overlap_chars:] if overlap_chars else ""
                candidate = f"{prefix}{candidate[split:]}".strip()
            buffer = candidate
        if buffer:
            _append(chunks, buffer, block.page, block.section)
    return chunks


def _sentence_boundary(text: str, maximum: int) -> int:
    floor = max(maximum // 2, 1)
    candidates = [text.rfind(mark, floor, maximum) for mark in (". ", "! ", "? ", "\n", " ")]
    boundary = max(candidates)
    return boundary + 1 if boundary >= floor else maximum


def _append(chunks: list[ChunkDraft], text: str, page: int | None, section: str | None) -> None:
    text = text.strip()
    if not text:
        return
    chunks.append(
        ChunkDraft(
            text=text,
            page=page,
            section=section,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            character_count=len(text),
            token_count=max(1, len(re.findall(r"\S+", text))),
        )
    )
