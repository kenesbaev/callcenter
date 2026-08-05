from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
from minio import Minio
from minio.error import S3Error

from teamora_worker.background_jobs import (
    BackgroundJobClaim,
    JobCancelled,
    JobExecutionContext,
    JobExecutionError,
    JobHandler,
    JobResult,
)
from teamora_worker.config import WorkerSettings

_STANDARD_FIELDS = {
    "display_name",
    "phone",
    "alternate_phone",
    "email",
    "external_reference",
    "preferred_language",
    "status",
    "city",
    "region",
    "address",
    "job_title",
    "organization",
    "tags",
    "description",
    "source",
    "next_contact_at",
}
_CUSTOMER_STATUSES = {"new", "assigned", "callback", "completed", "do_not_call"}
_LANGUAGES = {"ru", "uz", "en", "kaa"}
_PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CUSTOMER_TEXT_LIMITS = {
    "display_name": 160,
    "external_reference": 160,
    "external_reference_normalized": 160,
    "city": 160,
    "region": 160,
    "address": 500,
    "job_title": 160,
    "organization": 200,
    "description": 4000,
    "source": 120,
}
_MAX_CONTACTS = 20
_MAX_CONTACT_LENGTH = 320
_MAX_TAGS = 30
_MAX_TAG_LENGTH = 60


@dataclass(frozen=True)
class ImportSource:
    import_id: UUID
    project_id: UUID
    created_by_user_id: UUID
    file_name: str
    file_type: str
    selected_sheet: str
    mapping: dict[str, str]
    update_rule: str
    status: str
    storage_object_id: UUID
    bucket: str
    object_key: str
    size_bytes: int
    checksum_sha256: str | None
    content_type: str


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    name: str
    field_type: str
    required: bool
    options: tuple[str, ...]
    default_value: object | None


@dataclass(frozen=True)
class PreparedContact:
    kind: str
    normalized_value: str
    display_value: str
    primary: bool


@dataclass(frozen=True)
class PreparedRow:
    row_number: int
    values: dict[str, object]
    contacts: tuple[PreparedContact, ...]
    identities: tuple[str, ...]
    errors: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class RowStream:
    headers: tuple[str, ...]
    rows: Iterator[tuple[int, dict[str, object]]]
    sheet_names: tuple[str, ...]
    selected_sheet: str


class _RowMergeConflict(Exception):
    """Roll back one staging-row savepoint after a concurrent identity claim."""


