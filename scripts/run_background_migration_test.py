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
from run_knowledge_migration_test import seed_legacy_knowledge
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

KNOWLEDGE_BASE_HEAD = "o47g2l8j6h30"
OLD_HEAD = "p58h3m9k7i41"


def migration_test_url(source: str) -> URL:
    url = make_url(source)
    return url.set(database=f"{url.database}_background_migration_test")


async def seed_stage_13_records(database_url: URL, ids: dict[str, str]) -> None:
    """Create data in the exact schema immediately preceding Stage 14."""

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenant_settings
                      (id, tenant_id, timezone, default_language, recording_enabled,
                       recording_disclosure_required, retention_days, max_concurrent_calls,
                       created_at, updated_at)
                    SELECT gen_random_uuid(), :tenant, 'Asia/Tashkent', 'ru', false,
                           true, 90, 5, now(), now()
                    WHERE NOT EXISTS (
                      SELECT 1 FROM tenant_settings WHERE tenant_id = :tenant
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_document_versions
                    SET object_key=:knowledge_object_key
                    WHERE tenant_id=:tenant AND document_id=:document
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO document_ingestion_jobs
                      (id, tenant_id, project_id, document_version_id, status, stage,
                       attempts, max_attempts, next_attempt_at, safe_error_code,
                       idempotency_key, completed_at, created_at, updated_at)
                    SELECT :ingestion, version.tenant_id, version.project_id, version.id,
                           'failed', 'embedding', 2, 4, now(), 'legacy_embedding_error',
                           :ingestion_key, now(), now() - interval '1 hour', now()
                    FROM knowledge_document_versions AS version
                    WHERE version.tenant_id=:tenant AND version.document_id=:document
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO customer_imports
                      (id, tenant_id, project_id, created_by_user_id, file_name, file_type,
                       sheet_names, selected_sheet, source_rows, mapping, update_rule,
                       status, row_count, report, idempotency_key, expires_at,
                       committed_at, created_at, updated_at)
                    VALUES
                      (:customer_import, :tenant, :project, :user, 'legacy-customers.csv', 'csv',
                       '["Sheet1"]'::json, 'Sheet1',
                       '{"rows":[{"full_name":"Legacy Customer","phone":"+998901112233"}]}'::json,
                       '{"name":"full_name","phone":"phone"}'::json, 'skip',
                       'preview', 1, '{"errors":[]}'::json, :import_key,
                       now() + interval '1 hour', NULL, now(), now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO call_recordings
                      (id, tenant_id, call_id, storage_key, encryption_key_ref,
                       content_type, size_bytes, recording_paused_ranges, delete_after,
                       deleted_at, created_at, updated_at)
                    VALUES
                      (:recording, :tenant, :call, :recording_object_key,
                       'test-key-reference', 'audio/wav', 2048, '[]'::json,
                       now() + interval '90 days', NULL, now(), now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO transcript_segments
                      (id, tenant_id, call_id, sequence, speaker, language, text,
                       confidence, started_ms, ended_ms, created_at, updated_at)
                    VALUES
                      (:transcript, :tenant, :call, 1, 'customer', 'ru',
                       'Legacy transcript remains intact', 0.9900, 0, 1000, now(), now())
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
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM calls WHERE id=:call"), ids
                )
                == 1
            )
            legacy_import = (
                await connection.execute(
                    text(
                        """
                        SELECT status, execution_mode, progress, total_rows, valid_rows,
                               invalid_rows, mapping_snapshot, update_policy_snapshot
                        FROM customer_imports WHERE id=:customer_import
                        """
                    ),
                    ids,
                )
            ).one()
            assert legacy_import.status == "preview"
            assert legacy_import.execution_mode == "synchronous"
            assert legacy_import.progress == 0
            assert legacy_import.total_rows == 1
            assert legacy_import.valid_rows == 1
            assert legacy_import.invalid_rows == 0
            assert legacy_import.mapping_snapshot == {
                "name": "full_name",
                "phone": "phone",
            }
            assert legacy_import.update_policy_snapshot == "skip"

            background = (
                await connection.execute(
                    text(
                        """
                        SELECT id, status, attempt_count, safe_error_code
                        FROM background_jobs WHERE id=:ingestion
                        """
                    ),
                    ids,
                )
            ).one()
            assert str(background.id) == ids["ingestion"]
            assert background.status == "failed"
            assert background.attempt_count == 2
            assert background.safe_error_code == "legacy_embedding_error"
            assert (
                await connection.scalar(
                    text(
                        "SELECT background_job_id FROM document_ingestion_jobs "
                        "WHERE id=:ingestion"
                    ),
                    ids,
                )
                == background.id
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM background_job_attempts "
                        "WHERE job_id=:ingestion AND attempt_number=2"
                    ),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM background_job_events WHERE job_id=:ingestion"
                    ),
                    ids,
                )
                == 1
            )

            knowledge_storage = (
                await connection.execute(
                    text(
                        """
                        SELECT object.category, object.object_key, version.storage_object_id
                        FROM knowledge_document_versions AS version
                        JOIN storage_objects AS object
                          ON object.tenant_id=version.tenant_id
                         AND object.id=version.storage_object_id
                        WHERE version.tenant_id=:tenant AND version.document_id=:document
                        """
                    ),
                    ids,
                )
            ).one()
            assert knowledge_storage.category == "knowledge_original"
            assert knowledge_storage.object_key == ids["knowledge_object_key"]
            assert knowledge_storage.storage_object_id is not None

            recording = (
                await connection.execute(
                    text(
                        """
                        SELECT recording.retention_state, recording.storage_object_id,
                               object.category, object.size_bytes
                        FROM call_recordings AS recording
                        JOIN storage_objects AS object
                          ON object.tenant_id=recording.tenant_id
                         AND object.id=recording.storage_object_id
                        WHERE recording.id=:recording
                        """
                    ),
                    ids,
                )
            ).one()
            assert recording.retention_state == "retained"
            assert recording.storage_object_id is not None
            assert recording.category == "call_recording"
            assert recording.size_bytes == 2048
            transcript = (
                await connection.execute(
                    text(
                        "SELECT text, retention_redacted_at FROM transcript_segments "
                        "WHERE id=:transcript"
                    ),
                    ids,
                )
            ).one()
            assert transcript.text == "Legacy transcript remains intact"
            assert transcript.retention_redacted_at is None

            policy = (
                await connection.execute(
                    text(
                        """
                        SELECT enabled, policy_version, grace_period_days, recording_days
                        FROM retention_policies
                        WHERE tenant_id=:tenant AND project_id IS NULL
                        """
                    ),
                    ids,
                )
            ).one()
            assert policy.enabled is False
            assert policy.policy_version == 1
            assert policy.grace_period_days == 7
            assert policy.recording_days == 90

            rls_tables = (
                "background_jobs",
                "background_job_attempts",
                "background_job_events",
                "scheduled_jobs",
                "job_command_submissions",
                "storage_objects",
                "storage_consistency_issues",
                "retention_policies",
                "retention_candidates",
                "legal_holds",
                "customer_import_staging_rows",
            )
            for table in rls_tables:
                rls = (
                    await connection.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                            "WHERE oid=to_regclass(:table)"
                        ),
                        {"table": table},
                    )
                ).one()
                assert rls == (True, True), table
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_policies "
                            "WHERE schemaname='public' AND tablename=:table"
                        ),
                        {"table": table},
                    )
                    == 1
                ), table
    finally:
        await engine.dispose()


