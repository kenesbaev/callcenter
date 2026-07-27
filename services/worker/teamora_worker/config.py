from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "staging", "production"] = "development"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    worker_queue: str = "teamora:jobs"
    worker_dead_letter_queue: str = "teamora:jobs:dead"


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
