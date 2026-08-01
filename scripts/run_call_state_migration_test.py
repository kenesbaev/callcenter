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

OLD_HEAD = "j92b7g3e1c85"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_call_state_migration_test")


def alembic(
    revision: str,
    environment: dict[str, str],
    *,
    action: str = "upgrade",
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


async def seed_legacy_graph(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            statements: tuple[tuple[str, dict[str, str]], ...] = (
                (
                    (
                        "INSERT INTO tenants (id, name, slug, status, is_demo) "
                        "VALUES (:tenant, 'Call state migration', 'call-state-migration', 'active', false)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO users (id, email, password_hash, display_name, is_active, is_platform_admin) "
                        "VALUES (:user, 'migration@example.test', 'not-a-real-password', "
                        "'Migration owner', true, false)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO memberships (id, tenant_id, user_id, role, is_active) "
                        "VALUES (:membership, :tenant, :user, 'tenant_owner', true)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO projects (id, tenant_id, name, description, status, working_hours, "
                        "max_attempts, retry_intervals_minutes, callback_rules, is_default) VALUES "
                        "(:project, :tenant, 'Legacy project', '', 'active', '{}'::json, 3, "
                        "'[15, 60]'::json, '{}'::json, true)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO customers (id, tenant_id, project_id, display_name, is_anonymized, "
                        "status, tags, description, custom_fields) VALUES "
                        "(:customer, :tenant, :project, 'Legacy customer', false, 'new', '[]'::json, '', '{}'::json)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO call_flows (id, tenant_id, project_id, name, description, "
                        "default_language_code, language_codes, is_active) VALUES "
                        "(:flow, :tenant, :project, 'Legacy flow', '', 'ru', '[\"ru\"]'::json, true)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO call_flow_versions (id, tenant_id, project_id, call_flow_id, version, "
                        "status, definition, lock_version, published_at) VALUES "
                        "(:flow_version, :tenant, :project, :flow, 1, 'published', '{}'::json, 1, now())"
                    ),
                    {},
                ),
                (
                    "UPDATE call_flows SET active_version_id = :flow_version WHERE id = :flow",
                    {},
                ),
                (
                    (
                        "INSERT INTO call_result_catalogs (id, tenant_id, project_id, name, is_active) "
                        "VALUES (:catalog, :tenant, :project, 'Legacy results', true)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO call_result_definitions (id, tenant_id, project_id, catalog_id, "
                        "system_code, category, name, name_translations, description, color, sort_order, "
                        "is_active, requires_comment, requires_callback, requires_callback_at, creates_task, "
                        "return_to_queue, completes_customer, do_not_call, counts_as_success) VALUES "
                        "(:result_definition, :tenant, :project, :catalog, 'legacy_result', 'intermediate', "
                        "'Legacy result', '{}'::json, '', '#64748B', 0, true, false, true, false, false, "
                        "false, false, false, false)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO calls (id, tenant_id, project_id, channel, status, direction, "
                        "customer_id, call_flow_version_id, duration_seconds, provider, provider_state, "
                        "recording_state, provider_metadata, started_at, answered_at, ended_at, is_demo) VALUES "
                        "(:call, :tenant, :project, 'development_simulator', 'completed', 'outbound', "
                        ":customer, :flow_version, 30, 'mock', 'completed', 'stopped', '{}'::json, "
                        "now() - interval '40 seconds', now() - interval '30 seconds', now(), true)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO call_outcomes (id, tenant_id, project_id, call_id, result_definition_id, "
                        "code, label, category, color, label_translations, details) VALUES "
                        "(:outcome, :tenant, :project, :call, :result_definition, 'legacy_result', "
                        "'Legacy result', 'intermediate', '#64748B', '{}'::json, '{}'::json)"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO callback_tasks (id, tenant_id, project_id, customer_id, call_id, "
                        "call_outcome_id, task_type, title, description, priority, created_by_user_id, due_at, "
                        "status, comment, source, note) VALUES (:task, :tenant, :project, :customer, :call, "
                        ":outcome, 'callback', 'Legacy callback', '', 'normal', :user, now() + interval '1 hour', "
                        "'pending', '', 'call_result', '')"
                    ),
                    {},
                ),
                (
                    (
                        "INSERT INTO call_events (id, tenant_id, call_id, event_type, sequence, safe_payload, "
                        "provider, provider_event_id, external_call_id, occurred_at, provider_timestamp, "
                        "correlation_id) VALUES (:event, :tenant, :call, 'call.hangup', 1, '{}'::json, "
                        "'mock', 'legacy-provider-event', 'mock:legacy', now(), now(), 'legacy-call-state')"
                    ),
                    {},
                ),
            )
            parameters = dict(ids)
            for statement, extra in statements:
                await connection.execute(text(statement), {**parameters, **extra})
    finally:
        await engine.dispose()


async def verify_upgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            call = (
                await connection.execute(
                    text(
                        "SELECT status, direction, caller_type, ringing_at, state_version, "
                        "last_provider_event_at, call_flow_version_id FROM calls WHERE id = :call"
                    ),
                    ids,
                )
            ).one()
            assert call[0:3] == ("completed", "outbound", "human_operator")
            assert call[3] is not None
            assert call[4] == 1
            assert call[5] is not None
            assert str(call[6]) == ids["flow_version"]
            outcome_call = await connection.scalar(
                text("SELECT call_id FROM call_outcomes WHERE id = :outcome"), ids
            )
            task_links = (
                await connection.execute(
                    text(
                        "SELECT call_id, call_outcome_id FROM callback_tasks WHERE id = :task"
                    ),
                    ids,
                )
            ).one()
            assert str(outcome_call) == ids["call"]
            assert tuple(str(value) for value in task_links) == (
                ids["call"],
                ids["outcome"],
            )
            rls = (
                await connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'calls'"
                    )
                )
            ).one()
            assert rls == (True, True)
    finally:
        await engine.dispose()


async def verify_downgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            call = (
                await connection.execute(
                    text(
                        "SELECT status, call_flow_version_id FROM calls WHERE id = :call"
                    ),
                    ids,
                )
            ).one()
            assert call[0] == "completed"
            assert str(call[1]) == ids["flow_version"]
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM call_outcomes WHERE id = :outcome"), ids
                )
            ) == 1
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM callback_tasks WHERE id = :task"), ids
                )
            ) == 1
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'calls' AND column_name = 'state_version'"
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
        raise RuntimeError(
            "The call state migration test accepts only local PostgreSQL"
        )
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
    )
    ids = {key: str(uuid4()) for key in keys}
    database_name = app_url.database or ""
    print(
        f"Preparing isolated call state migration database {database_name!r}",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated call state migration database {database_name!r}",
            flush=True,
        )
    print("Call state migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