class CustomerImportProcessor:
    """Durable, staged customer import handlers.

    Parsing and validation only write tenant-scoped staging rows. The customer
    tables are changed later in one database transaction, so cancellation or a
    crash before finalization cannot expose a partial import.
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        parsed = urlparse(settings.minio_endpoint)
        self.storage = Minio(
            parsed.netloc or parsed.path,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=parsed.scheme == "https",
        )

    def registry(self) -> dict[str, JobHandler]:
        return {
            "customer_import.prepare_preview": self.prepare_preview,
            "customer_import.process": self.process_import,
        }

    async def prepare_preview(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        import_id = _payload_uuid(job.safe_payload, "import_id")
        try:
            source = await self._load_source(job, context)
            self._validate_claim_scope(job, source)
            with tempfile.TemporaryDirectory(prefix="kline-import-") as directory:
                path = Path(directory) / "source"
                await self._download_source(source, path)
                with self._open_rows(path, source) as stream:
                    preview: list[dict[str, object]] = []
                    total = 0
                    for row_number, row in stream.rows:
                        context.ensure_active()
                        total += 1
                        if total > self.settings.customer_import_max_rows:
                            raise JobExecutionError(
                                "customer_import_row_limit",
                                "Customer import exceeds the configured row limit",
                                retryable=False,
                            )
                        if len(preview) < self.settings.customer_import_preview_rows:
                            preview.append(
                                {
                                    "row_number": row_number,
                                    "values": row,
                                    "duplicate_fields": [],
                                    "errors": [],
                                }
                            )
                    await self._store_preview(
                        job,
                        context,
                        source,
                        stream.headers,
                        stream.sheet_names,
                        stream.selected_sheet,
                        preview,
                        total,
                    )
        except JobCancelled:
            await self._mark_cancelled(job, context, import_id)
            raise
        except JobExecutionError as exc:
            if not exc.retryable or job.attempt_count >= job.max_attempts:
                await self._mark_failed(job, context, import_id)
            raise
        except Exception:
            if job.attempt_count >= job.max_attempts:
                await self._mark_failed(job, context, import_id)
            raise
        await context.update_progress(100, detail={"phase": "preview_ready"})
        return JobResult({"import_id": str(source.import_id), "total_rows": total})

    async def process_import(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> JobResult:
        import_id = _payload_uuid(job.safe_payload, "import_id")
        try:
            source = await self._load_source(job, context)
            self._validate_claim_scope(job, source)
            if source.status == "completed":
                await self._write_error_report(job, context, source)
                return await self._completed_result(job, context, source.import_id)
            definitions = await self._load_definitions(job, context, source.project_id)
            with tempfile.TemporaryDirectory(prefix="kline-import-") as directory:
                path = Path(directory) / "source"
                await self._download_source(source, path)
                await self._mark_running(job, context, source.import_id)
                await self._stage_file(job, context, source, definitions, path)
                context.ensure_active()
                result = await self._finalize(job, context, source)
                context.ensure_active()
                await self._write_error_report(job, context, source)
                return result
        except JobCancelled:
            await self._mark_cancelled(job, context, import_id)
            raise
        except JobExecutionError as exc:
            if not exc.retryable or job.attempt_count >= job.max_attempts:
                await self._mark_failed(job, context, import_id)
            raise
        except Exception:
            if job.attempt_count >= job.max_attempts:
                await self._mark_failed(job, context, import_id)
            raise

    async def _load_source(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
    ) -> ImportSource:
        import_id = _payload_uuid(job.safe_payload, "import_id")
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                row = await connection.fetchrow(
                    """
                    SELECT import_record.id, import_record.project_id,
                           import_record.created_by_user_id, import_record.file_name,
                           import_record.file_type, import_record.selected_sheet,
                           import_record.mapping, import_record.update_rule,
                           import_record.status, object.id AS storage_object_id,
                           object.bucket, object.object_key, object.size_bytes,
                           object.checksum_sha256, object.content_type
                    FROM customer_imports AS import_record
                    JOIN storage_objects AS object
                      ON object.tenant_id=import_record.tenant_id
                     AND object.id=import_record.source_storage_object_id
                    WHERE import_record.tenant_id=$1 AND import_record.id=$2
                      AND object.status='active' AND object.category='import_source'
                    """,
                    job.tenant_id,
                    import_id,
                )
        if row is None:
            raise JobExecutionError(
                "customer_import_source_unavailable",
                "Customer import source is unavailable",
                retryable=False,
            )
        return ImportSource(
            import_id=row["id"],
            project_id=row["project_id"],
            created_by_user_id=row["created_by_user_id"],
            file_name=row["file_name"],
            file_type=str(row["file_type"]).lower().lstrip("."),
            selected_sheet=row["selected_sheet"],
            mapping=_json_dict(row["mapping"]),
            update_rule=row["update_rule"],
            status=row["status"],
            storage_object_id=row["storage_object_id"],
            bucket=row["bucket"],
            object_key=row["object_key"],
            size_bytes=row["size_bytes"],
            checksum_sha256=row["checksum_sha256"],
            content_type=row["content_type"],
        )

    @staticmethod
    def _validate_claim_scope(job: BackgroundJobClaim, source: ImportSource) -> None:
        if job.project_id != source.project_id:
            raise JobExecutionError(
                "customer_import_scope_mismatch",
                "Customer import does not belong to the job project",
                retryable=False,
            )
        if not source.object_key.startswith(f"tenants/{job.tenant_id}/"):
            raise JobExecutionError(
                "customer_import_storage_scope_mismatch",
                "Customer import object is outside the tenant namespace",
                retryable=False,
            )

    async def _download_source(self, source: ImportSource, path: Path) -> None:
        allowed_content_types = (
            {
                "text/csv",
                "application/csv",
                "text/plain",
                "application/vnd.ms-excel",
                "application/octet-stream",
            }
            if source.file_type == "csv"
            else {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
            }
        )
        if (
            source.file_type not in {"csv", "xlsx"}
            or source.content_type not in allowed_content_types
        ):
            raise JobExecutionError(
                "customer_import_mime_mismatch",
                "Customer import content type does not match the file type",
                retryable=False,
            )
        if (
            source.size_bytes <= 0
            or source.size_bytes > self.settings.customer_import_max_file_bytes
        ):
            raise JobExecutionError(
                "customer_import_size_invalid",
                "Customer import file size is outside the configured limit",
                retryable=False,
            )
        if not _valid_object_key(source.object_key):
            raise JobExecutionError(
                "customer_import_object_key_invalid",
                "Customer import object key is invalid",
                retryable=False,
            )
        try:
            stat = await asyncio.to_thread(
                self.storage.stat_object, source.bucket, source.object_key
            )
            if int(stat.size or 0) != source.size_bytes:
                raise JobExecutionError(
                    "customer_import_size_mismatch",
                    "Customer import object size does not match the registry",
                    retryable=False,
                )
            await asyncio.to_thread(
                self.storage.fget_object,
                source.bucket,
                source.object_key,
                str(path),
            )
        except JobExecutionError:
            raise
        except S3Error as exc:
            raise JobExecutionError(
                "customer_import_storage_unavailable",
                "Customer import object storage is unavailable",
                retryable=exc.code not in {"NoSuchKey", "NoSuchObject"},
            ) from exc
        digest = await asyncio.to_thread(_file_sha256, path)
        if source.checksum_sha256 is not None and digest != source.checksum_sha256:
            raise JobExecutionError(
                "customer_import_checksum_mismatch",
                "Customer import checksum does not match the registry",
                retryable=False,
            )

    @contextmanager
    def _open_rows(self, path: Path, source: ImportSource) -> Iterator[RowStream]:
        if source.file_type == "csv":
            with _open_csv(path, self.settings.customer_import_max_columns) as stream:
                yield stream
            return
        if source.file_type == "xlsx":
            _validate_xlsx(
                path,
                max_expanded_bytes=self.settings.customer_import_max_xlsx_expanded_bytes,
                max_ratio=self.settings.customer_import_max_zip_ratio,
                max_sheets=self.settings.customer_import_max_sheets,
            )
            workbook = import_module("openpyxl").load_workbook(
                path,
                read_only=True,
                data_only=True,
            )
            try:
                selected_sheet = source.selected_sheet
                if selected_sheet in {"", "pending"} and workbook.sheetnames:
                    selected_sheet = workbook.sheetnames[0]
                if selected_sheet not in workbook.sheetnames:
                    raise JobExecutionError(
                        "customer_import_sheet_missing",
                        "Selected XLSX sheet no longer exists",
                        retryable=False,
                    )
                worksheet = workbook[selected_sheet]
                iterator = worksheet.iter_rows(values_only=True)
                first = next(iterator, None)
                if first is None:
                    raise JobExecutionError(
                        "customer_import_empty",
                        "Customer import file is empty",
                        retryable=False,
                    )
                headers = _validate_headers(first, self.settings.customer_import_max_columns)

                def rows() -> Iterator[tuple[int, dict[str, object]]]:
                    for row_number, raw in enumerate(iterator, start=2):
                        values = list(raw[: len(headers)])
                        values.extend([""] * (len(headers) - len(values)))
                        if not any(_string_value(value) for value in values):
                            continue
                        yield (
                            row_number,
                            {
                                header: _cell_value(value)
                                for header, value in zip(headers, values, strict=True)
                            },
                        )

                yield RowStream(
                    tuple(headers),
                    rows(),
                    tuple(workbook.sheetnames),
                    selected_sheet,
                )
            finally:
                workbook.close()
            return
        raise JobExecutionError(
            "customer_import_type_invalid",
            "Only CSV and XLSX customer imports are supported",
            retryable=False,
        )

    async def _store_preview(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        source: ImportSource,
        headers: tuple[str, ...],
        sheet_names: tuple[str, ...],
        selected_sheet: str,
        preview: list[dict[str, object]],
        total: int,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports
                    SET status='ready', sheet_names=$3::jsonb, selected_sheet=$4,
                        source_rows=$5::jsonb, row_count=$6, total_rows=$6,
                        progress=100, processing_completed_at=now(), updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                      AND status IN ('processing_preview','queued','ready')
                    """,
                    job.tenant_id,
                    source.import_id,
                    json.dumps(list(sheet_names)),
                    selected_sheet,
                    json.dumps(
                        {
                            selected_sheet: {
                                "headers": list(headers),
                                "rows": preview,
                            }
                        }
                    ),
                    total,
                )
                await _insert_import_realtime(
                    connection,
                    job,
                    source.import_id,
                    "import.progress",
                    {"status": "ready", "progress": 100},
                )

    async def _load_definitions(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        project_id: UUID,
    ) -> dict[str, FieldDefinition]:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                rows = await connection.fetch(
                    """
                    SELECT key, name, field_type, is_required, options, default_value
                    FROM customer_field_definitions
                    WHERE tenant_id=$1 AND project_id=$2 AND is_active
                    ORDER BY sort_order, id
                    """,
                    job.tenant_id,
                    project_id,
                )
        return {
            row["key"]: FieldDefinition(
                key=row["key"],
                name=row["name"],
                field_type=row["field_type"],
                required=row["is_required"],
                options=tuple(_json_list(row["options"])),
                default_value=_json_value(row["default_value"]),
            )
            for row in rows
        }

    async def _mark_running(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        import_id: UUID,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports
                    SET status='running', progress=1,
                        processing_started_at=COALESCE(processing_started_at,now()),
                        mapping_snapshot=mapping,
                        update_policy_snapshot=update_rule,
                        updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                      AND status IN ('ready','queued','running','failed')
                    """,
                    job.tenant_id,
                    import_id,
                )
                await _insert_import_realtime(
                    connection,
                    job,
                    import_id,
                    "import.progress",
                    {"status": "running", "progress": 1},
                )

    async def _stage_file(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        source: ImportSource,
        definitions: Mapping[str, FieldDefinition],
        path: Path,
    ) -> None:
        file_identities: set[str] = set()
        total = valid = invalid = duplicate = 0
        with self._open_rows(path, source) as stream:
            mapping = _validate_mapping(source.mapping, stream.headers, definitions)
            batch: list[PreparedRow] = []
            for row_number, raw_row in stream.rows:
                context.ensure_active()
                total += 1
                if total > self.settings.customer_import_max_rows:
                    raise JobExecutionError(
                        "customer_import_row_limit",
                        "Customer import exceeds the configured row limit",
                        retryable=False,
                    )
                prepared = _prepare_row(row_number, raw_row, mapping, definitions)
                duplicate_in_file = any(item in file_identities for item in prepared.identities)
                file_identities.update(prepared.identities)
                if duplicate_in_file:
                    prepared = PreparedRow(
                        prepared.row_number,
                        prepared.values,
                        prepared.contacts,
                        prepared.identities,
                        (*prepared.errors, {"code": "duplicate_in_file"}),
                    )
                batch.append(prepared)
                if len(batch) >= self.settings.customer_import_batch_rows:
                    counts = await self._stage_batch(job, context, source, batch)
                    valid += counts[0]
                    invalid += counts[1]
                    duplicate += counts[2]
                    batch.clear()
                    await self._import_progress(context, total, source.import_id)
            if batch:
                counts = await self._stage_batch(job, context, source, batch)
                valid += counts[0]
                invalid += counts[1]
                duplicate += counts[2]
            if total == 0:
                raise JobExecutionError(
                    "customer_import_empty",
                    "Customer import file contains no data rows",
                    retryable=False,
                )
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports
                    SET total_rows=$3, valid_rows=$4, invalid_rows=$5,
                        duplicate_rows=$6, row_count=$3, progress=85, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND status='running'
                    """,
                    job.tenant_id,
                    source.import_id,
                    total,
                    valid,
                    invalid,
                    duplicate,
                )
        await context.update_progress(
            85,
            detail={"phase": "staged", "processed_rows": total},
        )

    async def _stage_batch(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        source: ImportSource,
        batch: list[PreparedRow],
    ) -> tuple[int, int, int]:
        identities = sorted({identity for row in batch for identity in row.identities})
        matches: dict[str, set[UUID]] = {identity: set() for identity in identities}
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                if identities:
                    rows = await connection.fetch(
                        """
                        SELECT identity, customer_id FROM (
                          SELECT 'external:' || external_reference_normalized AS identity,
                                 id AS customer_id
                          FROM customers
                          WHERE tenant_id=$1 AND project_id=$2
                            AND external_reference_normalized IS NOT NULL
                            AND ('external:' || external_reference_normalized)=ANY($3::text[])
                          UNION ALL
                          SELECT kind || ':' || normalized_value AS identity, customer_id
                          FROM customer_contacts
                          WHERE tenant_id=$1 AND project_id=$2
                            AND (kind || ':' || normalized_value)=ANY($3::text[])
                        ) AS identities
                        """,
                        job.tenant_id,
                        source.project_id,
                        identities,
                    )
                    for row in rows:
                        matches.setdefault(row["identity"], set()).add(row["customer_id"])
                arguments: list[tuple[object, ...]] = []
                valid = invalid = duplicates = 0
                for prepared in batch:
                    errors = list(prepared.errors)
                    duplicate_in_file = any(
                        error.get("code") == "duplicate_in_file" for error in errors
                    )
                    customer_ids = {
                        customer_id
                        for identity in prepared.identities
                        for customer_id in matches.get(identity, set())
                    }
                    target_customer_id: UUID | None = None
                    if errors:
                        status = "invalid"
                        action = "skipped"
                        invalid += 1
                        duplicates += int(duplicate_in_file)
                    elif customer_ids:
                        duplicates += 1
                        if source.update_rule == "update" and len(customer_ids) == 1:
                            status = "duplicate"
                            action = "update"
                            target_customer_id = next(iter(customer_ids))
                            valid += 1
                        else:
                            status = "duplicate"
                            action = "skipped"
                            if len(customer_ids) > 1:
                                errors.append({"code": "ambiguous_duplicate"})
                            invalid += 1
                    else:
                        status = "valid"
                        action = "create"
                        valid += 1
                    payload = {
                        "values": prepared.values,
                        "contacts": [
                            {
                                "kind": contact.kind,
                                "normalized_value": contact.normalized_value,
                                "display_value": contact.display_value,
                                "primary": contact.primary,
                            }
                            for contact in prepared.contacts
                        ],
                    }
                    arguments.append(
                        (
                            uuid4(),
                            job.tenant_id,
                            source.project_id,
                            source.import_id,
                            prepared.row_number,
                            status,
                            json.dumps(payload, ensure_ascii=False),
                            json.dumps(list(prepared.identities)),
                            json.dumps(errors),
                            target_customer_id,
                            action,
                        )
                    )
                await connection.executemany(
                    """
                    INSERT INTO customer_import_staging_rows
                        (id, tenant_id, project_id, import_id, row_number, status,
                         normalized_payload, identity_hashes, safe_errors,
                         target_customer_id, result_action, created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,
                            $10,$11,now(),now())
                    ON CONFLICT (tenant_id, import_id, row_number) DO UPDATE
                    SET status=EXCLUDED.status,
                        normalized_payload=EXCLUDED.normalized_payload,
                        identity_hashes=EXCLUDED.identity_hashes,
                        safe_errors=EXCLUDED.safe_errors,
                        target_customer_id=EXCLUDED.target_customer_id,
                        result_action=EXCLUDED.result_action,
                        processed_at=NULL, updated_at=now()
                    """,
                    arguments,
                )
        return valid, invalid, duplicates

    async def _import_progress(
        self,
        context: JobExecutionContext,
        processed: int,
        import_id: UUID,
    ) -> None:
        progress = min(80, max(2, processed // 1250))
        await context.update_progress(
            progress,
            detail={"phase": "validating", "processed_rows": processed},
        )
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, context.job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports SET progress=$3, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND status='running'
                    """,
                    context.job.tenant_id,
                    import_id,
                    progress,
                )
                await _insert_import_realtime(
                    connection,
                    context.job,
                    import_id,
                    "import.progress",
                    {"status": "running", "progress": progress},
                )

    async def _finalize(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        source: ImportSource,
    ) -> JobResult:
        created = updated = skipped = runtime_duplicates = 0
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"customer-import:{job.tenant_id}:{source.project_id}",
                )
                record = await connection.fetchrow(
                    """
                    SELECT status, created_count, updated_count, skipped_count
                    FROM customer_imports
                    WHERE tenant_id=$1 AND id=$2
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    source.import_id,
                )
                if record is None:
                    raise JobExecutionError(
                        "customer_import_missing",
                        "Customer import no longer exists",
                        retryable=False,
                    )
                if record["status"] == "completed":
                    return JobResult(
                        {
                            "import_id": str(source.import_id),
                            "created": int(record["created_count"] or 0),
                            "updated": int(record["updated_count"] or 0),
                            "skipped": int(record["skipped_count"] or 0),
                        }
                    )
                if record["status"] == "cancel_requested":
                    raise JobCancelled
                if record["status"] != "running":
                    raise JobExecutionError(
                        "customer_import_state_conflict",
                        "Customer import is not ready for finalization",
                        retryable=False,
                    )
                await connection.execute(
                    """
                    UPDATE customer_imports SET status='finalizing', progress=90, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    source.import_id,
                )
                rows = await connection.fetch(
                    """
                    SELECT id, row_number, normalized_payload, identity_hashes,
                           target_customer_id, result_action
                    FROM customer_import_staging_rows
                    WHERE tenant_id=$1 AND import_id=$2
                    ORDER BY row_number, id
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    source.import_id,
                )
                identity_matches = await self._final_identity_matches(
                    connection,
                    job,
                    source,
                    rows,
                )
                for row in rows:
                    action = str(row["result_action"])
                    runtime_error_code: str | None = None
                    target_customer_id = row["target_customer_id"]
                    identities = [str(value) for value in _json_list(row["identity_hashes"])]
                    matched_customers = {
                        customer_id
                        for identity in identities
                        for customer_id in identity_matches.get(identity, set())
                    }
                    if action in {"create", "update"} and identities and matched_customers:
                        if source.update_rule == "update" and len(matched_customers) == 1:
                            action = "update"
                            target_customer_id = next(iter(matched_customers))
                        else:
                            action = "skipped"
                            target_customer_id = None
                            runtime_error_code = "duplicate_during_finalization"
                    merged = False
                    try:
                        # A nested asyncpg transaction is a savepoint. It keeps
                        # scalar updates and contact inserts atomic for this row
                        # if another writer claims an identity after our final
                        # preflight query.
                        async with connection.transaction():
                            if action == "create":
                                merged = await self._create_customer(connection, job, source, row)
                                if not merged:
                                    raise _RowMergeConflict
                            elif action == "update" and target_customer_id is not None:
                                merged = await self._update_customer(
                                    connection,
                                    job,
                                    source,
                                    row,
                                    target_customer_id=target_customer_id,
                                )
                                if not merged:
                                    raise _RowMergeConflict
                    except (_RowMergeConflict, asyncpg.UniqueViolationError):
                        merged = False
                        action = "skipped"
                        runtime_error_code = "duplicate_during_finalization"
                    if merged and action == "create":
                        created += 1
                    elif merged and action == "update":
                        updated += 1
                    else:
                        skipped += 1
                    runtime_duplicates += int(runtime_error_code is not None)
                    await connection.execute(
                        """
                        UPDATE customer_import_staging_rows
                        SET status=CASE WHEN $4 THEN 'merged' ELSE 'skipped' END,
                            result_action=$5,
                            safe_errors=CASE
                              WHEN $6::text IS NULL THEN safe_errors
                              ELSE (
                                safe_errors::jsonb || jsonb_build_array(
                                  jsonb_build_object('code',$6::text)
                                )
                              )::json
                            END,
                            processed_at=now(), updated_at=now()
                        WHERE tenant_id=$1 AND import_id=$2 AND id=$3
                        """,
                        job.tenant_id,
                        source.import_id,
                        row["id"],
                        merged,
                        action,
                        runtime_error_code,
                    )
                report = {
                    "import_id": str(source.import_id),
                    "created": created,
                    "updated": updated,
                    "skipped": skipped,
                    "cancelled": False,
                }
                await connection.execute(
                    """
                    UPDATE customer_imports
                    SET status='completed', progress=100, created_count=$3,
                        updated_count=$4, skipped_count=$5, committed_at=now(),
                        processing_completed_at=now(), report=$6::jsonb,
                        invalid_rows=COALESCE(invalid_rows,0)+$7,
                        duplicate_rows=COALESCE(duplicate_rows,0)+$7,
                        source_rows='{}'::jsonb, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    source.import_id,
                    created,
                    updated,
                    skipped,
                    json.dumps(report),
                    runtime_duplicates,
                )
                await _insert_import_realtime(
                    connection,
                    job,
                    source.import_id,
                    "import.completed",
                    {
                        "status": "completed",
                        "progress": 100,
                        "created": created,
                        "updated": updated,
                        "skipped": skipped,
                    },
                )
        await context.update_progress(100, detail={"phase": "completed"})
        return JobResult(report)

    async def _final_identity_matches(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        source: ImportSource,
        rows: list[asyncpg.Record],
    ) -> dict[str, set[UUID]]:
        identities = sorted(
            {
                str(identity)
                for row in rows
                for identity in _json_list(row["identity_hashes"])
                if identity
            }
        )
        matches: dict[str, set[UUID]] = {identity: set() for identity in identities}
        for offset in range(0, len(identities), 5000):
            batch = identities[offset : offset + 5000]
            found = await connection.fetch(
                """
                SELECT identity, customer_id FROM (
                  SELECT 'external:' || external_reference_normalized AS identity,
                         id AS customer_id
                  FROM customers
                  WHERE tenant_id=$1 AND project_id=$2
                    AND external_reference_normalized IS NOT NULL
                    AND ('external:' || external_reference_normalized)=ANY($3::text[])
                  UNION ALL
                  SELECT kind || ':' || normalized_value AS identity, customer_id
                  FROM customer_contacts
                  WHERE tenant_id=$1 AND project_id=$2
                    AND (kind || ':' || normalized_value)=ANY($3::text[])
                ) AS identities
                """,
                job.tenant_id,
                source.project_id,
                batch,
            )
            for match in found:
                matches.setdefault(str(match["identity"]), set()).add(match["customer_id"])
        return matches

    async def _create_customer(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        source: ImportSource,
        row: asyncpg.Record,
    ) -> bool:
        payload = _json_dict(row["normalized_payload"])
        values = _json_dict(payload.get("values"))
        customer_id = uuid4()
        inserted_id = await connection.fetchval(
            """
            INSERT INTO customers
                (id, tenant_id, project_id, display_name, external_reference,
                 external_reference_normalized, preferred_language, status, city,
                 region, address, job_title, organization, tags, description,
                 source, next_call_at, custom_fields, is_anonymized,
                 created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
                    $15,$16,$17,$18::jsonb,false,now(),now())
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            customer_id,
            job.tenant_id,
            source.project_id,
            _nullable_string(values.get("display_name")),
            _nullable_string(values.get("external_reference")),
            _nullable_string(values.get("external_reference_normalized")),
            _nullable_string(values.get("preferred_language")) or "ru",
            _nullable_string(values.get("status")) or "new",
            _nullable_string(values.get("city")),
            _nullable_string(values.get("region")),
            _nullable_string(values.get("address")),
            _nullable_string(values.get("job_title")),
            _nullable_string(values.get("organization")),
            json.dumps(_json_list(values.get("tags")), ensure_ascii=False),
            _nullable_string(values.get("description")) or "",
            _nullable_string(values.get("source")),
            _optional_datetime(values.get("next_contact_at")),
            json.dumps(_json_dict(values.get("custom_fields")), ensure_ascii=False),
        )
        if inserted_id is None:
            return False
        contacts_inserted = await self._insert_contacts(
            connection,
            job,
            source,
            customer_id,
            payload,
        )
        if not contacts_inserted:
            await connection.execute(
                "DELETE FROM customers WHERE tenant_id=$1 AND project_id=$2 AND id=$3",
                job.tenant_id,
                source.project_id,
                customer_id,
            )
            return False
        return True

    async def _update_customer(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        source: ImportSource,
        row: asyncpg.Record,
        *,
        target_customer_id: UUID | None = None,
    ) -> bool:
        customer_id = target_customer_id or cast(UUID, row["target_customer_id"])
        payload = _json_dict(row["normalized_payload"])
        values = _json_dict(payload.get("values"))
        current = await connection.fetchrow(
            """
            SELECT id FROM customers
            WHERE tenant_id=$1 AND project_id=$2 AND id=$3
            FOR UPDATE
            """,
            job.tenant_id,
            source.project_id,
            customer_id,
        )
        if current is None:
            return False
        await connection.execute(
            """
            UPDATE customers SET
              display_name=COALESCE($4,display_name),
              external_reference=COALESCE($5,external_reference),
              external_reference_normalized=COALESCE($6,external_reference_normalized),
              preferred_language=COALESCE($7,preferred_language),
              status=COALESCE($8,status), city=COALESCE($9,city),
              region=COALESCE($10,region), address=COALESCE($11,address),
              job_title=COALESCE($12,job_title), organization=COALESCE($13,organization),
              tags=CASE WHEN $14::jsonb='[]'::jsonb THEN tags ELSE $14::jsonb END,
              description=COALESCE($15,description), source=COALESCE($16,source),
              next_call_at=COALESCE($17,next_call_at),
              custom_fields=custom_fields || $18::jsonb, updated_at=now()
            WHERE tenant_id=$1 AND project_id=$2 AND id=$3
            """,
            job.tenant_id,
            source.project_id,
            customer_id,
            _nullable_string(values.get("display_name")),
            _nullable_string(values.get("external_reference")),
            _nullable_string(values.get("external_reference_normalized")),
            _nullable_string(values.get("preferred_language")),
            _nullable_string(values.get("status")),
            _nullable_string(values.get("city")),
            _nullable_string(values.get("region")),
            _nullable_string(values.get("address")),
            _nullable_string(values.get("job_title")),
            _nullable_string(values.get("organization")),
            json.dumps(_json_list(values.get("tags")), ensure_ascii=False),
            _nullable_string(values.get("description")),
            _nullable_string(values.get("source")),
            _optional_datetime(values.get("next_contact_at")),
            json.dumps(_json_dict(values.get("custom_fields")), ensure_ascii=False),
        )
        return await self._insert_contacts(connection, job, source, customer_id, payload)

    async def _insert_contacts(
        self,
        connection: asyncpg.Connection,
        job: BackgroundJobClaim,
        source: ImportSource,
        customer_id: UUID,
        payload: Mapping[str, object],
    ) -> bool:
        contacts = payload.get("contacts")
        if not isinstance(contacts, list):
            return True
        for value in contacts:
            contact = _json_dict(value)
            kind = _nullable_string(contact.get("kind"))
            normalized = _nullable_string(contact.get("normalized_value"))
            display = _nullable_string(contact.get("display_value"))
            if kind not in {"phone", "email"} or not normalized or not display:
                continue
            has_primary = bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM customer_contacts
                      WHERE tenant_id=$1 AND project_id=$2 AND customer_id=$3
                        AND kind=$4 AND is_primary
                    )
                    """,
                    job.tenant_id,
                    source.project_id,
                    customer_id,
                    kind,
                )
            )
            inserted_id = await connection.fetchval(
                """
                INSERT INTO customer_contacts
                    (id, tenant_id, project_id, customer_id, kind, normalized_value,
                     display_value, is_primary, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now(),now())
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                uuid4(),
                job.tenant_id,
                source.project_id,
                customer_id,
                kind,
                normalized,
                display,
                bool(contact.get("primary")) and not has_primary,
            )
            if inserted_id is None:
                existing_customer_id = await connection.fetchval(
                    """
                    SELECT customer_id FROM customer_contacts
                    WHERE tenant_id=$1 AND project_id=$2 AND kind=$3 AND normalized_value=$4
                    """,
                    job.tenant_id,
                    source.project_id,
                    kind,
                    normalized,
                )
                if existing_customer_id != customer_id:
                    return False
        return True

    async def _write_error_report(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        source: ImportSource,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                existing_report_id = await connection.fetchval(
                    """
                    SELECT report_storage_object_id FROM customer_imports
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    source.import_id,
                )
                if existing_report_id is not None:
                    return
                rows = await connection.fetch(
                    """
                    SELECT row_number, status, safe_errors, result_action
                    FROM customer_import_staging_rows
                    WHERE tenant_id=$1 AND import_id=$2
                      AND (safe_errors::jsonb<>'[]'::jsonb OR result_action='skipped')
                    ORDER BY row_number
                    """,
                    job.tenant_id,
                    source.import_id,
                )
        if not rows:
            return
        content = json.dumps(
            {
                "import_id": str(source.import_id),
                "errors": [
                    {
                        "row_number": row["row_number"],
                        "status": row["status"],
                        "error_codes": [
                            _json_dict(item).get("code") for item in _json_list(row["safe_errors"])
                        ],
                    }
                    for row in rows
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        checksum = hashlib.sha256(content).hexdigest()
        object_id = uuid4()
        object_key = (
            f"tenants/{job.tenant_id}/projects/{source.project_id}/"
            f"import-reports/{source.import_id}/{object_id}.json"
        )
        try:
            await asyncio.to_thread(
                self.storage.put_object,
                self.settings.minio_bucket,
                object_key,
                io.BytesIO(content),
                len(content),
                content_type="application/json",
                metadata={"sha256": checksum},
            )
        except S3Error as exc:
            raise JobExecutionError(
                "customer_import_report_storage_unavailable",
                "Customer import completed but report storage is unavailable",
                retryable=True,
            ) from exc
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    INSERT INTO storage_objects
                        (id, tenant_id, project_id, bucket, object_key, category,
                         owner_aggregate_type, owner_aggregate_id, checksum_sha256,
                         size_bytes, content_type, status, retention_state, legal_hold,
                         lock_version, created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,'import_report','customer_import',$6,$7,$8,
                            'application/json','active','retained',false,1,now(),now())
                    ON CONFLICT (bucket, object_key) DO NOTHING
                    """,
                    object_id,
                    job.tenant_id,
                    source.project_id,
                    self.settings.minio_bucket,
                    object_key,
                    source.import_id,
                    checksum,
                    len(content),
                )
                await connection.execute(
                    """
                    UPDATE customer_imports SET report_storage_object_id=$3, updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND report_storage_object_id IS NULL
                    """,
                    job.tenant_id,
                    source.import_id,
                    object_id,
                )

    async def _completed_result(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        import_id: UUID,
    ) -> JobResult:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                row = await connection.fetchrow(
                    """
                    SELECT created_count, updated_count, skipped_count
                    FROM customer_imports WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    import_id,
                )
        if row is None:
            raise JobExecutionError(
                "customer_import_missing", "Customer import no longer exists", retryable=False
            )
        return JobResult(
            {
                "import_id": str(import_id),
                "created": int(row["created_count"] or 0),
                "updated": int(row["updated_count"] or 0),
                "skipped": int(row["skipped_count"] or 0),
            }
        )

    async def _mark_cancelled(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        import_id: UUID,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports
                    SET status='cancelled', cancelled_at=now(), processing_completed_at=now(),
                        progress=COALESCE(progress,0), updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND status<>'completed'
                    """,
                    job.tenant_id,
                    import_id,
                )
                await _insert_import_realtime(
                    connection,
                    job,
                    import_id,
                    "import.progress",
                    {"status": "cancelled"},
                )

    async def _mark_failed(
        self,
        job: BackgroundJobClaim,
        context: JobExecutionContext,
        import_id: UUID,
    ) -> None:
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, job.tenant_id)
                await connection.execute(
                    """
                    UPDATE customer_imports SET status='failed', updated_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND status NOT IN ('completed','cancelled')
                    """,
                    job.tenant_id,
                    import_id,
                )
                await _insert_import_realtime(
                    connection,
                    job,
                    import_id,
                    "import.progress",
                    {"status": "failed"},
                )


