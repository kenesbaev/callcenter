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

OLD_HEAD = "t92l7q3o1m85"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_live_transfer_migration_test")


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


def alembic_check(environment: dict[str, str]) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(API_ROOT / "alembic.ini"),
            "check",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if "No new upgrade operations detected." not in output:
        raise RuntimeError(f"Unexpected Alembic check output: {output}")


async def seed_legacy(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,status,is_demo) "
                    "VALUES (:tenant,'Transfer migration','transfer-migration','active',false)"
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
                      (:project,:tenant,'Legacy transfer project','','active','{}'::json,3,
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
                       provider,is_demo,language)
                    VALUES
                      (:call,:tenant,:project,'development_simulator','active',
                       'outbound',0,'mock',true,'kaa')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO transfer_requests
                      (id,tenant_id,call_id,status,reason,summary,requested_at)
                    VALUES
                      (:transfer,:tenant,:call,'requested','Legacy reason','Legacy summary',now())
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
            row = (
                await connection.execute(
                    text(
                        "SELECT project_id,language_code,routing_strategy,destination_type,"
                        "attempt_count,max_attempts,idempotency_key,lock_version,reason,summary "
                        "FROM transfer_requests WHERE id=:transfer"
                    ),
                    ids,
                )
            ).one()
            assert str(row.project_id) == ids["project"]
            assert row.language_code == "kaa"
            assert row.routing_strategy == "longest_idle"
            assert row.destination_type == "browser"
            assert row.attempt_count == 0
            assert row.max_attempts == 3
            assert row.idempotency_key == f"legacy:{ids['transfer']}"
            assert row.lock_version == 1
            assert row.reason == "Legacy reason"
            assert row.summary == "Legacy summary"
            for table in ("transfer_attempts", "operator_transfer_endpoints"):
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
            row = (
                await connection.execute(
                    text(
                        "SELECT call_id,reason,summary FROM transfer_requests WHERE id=:transfer"
                    ),
                    ids,
                )
            ).one()
            assert str(row.call_id) == ids["call"]
            assert row.reason == "Legacy reason"
            assert row.summary == "Legacy summary"
            for table in ("transfer_attempts", "operator_transfer_endpoints"):
                count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name=:table"
                    ),
                    {"table": table},
                )
                assert count == 0
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
        raise RuntimeError("Live transfer migration test accepts only local PostgreSQL")
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
        "transfer": str(uuid4()),
    }
    database_name = app_url.database or ""
    print(
        f"Preparing isolated live transfer migration database {database_name!r}",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic_check(environment)
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic_check(environment)
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated live transfer migration database {database_name!r}",
            flush=True,
        )
    print("Live transfer migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