async def verify_downgrade(database_url: URL, ids: dict[str, str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            for table in (
                "background_jobs",
                "background_job_attempts",
                "background_job_events",
                "scheduled_jobs",
                "job_command_submissions",
                "storage_objects",
                "storage_consistency_issues",
                "retention_policies",
                "retention_candidates",
                "legal_holds",
                "customer_import_staging_rows",
            ):
                assert not bool(
                    await connection.scalar(
                        text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}
                    )
                ), table
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM document_ingestion_jobs WHERE id=:ingestion"
                    ),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM customer_imports WHERE id=:customer_import"
                    ),
                    ids,
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT status FROM customer_imports WHERE id=:customer_import"
                    ),
                    ids,
                )
                == "preview"
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
                    text("SELECT text FROM transcript_segments WHERE id=:transcript"),
                    ids,
                )
                == "Legacy transcript remains intact"
            )
            for table, columns in {
                "document_ingestion_jobs": ("background_job_id",),
                "knowledge_document_versions": ("storage_object_id",),
                "customer_imports": (
                    "execution_mode",
                    "background_job_id",
                    "source_storage_object_id",
                    "report_storage_object_id",
                    "progress",
                ),
                "call_recordings": (
                    "storage_object_id",
                    "retention_state",
                    "purged_at",
                ),
                "transcript_segments": ("retention_redacted_at",),
            }.items():
                remaining = int(
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name=:table "
                            "AND column_name = ANY(:columns)"
                        ),
                        {"table": table, "columns": list(columns)},
                    )
                    or 0
                )
                assert remaining == 0, (table, columns)
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
            "The background migration test accepts only local PostgreSQL"
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
        "node",
        "source",
        "document",
        "chunk",
        "ingestion",
        "customer_import",
        "recording",
        "transcript",
    )
    ids = {key: str(uuid4()) for key in keys}
    ids.update(
        {
            "document_hash": "a" * 64,
            "ingestion_key": f"legacy-ingestion-{uuid4()}",
            "import_key": f"legacy-import-{uuid4()}",
            "knowledge_object_key": (
                f"tenants/{ids['tenant']}/projects/{ids['project']}/knowledge/legacy.txt"
            ),
            "recording_object_key": (
                f"tenants/{ids['tenant']}/projects/{ids['project']}/recordings/legacy.wav"
            ),
        }
    )
    database_name = app_url.database or ""
    print(
        f"Preparing isolated background migration database {database_name!r}",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        alembic(KNOWLEDGE_BASE_HEAD, environment)
        asyncio.run(seed_legacy_graph(migration_url, ids))
        asyncio.run(seed_legacy_knowledge(migration_url, ids))
        alembic(OLD_HEAD, environment)
        asyncio.run(seed_stage_13_records(migration_url, ids))
        alembic("head", environment)
        asyncio.run(verify_upgrade(migration_url, ids))
        alembic(OLD_HEAD, environment, action="downgrade")
        asyncio.run(verify_downgrade(migration_url, ids))
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(
            f"Removed isolated background migration database {database_name!r}",
            flush=True,
        )
    print("Background migration preservation test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
