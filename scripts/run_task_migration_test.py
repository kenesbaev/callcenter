from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
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

OLD_HEAD = "h70f5e1c9a63"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_task_migration_test")


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


async def seed_legacy_callback(
    database_url: URL,
    *,
    tenant_id: str,
    user_id: str,
    membership_id: str,
    project_id: str,
    project_user_id: str,
    customer_id: str,
    call_id: str,
    catalog_id: str,
    definition_id: str,
    outcome_id: str,
    task_id: str,
    due_at: datetime,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, slug, status, is_demo)
                    VALUES (:tenant_id, 'Task migration tenant', 'task-migration', 'active', false)
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash, display_name, is_active, is_platform_admin)
                    VALUES (:user_id, 'task-migration@example.com', 'legacy-hash', 'Legacy owner', true, false)
                    """
                ),
                {"user_id": user_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO memberships (id, tenant_id, user_id, role, is_active)
                    VALUES (:membership_id, :tenant_id, :user_id, 'tenant_owner', true)
                    """
                ),
                {
                    "membership_id": membership_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects (
                      id, tenant_id, name, description, status, max_concurrent_calls,
                      working_hours, is_default, max_attempts, retry_intervals_minutes,
                      callback_rules
                    ) VALUES (
                      :project_id, :tenant_id, 'Legacy callback project', '', 'active', 2,
                      '{}'::json, true, 3, '[15, 60]'::json, '{}'::json
                    )
                    """
                ),
                {"project_id": project_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO project_users (id, tenant_id, project_id, user_id, is_active)
                    VALUES (:id, :tenant_id, :project_id, :user_id, true)
                    """
                ),
                {
                    "id": project_user_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO customers (
                      id, tenant_id, project_id, display_name, is_anonymized, status,
                      tags, description, custom_fields
                    ) VALUES (
                      :customer_id, :tenant_id, :project_id, 'Legacy customer', false,
                      'callback', '[]'::json, '', '{}'::json
                    )
                    """
                ),
                {
                    "customer_id": customer_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls (
                      id, tenant_id, project_id, customer_id, operator_user_id,
                      channel, status, direction, duration_seconds, provider, is_demo
                    ) VALUES (
                      :call_id, :tenant_id, :project_id, :customer_id, :user_id,
                      'development_simulator', 'completed', 'outbound', 45, 'mock', true
                    )
                    """
                ),
                {
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "customer_id": customer_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_result_catalogs (id, tenant_id, project_id, name, is_active)
                    VALUES (:id, :tenant_id, :project_id, 'Legacy results', true)
                    """
                ),
                {"id": catalog_id, "tenant_id": tenant_id, "project_id": project_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_result_definitions (
                      id, tenant_id, project_id, catalog_id, system_code, category, name,
                      name_translations, description, color, sort_order, is_active,
                      requires_comment, requires_callback, requires_callback_at, creates_task,
                      return_to_queue, completes_customer, do_not_call, counts_as_success
                    ) VALUES (
                      :id, :tenant_id, :project_id, :catalog_id, 'callback', 'intermediate',
                      'Перезвонить', '{"ru":"Перезвонить"}'::json, '', '#F59E0B', 1, true,
                      false, true, true, false, false, false, false, false
                    )
                    """
                ),
                {
                    "id": definition_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "catalog_id": catalog_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_outcomes (
                      id, tenant_id, project_id, call_id, result_definition_id, code, label,
                      category, color, label_translations, details
                    ) VALUES (
                      :id, :tenant_id, :project_id, :call_id, :definition_id, 'callback',
                      'Перезвонить', 'intermediate', '#F59E0B', '{"ru":"Перезвонить"}'::json,
                      '{"comment":"Legacy note"}'::json
                    )
                    """
                ),
                {
                    "id": outcome_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "call_id": call_id,
                    "definition_id": definition_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO callback_tasks (
                      id, tenant_id, project_id, customer_id, call_id, assigned_user_id,
                      due_at, status, note
                    ) VALUES (
                      :id, :tenant_id, :project_id, :customer_id, :call_id, :user_id,
                      :due_at, 'pending', 'Legacy note'
                    )
                    """
                ),
                {
                    "id": task_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "customer_id": customer_id,
                    "call_id": call_id,
                    "user_id": user_id,
                    "due_at": due_at,
                },
            )
    finally:
        await engine.dispose()


