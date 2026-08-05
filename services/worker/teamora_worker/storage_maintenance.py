from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import cast
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import urllib3
from minio import Minio
from minio.datatypes import Object as MinioObject
from minio.error import S3Error

from teamora_worker.background_jobs import (
    BackgroundJobClaim,
    JobExecutionContext,
    JobExecutionError,
    JobHandler,
    JobResult,
)
from teamora_worker.config import WorkerSettings


@dataclass(frozen=True)
class StoredObject:
    id: UUID
    project_id: UUID | None
    bucket: str
    object_key: str
    checksum_sha256: str | None
    size_bytes: int
    status: str
    last_verified_at: datetime | None


def _next_object_page(
    objects: Iterator[MinioObject],
    page_size: int,
) -> list[MinioObject]:
    return list(islice(objects, page_size))


class StorageMaintenance:
    """Tenant-scoped MinIO reconciliation and conservative retention handlers."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        parsed = urlparse(settings.minio_endpoint)
        endpoint = parsed.netloc or parsed.path
        self.client = Minio(
            endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=parsed.scheme == "https",
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5.0, read=10.0),
                retries=False,
            ),
        )

    def registry(self) -> dict[str, JobHandler]:
        return {
            "storage.orphan_scan": self.consistency_scan,
            "storage.temporary_cleanup": self.mark_expired_temporary_objects,
            "retention.eligibility_scan": self.retention_eligibility_scan,
            "retention.purge": self.purge_due_candidates,
        }

    async def consistency_scan(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        objects = await self._registered_objects(context, job.tenant_id)
        detected = 0
        resolved = 0
        for index, item in enumerate(objects, start=1):
            context.ensure_active()
            try:
                stat = await asyncio.wait_for(
                    asyncio.to_thread(self.client.stat_object, item.bucket, item.object_key),
                    timeout=10,
                )
            except S3Error as exc:
                if exc.code == "NoSuchBucket":
                    raise JobExecutionError(
                        "storage_unavailable",
                        "Private object storage bucket is unavailable",
                        retryable=True,
                    ) from exc
                if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                    raise JobExecutionError(
                        "storage_unavailable",
                        "Object storage consistency check failed",
                        retryable=True,
                    ) from exc
                await self._record_issue(
                    context,
                    job,
                    item,
                    issue_type="missing_object",
                    metadata={},
                )
                detected += 1
                continue
            except TimeoutError as exc:
                raise JobExecutionError(
                    "storage_timeout", "Object storage consistency check timed out", retryable=True
                ) from exc
            stored_checksum = None
            for key in ("x-amz-meta-sha256", "sha256"):
                candidate = stat.metadata.get(key) if stat.metadata else None
                if candidate:
                    stored_checksum = str(candidate)
                    break
            actual_size = int(stat.size or 0)
            if actual_size != item.size_bytes:
                await self._record_issue(
                    context,
                    job,
                    item,
                    issue_type="size_mismatch",
                    metadata={"expected_size": item.size_bytes, "actual_size": actual_size},
                )
                detected += 1
            elif (
                item.checksum_sha256 is not None
                and stored_checksum is not None
                and stored_checksum != item.checksum_sha256
            ):
                await self._record_issue(
                    context,
                    job,
                    item,
                    issue_type="checksum_mismatch",
                    metadata={},
                )
                detected += 1
            else:
                resolved += await self._mark_verified(context, job, item)
            if index % 25 == 0:
                await context.update_progress(
                    min(70, int(index * 70 / max(1, len(objects)))),
                    detail={"stage": "registered_objects"},
                )

        listed_count = 0
        async for listed in self._list_tenant_objects(job.tenant_id):
            context.ensure_active()
            listed_count += len(listed)
            by_bucket: dict[str, list[str]] = {}
            for bucket, object_name, _size in listed:
                by_bucket.setdefault(bucket, []).append(object_name)
            registered_keys = {
                (bucket, object_name)
                for bucket, object_names in by_bucket.items()
                for object_name in await self._lookup_registered_object_keys(
                    context,
                    job.tenant_id,
                    bucket=bucket,
                    object_keys=object_names,
                )
            }
            for bucket, object_name, size in listed:
                context.ensure_active()
                if (bucket, object_name) in registered_keys:
                    continue
                await self._record_orphan(context, job, bucket, object_name, size)
                detected += 1
        for bucket, object_name in await self._list_incomplete_tenant_uploads(job.tenant_id):
            context.ensure_active()
            await self._record_unregistered_issue(
                context,
                job,
                bucket=bucket,
                object_key=object_name,
                issue_type="incomplete_multipart",
                metadata={},
            )
            detected += 1
        await context.update_progress(95, detail={"stage": "orphan_candidates"})
        return JobResult(
            {
                "checked_objects": len(objects),
                "listed_objects": listed_count,
                "issues_detected": detected,
                "issues_resolved": resolved,
            }
        )

    async def mark_expired_temporary_objects(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        context.ensure_active()
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                rows = await connection.fetch(
                    """
                    SELECT id, project_id, bucket, object_key
                    FROM storage_objects
                    WHERE tenant_id=$1 AND category='temporary_preview'
                      AND expires_at <= now() AND status NOT IN ('purged','pending_purge')
                    ORDER BY expires_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 500
                    """,
                    job.tenant_id,
                )
                for row in rows:
                    await _upsert_issue(
                        connection,
                        tenant_id=job.tenant_id,
                        project_id=row["project_id"],
                        storage_object_id=row["id"],
                        issue_type="expired_temporary",
                        bucket=row["bucket"],
                        object_key=row["object_key"],
                        metadata={},
                        grace_days=self.settings.storage_consistency_grace_days,
                    )
        return JobResult({"eligible_temporary_objects": len(rows)})

    async def retention_eligibility_scan(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        """Create reversible candidates only; this handler never deletes content."""

        context.ensure_active()
        created = 0
        pending = 0
        requested_policy_id = _optional_uuid(job.safe_payload.get("policy_id"))
        requested_policy_version = _optional_int(job.safe_payload.get("policy_version"))
        preview_id = _optional_uuid(job.safe_payload.get("preview_id"))
        if preview_id is not None:
            if requested_policy_id is None or requested_policy_version is None:
                raise JobExecutionError(
                    "retention_preview_invalid",
                    "Retention run is missing its reviewed policy snapshot",
                    retryable=False,
                )
            return await self._promote_reviewed_candidates(
                job,
                context,
                preview_id=preview_id,
                policy_id=requested_policy_id,
                policy_version=requested_policy_version,
            )
        confirmed_run = False
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                created += await self._reconsider_blocked_candidates(
                    connection,
                    job,
                )
                rows = await connection.fetch(
                    """
                    SELECT object.id, object.project_id, object.category,
                           object.owner_aggregate_type, object.owner_aggregate_id,
                           object.created_at, object.archived_at, object.expires_at,
                           policy.id AS policy_id, policy.policy_version,
                           policy.grace_period_days,
                           CASE object.category
                             WHEN 'call_recording' THEN policy.recording_days
                             WHEN 'import_source' THEN policy.temporary_import_days
                             WHEN 'temporary_preview' THEN policy.temporary_import_days
                             WHEN 'import_report' THEN policy.import_report_days
                             WHEN 'generated_report' THEN policy.import_report_days
                             WHEN 'knowledge_original' THEN policy.archived_knowledge_days
                             ELSE NULL
                           END AS retention_days
                    FROM storage_objects AS object
                    JOIN LATERAL (
                      SELECT candidate.* FROM retention_policies AS candidate
                      WHERE candidate.tenant_id=object.tenant_id
                        AND candidate.enabled=true
                        AND (candidate.project_id=object.project_id OR candidate.project_id IS NULL)
                        AND ($3::uuid IS NULL OR candidate.id=$3)
                        AND ($4::integer IS NULL OR candidate.policy_version=$4)
                      ORDER BY (candidate.project_id IS NOT NULL) DESC
                      LIMIT 1
                    ) AS policy ON true
                    WHERE object.tenant_id=$1 AND object.legal_hold=false
                      AND object.status IN ('active','archived')
                      AND (
                        object.retention_state='retained' OR
                        ($5::boolean AND object.retention_state='eligible')
                      )
                      AND (
                        object.category <> 'knowledge_original' OR object.status='archived'
                      )
                      AND COALESCE(object.archived_at, object.created_at) <= now() - (
                        CASE object.category
                          WHEN 'call_recording' THEN policy.recording_days
                          WHEN 'import_source' THEN policy.temporary_import_days
                          WHEN 'temporary_preview' THEN policy.temporary_import_days
                          WHEN 'import_report' THEN policy.import_report_days
                          WHEN 'generated_report' THEN policy.import_report_days
                          WHEN 'knowledge_original' THEN policy.archived_knowledge_days
                          ELSE NULL
                        END * interval '1 day'
                      )
                      AND (object.expires_at IS NULL OR object.expires_at <= now())
                      AND NOT EXISTS (
                        SELECT 1 FROM retention_candidates AS existing
                        WHERE existing.tenant_id=object.tenant_id
                          AND existing.storage_object_id=object.id
                          AND existing.policy_id=policy.id
                          AND existing.policy_version=policy.policy_version
                      )
                    ORDER BY object.created_at, object.id
                    FOR UPDATE OF object SKIP LOCKED
                    LIMIT $2
                    """,
                    job.tenant_id,
                    self.settings.retention_scan_batch_size,
                    requested_policy_id,
                    requested_policy_version,
                    confirmed_run,
                )
                for row in rows:
                    days = row["retention_days"]
                    if days is None or not _old_enough(row, int(days)):
                        continue
                    if await _has_live_reference(connection, job.tenant_id, row):
                        await self._insert_blocked_candidate(
                            connection,
                            job,
                            row,
                            reason="live_reference_or_hold",
                        )
                        continue
                    idempotency_key = (
                        f"retention:{row['policy_id']}:{row['policy_version']}:{row['id']}"
                    )
                    candidate_id = await connection.fetchval(
                        """
                        INSERT INTO retention_candidates
                            (id, tenant_id, project_id, policy_id, storage_object_id,
                             category, resource_type, resource_id, policy_version,
                             status, eligible_at, pending_purge_at, grace_until,
                             idempotency_key, safe_metadata,
                             lock_version, created_at, updated_at)
                        VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,
                                CASE WHEN $10 THEN 'pending_purge' ELSE 'eligible' END,
                                now(),CASE WHEN $10 THEN now() ELSE NULL END,
                                CASE WHEN $10
                                  THEN now()+($11::integer * interval '1 day')
                                  ELSE NULL END,
                                $9,'{}'::jsonb,1,now(),now())
                        ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                        SET status=CASE WHEN $10 THEN 'pending_purge' ELSE 'eligible' END,
                            pending_purge_at=CASE WHEN $10 THEN now() ELSE NULL END,
                            grace_until=CASE WHEN $10
                              THEN now()+($11::integer * interval '1 day') ELSE NULL END,
                            lock_version=retention_candidates.lock_version+1,
                            updated_at=now()
                        WHERE retention_candidates.status='blocked'
                           OR ($10 AND retention_candidates.status='eligible')
                        RETURNING id
                        """,
                        job.tenant_id,
                        row["project_id"],
                        row["policy_id"],
                        row["id"],
                        row["category"],
                        row["owner_aggregate_type"] or "storage_object",
                        row["owner_aggregate_id"] or row["id"],
                        row["policy_version"],
                        idempotency_key,
                        confirmed_run,
                        row["grace_period_days"],
                    )
                    if candidate_id is None:
                        continue
                    await connection.execute(
                        """
                        UPDATE storage_objects
                        SET retention_state=CASE WHEN $3 THEN 'pending_purge' ELSE 'eligible' END,
                            status=CASE WHEN $3 THEN 'pending_purge' ELSE status END,
                            pending_purge_at=CASE WHEN $3 THEN now() ELSE pending_purge_at END,
                            lock_version=lock_version+1, updated_at=now()
                        WHERE tenant_id=$1 AND id=$2
                        """,
                        job.tenant_id,
                        row["id"],
                        confirmed_run,
                    )
                    created += 1
                    pending += int(confirmed_run)
                database_created, database_pending = await self._scan_database_retention(
                    connection,
                    job,
                    requested_policy_id=requested_policy_id,
                    requested_policy_version=requested_policy_version,
                    confirmed_run=confirmed_run,
                )
                created += database_created
                pending += database_pending
                await _insert_storage_realtime(
                    connection,
                    job,
                    "retention.preview_ready",
                    {"candidates": created, "pending_purge": pending},
                )
        return JobResult(
            {"retention_candidates_created": created, "pending_purge_candidates": pending}
        )

    async def _promote_reviewed_candidates(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        *,
        preview_id: UUID,
        policy_id: UUID,
        policy_version: int,
    ) -> JobResult:
        """Promote only the exact candidate set materialized by an admin preview."""

        promoted = 0
        blocked = 0
        while True:
            context.ensure_active()
            async with context.pool.acquire() as connection:
                async with connection.transaction():
                    await _set_tenant(connection, job.tenant_id)
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"retention-tenant:{job.tenant_id}",
                    )
                    candidate_ids = await connection.fetch(
                        """
                        SELECT candidate.id
                        FROM retention_candidates AS candidate
                        WHERE candidate.tenant_id=$1
                          AND candidate.policy_id=$2
                          AND candidate.policy_version=$3
                          AND candidate.status='eligible'
                          AND candidate.safe_metadata->>'preview_id'=$4
                        ORDER BY candidate.eligible_at, candidate.id
                        FOR UPDATE SKIP LOCKED
                        LIMIT $5
                        """,
                        job.tenant_id,
                        policy_id,
                        policy_version,
                        str(preview_id),
                        self.settings.retention_scan_batch_size,
                    )
                    if not candidate_ids:
                        break
                    for candidate in candidate_ids:
                        row = await self._load_candidate_for_recheck(
                            connection,
                            job,
                            candidate["id"],
                            expected_status="eligible",
                        )
                        if row is None or not await self._candidate_is_current_and_eligible(
                            connection,
                            job,
                            row,
                        ):
                            await self._block_candidate(
                                connection,
                                job.tenant_id,
                                candidate["id"],
                                row["object_id"] if row is not None else None,
                            )
                            blocked += 1
                            continue
                        updated = await connection.fetchval(
                            """
                            UPDATE retention_candidates
                            SET status='pending_purge', pending_purge_at=now(),
                                grace_until=now()+($4::integer * interval '1 day'),
                                lock_version=lock_version+1, updated_at=now()
                            WHERE tenant_id=$1 AND id=$2 AND status='eligible'
                              AND safe_metadata->>'preview_id'=$3
                            RETURNING id
                            """,
                            job.tenant_id,
                            candidate["id"],
                            str(preview_id),
                            row["grace_period_days"],
                        )
                        if updated is None:
                            continue
                        if row["object_id"] is not None:
                            await connection.execute(
                                """
                                UPDATE storage_objects
                                SET retention_state='pending_purge', status='pending_purge',
                                    pending_purge_at=now(), lock_version=lock_version+1,
                                    updated_at=now()
                                WHERE tenant_id=$1 AND id=$2
                                  AND retention_state IN ('retained','eligible')
                                  AND status IN ('active','archived')
                                """,
                                job.tenant_id,
                                row["object_id"],
                            )
                        promoted += 1
            await context.update_progress(
                min(95, 5 + promoted),
                detail={"promoted": promoted, "blocked": blocked},
            )
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await _insert_storage_realtime(
                    connection,
                    job,
                    "retention.run_completed",
                    {"pending_purge": promoted, "blocked": blocked},
                )
        return JobResult(
            {
                "retention_candidates_created": 0,
                "pending_purge_candidates": promoted,
                "blocked_candidates": blocked,
                "preview_id": str(preview_id),
            }
        )

    async def _insert_blocked_candidate(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        row: asyncpg.Record,
        *,
        reason: str,
    ) -> None:
        idempotency_key = f"retention:{row['policy_id']}:{row['policy_version']}:{row['id']}"
        await connection.execute(
            """
            INSERT INTO retention_candidates
                (id, tenant_id, project_id, policy_id, storage_object_id,
                 category, resource_type, resource_id, policy_version,
                 status, eligible_at, idempotency_key, safe_metadata,
                 lock_version, created_at, updated_at)
            VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,
                    'blocked',now(),$9,$10::jsonb,1,now(),now())
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            """,
            job.tenant_id,
            row["project_id"],
            row["policy_id"],
            row["id"],
            row["category"],
            row["owner_aggregate_type"] or "storage_object",
            row["owner_aggregate_id"] or row["id"],
            row["policy_version"],
            idempotency_key,
            json.dumps({"blocked_reason": reason}, separators=(",", ":")),
        )

    async def _reconsider_blocked_candidates(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
    ) -> int:
        candidate_ids = await connection.fetch(
            """
            SELECT id FROM retention_candidates
            WHERE tenant_id=$1 AND status='blocked'
            ORDER BY updated_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT $2
            """,
            job.tenant_id,
            self.settings.retention_scan_batch_size,
        )
        restored = 0
        for candidate in candidate_ids:
            row = await self._load_candidate_for_recheck(
                connection,
                job,
                candidate["id"],
                expected_status="blocked",
            )
            if row is None:
                continue
            if not await self._candidate_is_current_and_eligible(connection, job, row):
                await connection.execute(
                    """
                    UPDATE retention_candidates
                    SET updated_at=now(), lock_version=lock_version+1
                    WHERE tenant_id=$1 AND id=$2 AND status='blocked'
                    """,
                    job.tenant_id,
                    candidate["id"],
                )
                continue
            updated = await connection.fetchval(
                """
                UPDATE retention_candidates
                SET status='eligible', safe_metadata=safe_metadata-'blocked_reason',
                    pending_purge_at=NULL, grace_until=NULL,
                    lock_version=lock_version+1, updated_at=now()
                WHERE tenant_id=$1 AND id=$2 AND status='blocked'
                RETURNING id
                """,
                job.tenant_id,
                candidate["id"],
            )
            if updated is None:
                continue
            if row["object_id"] is not None:
                await connection.execute(
                    """
                    UPDATE storage_objects
                    SET retention_state='eligible', lock_version=lock_version+1,
                        updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                      AND status IN ('active','archived')
                      AND retention_state='retained'
                    """,
                    job.tenant_id,
                    row["object_id"],
                )
            restored += 1
        return restored

    async def _scan_database_retention(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        *,
        requested_policy_id: UUID | None,
        requested_policy_version: int | None,
        confirmed_run: bool,
    ) -> tuple[int, int]:
        """Create two-phase candidates for content that is stored in PostgreSQL.

        Calls, outcomes and usage rows are never candidates. Transcript purge
        only redacts segment text, while completed job purge only removes safe
        payload/result details and attempt history.
        """

        rows = await connection.fetch(
            """
            WITH resources AS (
              SELECT call.project_id, 'transcript'::varchar AS category,
                     'call_transcript'::varchar AS resource_type, call.id AS resource_id,
                     call.ended_at AS aged_at, policy.id AS policy_id,
                     policy.policy_version, policy.grace_period_days,
                     policy.transcript_days AS retention_days
              FROM calls AS call
              JOIN LATERAL (
                SELECT candidate.* FROM retention_policies AS candidate
                WHERE candidate.tenant_id=call.tenant_id AND candidate.enabled=true
                  AND (candidate.project_id=call.project_id OR candidate.project_id IS NULL)
                  AND ($2::uuid IS NULL OR candidate.id=$2)
                  AND ($3::integer IS NULL OR candidate.policy_version=$3)
                ORDER BY (candidate.project_id IS NOT NULL) DESC LIMIT 1
              ) AS policy ON true
              WHERE call.tenant_id=$1
                AND call.status IN ('completed','busy','no_answer','failed','cancelled')
                AND call.ended_at IS NOT NULL
                AND policy.transcript_days IS NOT NULL
                AND call.ended_at <= now()-(policy.transcript_days * interval '1 day')
                AND EXISTS (
                  SELECT 1 FROM transcript_segments AS segment
                  WHERE segment.tenant_id=call.tenant_id AND segment.call_id=call.id
                    AND segment.retention_redacted_at IS NULL
                )
              UNION ALL
              SELECT background.project_id,
                     CASE WHEN background.status='completed'
                       THEN 'completed_job' ELSE 'failed_job' END,
                     'background_job', background.id,
                     COALESCE(background.completed_at, background.cancelled_at,
                              background.updated_at),
                     policy.id, policy.policy_version, policy.grace_period_days,
                     CASE WHEN background.status='completed'
                       THEN policy.completed_job_days ELSE policy.failed_job_days END
              FROM background_jobs AS background
              JOIN LATERAL (
                SELECT candidate.* FROM retention_policies AS candidate
                WHERE candidate.tenant_id=background.tenant_id AND candidate.enabled=true
                  AND (candidate.project_id=background.project_id OR candidate.project_id IS NULL)
                  AND ($2::uuid IS NULL OR candidate.id=$2)
                  AND ($3::integer IS NULL OR candidate.policy_version=$3)
                ORDER BY (candidate.project_id IS NOT NULL) DESC LIMIT 1
              ) AS policy ON true
              WHERE background.tenant_id=$1 AND background.id<>$4
                AND background.status IN ('completed','failed','dead_letter','cancelled')
                AND CASE WHEN background.status='completed'
                      THEN policy.completed_job_days ELSE policy.failed_job_days END IS NOT NULL
                AND COALESCE(background.completed_at, background.cancelled_at,
                             background.updated_at) <= now()-(
                      CASE WHEN background.status='completed'
                        THEN policy.completed_job_days ELSE policy.failed_job_days END
                      * interval '1 day'
                    )
              UNION ALL
              SELECT event.project_id, 'realtime_event', 'realtime_event', event.id,
                     event.created_at, policy.id, policy.policy_version,
                     policy.grace_period_days, policy.realtime_event_days
              FROM realtime_events AS event
              JOIN LATERAL (
                SELECT candidate.* FROM retention_policies AS candidate
                WHERE candidate.tenant_id=event.tenant_id AND candidate.enabled=true
                  AND (candidate.project_id=event.project_id OR candidate.project_id IS NULL)
                  AND ($2::uuid IS NULL OR candidate.id=$2)
                  AND ($3::integer IS NULL OR candidate.policy_version=$3)
                ORDER BY (candidate.project_id IS NOT NULL) DESC LIMIT 1
              ) AS policy ON true
              WHERE event.tenant_id=$1 AND event.publish_status='published'
                AND policy.realtime_event_days IS NOT NULL
                AND event.created_at <= now()-(policy.realtime_event_days * interval '1 day')
            )
            SELECT resource.* FROM resources AS resource
            WHERE NOT EXISTS (
              SELECT 1 FROM retention_candidates AS existing
              WHERE existing.tenant_id=$1
                AND existing.policy_id=resource.policy_id
                AND existing.policy_version=resource.policy_version
                AND existing.resource_type=resource.resource_type
                AND existing.resource_id=resource.resource_id
            )
            ORDER BY resource.aged_at, resource.resource_id
            LIMIT $5
            """,
            job.tenant_id,
            requested_policy_id,
            requested_policy_version,
            job.id,
            self.settings.retention_scan_batch_size,
        )
        created = pending = 0
        for row in rows:
            if await _database_resource_held(connection, job.tenant_id, row):
                idempotency_key = (
                    f"retention:{row['policy_id']}:{row['policy_version']}:"
                    f"{row['category']}:{row['resource_id']}"
                )
                await connection.execute(
                    """
                    INSERT INTO retention_candidates
                        (id, tenant_id, project_id, policy_id, storage_object_id,
                         category, resource_type, resource_id, policy_version,
                         status, eligible_at, idempotency_key, safe_metadata,
                         lock_version, created_at, updated_at)
                    VALUES (gen_random_uuid(),$1,$2,$3,NULL,$4,$5,$6,$7,
                            'blocked',now(),$8,$9::jsonb,1,now(),now())
                    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                    """,
                    job.tenant_id,
                    row["project_id"],
                    row["policy_id"],
                    row["category"],
                    row["resource_type"],
                    row["resource_id"],
                    row["policy_version"],
                    idempotency_key,
                    '{"blocked_reason":"legal_hold"}',
                )
                continue
            idempotency_key = (
                f"retention:{row['policy_id']}:{row['policy_version']}:"
                f"{row['category']}:{row['resource_id']}"
            )
            candidate_id = await connection.fetchval(
                """
                INSERT INTO retention_candidates
                    (id, tenant_id, project_id, policy_id, storage_object_id,
                     category, resource_type, resource_id, policy_version,
                     status, eligible_at, pending_purge_at, grace_until,
                     idempotency_key, safe_metadata, lock_version, created_at, updated_at)
                VALUES (gen_random_uuid(),$1,$2,$3,NULL,$4,$5,$6,$7,
                        CASE WHEN $9 THEN 'pending_purge' ELSE 'eligible' END,
                        now(),CASE WHEN $9 THEN now() ELSE NULL END,
                        CASE WHEN $9 THEN now()+($10::integer * interval '1 day') ELSE NULL END,
                        $8,'{}'::jsonb,1,now(),now())
                ON CONFLICT (tenant_id, idempotency_key) DO UPDATE
                SET status=CASE WHEN $9 THEN 'pending_purge' ELSE 'eligible' END,
                    pending_purge_at=CASE WHEN $9 THEN now() ELSE NULL END,
                    grace_until=CASE WHEN $9
                      THEN now()+($10::integer * interval '1 day') ELSE NULL END,
                    lock_version=retention_candidates.lock_version+1,
                    updated_at=now()
                WHERE retention_candidates.status='blocked'
                   OR ($9 AND retention_candidates.status='eligible')
                RETURNING id
                """,
                job.tenant_id,
                row["project_id"],
                row["policy_id"],
                row["category"],
                row["resource_type"],
                row["resource_id"],
                row["policy_version"],
                idempotency_key,
                confirmed_run,
                row["grace_period_days"],
            )
            if candidate_id is not None:
                created += 1
                pending += int(confirmed_run)
        return created, pending

    async def purge_due_candidates(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        """Idempotent second-phase purge after policy, hold, age and reference recheck."""

        context.ensure_active()
        candidate_id = _optional_uuid(job.safe_payload.get("retention_candidate_id"))
        candidates = await self._due_purge_candidates(context, job, candidate_id)
        purged = 0
        blocked = 0
        for candidate in candidates:
            context.ensure_active()
            async with context.pool.acquire() as connection:
                async with connection.transaction():
                    await _set_tenant(connection, job.tenant_id)
                    # Hold the same transaction-scoped tenant lock used by the
                    # API for legal holds, policy edits, candidate cancellation,
                    # and Knowledge publication. The final reference recheck,
                    # external object deletion, and metadata transition are one
                    # serialized purge decision. If the process dies after the
                    # idempotent MinIO removal, the DB transaction rolls back and
                    # a retry completes the metadata transition.
                    await connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"retention-tenant:{job.tenant_id}",
                    )
                    eligible = await self._recheck_purge(
                        connection,
                        job,
                        candidate["id"],
                    )
                    if eligible is None:
                        blocked += 1
                        continue
                    if eligible["object_id"] is not None:
                        try:
                            await _run_blocking_to_completion(
                                self.client.remove_object,
                                eligible["bucket"],
                                eligible["object_key"],
                            )
                        except S3Error as exc:
                            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                                raise JobExecutionError(
                                    "storage_delete_failed",
                                    "Object deletion failed",
                                    retryable=True,
                                ) from exc
                    if eligible["object_id"] is not None:
                        updated = await self._mark_storage_purged(
                            connection,
                            job,
                            candidate["id"],
                            eligible["object_id"],
                        )
                    else:
                        updated = await self._purge_database_resource(
                            connection,
                            job,
                            candidate["id"],
                            str(eligible["category"]),
                            eligible["resource_id"],
                        )
                    if not updated:
                        raise JobExecutionError(
                            "retention_state_conflict",
                            "Retention candidate changed during purge",
                            retryable=True,
                        )
                    await _insert_audit(
                        connection,
                        job,
                        action="retention.resource_purged",
                        resource_type=str(eligible["resource_type"]),
                        resource_id=eligible["resource_id"],
                    )
                    purged += 1
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await _insert_storage_realtime(
                    connection,
                    job,
                    "retention.run_completed",
                    {"purged": purged, "blocked": blocked},
                )
        return JobResult({"purged_objects": purged, "blocked_candidates": blocked})

    async def _mark_storage_purged(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        candidate_id: UUID,
        object_id: UUID,
    ) -> bool:
        updated = await connection.fetchval(
            """
            UPDATE retention_candidates
            SET status='purged', purged_at=now(), lock_version=lock_version+1,
                updated_at=now()
            WHERE tenant_id=$1 AND id=$2 AND status='pending_purge'
              AND storage_object_id=$3
            RETURNING id
            """,
            job.tenant_id,
            candidate_id,
            object_id,
        )
        if updated is None:
            return False
        await connection.execute(
            """
            UPDATE storage_objects
            SET status='purged', retention_state='purged', purged_at=now(),
                lock_version=lock_version+1, updated_at=now()
            WHERE tenant_id=$1 AND id=$2
            """,
            job.tenant_id,
            object_id,
        )
        await connection.execute(
            """
            UPDATE call_recordings
            SET retention_state='purged', purged_at=now(), deleted_at=now(),
                storage_object_id=NULL, updated_at=now()
            WHERE tenant_id=$1 AND storage_object_id=$2
            """,
            job.tenant_id,
            object_id,
        )
        return True

    async def _purge_database_resource(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        candidate_id: UUID,
        category: str,
        resource_id: UUID,
    ) -> bool:
        if category == "transcript":
            await connection.execute(
                """
                UPDATE transcript_segments
                SET text='', retention_redacted_at=COALESCE(retention_redacted_at,now()),
                    updated_at=now()
                WHERE tenant_id=$1 AND call_id=$2 AND retention_redacted_at IS NULL
                """,
                job.tenant_id,
                resource_id,
            )
        elif category in {"completed_job", "failed_job"}:
            updated_job = await connection.fetchval(
                """
                UPDATE background_jobs
                SET safe_payload='{}'::jsonb,
                    result_metadata=jsonb_build_object('retention_payload_purged', true),
                    safe_error_message=NULL, lock_version=lock_version+1,
                    updated_at=now()
                WHERE tenant_id=$1 AND id=$2
                  AND status IN ('completed','failed','dead_letter','cancelled')
                RETURNING id
                """,
                job.tenant_id,
                resource_id,
            )
            if updated_job is None:
                return False
            await connection.execute(
                """
                DELETE FROM background_job_attempts
                WHERE tenant_id=$1 AND job_id=$2
                """,
                job.tenant_id,
                resource_id,
            )
            await connection.execute(
                """
                DELETE FROM background_job_events
                WHERE tenant_id=$1 AND job_id=$2
                """,
                job.tenant_id,
                resource_id,
            )
        elif category == "realtime_event":
            await connection.execute(
                """
                DELETE FROM realtime_events WHERE tenant_id=$1 AND id=$2
                """,
                job.tenant_id,
                resource_id,
            )
        else:
            return False
        updated = await connection.fetchval(
            """
            UPDATE retention_candidates
            SET status='purged', purged_at=now(), lock_version=lock_version+1,
                updated_at=now()
            WHERE tenant_id=$1 AND id=$2 AND status='pending_purge'
            RETURNING id
            """,
            job.tenant_id,
            candidate_id,
        )
        return updated is not None

    async def _registered_objects(
        self,
        context: JobExecutionContext,
        tenant_id: UUID,
    ) -> list[StoredObject]:
        result: list[StoredObject] = []
        cursor_id: UUID | None = None
        cursor_verified_at: datetime | None = None
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, tenant_id)
                while len(result) < self.settings.storage_scan_max_objects:
                    page_size = min(500, self.settings.storage_scan_max_objects - len(result))
                    rows = await connection.fetch(
                        """
                        SELECT id, project_id, bucket, object_key, checksum_sha256,
                               size_bytes, status, last_verified_at
                        FROM storage_objects
                        WHERE tenant_id=$1 AND status <> 'purged'
                          AND (
                            $2::uuid IS NULL
                            OR (
                              $3::timestamptz IS NULL
                              AND (
                                (last_verified_at IS NULL AND id > $2)
                                OR last_verified_at IS NOT NULL
                              )
                            )
                            OR (
                              $3::timestamptz IS NOT NULL
                              AND last_verified_at IS NOT NULL
                              AND (
                                last_verified_at > $3
                                OR (last_verified_at = $3 AND id > $2)
                              )
                            )
                          )
                        ORDER BY last_verified_at ASC NULLS FIRST, id
                        LIMIT $4
                        """,
                        tenant_id,
                        cursor_id,
                        cursor_verified_at,
                        page_size,
                    )
                    if not rows:
                        break
                    result.extend(
                        StoredObject(
                            id=row["id"],
                            project_id=row["project_id"],
                            bucket=row["bucket"],
                            object_key=row["object_key"],
                            checksum_sha256=row["checksum_sha256"],
                            size_bytes=row["size_bytes"],
                            status=row["status"],
                            last_verified_at=row["last_verified_at"],
                        )
                        for row in rows
                    )
                    cursor_id = rows[-1]["id"]
                    cursor_verified_at = rows[-1]["last_verified_at"]
                    if len(rows) < page_size:
                        break
        return result

    async def _list_tenant_objects(
        self,
        tenant_id: UUID,
    ) -> AsyncIterator[list[tuple[str, str, int]]]:
        """Stream every controlled tenant prefix through MinIO's native pagination."""

        page_size = min(500, self.settings.storage_scan_max_objects)
        for prefix in tenant_prefixes(tenant_id):
            objects: Iterator[MinioObject] = iter(
                self.client.list_objects(
                    self.settings.minio_bucket,
                    prefix=prefix,
                    recursive=True,
                )
            )
            while True:
                try:
                    page = await asyncio.wait_for(
                        asyncio.to_thread(_next_object_page, objects, page_size),
                        timeout=60,
                    )
                except TimeoutError as exc:
                    raise JobExecutionError(
                        "storage_timeout",
                        "Object listing timed out",
                        retryable=True,
                    ) from exc
                except S3Error as exc:
                    raise JobExecutionError(
                        "storage_unavailable",
                        "Object storage listing failed",
                        retryable=True,
                    ) from exc
                if not page:
                    break
                yield [
                    (
                        self.settings.minio_bucket,
                        item.object_name,
                        int(item.size or 0),
                    )
                    for item in page
                    if item.object_name is not None
                ]

    async def _lookup_registered_object_keys(
        self,
        context: JobExecutionContext,
        tenant_id: UUID,
        *,
        bucket: str,
        object_keys: list[str],
    ) -> set[str]:
        if not object_keys:
            return set()
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, tenant_id)
                rows = await connection.fetch(
                    """
                    SELECT object_key
                    FROM storage_objects
                    WHERE tenant_id=$1 AND bucket=$2
                      AND object_key = ANY($3::text[])
                      AND status <> 'purged'
                    """,
                    tenant_id,
                    bucket,
                    object_keys,
                )
        return {str(row["object_key"]) for row in rows}

    async def _list_incomplete_tenant_uploads(
        self,
        tenant_id: UUID,
    ) -> list[tuple[str, str]]:
        prefixes = tenant_prefixes(tenant_id)

        def collect() -> list[tuple[str, str]]:
            result: list[tuple[str, str]] = []
            # MinIO exposes S3 ListMultipartUploads through this pinned client
            # method but has no public iterator as of the supported 7.x line.
            method = self.client._list_multipart_uploads
            for prefix in prefixes:
                response = method(
                    self.settings.minio_bucket,
                    prefix=prefix,
                    max_uploads=min(1000, self.settings.storage_scan_max_objects),
                )
                for upload in response.uploads or []:
                    if upload.object_name:
                        result.append((self.settings.minio_bucket, upload.object_name))
            return result[: self.settings.storage_scan_max_objects]

        try:
            return await asyncio.wait_for(asyncio.to_thread(collect), timeout=30)
        except TimeoutError as exc:
            raise JobExecutionError(
                "storage_timeout", "Multipart upload listing timed out", retryable=True
            ) from exc

    async def _record_unregistered_issue(
        self,
        context: JobExecutionContext,
        job: BackgroundJobClaim,
        *,
        bucket: str,
        object_key: str,
        issue_type: str,
        metadata: dict[str, object],
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await _upsert_issue(
                    connection,
                    tenant_id=job.tenant_id,
                    project_id=None,
                    storage_object_id=None,
                    issue_type=issue_type,
                    bucket=bucket,
                    object_key=object_key,
                    metadata=metadata,
                    grace_days=self.settings.storage_consistency_grace_days,
                )
                await _insert_storage_realtime(
                    connection,
                    job,
                    "storage.issue_detected",
                    {"issue_type": issue_type},
                )

    async def _record_issue(
        self,
        context: JobExecutionContext,
        job: BackgroundJobClaim,
        item: StoredObject,
        *,
        issue_type: str,
        metadata: dict[str, object],
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                current_status = await connection.fetchval(
                    """
                    SELECT status FROM storage_objects
                    WHERE tenant_id=$1 AND id=$2
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    item.id,
                )
                if current_status is None or current_status == "purged":
                    return
                await _upsert_issue(
                    connection,
                    tenant_id=job.tenant_id,
                    project_id=item.project_id,
                    storage_object_id=item.id,
                    issue_type=issue_type,
                    bucket=item.bucket,
                    object_key=item.object_key,
                    metadata=metadata,
                    grace_days=self.settings.storage_consistency_grace_days,
                )
                await connection.execute(
                    """
                    UPDATE storage_objects
                    SET status=CASE
                          WHEN $3 AND status<>'pending_purge' THEN 'missing'
                          ELSE status
                        END,
                        missing_at=CASE WHEN $3 THEN COALESCE(missing_at,now()) ELSE missing_at END,
                        last_verified_at=now(), lock_version=lock_version+1, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    item.id,
                    issue_type == "missing_object",
                )
                await _insert_storage_realtime(
                    connection,
                    job,
                    "storage.issue_detected",
                    {"issue_type": issue_type, "storage_object_id": str(item.id)},
                )

    async def _mark_verified(
        self,
        context: JobExecutionContext,
        job: BackgroundJobClaim,
        item: StoredObject,
    ) -> int:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE storage_objects
                    SET last_verified_at=now(), missing_at=NULL,
                        status=CASE
                          WHEN status='missing' AND archived_at IS NOT NULL THEN 'archived'
                          WHEN status='missing' THEN 'active'
                          ELSE status
                        END,
                        lock_version=lock_version+1, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    item.id,
                )
                result = await connection.execute(
                    """
                    UPDATE storage_consistency_issues
                    SET status='resolved', resolved_at=now(), last_detected_at=now(),
                        lock_version=lock_version+1, updated_at=now()
                    WHERE tenant_id=$1 AND storage_object_id=$2
                      AND status IN ('open','confirmed')
                      AND issue_type IN ('missing_object','size_mismatch','checksum_mismatch')
                    """,
                    job.tenant_id,
                    item.id,
                )
        return _affected_rows(result)

    async def _record_orphan(
        self,
        context: JobExecutionContext,
        job: BackgroundJobClaim,
        bucket: str,
        object_key: str,
        size: int,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await _upsert_issue(
                    connection,
                    tenant_id=job.tenant_id,
                    project_id=None,
                    storage_object_id=None,
                    issue_type="orphan_object",
                    bucket=bucket,
                    object_key=object_key,
                    metadata={"size_bytes": size},
                    grace_days=self.settings.storage_consistency_grace_days,
                )
                await _insert_storage_realtime(
                    connection,
                    job,
                    "storage.issue_detected",
                    {"issue_type": "orphan_object"},
                )

    async def _due_purge_candidates(
        self,
        context: JobExecutionContext,
        job: BackgroundJobClaim,
        candidate_id: UUID | None,
    ) -> list[asyncpg.Record]:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                rows = await connection.fetch(
                    """
                    SELECT id FROM retention_candidates
                    WHERE tenant_id=$1 AND status='pending_purge' AND grace_until <= now()
                      AND ($2::uuid IS NULL OR id=$2)
                    ORDER BY grace_until, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT $3
                    """,
                    job.tenant_id,
                    candidate_id,
                    self.settings.retention_purge_batch_size,
                )
                return list(rows)

    async def _recheck_purge(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        candidate_id: UUID,
    ) -> asyncpg.Record | None:
        row = await self._load_candidate_for_recheck(
            connection,
            job,
            candidate_id,
            expected_status="pending_purge",
        )
        if (
            row is None
            or row["grace_until"] is None
            or row["grace_until"] > datetime.now(UTC)
            or not await self._candidate_is_current_and_eligible(connection, job, row)
        ):
            await self._block_candidate(
                connection,
                job.tenant_id,
                candidate_id,
                row["object_id"] if row is not None else None,
            )
            return None
        return row

    async def _load_candidate_for_recheck(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        candidate_id: UUID,
        *,
        expected_status: str,
    ) -> asyncpg.Record | None:
        row = await connection.fetchrow(
            """
            SELECT candidate.id, candidate.project_id, candidate.category,
                   candidate.resource_type, candidate.resource_id,
                   candidate.storage_object_id, candidate.status AS candidate_status,
                   candidate.grace_until, candidate.policy_id,
                   candidate.policy_version, candidate.safe_metadata,
                   object.id AS object_id, object.bucket, object.object_key,
                   object.category AS object_category, object.status AS object_status,
                   object.retention_state AS object_retention_state,
                   object.legal_hold AS object_legal_hold,
                   object.owner_aggregate_type, object.owner_aggregate_id,
                   object.created_at AS object_created_at,
                   object.archived_at AS object_archived_at,
                   object.expires_at AS object_expires_at,
                   policy.enabled AS policy_enabled,
                   policy.policy_version AS current_policy_version,
                   policy.grace_period_days,
                   CASE object.category
                     WHEN 'call_recording' THEN policy.recording_days
                     WHEN 'import_source' THEN policy.temporary_import_days
                     WHEN 'temporary_preview' THEN policy.temporary_import_days
                     WHEN 'import_report' THEN policy.import_report_days
                     WHEN 'generated_report' THEN policy.import_report_days
                     WHEN 'knowledge_original' THEN policy.archived_knowledge_days
                     ELSE CASE candidate.category
                       WHEN 'transcript' THEN policy.transcript_days
                       WHEN 'completed_job' THEN policy.completed_job_days
                       WHEN 'failed_job' THEN policy.failed_job_days
                       WHEN 'realtime_event' THEN policy.realtime_event_days
                       ELSE NULL
                     END
                   END AS retention_days,
                   CASE candidate.category
                     WHEN 'transcript' THEN (
                       SELECT call.ended_at FROM calls AS call
                       WHERE call.tenant_id=candidate.tenant_id
                         AND call.id=candidate.resource_id
                         AND call.status IN ('completed','busy','no_answer','failed','cancelled')
                         AND EXISTS (
                           SELECT 1 FROM transcript_segments AS segment
                           WHERE segment.tenant_id=call.tenant_id AND segment.call_id=call.id
                             AND segment.retention_redacted_at IS NULL
                         )
                     )
                     WHEN 'completed_job' THEN (
                       SELECT COALESCE(background.completed_at, background.updated_at)
                       FROM background_jobs AS background
                       WHERE background.tenant_id=candidate.tenant_id
                         AND background.id=candidate.resource_id
                         AND background.status='completed'
                     )
                     WHEN 'failed_job' THEN (
                       SELECT COALESCE(background.completed_at, background.cancelled_at,
                                       background.updated_at)
                       FROM background_jobs AS background
                       WHERE background.tenant_id=candidate.tenant_id
                         AND background.id=candidate.resource_id
                         AND background.status IN ('failed','dead_letter','cancelled')
                     )
                     WHEN 'realtime_event' THEN (
                       SELECT event.created_at FROM realtime_events AS event
                       WHERE event.tenant_id=candidate.tenant_id
                         AND event.id=candidate.resource_id
                         AND event.publish_status='published'
                     )
                     ELSE NULL
                   END AS resource_aged_at
            FROM retention_candidates AS candidate
            LEFT JOIN storage_objects AS object
              ON object.tenant_id=candidate.tenant_id
             AND object.id=candidate.storage_object_id
            LEFT JOIN retention_policies AS policy
              ON policy.tenant_id=candidate.tenant_id AND policy.id=candidate.policy_id
            WHERE candidate.tenant_id=$1 AND candidate.id=$2
              AND candidate.status=$3
            FOR UPDATE OF candidate
            """,
            job.tenant_id,
            candidate_id,
            expected_status,
        )
        if row is not None and row["object_id"] is not None:
            await connection.fetchval(
                """
                SELECT id FROM storage_objects
                WHERE tenant_id=$1 AND id=$2
                FOR UPDATE
                """,
                job.tenant_id,
                row["object_id"],
            )
        if row is not None and row["category"] in {"completed_job", "failed_job"}:
            # Serialize final retention with every retry/status command, which
            # locks the same BackgroundJob row. Recheck the status after taking
            # the lock because it may have changed after the candidate query.
            expected_statuses = (
                ("completed",)
                if row["category"] == "completed_job"
                else ("failed", "dead_letter", "cancelled")
            )
            locked_status = await connection.fetchval(
                """
                SELECT status FROM background_jobs
                WHERE tenant_id=$1 AND id=$2
                FOR UPDATE
                """,
                job.tenant_id,
                row["resource_id"],
            )
            if locked_status not in expected_statuses:
                return None
        return row

    async def _candidate_is_current_and_eligible(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        row: asyncpg.Record,
    ) -> bool:
        if (
            not row["policy_enabled"]
            or row["current_policy_version"] != row["policy_version"]
            or row["retention_days"] is None
        ):
            return False
        if row["storage_object_id"] is not None:
            if row["object_id"] is None or row["object_legal_hold"]:
                return False
            if row["candidate_status"] == "pending_purge":
                if (
                    row["object_status"] not in {"pending_purge", "missing"}
                    or row["object_retention_state"] != "pending_purge"
                ):
                    return False
            elif row["object_status"] not in {"active", "archived"} or row[
                "object_retention_state"
            ] not in {"retained", "eligible"}:
                return False
            if (
                row["object_category"] == "knowledge_original"
                and row["object_status"] != "archived"
            ):
                return False
            if not _storage_old_enough(row, int(row["retention_days"])):
                return False
        else:
            aged_at = cast(datetime | None, row["resource_aged_at"])
            if aged_at is None or datetime.now(UTC) < aged_at + _days(int(row["retention_days"])):
                return False
        return not await _candidate_blocked(connection, job.tenant_id, row)

    async def _block_candidate(
        self,
        connection: asyncpg.Connection,
        tenant_id: UUID,
        candidate_id: UUID,
        object_id: UUID | None,
    ) -> None:
        if object_id is not None:
            await connection.execute(
                """
                UPDATE storage_objects
                SET retention_state='retained',
                    status=CASE WHEN archived_at IS NOT NULL THEN 'archived' ELSE 'active' END,
                    pending_purge_at=NULL, lock_version=lock_version+1, updated_at=now()
                WHERE tenant_id=$1 AND id=$2
                  AND retention_state IN ('eligible','pending_purge')
                """,
                tenant_id,
                object_id,
            )
        await connection.execute(
            """
            UPDATE retention_candidates
            SET status='blocked', pending_purge_at=NULL, grace_until=NULL,
                lock_version=lock_version+1, updated_at=now()
            WHERE tenant_id=$1 AND id=$2 AND status IN ('eligible','pending_purge')
            """,
            tenant_id,
            candidate_id,
        )


async def _upsert_issue(
    connection: asyncpg.Connection,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
    storage_object_id: UUID | None,
    issue_type: str,
    bucket: str,
    object_key: str,
    metadata: dict[str, object],
    grace_days: int,
) -> None:
    existing = await connection.fetchrow(
        """
        SELECT id, status, grace_until FROM storage_consistency_issues
        WHERE tenant_id=$1 AND issue_type=$2 AND bucket=$3 AND object_key=$4
          AND status IN ('open','confirmed')
        FOR UPDATE
        """,
        tenant_id,
        issue_type,
        bucket,
        object_key,
    )
    if existing is None:
        await connection.execute(
            """
            INSERT INTO storage_consistency_issues
                (id, tenant_id, project_id, storage_object_id, issue_type, status,
                 bucket, object_key, safe_metadata, first_detected_at,
                 last_detected_at, grace_until, lock_version, created_at, updated_at)
            VALUES (gen_random_uuid(),$1,$2,$3,$4,'open',$5,$6,$7::jsonb,now(),now(),
                    now()+($8::integer * interval '1 day'),1,now(),now())
            """,
            tenant_id,
            project_id,
            storage_object_id,
            issue_type,
            bucket,
            object_key,
            json.dumps(metadata, separators=(",", ":")),
            grace_days,
        )
        return
    await connection.execute(
        """
        UPDATE storage_consistency_issues
        SET status=CASE WHEN grace_until <= now() THEN 'confirmed' ELSE status END,
            confirmed_at=CASE WHEN grace_until <= now() THEN COALESCE(confirmed_at,now())
                              ELSE confirmed_at END,
            safe_metadata=$3::jsonb, last_detected_at=now(),
            lock_version=lock_version+1, updated_at=now()
        WHERE tenant_id=$1 AND id=$2
        """,
        tenant_id,
        existing["id"],
        json.dumps(metadata, separators=(",", ":")),
    )


async def _has_live_reference(
    connection: asyncpg.Connection,
    tenant_id: UUID,
    row: asyncpg.Record,
) -> bool:
    owner_type = row.get("owner_aggregate_type")
    owner_id = row.get("owner_aggregate_id")
    storage_object_id = row.get("object_id") or row.get("id")
    if await connection.fetchval(
        """
        SELECT EXISTS(
          SELECT 1 FROM legal_holds
          WHERE tenant_id=$1 AND released_at IS NULL
            AND (
              scope_type='tenant' OR
              (scope_type='project' AND scope_id=$2) OR
              (scope_id=$3 AND scope_type IN ('call','customer','document'))
            )
        )
        """,
        tenant_id,
        row.get("project_id"),
        owner_id,
    ):
        return True
    if storage_object_id is not None:
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM call_recordings AS recording
              JOIN calls AS call
                ON call.tenant_id=recording.tenant_id AND call.id=recording.call_id
              JOIN legal_holds AS hold
                ON hold.tenant_id=recording.tenant_id
               AND (
                 (hold.scope_type='call' AND hold.scope_id=recording.call_id) OR
                 (hold.scope_type='customer' AND hold.scope_id=call.customer_id)
               )
               AND hold.released_at IS NULL
              WHERE recording.tenant_id=$1 AND recording.storage_object_id=$2
            )
            """,
            tenant_id,
            storage_object_id,
        ):
            return True
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM knowledge_document_versions AS version
              JOIN legal_holds AS hold
                ON hold.tenant_id=version.tenant_id
               AND hold.scope_type='document'
               AND hold.scope_id=version.document_id
               AND hold.released_at IS NULL
              WHERE version.tenant_id=$1 AND version.storage_object_id=$2
            )
            """,
            tenant_id,
            storage_object_id,
        ):
            return True
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM call_recordings AS recording
              JOIN calls AS call
                ON call.tenant_id=recording.tenant_id AND call.id=recording.call_id
              WHERE recording.tenant_id=$1 AND recording.storage_object_id=$2
                AND call.status IN ('queued','initiated','ringing','active','on_hold',
                                    'transfer_requested','transferring','transferred')
            )
            """,
            tenant_id,
            storage_object_id,
        ):
            return True
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM knowledge_document_versions AS version
              JOIN knowledge_base_revisions AS revision
                ON revision.tenant_id=version.tenant_id AND revision.id=version.revision_id
              JOIN knowledge_sources AS source
                ON source.tenant_id=revision.tenant_id
               AND source.id=revision.knowledge_source_id
              WHERE version.tenant_id=$1 AND version.storage_object_id=$2
                AND version.status<>'archived'
                AND (revision.status='draft' OR source.active_revision_id=revision.id)
            )
            """,
            tenant_id,
            storage_object_id,
        ):
            return True
    if owner_type == "call_recording" and owner_id is not None:
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM call_recordings AS recording
              JOIN calls AS call
                ON call.tenant_id=recording.tenant_id AND call.id=recording.call_id
              JOIN legal_holds AS hold
                ON hold.tenant_id=recording.tenant_id
               AND (
                 (hold.scope_type='call' AND hold.scope_id=recording.call_id) OR
                 (hold.scope_type='customer' AND hold.scope_id=call.customer_id)
               )
               AND hold.released_at IS NULL
              WHERE recording.tenant_id=$1 AND recording.id=$2
            )
            """,
            tenant_id,
            owner_id,
        ):
            return True
    if owner_type == "customer_import" and owner_id is not None:
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1 FROM customer_imports AS import_record
                  LEFT JOIN background_jobs AS job
                    ON job.tenant_id=import_record.tenant_id
                   AND job.id=import_record.background_job_id
                  WHERE import_record.tenant_id=$1 AND import_record.id=$2
                    AND (
                      import_record.status IN (
                        'processing_preview','ready','queued','running',
                        'finalizing','cancel_requested'
                      ) OR
                      job.status IN (
                        'pending','scheduled','running','retry_wait','cancel_requested'
                      )
                    )
                )
                """,
                tenant_id,
                owner_id,
            )
        )
    if owner_type == "knowledge_document_version" and owner_id is not None:
        if await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM knowledge_document_versions AS version
              JOIN legal_holds AS hold
                ON hold.tenant_id=version.tenant_id
               AND hold.scope_type='document'
               AND hold.scope_id=version.document_id
               AND hold.released_at IS NULL
              WHERE version.tenant_id=$1 AND version.id=$2
            )
            """,
            tenant_id,
            owner_id,
        ):
            return True
    if owner_type in {"call", "call_recording"} and owner_id is not None:
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1 FROM calls AS call
                  LEFT JOIN call_recordings AS recording
                    ON recording.tenant_id=call.tenant_id AND recording.call_id=call.id
                  WHERE call.tenant_id=$1
                    AND (($2='call' AND call.id=$3) OR
                         ($2='call_recording' AND recording.id=$3))
                    AND call.status IN ('queued','initiated','ringing','active','on_hold',
                                        'transfer_requested','transferring','transferred')
                )
                """,
                tenant_id,
                owner_type,
                owner_id,
            )
        )
    if owner_type in {"knowledge_document", "knowledge_document_version"} and owner_id is not None:
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1 FROM knowledge_document_versions AS version
                  JOIN knowledge_base_revisions AS revision
                    ON revision.tenant_id=version.tenant_id AND revision.id=version.revision_id
                  JOIN knowledge_sources AS source
                    ON source.tenant_id=revision.tenant_id
                   AND source.id=revision.knowledge_source_id
                  WHERE version.tenant_id=$1
                    AND (($2='knowledge_document_version' AND version.id=$3) OR
                         ($2='knowledge_document' AND version.document_id=$3))
                    AND version.status<>'archived'
                    AND (revision.status='draft' OR source.active_revision_id=revision.id)
                )
                """,
                tenant_id,
                owner_type,
                owner_id,
            )
        )
    return False


async def _candidate_blocked(
    connection: asyncpg.Connection,
    tenant_id: UUID,
    row: asyncpg.Record,
) -> bool:
    if await _database_resource_held(connection, tenant_id, row):
        return True
    if row.get("object_id") is not None:
        return await _has_live_reference(connection, tenant_id, row)
    if row.get("category") == "transcript":
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1 FROM calls
                  WHERE tenant_id=$1 AND id=$2
                    AND status IN ('queued','initiated','ringing','active','on_hold',
                                   'transfer_requested','transferring','transferred')
                )
                """,
                tenant_id,
                row.get("resource_id"),
            )
        )
    return False


