from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from teamora_worker.background_jobs import BackgroundJobClaim, JobExecutionContext
from teamora_worker.config import WorkerSettings
from teamora_worker.storage_maintenance import (
    StorageMaintenance,
    StoredObject,
    _database_resource_held,
    _old_enough,
    storage_issue_fingerprint,
    tenant_prefixes,
)


def settings(**values: object) -> WorkerSettings:
    return WorkerSettings(_env_file=None, **values)  # type: ignore[call-arg]


def scan_job(tenant_id: UUID) -> BackgroundJobClaim:
    return BackgroundJobClaim(
        id=uuid4(),
        tenant_id=tenant_id,
        project_id=None,
        job_type="storage.orphan_scan",
        queue="maintenance",
        safe_payload={},
        attempt_count=1,
        max_attempts=4,
        lease_owner="worker-test",
        lease_token=uuid4(),
        correlation_id="storage-scan-test",
        causation_id=None,
    )


class AsyncResource:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class ScanContext:
    def __init__(self, pool: object = object()) -> None:
        self.pool = pool
        self.progress: list[tuple[int, dict[str, object]]] = []

    def ensure_active(self) -> None:
        return None

    async def update_progress(
        self,
        progress: int,
        *,
        detail: dict[str, object] | None = None,
    ) -> None:
        self.progress.append((progress, detail or {}))


def test_tenant_prefixes_cover_canonical_and_historical_namespaces_only() -> None:
    tenant_id = uuid4()
    assert tenant_prefixes(tenant_id) == (
        f"tenants/{tenant_id}/",
        f"knowledge/{tenant_id}/",
    )
    assert str(uuid4()) not in "".join(tenant_prefixes(tenant_id))


async def test_minio_scan_streams_every_tenant_prefix_without_a_global_limit() -> None:
    tenant_id = uuid4()
    prefixes = tenant_prefixes(tenant_id)

    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_objects(
            self,
            _bucket: str,
            *,
            prefix: str,
            recursive: bool,
        ) -> object:
            assert recursive is True
            self.calls.append(prefix)
            count = 125 if prefix == prefixes[0] else 3
            return iter(
                SimpleNamespace(object_name=f"{prefix}{index}", size=index)
                for index in range(count)
            )

    client = Client()
    maintenance = object.__new__(StorageMaintenance)
    maintenance.settings = settings(storage_scan_max_objects=100)
    maintenance.client = client  # type: ignore[assignment]

    pages = [page async for page in maintenance._list_tenant_objects(tenant_id)]

    assert [len(page) for page in pages] == [100, 25, 3]
    assert sum(map(len, pages)) == 128
    assert client.calls == list(prefixes)


async def test_registered_scan_uses_last_verified_keyset_pages() -> None:
    tenant_id = uuid4()
    rows = [
        {
            "id": UUID(int=index + 1),
            "project_id": None,
            "bucket": "teamora-private",
            "object_key": f"tenants/{tenant_id}/objects/{index}",
            "checksum_sha256": None,
            "size_bytes": index,
            "status": "active",
            "last_verified_at": None,
        }
        for index in range(501)
    ]

    class Connection:
        def __init__(self) -> None:
            self.pages = [rows[:500], rows[500:]]
            self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

        def transaction(self) -> AsyncResource:
            return AsyncResource(self)

        async def execute(self, _query: str, *_args: object) -> None:
            return None

        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            self.fetch_calls.append((query, args))
            return self.pages.pop(0)

    class Pool:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        def acquire(self) -> AsyncResource:
            return AsyncResource(self.connection)

    connection = Connection()
    context = cast(JobExecutionContext, ScanContext(Pool(connection)))
    maintenance = object.__new__(StorageMaintenance)
    maintenance.settings = settings(storage_scan_max_objects=600)

    objects = await maintenance._registered_objects(context, tenant_id)

    assert len(objects) == 501
    assert len(connection.fetch_calls) == 2
    first_query, first_args = connection.fetch_calls[0]
    _second_query, second_args = connection.fetch_calls[1]
    assert "ORDER BY last_verified_at ASC NULLS FIRST, id" in first_query
    assert "OFFSET" not in first_query
    assert first_args[1:3] == (None, None)
    assert second_args[1] == rows[499]["id"]
    assert second_args[2] is None


