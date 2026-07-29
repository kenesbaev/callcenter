import asyncio

from redis.asyncio import Redis

from teamora_worker.config import get_settings


async def check() -> None:
    client = Redis.from_url(get_settings().redis_url)
    try:
        if not await client.ping():
            raise SystemExit(1)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(check())