async def verify_upgrade(
    database_url: URL,
    *,
    tenant_id: str,
    task_id: str,
    outcome_id: str,
    user_id: str,
    due_at: datetime,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            task = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, task_type, title, priority, status, comment, note,
                                   source, call_outcome_id, created_by_user_id, due_at
                            FROM callback_tasks
                            WHERE tenant_id = :tenant_id AND id = :task_id
                            """
                        ),
                        {"tenant_id": tenant_id, "task_id": task_id},
                    )
                )
                .mappings()
                .one()
            )
            assert str(task["id"]) == task_id
            assert task["task_type"] == "callback"
            assert task["title"] == "Перезвон клиенту"
            assert task["priority"] == "normal"
            assert task["status"] == "pending"
            assert task["comment"] == task["note"] == "Legacy note"
            assert task["source"] == "legacy_callback"
            assert str(task["call_outcome_id"]) == outcome_id
            assert str(task["created_by_user_id"]) == user_id
            assert task["due_at"] == due_at
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM task_events "
                        "WHERE tenant_id = :tenant_id AND task_id = :task_id"
                    ),
                    {"tenant_id": tenant_id, "task_id": task_id},
                )
            ) == 1
            rls = (
                await connection.execute(
                    text(
                        """
                        SELECT relrowsecurity, relforcerowsecurity
                        FROM pg_class WHERE relname = 'task_events'
                        """
                    )
                )
            ).one()
            assert rls == (True, True)
    finally:
        await engine.dispose()


async def verify_downgrade(
    database_url: URL,
    *,
    tenant_id: str,
    task_id: str,
    due_at: datetime,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            task = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, due_at, status, note
                            FROM callback_tasks
                            WHERE tenant_id = :tenant_id AND id = :task_id
                            """
                        ),
                        {"tenant_id": tenant_id, "task_id": task_id},
                    )
                )
                .mappings()
                .one()
            )
            assert str(task["id"]) == task_id
            assert task["due_at"] == due_at
            assert task["status"] == "pending"
            assert task["note"] == "Legacy note"
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name IN ('task_events', 'task_command_submissions')"
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
        raise RuntimeError("The task migration test accepts only local PostgreSQL")
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )
    ids = [str(uuid4()) for _ in range(11)]
    (
        tenant_id,
        user_id,
        membership_id,
        project_id,
        project_user_id,
        customer_id,
        call_id,
        catalog_id,
        definition_id,
        outcome_id,
        task_id,
    ) = ids
    due_at = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    database_name = app_url.database or ""
    print(f"Preparing isolated task migration database {database_name!r}", flush=True)
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(
            seed_legacy_callback(
                migration_url,
                tenant_id=tenant_id,
                user_id=user_id,
                membership_id=membership_id,
                project_id=project_id,
                project_user_id=project_user_id,
                customer_id=customer_id,
                call_id=call_id,
                catalog_id=catalog_id,
                definition_id=definition_id,
                outcome_id=outcome_id,
                task_id=task_id,
                due_at=due_at,
            )
        )
        alembic("head", environment)
        asyncio.run(
            verify_upgrade(
                migration_url,
                tenant_id=tenant_id,
                task_id=task_id,
                outcome_id=outcome_id,
                user_id=user_id,
                due_at=due_at,
            )
        )
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(
            verify_downgrade(
                migration_url,
                tenant_id=tenant_id,
                task_id=task_id,
                due_at=due_at,
            )
        )
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated task migration database {database_name!r}", flush=True)
    print("Task migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
