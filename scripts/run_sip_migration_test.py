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

OLD_HEAD = "q69i4n0l8j52"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_sip_migration_test")


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


async def seed(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,status,is_demo) "
                    "VALUES (:tenant,'SIP migration','sip-migration','active',false)"
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects
                      (id,tenant_id,name,description,status,working_hours,max_attempts,
                       retry_intervals_minutes,callback_rules,is_default)
                    VALUES (:project,:tenant,'Legacy SIP project','','active','{}'::json,3,
                            '[15,60]'::json,'{}'::json,true)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO sip_trunks
                      (id,tenant_id,name,provider_host,provider_port,transport,allowed_ips,
                       max_channels,status)
                    VALUES (:trunk,:tenant,'Legacy trunk','sip.invalid',5060,'udp',
                            '["127.0.0.1/32"]'::json,2,'configured')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls
                      (id,tenant_id,project_id,channel,status,direction,caller_type,
                       duration_seconds,provider,provider_state,recording_state,
                       provider_metadata,state_version,is_demo)
                    VALUES (:call,:tenant,:project,'sip','completed','inbound','human_operator',
                            12,'asterisk-ari','completed','stopped','{}'::json,3,false)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_recordings
                      (id,tenant_id,call_id,storage_key,encryption_key_ref,content_type,
                       size_bytes,recording_paused_ranges,delete_after,retention_state)
                    VALUES (:recording,:tenant,:call,'legacy/recording.wav','legacy-key',
                            'audio/wav',42,'[]'::json,now()+interval '30 days','retained')
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
            trunk = (
                await connection.execute(
                    text(
                        "SELECT auth_mode,codecs,dtmf_mode,channel_pool_mode,lock_version "
                        "FROM sip_trunks WHERE id=:trunk"
                    ),
                    ids,
                )
            ).one()
            assert trunk == ("registration", [], "auto", "shared", 1)
            assert (
                await connection.scalar(
                    text("SELECT sip_trunk_id FROM calls WHERE id=:call"), ids
                )
                is None
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM call_recordings WHERE id=:recording"), ids
                )
                == "available"
            )
            for table in (
                "telephony_channel_reservations",
                "telephony_resources",
                "call_detail_records",
                "telephony_diagnostic_runs",
            ):
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"
                        ),
                        {"table": table},
                    )
                ).one()
                assert rls == (True, True)
            diagnostic_call_nullable = await connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name='telephony_diagnostic_runs' AND column_name='call_id'"
                )
            )
            assert diagnostic_call_nullable == "YES"
    finally:
        await engine.dispose()


async def verify_downgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM calls WHERE id=:call"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM sip_trunks WHERE id=:trunk"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM call_recordings WHERE id=:recording"),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name='telephony_channel_reservations'"
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
        raise RuntimeError("SIP migration test accepts only local PostgreSQL")
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
        name: str(uuid4())
        for name in ("tenant", "project", "trunk", "call", "recording")
    }
    database_name = app_url.database or ""
    print(f"Preparing isolated SIP migration database {database_name!r}", flush=True)
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated SIP migration database {database_name!r}", flush=True)
    print("SIP migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