@contextmanager
def _open_csv(path: Path, max_columns: int) -> Iterator[RowStream]:
    raw = path.open("rb")
    text: TextIO | None = None
    try:
        sample = raw.read(8192)
        if b"\x00" in sample or sample.startswith(b"PK\x03\x04"):
            raise JobExecutionError(
                "customer_import_csv_format_invalid",
                "CSV content does not match the declared file type",
                retryable=False,
            )
        encoding = _detect_encoding(sample)
        raw.seek(0)
        text = io.TextIOWrapper(raw, encoding=encoding, newline="")
        sample_text = sample.decode(encoding)
        try:
            dialect = csv.Sniffer().sniff(sample_text, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(text, dialect)
        first = next(reader, None)
        if first is None:
            raise JobExecutionError(
                "customer_import_empty", "Customer import file is empty", retryable=False
            )
        headers = _validate_headers(first, max_columns)

        def rows() -> Iterator[tuple[int, dict[str, object]]]:
            for row_number, raw_row in enumerate(reader, start=2):
                values = list(raw_row[: len(headers)])
                values.extend([""] * (len(headers) - len(values)))
                if not any(_string_value(value) for value in values):
                    continue
                yield row_number, dict(zip(headers, values, strict=True))

        yield RowStream(tuple(headers), rows(), ("CSV",), "CSV")
    finally:
        if text is not None:
            text.close()
        else:
            raw.close()


def _validate_xlsx(
    path: Path,
    *,
    max_expanded_bytes: int,
    max_ratio: int,
    max_sheets: int,
) -> None:
    if not zipfile.is_zipfile(path):
        raise JobExecutionError(
            "customer_import_xlsx_invalid", "XLSX archive is invalid", retryable=False
        )
    total = 0
    worksheets = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            pure = PurePosixPath(info.filename.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                raise JobExecutionError(
                    "customer_import_xlsx_path_invalid",
                    "XLSX contains an unsafe path",
                    retryable=False,
                )
            lowered = info.filename.casefold()
            if info.flag_bits & 0x1:
                raise JobExecutionError(
                    "customer_import_xlsx_encrypted",
                    "Encrypted XLSX files are not supported",
                    retryable=False,
                )
            if "vbaproject.bin" in lowered or lowered.startswith("xl/externallinks/"):
                raise JobExecutionError(
                    "customer_import_xlsx_active_content",
                    "XLSX active or external content is not supported",
                    retryable=False,
                )
            if lowered.startswith("xl/worksheets/") and lowered.endswith(".xml"):
                worksheets += 1
            total += info.file_size
            if total > max_expanded_bytes:
                raise JobExecutionError(
                    "customer_import_xlsx_expanded_limit",
                    "Expanded XLSX exceeds the configured size limit",
                    retryable=False,
                )
            if info.file_size > 0 and info.compress_size == 0:
                raise JobExecutionError(
                    "customer_import_xlsx_zip_bomb",
                    "XLSX compression structure is unsafe",
                    retryable=False,
                )
            if info.compress_size and info.file_size / info.compress_size > max_ratio:
                raise JobExecutionError(
                    "customer_import_xlsx_zip_bomb",
                    "XLSX compression ratio exceeds the configured limit",
                    retryable=False,
                )
            if lowered.endswith(".xml"):
                with archive.open(info) as member:
                    prefix = member.read(4096).upper()
                if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
                    raise JobExecutionError(
                        "customer_import_xlsx_xml_unsafe",
                        "XLSX contains unsafe XML declarations",
                        retryable=False,
                    )
    if worksheets == 0 or worksheets > max_sheets:
        raise JobExecutionError(
            "customer_import_xlsx_sheet_limit",
            "XLSX sheet count is outside the configured limit",
            retryable=False,
        )


def _validate_headers(values: Any, max_columns: int) -> list[str]:
    headers = [_string_value(value) for value in values]
    while headers and not headers[-1]:
        headers.pop()
    normalized = [_normalize_header(value) for value in headers]
    if not headers or any(not value for value in headers):
        raise JobExecutionError(
            "customer_import_headers_invalid",
            "All import columns must have headers",
            retryable=False,
        )
    if len(headers) > max_columns:
        raise JobExecutionError(
            "customer_import_column_limit",
            "Customer import exceeds the configured column limit",
            retryable=False,
        )
    if len(set(normalized)) != len(normalized):
        raise JobExecutionError(
            "customer_import_headers_duplicate",
            "Customer import contains duplicate headers",
            retryable=False,
        )
    return headers


def _validate_mapping(
    mapping: Mapping[str, str],
    headers: tuple[str, ...],
    definitions: Mapping[str, FieldDefinition],
) -> dict[str, str]:
    allowed = _STANDARD_FIELDS | {f"custom.{key}" for key in definitions}
    result = {str(field): str(header) for field, header in mapping.items() if header}
    if not result or "display_name" not in result:
        raise JobExecutionError(
            "customer_import_name_mapping_required",
            "Customer display name mapping is required",
            retryable=False,
        )
    if set(result) - allowed or set(result.values()) - set(headers):
        raise JobExecutionError(
            "customer_import_mapping_invalid",
            "Customer import mapping contains an unknown field or column",
            retryable=False,
        )
    if len(set(result.values())) != len(result):
        raise JobExecutionError(
            "customer_import_mapping_duplicate",
            "One import column cannot map to multiple fields",
            retryable=False,
        )
    return result


def _prepare_row(
    row_number: int,
    raw: Mapping[str, object],
    mapping: Mapping[str, str],
    definitions: Mapping[str, FieldDefinition],
) -> PreparedRow:
    values: dict[str, object] = {}
    contacts: list[PreparedContact] = []
    errors: list[dict[str, object]] = []
    custom: dict[str, object] = {}
    for field, header in mapping.items():
        raw_value = raw.get(header)
        value = _string_value(raw_value)
        try:
            if field in {"phone", "alternate_phone"} and value:
                contacts.append(
                    PreparedContact("phone", _normalize_phone(value), value, field == "phone")
                )
            elif field == "email" and value:
                contacts.append(PreparedContact("email", _normalize_email(value), value, True))
            elif field == "tags" and value:
                values[field] = _normalize_tags(value)
            elif field == "preferred_language" and value:
                language = value.casefold().replace("_", "-")
                if language not in _LANGUAGES:
                    raise ValueError("unsupported_language")
                values[field] = language
            elif field == "next_contact_at" and value:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone_required")
                values[field] = parsed.astimezone(UTC).isoformat()
            elif field.startswith("custom."):
                key = field.removeprefix("custom.")
                custom_parsed = _custom_value(definitions[key], raw_value)
                if custom_parsed is not None:
                    custom[key] = custom_parsed
            elif value:
                values[field] = value
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"code": "invalid_field", "field": field, "reason": str(exc)[:80]})
    name = _string_value(values.get("display_name"))
    if len(name) < 2:
        errors.append({"code": "display_name_required"})
    status = _nullable_string(values.get("status"))
    if status is not None and status not in _CUSTOMER_STATUSES:
        errors.append({"code": "status_invalid"})
    for definition in definitions.values():
        if definition.key not in custom and definition.default_value is not None:
            custom[definition.key] = definition.default_value
        if definition.required and definition.key not in custom:
            errors.append({"code": "custom_field_required", "field": f"custom.{definition.key}"})
    if custom:
        values["custom_fields"] = custom
    external = _nullable_string(values.get("external_reference"))
    if external:
        normalized_external = " ".join(external.split()).casefold()
        values["external_reference"] = " ".join(external.split())
        values["external_reference_normalized"] = normalized_external
    errors.extend(_database_length_errors(values, contacts))
    identities = [f"{item.kind}:{item.normalized_value}" for item in contacts]
    if external:
        identities.append(f"external:{values['external_reference_normalized']}")
    return PreparedRow(
        row_number,
        values,
        tuple(contacts),
        tuple(dict.fromkeys(identities)),
        tuple(errors),
    )


