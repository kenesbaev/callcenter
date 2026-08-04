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

OLD_HEAD = "o47g2l8j6h30"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_knowledge_migration_test")


async def seed_legacy_knowledge(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_sources
                        (id, tenant_id, name, source_type, status, created_at, updated_at)
                    VALUES (:source, :tenant, 'Legacy support', 'text', 'configured', now(), now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    "UPDATE projects SET knowledge_source_id=:source "
                    "WHERE tenant_id=:tenant AND id=:project"
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_documents
                        (id, tenant_id, source_id, title, language, content, content_hash,
                         is_active, created_at, updated_at)
                    VALUES (:document, :tenant, :source, 'Legacy hours', 'ru',
                            'Поддержка работает с девяти до восемнадцати.',
                            :document_hash, true, now(), now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_chunks
                        (id, tenant_id, document_id, ordinal, content, token_count,
                         embedding_ref, created_at, updated_at)
                    VALUES (:chunk, :tenant, :document, 0,
                            'Поддержка работает с девяти до восемнадцати.', 6,
                            NULL, now(), now())
                    """
                ),
                ids,
            )
    finally:
        await engine.dispose()


async def verify(database_url: URL, ids: dict[str, str], *, upgraded: bool) -> None:
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
                    text("SELECT count(*) FROM knowledge_sources WHERE id=:source"), ids
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM knowledge_documents WHERE id=:document"),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM knowledge_chunks WHERE id=:chunk"), ids
                )
                == 1
            )
            revision_table = bool(
                await connection.scalar(
                    text(
                        "SELECT to_regclass('public.knowledge_base_revisions') IS NOT NULL"
                    )
                )
            )
            assert revision_table is upgraded
            if not upgraded:
                return
            extension = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            assert extension == "0.8.2"
            revision = (
                await connection.execute(
                    text(
                        "SELECT id, status, version FROM knowledge_base_revisions "
                        "WHERE tenant_id=:tenant AND knowledge_source_id=:source"
                    ),
                    ids,
                )
            ).one()
            assert revision.status == "published"
            assert revision.version == 1
            version = (
                await connection.execute(
                    text(
                        "SELECT status, title_snapshot, normalized_text "
                        "FROM knowledge_document_versions WHERE tenant_id=:tenant "
                        "AND document_id=:document"
                    ),
                    ids,
                )
            ).one()
            assert version.status == "ready"
            assert version.title_snapshot == "Legacy hours"
            assert "девяти" in version.normalized_text
            assert (
                await connection.scalar(
                    text(
                        "SELECT knowledge_base_revision_id IS NULL FROM calls WHERE id=:call"
                    ),
                    ids,
                )
                is True
            )
            for table in (
                "knowledge_base_revisions",
                "knowledge_document_versions",
                "document_ingestion_jobs",
                "knowledge_retrieval_executions",
            ):
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                            "WHERE oid=to_regclass(:table)"
                        ),
                        {"table": table},
                    )
                ).one()
                assert rls == (True, True)
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
        raise RuntimeError("The knowledge migration test accepts only local PostgreSQL")
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
        "source",
        "document",
        "chunk",
    )
    ids = {key: str(uuid4()) for key in keys}
    ids["document_hash"] = "a" * 64
    database_name = app_url.database or ""
    print(
        f"Preparing isolated knowledge migration database {database_name!r}", flush=True
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        asyncio.run(seed_legacy_knowledge(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify(migration_url, ids, upgraded=True))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify(migration_url, ids, upgraded=False))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated knowledge migration database {database_name!r}",
            flush=True,
        )
    print("Knowledge migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
