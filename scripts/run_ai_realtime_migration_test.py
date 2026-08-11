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

OLD_HEAD = "s81k6p2n0l74"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_ai_realtime_migration_test")


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


async def seed_legacy(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,status,is_demo) "
                    "VALUES (:tenant,'AI migration','ai-realtime-migration','active',false)"
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects
                      (id,tenant_id,name,description,status,working_hours,max_attempts,
                       retry_intervals_minutes,callback_rules,is_default)
                    VALUES
                      (:project,:tenant,'Legacy AI project','','active','{}'::json,3,
                       '[15,60]'::json,'{}'::json,true)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls
                      (id,tenant_id,project_id,channel,status,direction,duration_seconds,
                       provider,is_demo)
                    VALUES
                      (:call,:tenant,:project,'development_simulator','completed',
                       'outbound',10,'mock',true)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO transcript_segments
                      (id,tenant_id,call_id,sequence,speaker,language,text,created_at,updated_at)
                    VALUES
                      (:transcript,:tenant,:call,1,'customer','kaa','Legacy KAA text',now(),now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO usage_records
                      (id,tenant_id,call_id,metric,quantity,unit,estimated_cost_usd,
                       idempotency_key,occurred_at,created_at,updated_at)
                    VALUES
                      (:usage,:tenant,:call,'legacy_ai_seconds',10,'second',0,
                       :usage_key,now(),now(),now())
                    """
                ),
                ids,
            )
    finally:
        await engine.dispose()


async def verify_upgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transcript = (
                await connection.execute(
                    text(
                        "SELECT language_code,is_final,interrupted,text "
                        "FROM transcript_segments WHERE id=:transcript"
                    ),
                    ids,
                )
            ).one()
            assert transcript == ("kaa", True, False, "Legacy KAA text")
            usage = (
                await connection.execute(
                    text(
                        "SELECT safe_metadata,pricing_available,quantity "
                        "FROM usage_records WHERE id=:usage"
                    ),
                    ids,
                )
            ).one()
            assert usage.safe_metadata == {}
            assert usage.pricing_available is False
            assert float(usage.quantity) == 10
            for table in ("ai_realtime_sessions", "ai_realtime_session_events"):
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                            "WHERE relname=:table"
                        ),
                        {"table": table},
                    )
                ).one()
                assert rls == (True, True)
    finally:
        await engine.dispose()


async def verify_downgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM transcript_segments WHERE id=:transcript"
                    ),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM usage_records WHERE id=:usage"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name='ai_realtime_sessions'"
                    )
                )
                == 0
            )
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
        raise RuntimeError("AI Realtime migration test accepts only local PostgreSQL")
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )
    ids = {
        "tenant": str(uuid4()),
        "project": str(uuid4()),
        "call": str(uuid4()),
        "transcript": str(uuid4()),
        "usage": str(uuid4()),
        "usage_key": f"legacy-ai-{uuid4()}",
    }
    database_name = app_url.database or ""
    print(
        f"Preparing isolated AI Realtime migration database {database_name!r}",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated AI Realtime migration database {database_name!r}",
            flush=True,
        )
    print("AI Realtime migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
