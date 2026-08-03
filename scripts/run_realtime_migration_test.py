from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from run_api_tests import (
    drop_database,
    recreate_database,
    rendered,
    settings_from_repository,
)
from run_call_state_migration_test import alembic, seed_legacy_graph
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

OLD_HEAD = "n36f1k7i5g29"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_realtime_migration_test")


async def verify(database_url: URL, ids: dict[str, str], *, upgraded: bool) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM calls WHERE id = :call"), ids
                )
                == 1
            )
            table_exists = bool(
                await connection.scalar(
                    text("SELECT to_regclass('public.realtime_events') IS NOT NULL")
                )
            )
            assert table_exists is upgraded
            if upgraded:
                row_security = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE oid = 'realtime_events'::regclass"
                        )
                    )
                ).one()
                assert row_security == (True, True)
                indexes = int(
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_indexes "
                            "WHERE tablename = 'realtime_events'"
                        )
                    )
                    or 0
                )
                assert indexes >= 7
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
        raise RuntimeError("The realtime migration test accepts only local PostgreSQL")
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )
    keys = (
        "tenant",
        "user",
        "membership",
        "project",
        "customer",
        "flow",
        "flow_version",
        "catalog",
        "result_definition",
        "call",
        "outcome",
        "task",
        "event",
        "node",
    )
    ids = {key: str(uuid4()) for key in keys}
    database_name = app_url.database or ""
    print(
        f"Preparing isolated realtime migration database {database_name!r}", flush=True
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify(migration_url, ids, upgraded=True))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify(migration_url, ids, upgraded=False))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated realtime migration database {database_name!r}",
            flush=True,
        )
    print("Realtime migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
