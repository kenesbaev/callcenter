from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from urllib.parse import urlparse

from minio import Minio

from teamora_api.config import Settings


class KnowledgeStorageUnavailable(RuntimeError):
    pass


class KnowledgeObjectStorage:
    def __init__(self, settings: Settings) -> None:
        parsed = urlparse(settings.minio_endpoint)
        endpoint = parsed.netloc or parsed.path
        if (
            not endpoint
            or not settings.minio_root_user
            or not settings.minio_root_password.get_secret_value()
        ):
            raise KnowledgeStorageUnavailable("Private object storage is not configured")
        self.bucket = settings.minio_bucket
        self.client = Minio(
            endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password.get_secret_value(),
            secure=parsed.scheme == "https",
            region=settings.minio_region,
        )

    async def put(self, *, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            self.bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )

    async def remove(self, *, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)

    async def presigned_download(self, *, key: str, filename: str) -> str:
        return await asyncio.to_thread(
            self.client.presigned_get_object,
            self.bucket,
            key,
            expires=timedelta(minutes=5),
            response_headers={"response-content-disposition": f'attachment; filename="{filename}"'},
        )


def knowledge_object_key(
    *,
    tenant_id: object,
    project_id: object,
    knowledge_base_id: object,
    document_id: object,
    version_id: object,
) -> str:
    return (
        f"knowledge/{tenant_id}/{project_id}/{knowledge_base_id}/"
        f"documents/{document_id}/versions/{version_id}/original"
    )
