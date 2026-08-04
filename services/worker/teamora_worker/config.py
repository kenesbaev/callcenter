from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "staging", "production"] = "development"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://teamora_app:local@localhost:5432/teamora_voice"
    minio_endpoint: str = "http://localhost:9000"
    minio_bucket: str = "teamora-private"
    minio_root_user: str = ""
    minio_root_password: str = ""
    knowledge_max_pdf_pages: int = 500
    knowledge_max_file_bytes: int = 25 * 1024 * 1024
    knowledge_max_extracted_chars: int = 5_000_000
    knowledge_max_chunks: int = 10_000
    knowledge_max_docx_expanded_bytes: int = 200 * 1024 * 1024
    knowledge_max_zip_ratio: int = 100
    knowledge_parser_timeout_seconds: int = 30
    knowledge_chunk_size_chars: int = 1_800
    knowledge_chunk_overlap_chars: int = 220
    knowledge_embedding_provider: str = "mock"
    knowledge_embedding_model: str = "kline-deterministic-v1"
    knowledge_embedding_dimension: int = 64
    knowledge_job_poll_seconds: float = 0.5
    knowledge_job_lease_seconds: int = 120
    knowledge_job_max_attempts: int = 4
    log_level: str = "INFO"
    worker_queue: str = "teamora:jobs"
    worker_dead_letter_queue: str = "teamora:jobs:dead"
    realtime_channel: str = "kline:realtime:v1"
    realtime_publisher_batch_size: int = 100
    realtime_publisher_poll_seconds: float = 0.5
    realtime_retention_cleanup_seconds: float = 300.0
    realtime_publisher_heartbeat_key: str = "kline:realtime:publisher:health"

    @property
    def asyncpg_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
