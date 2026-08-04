from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)


class EmbeddingProviderUnavailable(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimension: int
    status: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    def usage(self, texts: list[str]) -> dict[str, int]: ...


def deterministic_embedding(text: str, dimension: int = 64) -> list[float]:
    """Stable lexical feature vector for development/tests, never a production semantic claim."""
    vector = [0.0] * dimension
    tokens = [token for token in TOKEN_PATTERN.findall(text.casefold()) if token]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        slot = int.from_bytes(digest[:8], "big") % dimension
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[slot] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


@dataclass
class DeterministicEmbeddingProvider:
    model: str = "kline-deterministic-v1"
    dimension: int = 64
    name: str = "mock"
    status: str = "development"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(text, self.dimension) for text in texts]

    def usage(self, texts: list[str]) -> dict[str, int]:
        return {
            "input_items": len(texts),
            "input_characters": sum(len(text) for text in texts),
            "estimated_tokens": sum(max(1, len(TOKEN_PATTERN.findall(text))) for text in texts),
        }


@dataclass
class UnavailableEmbeddingProvider:
    name: str
    model: str
    dimension: int
    status: str = "unavailable"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise EmbeddingProviderUnavailable("Embedding provider unavailable — live verification required")

    def usage(self, texts: list[str]) -> dict[str, int]:
        return {
            "input_items": len(texts),
            "input_characters": sum(len(text) for text in texts),
            "estimated_tokens": 0,
        }


def embedding_provider(*, app_env: str, name: str, model: str, dimension: int) -> EmbeddingProvider:
    if name == "mock" and app_env in {"development", "test"}:
        return DeterministicEmbeddingProvider(model=model, dimension=dimension)
    return UnavailableEmbeddingProvider(name=name, model=model, dimension=dimension)