def _database_length_errors(
    values: Mapping[str, object], contacts: list[PreparedContact]
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for field, maximum in _CUSTOMER_TEXT_LIMITS.items():
        value = values.get(field)
        if isinstance(value, str) and len(value) > maximum:
            errors.append({"code": "field_too_long", "field": field, "max_length": maximum})
    if len(contacts) > _MAX_CONTACTS:
        errors.append({"code": "contact_limit", "max_items": _MAX_CONTACTS})
    for contact in contacts:
        if (
            len(contact.normalized_value) > _MAX_CONTACT_LENGTH
            or len(contact.display_value) > _MAX_CONTACT_LENGTH
        ):
            errors.append(
                {
                    "code": "contact_too_long",
                    "kind": contact.kind,
                    "max_length": _MAX_CONTACT_LENGTH,
                }
            )
    tags = values.get("tags")
    if isinstance(tags, list):
        if len(tags) > _MAX_TAGS:
            errors.append({"code": "tag_limit", "max_items": _MAX_TAGS})
        if any(isinstance(tag, str) and len(tag) > _MAX_TAG_LENGTH for tag in tags):
            errors.append({"code": "tag_too_long", "max_length": _MAX_TAG_LENGTH})
    return errors


def _custom_value(definition: FieldDefinition, raw: object) -> object | None:
    value = _string_value(raw)
    if not value:
        return None
    if definition.field_type == "number":
        return float(value.replace(",", "."))
    if definition.field_type == "boolean":
        normalized = value.casefold()
        if normalized in {"1", "true", "yes", "да", "y"}:
            return True
        if normalized in {"0", "false", "no", "нет", "n"}:
            return False
        raise ValueError("boolean_expected")
    if definition.field_type in {"date", "datetime"}:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat()
    if definition.field_type == "multiselect":
        result = _normalize_tags(value)
        if definition.options and any(item not in definition.options for item in result):
            raise ValueError("option_invalid")
        return result
    if definition.field_type == "select" and definition.options and value not in definition.options:
        raise ValueError("option_invalid")
    return value


def _normalize_phone(value: str) -> str:
    normalized = re.sub(r"[\s().-]+", "", value)
    if normalized.startswith("00"):
        normalized = f"+{normalized[2:]}"
    if not _PHONE_RE.fullmatch(normalized):
        raise ValueError("phone_invalid")
    return normalized


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or not _EMAIL_RE.fullmatch(normalized):
        raise ValueError("email_invalid")
    return normalized


def _normalize_tags(value: str) -> list[str]:
    result = list(
        dict.fromkeys(
            item.strip().casefold() for item in value.replace(";", ",").split(",") if item.strip()
        )
    )
    if len(result) > _MAX_TAGS:
        raise ValueError("tag_limit")
    if any(len(item) > _MAX_TAG_LENGTH for item in result):
        raise ValueError("tag_too_long")
    return result


def _normalize_header(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _detect_encoding(sample: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise JobExecutionError(
        "customer_import_encoding_invalid",
        "CSV must use UTF-8 or Windows-1251 encoding",
        retryable=False,
    )


def _cell_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _string_value(value: object) -> str:
    return "" if value is None else str(value).strip()


def _nullable_string(value: object) -> str | None:
    result = _string_value(value)
    return result or None


def _optional_datetime(value: object) -> datetime | None:
    text = _nullable_string(value)
    if text is None:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise JobExecutionError(
            "customer_import_datetime_invalid",
            "Customer import datetime must include a timezone",
            retryable=False,
        )
    return parsed.astimezone(UTC)


def _payload_uuid(payload: Mapping[str, object], key: str) -> UUID:
    try:
        return UUID(str(payload[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise JobExecutionError(
            "customer_import_payload_invalid",
            "Customer import job payload is invalid",
            retryable=False,
        ) from exc


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_dict(value: object) -> dict[str, Any]:
    parsed = _json_value(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    parsed = _json_value(value)
    return list(parsed) if isinstance(parsed, list) else []


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_object_key(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value and not path.is_absolute() and ".." not in path.parts)


async def _set_tenant(connection: asyncpg.Connection, tenant_id: UUID) -> None:
    await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))


async def _insert_import_realtime(
    connection: asyncpg.Connection,
    job: BackgroundJobClaim,
    import_id: UUID,
    event_type: str,
    payload: Mapping[str, object],
) -> None:
    await connection.execute(
        """
        INSERT INTO realtime_events
            (id, tenant_id, project_id, target_membership_id, event_type,
             aggregate_type, aggregate_id, aggregate_version, safe_payload,
             occurred_at, publish_status, publish_attempts, expires_at,
             correlation_id, causation_id, created_at, updated_at)
        VALUES (gen_random_uuid(),$1,$2,NULL,$3,'customer_import',$4,NULL,$5::jsonb,
                now(),'pending',0,now()+interval '24 hours',$6,$7,now(),now())
        """,
        job.tenant_id,
        job.project_id,
        event_type,
        import_id,
        json.dumps(dict(payload), separators=(",", ":")),
        job.correlation_id,
        job.id,
    )
