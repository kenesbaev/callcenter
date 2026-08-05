from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlparse
from uuid import UUID

from minio import Minio

from teamora_api.config import Settings


class ObjectStorageUnavailable(RuntimeError):
    """Raised when private object storage cannot be used safely."""


@dataclass(frozen=True)
class StoredObjectMetadata:
    checksum_sha256: str
    size_bytes: int
    content_type: str


class PrivateObjectStorage:
    """Small private-bucket adapter; object keys are always server generated."""

    def __init__(self, settings: Settings) -> None:
        parsed = urlparse(settings.minio_endpoint)
        endpoint = parsed.netloc or parsed.path
        password = settings.minio_root_password.get_secret_value()
        if not endpoint or not settings.minio_root_user or not password:
            raise ObjectStorageUnavailable("Private object storage is not configured")
        self.bucket = settings.minio_bucket
        self.client = Minio(
            endpoint,
            access_key=settings.minio_root_user,
            secret_key=password,
            secure=parsed.scheme == "https",
            region=settings.minio_region,
        )

    async def put_bytes(self, *, key: str, data: bytes, content_type: str) -> StoredObjectMetadata:
        checksum = sha256(data).hexdigest()
        await asyncio.to_thread(
            self.client.put_object,
            self.bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
            metadata={"sha256": checksum},
        )
        return StoredObjectMetadata(
            checksum_sha256=checksum,
            size_bytes=len(data),
            content_type=content_type,
        )

    async def stat(self, *, key: str) -> StoredObjectMetadata:
        result = await asyncio.to_thread(self.client.stat_object, self.bucket, key)
        metadata = result.metadata or {}
        checksum = str(metadata.get("x-amz-meta-sha256", ""))
        return StoredObjectMetadata(
            checksum_sha256=checksum,
            size_bytes=int(result.size or 0),
            content_type=str(result.content_type or "application/octet-stream"),
        )

    async def presigned_download(self, *, key: str, filename: str) -> str:
        safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")[:255]
        return await asyncio.to_thread(
            self.client.presigned_get_object,
            self.bucket,
            key,
            expires=timedelta(minutes=5),
            response_headers={"response-content-disposition": f'attachment; filename="{safe_name}"'},
        )

    async def remove(self, *, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)


def import_object_key(*, tenant_id: UUID, project_id: UUID, import_id: UUID, kind: str) -> str:
    if kind not in {"source", "report", "preview"}:
        raise ValueError("Unsupported import object kind")
    return f"tenants/{tenant_id}/projects/{project_id}/imports/{import_id}/{kind}"


def controlled_object_key(*, tenant_id: UUID, project_id: UUID | None, category: str, object_id: UUID) -> str:
    safe_category = category.replace("_", "-")
    if not safe_category.replace("-", "").isalnum():
        raise ValueError("Unsupported storage object category")
    project_part = str(project_id) if project_id is not None else "tenant"
    return f"tenants/{tenant_id}/projects/{project_part}/{safe_category}/{object_id}"
