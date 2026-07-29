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

OLD_HEAD = "c24a8f6e2d10"
PRE_PROJECT_REVISION = "b13f5e7d9a21"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    source_database = url.database or ""
    return url.set(database=f"{source_database}_project_migration_test")


def alembic_upgrade(revision: str, environment: dict[str, str]) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(API_ROOT / "alembic.ini"),
            "upgrade",
            revision,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


async def seed_tenant(database_url: URL, tenant_id: str, settings_id: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, slug, status, is_demo)
                    VALUES (:id, 'Migration tenant', 'migration-tenant', 'active', false)
                    """
                ),
                {"id": tenant_id},
            )
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO tenant_settings (
                      id, tenant_id, timezone, default_language, recording_enabled,
                      recording_disclosure_required, retention_days, max_concurrent_calls
                    ) VALUES (
                      :id, :tenant_id, 'Asia/Tashkent', 'ru', true, true, 90, 7
                    )
                    """
                ),
                {"id": settings_id, "tenant_id": tenant_id},
            )
    finally:
        await engine.dispose()


async def customize_old_project(database_url: URL, tenant_id: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            project_id = (
                await connection.execute(
                    text(
                        """
                        UPDATE projects
                        SET name = 'Existing project', description = 'Preserve me',
                            max_concurrent_calls = 4, working_hours = '{"monday": {}}'::json
                        WHERE tenant_id = :tenant_id AND is_default = true
                        RETURNING id
                        """
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
            return str(project_id)
    finally:
        await engine.dispose()


async def verify_migrated_project(
    database_url: URL, tenant_id: str, project_id: str
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT name, description, status, max_concurrent_calls, working_hours,
                               max_attempts, retry_intervals_minutes, callback_rules,
                               default_language, timezone, archived_at
                        FROM projects
                        WHERE tenant_id = :tenant_id AND id = :project_id
                        """
                        ),
                        {"tenant_id": tenant_id, "project_id": project_id},
                    )
                )
                .mappings()
                .one()
            )
            assert row["name"] == "Existing project"
            assert row["description"] == "Preserve me"
            assert row["status"] == "active"
            assert row["max_concurrent_calls"] == 4
            assert row["working_hours"] == {"monday": {}}
            assert row["max_attempts"] == 3
            assert row["retry_intervals_minutes"] == [15, 60]
            assert row["callback_rules"] == {}
            assert row["default_language"] is None
            assert row["timezone"] is None
            assert row["archived_at"] is None
            catalogs = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) FROM call_result_catalogs
                        WHERE tenant_id = :tenant_id AND project_id = :project_id
                        """
                    ),
                    {"tenant_id": tenant_id, "project_id": project_id},
                )
            ).scalar_one()
            assert catalogs == 1
    finally:
        await engine.dispose()


def main() -> int:
    settings = settings_from_repository()
    if settings.app_env in {"staging", "production"}:
        raise RuntimeError(
            "Refusing to run a migration test from a deployed environment"
        )
    if not settings.migration_database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")

    source_app_url = make_url(settings.database_url)
    source_migration_url = make_url(settings.migration_database_url)
    app_url = migration_test_url(rendered(source_app_url))
    migration_url = migration_test_url(rendered(source_migration_url))
    if app_url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The project migration test accepts only local PostgreSQL")

    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )
    database_name = app_url.database or ""
    tenant_id = str(uuid4())
    settings_id = str(uuid4())
    print(f"Preparing isolated migration database {database_name!r}", flush=True)
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic_upgrade(PRE_PROJECT_REVISION, environment)
        asyncio.run(seed_tenant(migration_url, tenant_id, settings_id))
        alembic_upgrade(OLD_HEAD, environment)
        project_id = asyncio.run(customize_old_project(migration_url, tenant_id))
        alembic_upgrade("head", environment)
        asyncio.run(verify_migrated_project(migration_url, tenant_id, project_id))
        print("Existing project preservation check passed", flush=True)
        return 0
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated migration database {database_name!r}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
