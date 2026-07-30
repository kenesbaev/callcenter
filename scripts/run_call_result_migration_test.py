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

OLD_HEAD = "f58d3c9a7e41"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_call_result_migration_test")


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


async def seed_legacy_data(
    database_url: URL,
    *,
    tenant_id: str,
    project_id: str,
    catalog_id: str,
    known_call_id: str,
    unknown_call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, slug, status, is_demo)
                    VALUES (:tenant_id, 'Result migration tenant', 'result-migration', 'active', false)
                    """
                ),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects (
                      id, tenant_id, name, description, status, max_concurrent_calls,
                      working_hours, is_default, max_attempts, retry_intervals_minutes,
                      callback_rules
                    ) VALUES (
                      :project_id, :tenant_id, 'Existing results project', '', 'active', 2,
                      '{}'::json, true, 3, '[15, 60]'::json, '{}'::json
                    )
                    """
                ),
                {"project_id": project_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_result_catalogs
                      (id, tenant_id, project_id, name, is_active)
                    VALUES (:catalog_id, :tenant_id, :project_id, 'Existing catalog', true)
                    """
                ),
                {
                    "catalog_id": catalog_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            for call_id in (known_call_id, unknown_call_id):
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
                    {
                        "call_id": call_id,
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                    },
                )
            for call_id, code, label in (
                (known_call_id, "success", "Старое успешное название"),
                (unknown_call_id, "legacy_vendor_value", "Неизвестный старый итог"),
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO call_outcomes
                          (id, tenant_id, call_id, code, label, details)
                        VALUES
                          (:id, :tenant_id, :call_id, :code, :label, '{"source":"legacy"}'::json)
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": tenant_id,
                        "call_id": call_id,
                        "code": code,
                        "label": label,
                    },
                )
    finally:
        await engine.dispose()


async def verify_upgrade(
    database_url: URL,
    *,
    tenant_id: str,
    project_id: str,
    catalog_id: str,
    known_call_id: str,
    unknown_call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            catalog = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, name FROM call_result_catalogs "
                            "WHERE tenant_id = :tenant_id AND project_id = :project_id"
                        ),
                        {"tenant_id": tenant_id, "project_id": project_id},
                    )
                )
                .mappings()
                .one()
            )
            assert str(catalog["id"]) == catalog_id
            assert catalog["name"] == "Existing catalog"
            defaults = (
                (
                    await connection.execute(
                        text(
                            "SELECT system_code, name_translations FROM call_result_definitions "
                            "WHERE tenant_id = :tenant_id AND project_id = :project_id "
                            "AND system_code NOT LIKE 'legacy_%'"
                        ),
                        {"tenant_id": tenant_id, "project_id": project_id},
                    )
                )
                .mappings()
                .all()
            )
            assert {row["system_code"] for row in defaults} == {
                "success",
                "no_answer",
                "busy",
                "callback",
                "wrong_number",
                "do_not_call",
                "not_interested",
                "failed",
                "other",
            }
            assert all("kaa" in row["name_translations"] for row in defaults)
            assert all("ka" not in row["name_translations"] for row in defaults)
            outcomes = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT outcome.call_id, outcome.code, outcome.label, outcome.details,
                               outcome.category, outcome.color, definition.system_code,
                               definition.is_active, definition.archived_at
                        FROM call_outcomes outcome
                        JOIN call_result_definitions definition
                          ON definition.id = outcome.result_definition_id
                         AND definition.tenant_id = outcome.tenant_id
                         AND definition.project_id = outcome.project_id
                        WHERE outcome.tenant_id = :tenant_id
                        """
                        ),
                        {"tenant_id": tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            by_call = {str(row["call_id"]): row for row in outcomes}
            known = by_call[known_call_id]
            assert known["code"] == "success"
            assert known["label"] == "Старое успешное название"
            assert known["category"] == "successful"
            assert known["system_code"] == "success"
            unknown = by_call[unknown_call_id]
            assert unknown["code"] == "legacy_vendor_value"
            assert unknown["label"] == "Неизвестный старый итог"
            assert unknown["details"] == {"source": "legacy"}
            assert unknown["category"] == "intermediate"
            assert unknown["system_code"].startswith("legacy_")
            assert unknown["is_active"] is False
            assert unknown["archived_at"] is not None
    finally:
        await engine.dispose()


async def verify_downgrade(
    database_url: URL,
    *,
    tenant_id: str,
    known_call_id: str,
    unknown_call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            columns = {
                row[0]
                for row in (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'call_outcomes'"
                        )
                    )
                ).all()
            }
            assert "result_definition_id" not in columns
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT call_id, code, label, details FROM call_outcomes "
                            "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            by_call = {str(row["call_id"]): row for row in rows}
            assert by_call[known_call_id]["label"] == "Старое успешное название"
            assert by_call[unknown_call_id]["code"] == "legacy_vendor_value"
            assert by_call[unknown_call_id]["details"] == {"source": "legacy"}
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
            "The call result migration test accepts only local PostgreSQL"
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
    tenant_id, project_id, catalog_id, known_call_id, unknown_call_id = (
        str(uuid4()) for _ in range(5)
    )
    database_name = app_url.database or ""
    print(
        f"Preparing isolated call result migration database {database_name!r}",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(
            seed_legacy_data(
                migration_url,
                tenant_id=tenant_id,
                project_id=project_id,
                catalog_id=catalog_id,
                known_call_id=known_call_id,
                unknown_call_id=unknown_call_id,
            )
        )
        alembic("head", environment)
        asyncio.run(
            verify_upgrade(
                migration_url,
                tenant_id=tenant_id,
                project_id=project_id,
                catalog_id=catalog_id,
                known_call_id=known_call_id,
                unknown_call_id=unknown_call_id,
            )
        )
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(
            verify_downgrade(
                migration_url,
                tenant_id=tenant_id,
                known_call_id=known_call_id,
                unknown_call_id=unknown_call_id,
            )
        )
        print("Legacy and unknown call result preservation check passed", flush=True)
        return 0
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated call result migration database {database_name!r}",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
