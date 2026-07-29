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

OLD_HEAD = "e47c2b8f6d30"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    source_database = url.database or ""
    return url.set(database=f"{source_database}_call_flow_migration_test")


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


async def seed_old_call_flow(
    database_url: URL,
    *,
    tenant_id: str,
    settings_id: str,
    project_id: str,
    flow_id: str,
    published_id: str,
    draft_two_id: str,
    draft_three_id: str,
    call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, slug, status, is_demo)
                    VALUES (:tenant_id, 'Call flow migration tenant', 'call-flow-migration', 'active', false)
                    """
                ),
                {"tenant_id": tenant_id},
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
                      :settings_id, :tenant_id, 'Asia/Tashkent', 'ru', true, true, 90, 5
                    )
                    """
                ),
                {"settings_id": settings_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects (
                      id, tenant_id, name, description, status, max_concurrent_calls,
                      working_hours, is_default, max_attempts, retry_intervals_minutes,
                      callback_rules
                    ) VALUES (
                      :project_id, :tenant_id, 'Existing flow project', '', 'active', 2,
                      '{}'::json, true, 3, '[15, 60]'::json, '{}'::json
                    )
                    """
                ),
                {"project_id": project_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_flows (
                      id, tenant_id, project_id, name, active_version_id, is_active
                    ) VALUES (
                      :flow_id, :tenant_id, :project_id, 'Existing flow', NULL, true
                    )
                    """
                ),
                {
                    "flow_id": flow_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            for version_id, version, status in (
                (published_id, 1, "published"),
                (draft_two_id, 2, "draft"),
                (draft_three_id, 3, "draft"),
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO call_flow_versions (
                          id, tenant_id, call_flow_id, version, status, definition, published_at
                        ) VALUES (
                          :version_id, :tenant_id, :flow_id, :version, CAST(:status AS varchar),
                          json_build_object('legacy', true, 'version', CAST(:version AS integer)),
                          CASE WHEN CAST(:status AS varchar) = 'published' THEN now() ELSE NULL END
                        )
                        """
                    ),
                    {
                        "version_id": version_id,
                        "tenant_id": tenant_id,
                        "flow_id": flow_id,
                        "version": version,
                        "status": status,
                    },
                )
            await connection.execute(
                text(
                    """
                    UPDATE call_flows SET active_version_id = :published_id WHERE id = :flow_id
                    """
                ),
                {
                    "published_id": published_id,
                    "flow_id": flow_id,
                },
            )
            await connection.execute(
                text(
                    "UPDATE projects SET call_flow_id = :flow_id WHERE id = :project_id"
                ),
                {"flow_id": flow_id, "project_id": project_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls (
                      id, tenant_id, project_id, channel, status, direction,
                      duration_seconds, provider, is_demo
                    ) VALUES (
                      :call_id, :tenant_id, :project_id, 'development_simulator', 'completed',
                      'outbound', 30, 'mock', true
                    )
                    """
                ),
                {
                    "call_id": call_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
    finally:
        await engine.dispose()


async def verify_call_flow(
    database_url: URL,
    *,
    tenant_id: str,
    project_id: str,
    flow_id: str,
    published_id: str,
    draft_two_id: str,
    draft_three_id: str,
    call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            flow = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT project_id, name, description, default_language_code,
                                   language_codes, active_version_id, is_active, archived_at
                            FROM call_flows
                            WHERE tenant_id = :tenant_id AND id = :flow_id
                            """
                        ),
                        {"tenant_id": tenant_id, "flow_id": flow_id},
                    )
                )
                .mappings()
                .one()
            )
            assert str(flow["project_id"]) == project_id
            assert flow["name"] == "Existing flow"
            assert flow["description"] == ""
            assert flow["default_language_code"] == "ru"
            assert flow["language_codes"] == ["ru"]
            assert str(flow["active_version_id"]) == published_id
            assert flow["is_active"] is True
            assert flow["archived_at"] is None

            versions = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, project_id, version, status, definition,
                                   lock_version, created_from_version_id
                            FROM call_flow_versions
                            WHERE tenant_id = :tenant_id AND call_flow_id = :flow_id
                            ORDER BY version
                            """
                        ),
                        {"tenant_id": tenant_id, "flow_id": flow_id},
                    )
                )
                .mappings()
                .all()
            )
            assert [str(row["id"]) for row in versions] == [
                published_id,
                draft_two_id,
                draft_three_id,
            ]
            assert all(str(row["project_id"]) == project_id for row in versions)
            assert all(row["lock_version"] == 1 for row in versions)
            assert all(row["created_from_version_id"] is None for row in versions)
            assert versions[0]["status"] == "published"
            assert versions[0]["definition"] == {"legacy": True, "version": 1}
            assert versions[1]["status"] == "archived"
            assert versions[2]["status"] == "draft"

            project_flow_id = (
                await connection.execute(
                    text(
                        "SELECT call_flow_id FROM projects WHERE tenant_id = :tenant_id AND id = :project_id"
                    ),
                    {"tenant_id": tenant_id, "project_id": project_id},
                )
            ).scalar_one()
            assert str(project_flow_id) == flow_id
            call_version_id = (
                await connection.execute(
                    text(
                        "SELECT call_flow_version_id FROM calls WHERE tenant_id = :tenant_id AND id = :call_id"
                    ),
                    {"tenant_id": tenant_id, "call_id": call_id},
                )
            ).scalar_one_or_none()
            assert call_version_id is None
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

    app_url = migration_test_url(settings.database_url)
    migration_url = migration_test_url(settings.migration_database_url)
    if app_url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The call flow migration test accepts only local PostgreSQL")

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
    identifiers = [str(uuid4()) for _ in range(8)]
    (
        tenant_id,
        settings_id,
        project_id,
        flow_id,
        published_id,
        draft_two_id,
        draft_three_id,
        call_id,
    ) = identifiers
    print(
        f"Preparing isolated call flow migration database {database_name!r}", flush=True
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic_upgrade(OLD_HEAD, environment)
        asyncio.run(
            seed_old_call_flow(
                migration_url,
                tenant_id=tenant_id,
                settings_id=settings_id,
                project_id=project_id,
                flow_id=flow_id,
                published_id=published_id,
                draft_two_id=draft_two_id,
                draft_three_id=draft_three_id,
                call_id=call_id,
            )
        )
        alembic_upgrade("head", environment)
        asyncio.run(
            verify_call_flow(
                migration_url,
                tenant_id=tenant_id,
                project_id=project_id,
                flow_id=flow_id,
                published_id=published_id,
                draft_two_id=draft_two_id,
                draft_three_id=draft_three_id,
                call_id=call_id,
            )
        )
        print(
            "Existing call flow, project and call preservation check passed", flush=True
        )
        return 0
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated call flow migration database {database_name!r}",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
