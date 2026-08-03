from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from uuid import uuid4

import pytest

from teamora_worker.config import WorkerSettings
from teamora_worker.realtime_publisher import RealtimePublisher


class Transaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.tenant_id = uuid4()
        self.event_id = uuid4()
        self.executed: list[str] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if query.startswith("SELECT id FROM tenants"):
            return [{"id": self.tenant_id}]
        return [
            {
                "id": self.event_id,
                "cursor": 17,
                "tenant_id": self.tenant_id,
            }
        ]

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append(" ".join(query.split()))
        return "UPDATE 1"

    def transaction(self) -> Transaction:
        return Transaction()


class CleanupConnection(FakeConnection):
    async def execute(self, query: str, *args: object) -> str:
        self.executed.append(" ".join(query.split()))
        return "DELETE 2" if "DELETE FROM realtime_events" in query else "SELECT 1"


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[str] = []

    async def publish(self, channel: str, message: str) -> int:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.messages.append(message)
        return 1


@pytest.mark.asyncio
async def test_publisher_marks_event_after_redis_notification() -> None:
    connection = FakeConnection()
    redis = FakeRedis()
    publisher = RealtimePublisher(WorkerSettings(), redis)  # type: ignore[arg-type]

    assert await publisher.publish_once(connection) == 1  # type: ignore[arg-type]
    assert len(redis.messages) == 1
    assert any("publish_status = 'published'" in query for query in connection.executed)


@pytest.mark.asyncio
async def test_redis_failure_keeps_event_retryable_and_records_attempt() -> None:
    connection = FakeConnection()
    publisher = RealtimePublisher(WorkerSettings(), FakeRedis(fail=True))  # type: ignore[arg-type]

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await publisher.publish_once(connection)  # type: ignore[arg-type]
    assert any("publish_attempts = publish_attempts + 1" in query for query in connection.executed)
    assert not any("publish_status = 'published'" in query for query in connection.executed)


@pytest.mark.asyncio
async def test_retention_cleanup_deletes_only_expired_published_events() -> None:
    connection = CleanupConnection()
    publisher = RealtimePublisher(WorkerSettings(), FakeRedis())  # type: ignore[arg-type]

    assert await publisher.cleanup_expired(connection) == 2  # type: ignore[arg-type]
    cleanup = next(query for query in connection.executed if "DELETE FROM realtime_events" in query)
    assert "expires_at <= now()" in cleanup
    assert "publish_status = 'published'" in cleanup
