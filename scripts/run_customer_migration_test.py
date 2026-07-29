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

OLD_HEAD = "d35b9f7a4c21"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    source_database = url.database or ""
    return url.set(database=f"{source_database}_customer_migration_test")


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


async def seed_old_customer(
    database_url: URL,
    *,
    tenant_id: str,
    settings_id: str,
    project_id: str,
    customer_id: str,
    contact_id: str,
    call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, slug, status, is_demo)
                    VALUES (:tenant_id, 'Customer migration tenant', 'customer-migration', 'active', false)
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
                      :project_id, :tenant_id, 'Existing customer project', '', 'active', 2,
                      '{}'::json, true, 3, '[15, 60]'::json, '{}'::json
                    )
                    """
                ),
                {"project_id": project_id, "tenant_id": tenant_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO customers (
                      id, tenant_id, project_id, display_name, external_reference,
                      preferred_language, is_anonymized, status, custom_fields
                    ) VALUES (
                      :customer_id, :tenant_id, :project_id, 'Existing customer', ' Legacy-42 ',
                      'ru', false, 'new', '{"segment": "retail"}'::json
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
                    INSERT INTO customer_contacts (
                      id, tenant_id, project_id, customer_id, kind,
                      normalized_value, display_value, is_primary
                    ) VALUES (
                      :contact_id, :tenant_id, :project_id, :customer_id, 'phone',
                      '+998901112233', '+998901112233', true
                    )
                    """
                ),
                {
                    "contact_id": contact_id,
                    "customer_id": customer_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO calls (
                      id, tenant_id, project_id, channel, status, direction, customer_id,
                      duration_seconds, provider, is_demo
                    ) VALUES (
                      :call_id, :tenant_id, :project_id, 'development_simulator', 'completed',
                      'outbound', :customer_id, 30, 'mock', true
                    )
                    """
                ),
                {
                    "call_id": call_id,
                    "customer_id": customer_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
    finally:
        await engine.dispose()


async def verify_customer(
    database_url: URL,
    *,
    tenant_id: str,
    project_id: str,
    customer_id: str,
    contact_id: str,
    call_id: str,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            customer = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT display_name, external_reference, external_reference_normalized,
                                   preferred_language, status, custom_fields, city, region, address,
                                   job_title, organization, tags, description, source,
                                   assigned_user_id, archived_at
                            FROM customers
                            WHERE tenant_id = :tenant_id AND project_id = :project_id AND id = :customer_id
                            """
                        ),
                        {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "customer_id": customer_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            assert customer["display_name"] == "Existing customer"
            assert customer["external_reference"] == " Legacy-42 "
            assert customer["external_reference_normalized"] == "legacy-42"
            assert customer["preferred_language"] == "ru"
            assert customer["status"] == "new"
            assert customer["custom_fields"] == {"segment": "retail"}
            assert customer["tags"] == []
            assert customer["description"] == ""
            assert customer["city"] is None
            assert customer["archived_at"] is None
            contact = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT normalized_value, display_value, label, is_primary
                        FROM customer_contacts
                        WHERE tenant_id = :tenant_id AND id = :contact_id
                        """
                        ),
                        {"tenant_id": tenant_id, "contact_id": contact_id},
                    )
                )
                .mappings()
                .one()
            )
            assert contact["normalized_value"] == "+998901112233"
            assert contact["display_value"] == "+998901112233"
            assert contact["label"] is None
            assert contact["is_primary"] is True
            call_customer_id = (
                await connection.execute(
                    text(
                        "SELECT customer_id FROM calls WHERE tenant_id = :tenant_id AND id = :call_id"
                    ),
                    {"tenant_id": tenant_id, "call_id": call_id},
                )
            ).scalar_one()
            assert str(call_customer_id) == customer_id
            definition = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT field_type, is_active FROM customer_field_definitions
                        WHERE tenant_id = :tenant_id AND project_id = :project_id AND key = 'segment'
                        """
                        ),
                        {"tenant_id": tenant_id, "project_id": project_id},
                    )
                )
                .mappings()
                .one()
            )
            assert definition["field_type"] == "text"
            assert definition["is_active"] is True
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
        raise RuntimeError("The customer migration test accepts only local PostgreSQL")

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
    identifiers = [str(uuid4()) for _ in range(6)]
    tenant_id, settings_id, project_id, customer_id, contact_id, call_id = identifiers
    print(
        f"Preparing isolated customer migration database {database_name!r}", flush=True
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic_upgrade(OLD_HEAD, environment)
        asyncio.run(
            seed_old_customer(
                migration_url,
                tenant_id=tenant_id,
                settings_id=settings_id,
                project_id=project_id,
                customer_id=customer_id,
                contact_id=contact_id,
                call_id=call_id,
            )
        )
        alembic_upgrade("head", environment)
        asyncio.run(
            verify_customer(
                migration_url,
                tenant_id=tenant_id,
                project_id=project_id,
                customer_id=customer_id,
                contact_id=contact_id,
                call_id=call_id,
            )
        )
        print("Existing customer and call preservation check passed", flush=True)
        return 0
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated customer migration database {database_name!r}",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
