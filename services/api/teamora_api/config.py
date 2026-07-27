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
    minio_endpoint: str = "http://localhost:9000"
    minio_bucket: str = "teamora-private"
    minio_region: str = "us-east-1"

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def simulator_available(self) -> bool:
        return self.app_env == "development" and self.enable_call_simulator

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
