from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "K-Line API"
    app_base_url: str = "http://localhost:8080"
    web_origin: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://localhost:8080"
    trusted_hosts: str = "localhost,127.0.0.1,testserver,api"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://teamora_app:local@localhost:5432/teamora_voice"
    migration_database_url: str | None = None
    database_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    dependency_health_timeout_seconds: float = Field(default=3.0, ge=0.5, le=15.0)
    redis_url: str = "redis://localhost:6379/0"
    realtime_retention_hours: int = Field(default=24, ge=1, le=168)
    realtime_replay_batch_size: int = Field(default=200, ge=10, le=1000)
    realtime_max_subscriptions: int = Field(default=50, ge=1, le=500)
    realtime_connections_per_membership: int = Field(default=5, ge=1, le=20)
    realtime_messages_per_minute: int = Field(default=120, ge=10, le=1000)
    realtime_max_message_bytes: int = Field(default=16 * 1024, ge=1024, le=64 * 1024)
    realtime_subscribe_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    realtime_send_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    realtime_database_sweep_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    realtime_auth_recheck_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    minio_endpoint: str = "http://localhost:9000"
    minio_bucket: str = "teamora-private"
    minio_region: str = "us-east-1"
    minio_root_user: str = ""
    minio_root_password: SecretStr = Field(default=SecretStr(""))
    knowledge_max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    knowledge_max_pdf_pages: int = Field(default=500, ge=1, le=2000)
    knowledge_max_extracted_chars: int = Field(default=5_000_000, ge=1000, le=20_000_000)
    knowledge_max_chunks: int = Field(default=10_000, ge=1, le=50_000)
    knowledge_max_docx_expanded_bytes: int = Field(default=200 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    knowledge_max_zip_ratio: int = Field(default=100, ge=2, le=1000)
    knowledge_chunk_size_chars: int = Field(default=1_800, ge=200, le=10_000)
    knowledge_chunk_overlap_chars: int = Field(default=220, ge=0, le=2_000)
    knowledge_embedding_provider: str = "mock"
    knowledge_embedding_model: str = "kline-deterministic-v1"
    knowledge_embedding_dimension: int = Field(default=64, ge=8, le=4096)
    background_import_max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    background_import_max_rows: int = Field(default=100_000, ge=501, le=1_000_000)
    background_import_preview_rows: int = Field(default=100, ge=10, le=1000)
    retention_default_grace_days: int = Field(default=7, ge=1, le=90)

    jwt_signing_key: SecretStr = Field(default=SecretStr("development-only-signing-key-change-me"))
    local_secret_encryption_key: SecretStr | None = None
    credential_store_backend: Literal["local", "vault", "kms"] = Field(
        default="local", validation_alias="SECRET_BACKEND"
    )
    local_secret_store_path: Path = Path(".teamora/local-secrets.enc")
    access_token_ttl_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_ttl_days: int = Field(default=14, ge=1, le=90)
    csrf_cookie_secure: bool = False

    openai_api_key: SecretStr | None = None
    openai_webhook_secret: SecretStr | None = None
    openai_realtime_model: str = "gpt-realtime-2.1-mini"
    openai_realtime_voice: str = "marin"

    karakalpak_experimental: bool = False
    enable_call_simulator: bool = False
    rate_limit_requests_per_minute: int = Field(default=120, ge=10, le=10_000)
    default_max_concurrent_calls: int = Field(default=5, ge=1, le=10_000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    gateway_service_token: SecretStr | None = None
    gateway_internal_url: str = "http://voice-gateway:8787"
    gateway_command_timeout_seconds: float = Field(default=5.0, ge=0.25, le=30.0)
    allow_mock_telephony_in_production: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def simulator_available(self) -> bool:
        return self.app_env in {"development", "test"} and self.enable_call_simulator

    @property
    def mock_telephony_available(self) -> bool:
        return self.app_env in {"development", "test"} or self.allow_mock_telephony_in_production

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> Settings:
        if self.app_env == "production":
            if self.jwt_signing_key.get_secret_value() == "development-only-signing-key-change-me":
                raise ValueError("JWT_SIGNING_KEY must be set in production")
            if not self.csrf_cookie_secure:
                raise ValueError("CSRF_COOKIE_SECURE must be true in production")
            if self.enable_call_simulator:
                raise ValueError("Call Simulator cannot be enabled in production")
            if self.credential_store_backend == "local":
                raise ValueError("SECRET_BACKEND must use vault or kms in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
