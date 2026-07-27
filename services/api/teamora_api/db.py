from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, func, text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from teamora_api.config import get_settings
from teamora_api.errors import ApiError
from teamora_api.logging import get_logger

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantOwnedMixin(TimestampMixin):
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )


settings = get_settings()
engine_options: dict[str, object] = {"pool_pre_ping": True, "pool_recycle": 900}
if settings.database_url.startswith("postgresql+asyncpg"):
    engine_options["connect_args"] = {"timeout": settings.database_connect_timeout_seconds}
    engine_options["pool_timeout"] = settings.database_connect_timeout_seconds
engine = create_async_engine(settings.database_url, **engine_options)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
logger = get_logger()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
        except (TimeoutError, OperationalError, InterfaceError) as exc:
            await session.rollback()
            logger.warning("database_unavailable", error_type=type(exc).__name__)
            raise ApiError(
                503,
                "database_unavailable",
                "Сервис временно недоступен. Попробуйте ещё раз.",
            ) from exc


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


@asynccontextmanager
async def tenant_transaction(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        yield session
