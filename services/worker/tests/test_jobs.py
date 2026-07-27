from uuid import uuid4

import pytest

from teamora_worker.jobs import JobEnvelope, execute_job, retry_decision


def job(job_type: str, attempt: int = 0) -> JobEnvelope:
    return JobEnvelope(
        tenant_id=uuid4(), job_type=job_type, idempotency_key="job-unique-123", attempt=attempt
    )  # type: ignore[arg-type]


def test_retries_only_safe_idempotent_jobs() -> None:
    assert retry_decision(job("crm_sync"), transient=True).retry is True
    assert retry_decision(job("call_summary"), transient=True).retry is False
    assert retry_decision(job("crm_sync"), transient=False).retry is False
    assert retry_decision(job("crm_sync", attempt=4), transient=True).retry is False


@pytest.mark.asyncio
async def test_job_timeout_is_enforced() -> None:
    async def blocked(_: JobEnvelope) -> None:
        import asyncio

        await asyncio.sleep(2)

    with pytest.raises(TimeoutError):
        await execute_job(job("usage_finalize"), blocked, timeout_seconds=0.01)
