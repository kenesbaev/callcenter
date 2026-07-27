from __future__ import annotations

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