async def test_consistency_scan_looks_up_listed_keys_outside_registered_window() -> None:
    tenant_id = uuid4()
    registered_key = f"tenants/{tenant_id}/objects/outside-window"
    orphan_key = f"tenants/{tenant_id}/objects/unregistered"

    class Maintenance(StorageMaintenance):
        def __init__(self) -> None:
            self.settings = settings()
            self.orphans: list[str] = []
            self.lookups: list[list[str]] = []

        async def _registered_objects(
            self,
            context: JobExecutionContext,
            tenant_id: UUID,
        ) -> list[StoredObject]:
            del context, tenant_id
            return []

        async def _list_tenant_objects(
            self,
            tenant_id: UUID,
        ) -> AsyncIterator[list[tuple[str, str, int]]]:
            del tenant_id
            yield [
                ("teamora-private", registered_key, 10),
                ("teamora-private", orphan_key, 20),
            ]

        async def _lookup_registered_object_keys(
            self,
            context: JobExecutionContext,
            tenant_id: UUID,
            *,
            bucket: str,
            object_keys: list[str],
        ) -> set[str]:
            del context, tenant_id, bucket
            self.lookups.append(object_keys)
            return {registered_key}

        async def _record_orphan(
            self,
            context: JobExecutionContext,
            job: BackgroundJobClaim,
            bucket: str,
            object_key: str,
            size: int,
        ) -> None:
            del context, job, bucket, size
            self.orphans.append(object_key)

        async def _list_incomplete_tenant_uploads(
            self,
            tenant_id: UUID,
        ) -> list[tuple[str, str]]:
            del tenant_id
            return []

    maintenance = Maintenance()
    context = cast(JobExecutionContext, ScanContext())

    result = await maintenance.consistency_scan(scan_job(tenant_id), context)

    assert maintenance.lookups == [[registered_key, orphan_key]]
    assert maintenance.orphans == [orphan_key]
    assert result.metadata == {
        "checked_objects": 0,
        "listed_objects": 2,
        "issues_detected": 1,
        "issues_resolved": 0,
    }


async def test_retention_recheck_locks_job_and_rejects_concurrent_retry_state() -> None:
    tenant_id = uuid4()
    resource_id = uuid4()
    candidate_id = uuid4()

    class Connection:
        def __init__(self) -> None:
            self.fetchval_queries: list[str] = []

        async def fetchrow(self, _query: str, *_args: object) -> dict[str, object]:
            return {
                "object_id": None,
                "category": "failed_job",
                "resource_id": resource_id,
            }

        async def fetchval(self, query: str, *_args: object) -> object:
            self.fetchval_queries.append(query)
            return "pending"

    connection = Connection()
    maintenance = object.__new__(StorageMaintenance)
    row = await maintenance._load_candidate_for_recheck(  # type: ignore[arg-type]
        connection,  # type: ignore[arg-type]
        scan_job(tenant_id),
        candidate_id,
        expected_status="pending_purge",
    )

    assert row is None
    assert len(connection.fetchval_queries) == 1
    assert "SELECT status FROM background_jobs" in connection.fetchval_queries[0]
    assert "FOR UPDATE" in connection.fetchval_queries[0]


