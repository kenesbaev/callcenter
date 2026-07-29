from __future__ import annotations

import asyncio
import signal

import structlog
from redis.asyncio import Redis

from teamora_worker.config import get_settings
from teamora_worker.jobs import JobEnvelope

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
    log.info("worker_started", queue=settings.worker_queue)
    try:
        while not stopping.is_set():
            item = await client.brpop(settings.worker_queue, timeout=1)
            if item is None:
                continue
            _, raw = item
            try:
                job = JobEnvelope.model_validate_json(raw)
                # Job handlers are introduced per domain. Unknown/unwired jobs fail closed.
                await client.lpush(settings.worker_dead_letter_queue, job.model_dump_json())
                log.warning(
                    "job_unavailable",
                    job_type=job.job_type,
                    tenant_id=str(job.tenant_id),
                    idempotency_key=job.idempotency_key,
                )
            except Exception as error:
                log.error("invalid_job", error_type=type(error).__name__)
    finally:
        await client.aclose()
        log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
