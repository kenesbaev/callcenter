from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    api_internal_url: str = "http://api:8000"
    gateway_service_token: SecretStr | None = None
    asterisk_recordings_path: Path = Path("/var/spool/asterisk/monitor")
    recording_max_file_bytes: int = Field(default=1024 * 1024 * 1024, ge=1024)
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
    background_job_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    background_job_lease_seconds: int = Field(default=120, ge=30, le=3600)
    background_job_heartbeat_seconds: float = Field(default=15.0, gt=0, le=300)
    background_job_handler_timeout_seconds: float = Field(default=900.0, gt=0, le=7200)
    background_job_shutdown_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    background_job_workers: int = Field(default=4, ge=1, le=32)
    background_job_tenant_concurrency: int = Field(default=4, ge=1, le=100)
    background_job_default_type_concurrency: int = Field(default=2, ge=1, le=100)
    background_job_type_concurrency: dict[str, int] = Field(
        default_factory=lambda: {
            "knowledge.ingest_document": 2,
            "customer_import.prepare_preview": 1,
            "customer_import.process": 1,
            "retention.purge": 1,
            "storage.orphan_scan": 1,
            "recording.upload": 1,
        },
        validation_alias="BACKGROUND_JOB_TYPE_CONCURRENCY_MAP",
    )
    background_job_priority_aging_seconds: int = Field(default=300, ge=30, le=86400)
    background_job_priority_age_cap: int = Field(default=20, ge=1, le=100)
    background_job_retry_base_seconds: float = Field(default=2.0, gt=0, le=60)
    background_job_retry_max_seconds: float = Field(default=300.0, gt=0, le=86400)
    background_job_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=0.5)
    scheduler_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    scheduler_advisory_key: str = "kline:background-scheduler:v1"
    scheduler_heartbeat_key: str = "kline:background-scheduler:health"
    scheduler_heartbeat_ttl_seconds: int = Field(default=15, ge=5, le=300)
    storage_scan_max_objects: int = Field(default=10_000, ge=100, le=100_000)
    storage_consistency_grace_days: int = Field(default=7, ge=1, le=90)
    customer_import_max_file_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        validation_alias="BACKGROUND_IMPORT_MAX_FILE_BYTES",
    )
    customer_import_max_rows: int = Field(
        default=100_000,
        ge=1,
        le=1_000_000,
        validation_alias="BACKGROUND_IMPORT_MAX_ROWS",
    )
    customer_import_max_columns: int = Field(default=100, ge=1, le=500)
    customer_import_max_sheets: int = Field(default=10, ge=1, le=100)
    customer_import_max_xlsx_expanded_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1024,
    )
    customer_import_max_zip_ratio: int = Field(default=100, ge=2, le=1000)
    customer_import_preview_rows: int = Field(
        default=50,
        ge=1,
        le=500,
        validation_alias="BACKGROUND_IMPORT_PREVIEW_ROWS",
    )
    customer_import_batch_rows: int = Field(default=500, ge=10, le=5000)
    retention_scan_batch_size: int = Field(default=500, ge=1, le=5_000)
    retention_purge_batch_size: int = Field(default=25, ge=1, le=500)
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
