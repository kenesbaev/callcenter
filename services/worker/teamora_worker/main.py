from __future__ import annotations

import asyncio
import signal

import asyncpg
import structlog
from redis.asyncio import Redis

from teamora_worker.background_jobs import BackgroundJobProcessor
from teamora_worker.config import get_settings
from teamora_worker.knowledge_ingestion import KnowledgeIngestionProcessor
from teamora_worker.maintenance import MaintenanceHandlers
from teamora_worker.realtime_publisher import RealtimePublisher
from teamora_worker.scheduler import BackgroundJobScheduler

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(service="worker")


async def run() -> None:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_: object) -> None:
        loop.call_soon_threadsafe(stopping.set)

    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, stopping.set)
        except NotImplementedError:
            signal.signal(name, request_stop)
    publisher = RealtimePublisher(settings, client)
    knowledge = KnowledgeIngestionProcessor(settings)
    pool = await asyncpg.create_pool(
        settings.asyncpg_database_url,
        min_size=1,
        max_size=max(8, settings.background_job_workers + 4),
        timeout=5,
    )
    handlers = MaintenanceHandlers(settings, knowledge).registry()
    processor = BackgroundJobProcessor(settings, pool, handlers)
    scheduler = BackgroundJobScheduler(settings, pool, client)
    log.info("worker_started", queue=settings.worker_queue)
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(publisher.run(stopping))
            tasks.create_task(processor.run(stopping))
            tasks.create_task(scheduler.run(stopping))
    finally:
        await pool.close()
        await client.aclose()
        log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
