from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

from run_api_tests import (
    API_ROOT,
    REPOSITORY_ROOT,
    drop_database,
    recreate_database,
    rendered,
    settings_from_repository,
)
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

OLD_HEAD = "i81a6f2d0b74"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_telephony_migration_test")


def alembic(
    revision: str, environment: dict[str, str], *, action: str = "upgrade"
) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(API_ROOT / "alembic.ini"),
            action,
            revision,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


async def seed_legacy_call(
    database_url: URL,
    *,
    tenant_id: str,
    project_id: str,
    call_id: str,
    event_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, status, is_demo) "
                    "VALUES (:id, 'Telephony migration', 'telephony-migration', 'active', false)"
                ),
                {"id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects (
                      id, tenant_id, name, description, status, working_hours,
                      max_attempts, retry_intervals_minutes, callback_rules, is_default
                    ) VALUES (
                      :project_id, :tenant_id, 'Legacy project', '', 'active', '{}'::json,
                      3, '[15, 60]'::json, '{}'::json, true
                    )
                    """
                ),
                {"tenant_id": tenant_id, "project_id": project_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls (
                      id, tenant_id, project_id, channel, status, direction,
                      duration_seconds, provider, is_demo
                    ) VALUES (
                      :call_id, :tenant_id, :project_id, 'development_simulator',
                      'completed', 'outbound', 30, 'mock', true
                    )
                    """
                ),
                {"tenant_id": tenant_id, "project_id": project_id, "call_id": call_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_events (
                      id, tenant_id, call_id, event_type, sequence, safe_payload
                    ) VALUES (
                      :event_id, :tenant_id, :call_id, 'call.hangup', 1,
                      CAST(:safe_payload AS json)
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "call_id": call_id,
                    "event_id": event_id,
                    "safe_payload": '{"duration_seconds":30}',
                },
            )
    finally:
        await engine.dispose()


async def verify_upgrade(database_url: URL, *, call_id: str, event_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            call = (
                await connection.execute(
                    text(
                        "SELECT provider_state, recording_state, provider_metadata "
                        "FROM calls WHERE id = :id"
                    ),
                    {"id": call_id},
                )
            ).one()
            assert call == ("completed", "stopped", {})
            event = (
                await connection.execute(
                    text(
                        "SELECT event_type, occurred_at, provider_event_id "
                        "FROM call_events WHERE id = :id"
                    ),
                    {"id": event_id},
                )
            ).one()
            assert event[0] == "call.hangup"
            assert event[1] is not None
            assert event[2] is None
            rls = (
                await connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'telephony_command_submissions'"
                    )
                )
            ).one()
            assert rls == (True, True)
    finally:
        await engine.dispose()


async def verify_downgrade(database_url: URL, *, call_id: str, event_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM calls WHERE id = :id"), {"id": call_id}
                )
            ) == 1
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM call_events WHERE id = :id"),
                    {"id": event_id},
                )
            ) == 1
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = 'telephony_command_submissions'"
                    )
                )
            ) == 0
    finally:
        await engine.dispose()


def main() -> int:
    settings = settings_from_repository()
    if settings.app_env in {"staging", "production"}:
        raise RuntimeError("Refusing to test migrations against a deployed environment")
    if not settings.migration_database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")
    app_url = migration_test_url(settings.database_url)
    migration_url = migration_test_url(settings.migration_database_url)
    if app_url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The telephony migration test accepts only local PostgreSQL")
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )
    tenant_id, project_id, call_id, event_id = (str(uuid4()) for _ in range(4))
    database_name = app_url.database or ""
    print(
        f"Preparing isolated telephony migration database {database_name!r}", flush=True
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(
            seed_legacy_call(
                migration_url,
                tenant_id=tenant_id,
                project_id=project_id,
                call_id=call_id,
                event_id=event_id,
            )
        )
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, call_id=call_id, event_id=event_id))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, call_id=call_id, event_id=event_id))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated telephony migration database {database_name!r}",
            flush=True,
        )
    print("Telephony migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