async def test_realtime_retention_hold_follows_aggregate_scope() -> None:
    tenant_id = uuid4()
    event_id = uuid4()

    class Connection:
        def __init__(self) -> None:
            self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetchval(self, query: str, *args: object) -> object:
            self.fetchval_calls.append((query, args))
            return True

    connection = Connection()
    held = await _database_resource_held(  # type: ignore[arg-type]
        connection,  # type: ignore[arg-type]
        tenant_id,
        {"resource_type": "realtime_event", "resource_id": event_id},  # type: ignore[arg-type]
    )

    assert held is True
    assert len(connection.fetchval_calls) == 1
    query, args = connection.fetchval_calls[0]
    assert args == (tenant_id, event_id)
    assert "FROM realtime_events AS event" in query
    assert "event.aggregate_type IN ('call','customer','document')" in query
    assert "hold.scope_type=event.aggregate_type" in query
    assert "FROM calls AS call" in query
    assert "call.customer_id=hold.scope_id" in query
    assert "event.aggregate_type='knowledge_document_version'" in query
    assert "version.document_id=hold.scope_id" in query


async def test_job_retention_requires_terminal_update_before_candidate_is_purged() -> None:
    tenant_id = uuid4()
    resource_id = uuid4()
    candidate_id = uuid4()

    class Connection:
        def __init__(self) -> None:
            self.fetchval_queries: list[str] = []
            self.execute_queries: list[str] = []

        async def fetchval(self, query: str, *_args: object) -> object:
            self.fetchval_queries.append(query)
            return None

        async def execute(self, query: str, *_args: object) -> None:
            self.execute_queries.append(query)

    connection = Connection()
    maintenance = object.__new__(StorageMaintenance)
    purged = await maintenance._purge_database_resource(  # type: ignore[arg-type]
        connection,  # type: ignore[arg-type]
        scan_job(tenant_id),
        candidate_id,
        "failed_job",
        resource_id,
    )

    assert purged is False
    assert len(connection.fetchval_queries) == 1
    update = connection.fetchval_queries[0]
    assert "RETURNING id" in update
    assert "retention_payload_purged" in update
    assert connection.execute_queries == []


async def test_job_retention_marks_payload_purged_before_deleting_history() -> None:
    tenant_id = uuid4()
    resource_id = uuid4()
    candidate_id = uuid4()

    class Connection:
        def __init__(self) -> None:
            self.fetchval_queries: list[str] = []
            self.execute_queries: list[str] = []

        async def fetchval(self, query: str, *_args: object) -> object:
            self.fetchval_queries.append(query)
            if "UPDATE background_jobs" in query:
                return resource_id
            if "UPDATE retention_candidates" in query:
                return candidate_id
            raise AssertionError(query)

        async def execute(self, query: str, *_args: object) -> None:
            self.execute_queries.append(query)

    connection = Connection()
    maintenance = object.__new__(StorageMaintenance)
    purged = await maintenance._purge_database_resource(  # type: ignore[arg-type]
        connection,  # type: ignore[arg-type]
        scan_job(tenant_id),
        candidate_id,
        "failed_job",
        resource_id,
    )

    assert purged is True
    assert "retention_payload_purged" in connection.fetchval_queries[0]
    assert "UPDATE retention_candidates" in connection.fetchval_queries[1]
    assert len(connection.execute_queries) == 2
    assert "DELETE FROM background_job_attempts" in connection.execute_queries[0]
    assert "DELETE FROM background_job_events" in connection.execute_queries[1]


def test_storage_issue_fingerprint_is_stable_and_type_specific() -> None:
    first = storage_issue_fingerprint("private", "tenant/object", "missing_object")
    assert first == storage_issue_fingerprint("private", "tenant/object", "missing_object")
    assert first != storage_issue_fingerprint("private", "tenant/object", "orphan_object")


def test_retention_age_honours_explicit_expiry_and_age() -> None:
    now = datetime.now(UTC)
    assert not _old_enough(
        {
            "expires_at": now - timedelta(days=1),
            "archived_at": None,
            "created_at": now,
        },
        30,  # type: ignore[arg-type]
    )
    assert not _old_enough(
        {
            "expires_at": now + timedelta(days=1),
            "archived_at": None,
            "created_at": now - timedelta(days=31),
        },
        30,  # type: ignore[arg-type]
    )
    assert _old_enough(
        {
            "expires_at": now - timedelta(days=1),
            "archived_at": None,
            "created_at": now - timedelta(days=31),
        },
        30,  # type: ignore[arg-type]
    )
