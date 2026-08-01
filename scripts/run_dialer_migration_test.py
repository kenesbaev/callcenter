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

OLD_HEAD = "k03c8h4f2d96"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_dialer_migration_test")


async def seed_runtime(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO call_flow_executions "
                    "(id, tenant_id, project_id, call_id, customer_id, operator_user_id, "
                    "call_flow_version_id, current_node_id, status, language_code, state_version, "
                    "values, started_at) VALUES (:execution, :tenant, :project, :call, :customer, "
                    ":user, :flow_version, :node, 'active', 'ru', 1, '{}'::json, now())"
                ),
                ids,
            )
            await connection.execute(
                text(
                    "INSERT INTO call_flow_execution_steps "
                    "(id, tenant_id, execution_id, sequence, node_id, system_key, node_type, "
                    "language_code, text_snapshot, hint_snapshot, next_node_id, action_status, "
                    "actor_user_id, idempotency_key, occurred_at) VALUES "
                    "(:step, :tenant, :execution, 1, :node, 'start', 'start', 'ru', '', '', "
                    ":node, 'not_applicable', :user, 'migration-step', now())"
                ),
                ids,
            )
            await connection.execute(
                text(
                    "INSERT INTO dialer_completion_submissions "
                    "(id, tenant_id, call_id, idempotency_key, request_fingerprint, response_payload) "
                    "VALUES (:submission, :tenant, :call, 'migration-complete', "
                    "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', '{}'::json)"
                ),
                ids,
            )
    finally:
        await engine.dispose()


async def verify_upgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM calls WHERE id = :call"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM call_outcomes WHERE id = :outcome"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM callback_tasks WHERE id = :task"), ids
                )
                == 1
            )
            for table in (
                "call_flow_executions",
                "call_flow_execution_steps",
                "dialer_completion_submissions",
            ):
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :table"
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
                    text("SELECT count(*) FROM calls WHERE id = :call"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM call_outcomes WHERE id = :outcome"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM callback_tasks WHERE id = :task"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                        "('call_flow_executions', 'call_flow_execution_steps', "
                        "'dialer_completion_submissions')"
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
        raise RuntimeError("The Dialer migration test accepts only local PostgreSQL")
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
        "execution",
        "step",
        "submission",
        "node",
    )
    ids = {key: str(uuid4()) for key in keys}
    database_name = app_url.database or ""
    print(f"Preparing isolated Dialer migration database {database_name!r}", flush=True)
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        alembic("head", environment)
        asyncio.run(seed_runtime(migration_url, ids))
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated Dialer migration database {database_name!r}", flush=True
        )
    print("Dialer migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
