from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class JobEnvelope(BaseModel):
    tenant_id: UUID
    job_type: Literal[
        "call_summary",
        "crm_sync",
        "retention_cleanup",
        "usage_finalize",
        "notification",
        "knowledge_ingestion",
    ]
    idempotency_key: str = Field(min_length=8, max_length=160)
    safe_payload: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(default=0, ge=0, le=10)


SAFE_RETRY_JOB_TYPES = {
    "crm_sync",
    "retention_cleanup",
    "usage_finalize",
    "notification",
    "knowledge_ingestion",
}


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float


def retry_decision(job: JobEnvelope, *, transient: bool, max_attempts: int = 4) -> RetryDecision:
    safe = job.job_type in SAFE_RETRY_JOB_TYPES and bool(job.idempotency_key)
    if not transient or not safe or job.attempt >= max_attempts:
        return RetryDecision(False, 0)
    return RetryDecision(True, min(30.0, float(2**job.attempt)))


JobHandler = Callable[[JobEnvelope], Awaitable[None]]


async def execute_job(
    job: JobEnvelope, handler: JobHandler, *, timeout_seconds: float = 30
) -> None:
    async with asyncio.timeout(timeout_seconds):
        await handler(job)
