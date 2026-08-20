from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import asyncpg
import httpx
import pytest

from teamora_worker.background_jobs import (
    BackgroundJobClaim,
    BackgroundJobProcessor,
    JobCancelled,
    JobExecutionContext,
    JobExecutionError,
    _safe_scalars,
    retry_delay_seconds,
)
from teamora_worker.config import WorkerSettings
from teamora_worker.maintenance import MaintenanceHandlers
from teamora_worker.scheduler import MAINTENANCE_SCHEDULES, BackgroundJobScheduler


def settings(**values: object) -> WorkerSettings:
    return WorkerSettings(_env_file=None, **values)  # type: ignore[call-arg]


def claim(*, attempt: int = 1, maximum: int = 4) -> BackgroundJobClaim:
    return BackgroundJobClaim(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        job_type="test.job",
        queue="default",
        safe_payload={"resource_id": str(uuid4())},
        attempt_count=attempt,
        max_attempts=maximum,
        lease_owner="worker-test",
        lease_token=uuid4(),
        correlation_id="test-correlation",
        causation_id=None,
    )


@pytest.mark.asyncio
async def test_transfer_timeout_handler_uses_service_auth_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, path: str, **kwargs: object) -> httpx.Response:
            captured["path"] = path
            captured["request"] = kwargs
            return httpx.Response(200, json={"processed": True, "status": "offered"})

    monkeypatch.setattr("teamora_worker.maintenance.httpx.AsyncClient", FakeClient)
    transfer_id = uuid4()
    transfer_job = BackgroundJobClaim(
        **{
            **claim().__dict__,
            "job_type": "transfer.offer_timeout",
            "safe_payload": {"transfer_request_id": str(transfer_id)},
        }
    )
    configured = settings(
        api_internal_url="http://api:8000",
        gateway_service_token="test-internal-token",  # noqa: S106 - isolated service test
    )
    context = JobExecutionContext(cast(asyncpg.Pool, object()), configured, transfer_job)
    handler = object.__new__(MaintenanceHandlers)
    result = await handler.process_transfer_offer_timeout(transfer_job, context)

    assert result.metadata == {"processed": True, "status": "offered"}
    assert captured["path"] == "/internal/v1/transfers/process-expired"
    request = cast(dict[str, object], captured["request"])
    headers = cast(dict[str, str], request["headers"])
    assert headers["Authorization"] == "Bearer test-internal-token"


def test_retry_backoff_is_deterministic_exponential_and_bounded() -> None:
    configured = settings(
        background_job_retry_base_seconds=2,
        background_job_retry_max_seconds=30,
        background_job_retry_jitter_ratio=0.2,
    )
    first = retry_delay_seconds(claim(attempt=1), configured)
    same_job = claim(attempt=1)
    same_job = BackgroundJobClaim(**{**same_job.__dict__, "id": uuid4()})
    assert 1.6 <= first <= 2.4
    assert retry_delay_seconds(claim(attempt=10), configured) <= 30

    stable = claim(attempt=3)
    assert retry_delay_seconds(stable, configured) == retry_delay_seconds(stable, configured)
    assert retry_delay_seconds(same_job, configured) >= 1.6


def test_execution_context_distinguishes_cancel_and_lost_lease() -> None:
    job = claim()
    context = JobExecutionContext(
        cast(asyncpg.Pool, object()),
        settings(),
        job,
    )
    context.cancellation_requested.set()
    with pytest.raises(JobCancelled):
        context.ensure_active()

    context.cancellation_requested.clear()
    context.lease_lost.set()
    with pytest.raises(JobExecutionError) as error:
        context.ensure_active()
    assert error.value.code == "lease_lost"
    assert error.value.retryable is True


def test_job_metadata_allows_only_bounded_scalars() -> None:
    identifier = uuid4()
    assert _safe_scalars({"id": identifier, "status": "ok", "count": 2}) == {
        "id": str(identifier),
        "status": "ok",
        "count": 2,
    }
    assert len(cast(str, _safe_scalars({"message": "x" * 900})["message"])) == 500
    with pytest.raises(ValueError):
        _safe_scalars({"unsafe": {"customer": "row"}})


@pytest.mark.asyncio
async def test_processor_graceful_shutdown_does_not_claim_new_work() -> None:
    stopping = asyncio.Event()
    stopping.set()
    processor = BackgroundJobProcessor(
        settings(background_job_workers=2, background_job_shutdown_timeout_seconds=1),
        cast(asyncpg.Pool, object()),
        {},
    )
    await asyncio.wait_for(processor.run(stopping), timeout=1)


def test_scheduler_has_one_durable_key_per_maintenance_job() -> None:
    keys = [schedule.key for schedule in MAINTENANCE_SCHEDULES]
    assert len(keys) == len(set(keys))
    assert "system.stale_job_recovery" in {schedule.job_type for schedule in MAINTENANCE_SCHEDULES}
    assert "storage.orphan_scan" in {schedule.job_type for schedule in MAINTENANCE_SCHEDULES}


