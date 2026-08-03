from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "staging", "production"] = "development"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://teamora_app:local@localhost:5432/teamora_voice"
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