async def _database_resource_held(
    connection: asyncpg.Connection,
    tenant_id: UUID,
    row: asyncpg.Record,
) -> bool:
    resource_type = row.get("resource_type")
    hold_scope = "call" if resource_type == "call_transcript" else resource_type
    if resource_type == "call_transcript":
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1
                  FROM calls AS call
                  JOIN legal_holds AS hold
                    ON hold.tenant_id=call.tenant_id
                   AND hold.released_at IS NULL
                   AND (
                     hold.scope_type='tenant' OR
                     (hold.scope_type='project' AND hold.scope_id=call.project_id) OR
                     (hold.scope_type='call' AND hold.scope_id=call.id) OR
                     (hold.scope_type='customer' AND hold.scope_id=call.customer_id)
                   )
                  WHERE call.tenant_id=$1 AND call.id=$2
                )
                """,
                tenant_id,
                row.get("resource_id"),
            )
        )
    if resource_type == "realtime_event":
        return bool(
            await connection.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1
                  FROM realtime_events AS event
                  JOIN legal_holds AS hold
                    ON hold.tenant_id=event.tenant_id
                   AND hold.released_at IS NULL
                   AND (
                     hold.scope_type='tenant' OR
                     (hold.scope_type='project' AND hold.scope_id=event.project_id) OR
                     (
                       event.aggregate_type IN ('call','customer','document')
                       AND hold.scope_type=event.aggregate_type
                       AND hold.scope_id=event.aggregate_id
                     ) OR
                     (
                       event.aggregate_type='call'
                       AND hold.scope_type='customer'
                       AND EXISTS (
                         SELECT 1 FROM calls AS call
                         WHERE call.tenant_id=event.tenant_id
                           AND call.id=event.aggregate_id
                           AND call.customer_id=hold.scope_id
                       )
                     ) OR
                     (
                       event.aggregate_type='knowledge_document'
                       AND hold.scope_type='document'
                       AND hold.scope_id=event.aggregate_id
                     ) OR
                     (
                       event.aggregate_type='knowledge_document_version'
                       AND hold.scope_type='document'
                       AND EXISTS (
                         SELECT 1 FROM knowledge_document_versions AS version
                         WHERE version.tenant_id=event.tenant_id
                           AND version.id=event.aggregate_id
                           AND version.document_id=hold.scope_id
                       )
                     )
                   )
                  WHERE event.tenant_id=$1 AND event.id=$2
                )
                """,
                tenant_id,
                row.get("resource_id"),
            )
        )
    return bool(
        await connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1 FROM legal_holds
              WHERE tenant_id=$1 AND released_at IS NULL
                AND (
                  scope_type='tenant' OR
                  (scope_type='project' AND scope_id=$2) OR
                  (scope_type=$3 AND scope_id=$4)
                )
            )
            """,
            tenant_id,
            row.get("project_id"),
            hold_scope,
            row.get("resource_id"),
        )
    )


async def _insert_storage_realtime(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    event_type: str,
    payload: dict[str, object],
) -> None:
    await connection.execute(
        """
        INSERT INTO realtime_events
            (id, tenant_id, project_id, target_membership_id, event_type,
             aggregate_type, aggregate_id, aggregate_version, safe_payload,
             occurred_at, publish_status, publish_attempts, expires_at,
             correlation_id, causation_id, created_at, updated_at)
        VALUES (gen_random_uuid(),$1,$2,NULL,$3,'background_job',$4,NULL,$5::jsonb,
                now(),'pending',0,now()+interval '24 hours',$6,$7,now(),now())
        """,
        job.tenant_id,
        job.project_id,
        event_type,
        job.id,
        json.dumps(payload, separators=(",", ":")),
        job.correlation_id,
        job.causation_id,
    )


async def _insert_audit(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
) -> None:
    correlation = job.correlation_id
    if len(correlation) > 80:
        digest = hashlib.sha256(correlation.encode()).hexdigest()[:12]
        correlation = f"{correlation[:67]}:{digest}"
    await connection.execute(
        """
        INSERT INTO audit_logs
            (id, tenant_id, actor_user_id, action, resource_type, resource_id,
             reason, correlation_id, safe_metadata, created_at, updated_at)
        VALUES (gen_random_uuid(),$1,NULL,$2,$3,$4,NULL,$5,$6::jsonb,now(),now())
        """,
        job.tenant_id,
        action,
        resource_type,
        resource_id,
        correlation,
        json.dumps({"background_job_id": str(job.id)}, separators=(",", ":")),
    )


def _old_enough(row: asyncpg.Record, days: int) -> bool:
    return _storage_old_enough(row, days)


def _storage_old_enough(row: asyncpg.Record, days: int) -> bool:
    created_at = cast(
        datetime | None,
        row.get("object_created_at", row.get("created_at")),
    )
    archived_at = cast(
        datetime | None,
        row.get("object_archived_at", row.get("archived_at")),
    )
    expires_at = cast(
        datetime | None,
        row.get("object_expires_at", row.get("expires_at")),
    )
    base = archived_at or created_at
    if base is None:
        return False
    threshold = base + _days(days)
    # Operational expiry is an additional lower bound, never a shortcut around
    # the tenant's configured minimum retention window.
    if expires_at is not None and expires_at > threshold:
        threshold = expires_at
    return datetime.now(UTC) >= threshold


def _days(value: int) -> timedelta:
    return timedelta(days=value)


def _optional_uuid(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise JobExecutionError(
            "retention_candidate_invalid",
            "Retention candidate identifier is invalid",
            retryable=False,
        ) from exc


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise JobExecutionError(
            "retention_policy_version_invalid",
            "Retention policy version is invalid",
            retryable=False,
        ) from exc


def _affected_rows(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


async def _run_blocking_to_completion(
    operation: Callable[..., object],
    *args: object,
) -> object:
    """Do not release a retention lock while a destructive SDK call is still running.

    MinIO is configured with transport-level connect/read timeouts.  Shielding
    the thread means a Worker shutdown or the outer handler timeout waits for
    that bounded SDK call to finish before the database transaction (and its
    advisory lock) can unwind.
    """

    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise


async def _set_tenant(connection: asyncpg.Connection, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


def tenant_prefixes(tenant_id: UUID) -> tuple[str, str]:
    """Only server-controlled namespaces are eligible for tenant orphan scans."""

    return f"tenants/{tenant_id}/", f"knowledge/{tenant_id}/"


def storage_issue_fingerprint(bucket: str, object_key: str, issue_type: str) -> str:
    return hashlib.sha256(f"{bucket}\0{object_key}\0{issue_type}".encode()).hexdigest()
