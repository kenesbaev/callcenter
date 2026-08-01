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

OLD_HEAD = "l14d9i5g3e07"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_team_migration_test")


async def seed_legacy_team(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO project_users "
                    "(id, tenant_id, project_id, user_id, is_active) "
                    "VALUES (:project_user, :tenant, :project, :user, true)"
                ),
                ids,
            )
            await connection.execute(
                text(
                    "INSERT INTO invitations "
                    "(id, tenant_id, email, role, token_hash, invited_by_user_id, expires_at) "
                    "VALUES (:invitation, :tenant, 'legacy-operator@example.com', "
                    "'human_operator', :token_hash, :user, now() + interval '7 days')"
                ),
                {**ids, "token_hash": "a" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, display_name, is_active, is_platform_admin) "
                    "VALUES (:orphan_user, 'former-owner@example.com', 'not-a-real-password', "
                    "'Former owner', true, false)"
                ),
                ids,
            )
            await connection.execute(
                text(
                    "INSERT INTO invitations "
                    "(id, tenant_id, email, role, token_hash, invited_by_user_id, expires_at) "
                    "VALUES (:orphan_invitation, :tenant, 'legacy-analyst@example.com', "
                    "'analyst', :token_hash, :orphan_user, now() + interval '7 days')"
                ),
                {**ids, "token_hash": "b" * 64},
            )
    finally:
        await engine.dispose()


async def verify_upgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            membership = (
                await connection.execute(
                    text(
                        "SELECT role, is_active, state_version, activated_at "
                        "FROM memberships WHERE id = :membership"
                    ),
                    ids,
                )
            ).one()
            assert membership[0] == "tenant_owner"
            assert membership[1] is True
            assert membership[2] == 1
            assert membership[3] is not None
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM project_users WHERE user_id = :user"),
                    ids,
                )
                == 1
            )
            invitation = (
                await connection.execute(
                    text(
                        "SELECT email, status, state_version, issued_at "
                        "FROM invitations WHERE id = :invitation"
                    ),
                    ids,
                )
            ).one()
            assert invitation[0] == "legacy-operator@example.com"
            assert invitation[1] == "pending"
            assert invitation[2] == 1
            assert invitation[3] is not None
            orphan_inviter = await connection.scalar(
                text(
                    "SELECT invited_by_membership_id FROM invitations "
                    "WHERE id = :orphan_invitation"
                ),
                ids,
            )
            assert orphan_inviter is None
            for table in (
                "invitation_projects",
                "operator_presences",
                "team_command_submissions",
            ):
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity "
                            "FROM pg_class WHERE relname = :table"
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
                    text("SELECT count(*) FROM memberships WHERE id = :membership"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM project_users WHERE user_id = :user"),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM invitations WHERE id = :invitation"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                        "('invitation_projects', 'operator_presences', 'team_command_submissions')"
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
        raise RuntimeError("The team migration test accepts only local PostgreSQL")
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
        "node",
        "invitation",
        "project_user",
        "orphan_user",
        "orphan_invitation",
    )
    ids = {key: str(uuid4()) for key in keys}
    database_name = app_url.database or ""
    print(f"Preparing isolated team migration database {database_name!r}", flush=True)
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        asyncio.run(seed_legacy_team(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated team migration database {database_name!r}", flush=True)
    print("Team migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