@pytest.mark.asyncio
async def test_scheduler_types_due_timestamp_when_advancing_schedule() -> None:
    tenant_id = uuid4()
    schedule_id = uuid4()
    scheduled_for = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)
    executed_queries: list[str] = []

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_arguments: object) -> None:
            return None

    class Connection:
        def transaction(self) -> Transaction:
            return Transaction()

        async def fetch(self, query: str, *_arguments: object) -> list[dict[str, object]]:
            if "SELECT id FROM tenants" in query:
                return [{"id": tenant_id}]
            if "FROM scheduled_jobs" in query:
                return [
                    {
                        "id": schedule_id,
                        "project_id": None,
                        "schedule_key": "stale-job-recovery",
                        "job_type": "system.stale_job_recovery",
                        "queue": "maintenance",
                        "priority": 100,
                        "safe_payload": {},
                        "interval_seconds": 30,
                        "next_run_at": scheduled_for,
                    }
                ]
            raise AssertionError(f"Unexpected query: {query}")

        async def fetchval(self, query: str, *_arguments: object) -> object:
            assert "INSERT INTO background_jobs" in query
            return uuid4()

        async def execute(self, query: str, *_arguments: object) -> str:
            executed_queries.append(query)
            return "UPDATE 1"

    scheduler = BackgroundJobScheduler(
        settings(),
        cast(asyncpg.Pool, object()),
        cast(object, object()),  # Redis is unused by enqueue_due.
    )
    assert await scheduler.enqueue_due(cast(asyncpg.Connection, Connection())) == 1
    advance_query = next(query for query in executed_queries if "UPDATE scheduled_jobs" in query)
    assert "last_enqueued_at=$3::timestamptz" in advance_query
    assert "$3::timestamptz+" in advance_query


@pytest.mark.asyncio
async def test_exhausted_stale_leases_dead_letter_and_sync_linked_domains() -> None:
    tenant_id = uuid4()
    project_id = uuid4()
    import_id = uuid4()
    document_version_id = uuid4()
    import_job_id = uuid4()
    knowledge_job_id = uuid4()
    statements: list[tuple[str, tuple[object, ...]]] = []
    stale_rows = [
        {
            "id": import_job_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "type": "customer_import.process",
            "queue": "imports",
            "status": "running",
            "safe_payload": {"import_id": str(import_id), "customer_row": "must-not-leak"},
            "attempt_count": 4,
            "max_attempts": 4,
            "lease_owner": "crashed-worker",
            "lease_token": uuid4(),
            "correlation_id": "stale-import",
            "causation_id": None,
        },
        {
            "id": knowledge_job_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "type": "knowledge.ingest_document",
            "queue": "knowledge",
            "status": "running",
            "safe_payload": {"document_version_id": str(document_version_id)},
            "attempt_count": 3,
            "max_attempts": 3,
            "lease_owner": "crashed-worker",
            "lease_token": uuid4(),
            "correlation_id": "stale-knowledge",
            "causation_id": None,
        },
    ]

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_arguments: object) -> None:
            return None

    class Connection:
        def transaction(self) -> Transaction:
            return Transaction()

        async def fetch(self, query: str, *_arguments: object) -> list[dict[str, object]]:
            assert "FROM background_jobs" in query
            return stale_rows

        async def execute(self, query: str, *arguments: object) -> str:
            statements.append((query, arguments))
            return "UPDATE 1"

    connection = Connection()

    class Acquisition:
        async def __aenter__(self) -> Connection:
            return connection

        async def __aexit__(self, *_arguments: object) -> None:
            return None

    class Pool:
        def acquire(self) -> Acquisition:
            return Acquisition()

    scheduler_job = BackgroundJobClaim(
        id=uuid4(),
        tenant_id=tenant_id,
        project_id=None,
        job_type="system.stale_job_recovery",
        queue="system",
        safe_payload={},
        attempt_count=1,
        max_attempts=4,
        lease_owner="scheduler",
        lease_token=uuid4(),
        correlation_id="stale-recovery-test",
        causation_id=None,
    )
    context = JobExecutionContext(cast(asyncpg.Pool, Pool()), settings(), scheduler_job)
    result = await MaintenanceHandlers.recover_stale_jobs(
        cast(MaintenanceHandlers, object()),
        scheduler_job,
        context,
    )
    assert result.metadata == {"recovered_jobs": 2, "dead_lettered_jobs": 2}
    executed_sql = "\n".join(query for query, _arguments in statements)
    assert "UPDATE customer_imports" in executed_sql
    assert "UPDATE document_ingestion_jobs" in executed_sql
    assert "UPDATE knowledge_document_versions" in executed_sql
    assert "status='failed'" in executed_sql
    event_arguments = [
        arguments
        for query, arguments in statements
        if "INSERT INTO background_job_events" in query or "INSERT INTO realtime_events" in query
    ]
    assert len(event_arguments) == 4
    assert all("must-not-leak" not in repr(arguments) for arguments in event_arguments)
    assert all("lease_expired" in repr(arguments) for arguments in event_arguments)
