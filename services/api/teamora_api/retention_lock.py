from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def retention_lock_key(tenant_id: UUID) -> str:
    return f"retention-tenant:{tenant_id}"


async def acquire_retention_lock(session: AsyncSession, tenant_id: UUID) -> None:
    """Serialize irreversible purge against policy, hold, and reference changes.

    The lock is transaction-scoped. It is deliberately tenant-wide because
    legal holds can cover a whole tenant and must never race a final purge.
    """

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": retention_lock_key(tenant_id)},
    )
