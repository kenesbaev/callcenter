from __future__ import annotations

import asyncio
import hashlib
import re
import wave
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import urllib3
from minio import Minio
from minio.error import S3Error
from minio.sse import SseS3

from teamora_worker.background_jobs import (
    BackgroundJobClaim,
    JobExecutionContext,
    JobExecutionError,
    JobResult,
)
from teamora_worker.config import WorkerSettings


class RecordingUploadProcessor:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        parsed = urlparse(settings.minio_endpoint)
        self.storage = Minio(
            parsed.netloc or parsed.path,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=parsed.scheme == "https",
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5.0, read=30.0), retries=False
            ),
        )

    async def handle(self, job: BackgroundJobClaim, context: JobExecutionContext) -> JobResult:
        recording_id = _uuid(job.safe_payload.get("recording_id"), "recording_id")
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", str(job.tenant_id)
                )
                row = await connection.fetchrow(
                    """
                    SELECT recording.id, recording.call_id, recording.provider_recording_id,
                           recording.storage_key, recording.status, recording.storage_object_id,
                           call.project_id
                    FROM call_recordings recording
                    JOIN calls call ON call.tenant_id=recording.tenant_id
                                   AND call.id=recording.call_id
                    WHERE recording.tenant_id=$1 AND recording.id=$2
                    FOR UPDATE
                    """,
                    job.tenant_id,
                    recording_id,
                )
                if row is None:
                    raise JobExecutionError(
                        "recording_not_found", "Recording metadata was not found", retryable=False
                    )
                if row["status"] == "available" and row["storage_object_id"] is not None:
                    return JobResult({"recording_id": str(recording_id), "already_uploaded": True})
                provider_id = str(row["provider_recording_id"] or "")
                if re.fullmatch(r"teamora-[0-9a-fA-F-]{36}", provider_id) is None:
                    raise JobExecutionError(
                        "recording_provider_id_invalid",
                        "Recording provider identifier is invalid",
                        retryable=False,
                    )
                await connection.execute(
                    "UPDATE call_recordings SET status='uploading', updated_at=now() "
                    "WHERE tenant_id=$1 AND id=$2",
                    job.tenant_id,
                    recording_id,
                )
        context.ensure_active()
        source = (self.settings.asterisk_recordings_path / f"{provider_id}.wav").resolve()
        root = self.settings.asterisk_recordings_path.resolve()
        if root not in source.parents:
            raise JobExecutionError(
                "recording_path_invalid", "Recording source path is invalid", retryable=False
            )
        if not source.is_file():
            raise JobExecutionError(
                "recording_not_ready", "Recording file is not ready", retryable=True
            )
        size = source.stat().st_size
        if size <= 0 or size > self.settings.recording_max_file_bytes:
            raise JobExecutionError(
                "recording_size_invalid",
                "Recording file size is outside configured limits",
                retryable=False,
            )
        checksum = await asyncio.to_thread(_sha256, source)
        duration = await asyncio.to_thread(_wav_duration, source)
        try:
            await asyncio.to_thread(
                self.storage.fput_object,
                self.settings.minio_bucket,
                str(row["storage_key"]),
                str(source),
                content_type="audio/wav",
                metadata={"checksum-sha256": checksum},
                sse=SseS3(),
            )
        except S3Error as exc:
            raise JobExecutionError(
                "recording_storage_unavailable",
                "Recording object upload failed",
                retryable=True,
            ) from exc
        context.ensure_active()
        storage_object_id = uuid4()
        async with context.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", str(job.tenant_id)
                )
                stored_id = await connection.fetchval(
                    """
                    INSERT INTO storage_objects
                        (id,tenant_id,project_id,bucket,object_key,category,
                         owner_aggregate_type,owner_aggregate_id,checksum_sha256,size_bytes,
                         content_type,status,retention_state,legal_hold,lock_version,
                         created_at,updated_at,last_verified_at)
                    VALUES ($1,$2,$3,$4,$5,'call_recording','call_recording',$6,$7,$8,
                            'audio/wav','active','retained',false,1,now(),now(),now())
                    ON CONFLICT (bucket,object_key) DO UPDATE
                    SET checksum_sha256=EXCLUDED.checksum_sha256,
                        size_bytes=EXCLUDED.size_bytes,status='active',updated_at=now(),
                        last_verified_at=now()
                    RETURNING id
                    """,
                    storage_object_id,
                    job.tenant_id,
                    row["project_id"],
                    self.settings.minio_bucket,
                    row["storage_key"],
                    recording_id,
                    checksum,
                    size,
                )
                await connection.execute(
                    """
                    UPDATE call_recordings
                    SET storage_object_id=$3, checksum_sha256=$4, size_bytes=$5,
                        duration_seconds=$6, status='available', updated_at=now()
                    WHERE tenant_id=$1 AND id=$2
                    """,
                    job.tenant_id,
                    recording_id,
                    stored_id,
                    checksum,
                    size,
                    duration,
                )
        await context.update_progress(100, detail={"recording_id": str(recording_id)})
        return JobResult(
            {
                "recording_id": str(recording_id),
                "storage_object_id": str(stored_id),
                "size_bytes": size,
                "duration_seconds": duration,
            }
        )


def _uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise JobExecutionError(
            "recording_payload_invalid", f"{field} is invalid", retryable=False
        ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _wav_duration(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as audio:
            return max(0, round(audio.getnframes() / max(1, audio.getframerate())))
    except (wave.Error, EOFError):
        return 0
