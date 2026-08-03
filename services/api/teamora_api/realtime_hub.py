from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import WebSocket
from prometheus_client import Counter, Gauge
from redis.asyncio import Redis
from sqlalchemy import select

from teamora_api.config import Settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.dependencies import Principal
from teamora_api.enums import TenantStatus
from teamora_api.logging import get_logger
from teamora_api.models import Membership, Tenant, User
from teamora_api.realtime import REALTIME_REDIS_CHANNEL
from teamora_api.security import ACCESS_COOKIE, decode_access_token

log = get_logger()

REALTIME_CONNECTIONS = Gauge(
    "kline_realtime_connections",
    "Current realtime WebSocket connections",
)
REALTIME_AUTHENTICATED_CONNECTIONS = Gauge(
    "kline_realtime_authenticated_connections",
    "Current authenticated realtime WebSocket connections",
)
REALTIME_RECONNECTS = Counter(
    "kline_realtime_reconnects_total",
    "Realtime connections that resumed from a durable cursor",
)
REALTIME_REPLAYS = Counter(
    "kline_realtime_replays_total",
    "Events replayed from PostgreSQL",
)
REALTIME_RESYNCS = Counter(
    "kline_realtime_resyncs_total",
    "Clients asked to perform a full REST resync",
)
REALTIME_DROPPED_SLOW_CLIENTS = Counter(
    "kline_realtime_dropped_slow_clients_total",
    "Realtime clients closed because outbound delivery timed out",
)
REALTIME_EVENTS_PUBLISHED = Gauge(
    "kline_realtime_events_published",
    "Events published by the current Worker process",
)
REALTIME_PUBLISH_FAILURES = Gauge(
    "kline_realtime_publish_failures",
    "Publish failures observed by the current Worker process",
)
REALTIME_PUBLISHER_LAG = Gauge(
    "kline_realtime_publisher_lag_seconds",
    "Age of the oldest unpublished realtime event",
)


@dataclass
class RealtimeConnection:
    id: UUID
    websocket: WebSocket
    principal: Principal
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class RealtimeRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connections: dict[UUID, RealtimeConnection] = {}
        self._listener_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.redis_connected = False
        self.last_notification_at: datetime | None = None

    async def start(self) -> None:
        self._stopping.clear()
        self._listener_task = asyncio.create_task(self._redis_listener(), name="realtime-redis-listener")

    async def stop(self) -> None:
        self._stopping.set()
        sockets = [connection.websocket for connection in self.connections.values()]

        async def close_socket(socket: WebSocket) -> None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    socket.send_json({"type": "server_shutdown", "retry_after_ms": 1_000}),
                    timeout=0.5,
                )
            with contextlib.suppress(Exception):
                await asyncio.wait_for(socket.close(code=1012, reason="service restart"), timeout=0.5)

        if sockets:
            await asyncio.gather(*(close_socket(socket) for socket in sockets))
        if self._listener_task is not None:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
        self.connections.clear()
        REALTIME_CONNECTIONS.set(0)
        REALTIME_AUTHENTICATED_CONNECTIONS.set(0)

    def register(self, websocket: WebSocket, principal: Principal) -> RealtimeConnection:
        existing_for_membership = sum(
            connection.principal.membership_id == principal.membership_id
            for connection in self.connections.values()
        )
        if existing_for_membership >= self.settings.realtime_connections_per_membership:
            raise ValueError("realtime_connection_limit")
        connection = RealtimeConnection(uuid4(), websocket, principal)
        self.connections[connection.id] = connection
        REALTIME_CONNECTIONS.set(len(self.connections))
        REALTIME_AUTHENTICATED_CONNECTIONS.set(len(self.connections))
        return connection

    def unregister(self, connection: RealtimeConnection) -> None:
        self.connections.pop(connection.id, None)
        REALTIME_CONNECTIONS.set(len(self.connections))
        REALTIME_AUTHENTICATED_CONNECTIONS.set(len(self.connections))

    def wake_all(self) -> None:
        for connection in self.connections.values():
            connection.wake.set()

    async def _redis_listener(self) -> None:
        delay = 0.5
        while not self._stopping.is_set():
            client = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=self.settings.dependency_health_timeout_seconds,
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.subscribe(REALTIME_REDIS_CHANNEL)
                self.redis_connected = True
                delay = 0.5
                while not self._stopping.is_set():
                    message = await pubsub.get_message(timeout=1.0)
                    if message is None:
                        await self._refresh_publisher_metrics(client)
                        continue
                    try:
                        notification = json.loads(str(message["data"]))
                        if not isinstance(notification.get("cursor"), int):
                            continue
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        log.warning("realtime_notification_invalid")
                        continue
                    self.last_notification_at = datetime.now(UTC)
                    self.wake_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.redis_connected = False
                log.warning("realtime_redis_listener_unavailable", error_type=type(exc).__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)
            finally:
                self.redis_connected = False
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                await client.aclose()

    async def _refresh_publisher_metrics(self, client: Redis) -> None:
        raw = await client.get("kline:realtime:publisher:health")
        if not raw:
            return
        try:
            health = json.loads(raw)
            REALTIME_EVENTS_PUBLISHED.set(float(health.get("events_published", 0)))
            REALTIME_PUBLISH_FAILURES.set(float(health.get("publish_failures", 0)))
            REALTIME_PUBLISHER_LAG.set(float(health.get("oldest_unpublished_seconds", 0)))
        except (TypeError, ValueError, json.JSONDecodeError):
            log.warning("realtime_publisher_health_invalid")


async def authenticate_websocket(websocket: WebSocket, settings: Settings) -> Principal | None:
    origin = websocket.headers.get("origin")
    if origin not in set(settings.cors_origin_list):
        return None
    claims = decode_access_token(websocket.cookies.get(ACCESS_COOKIE, ""), settings)
    if claims is None:
        return None
    async with SessionFactory() as session:
        await set_tenant_context(session, claims.tenant_id)
        row = (
            await session.execute(
                select(User, Membership, Tenant)
                .join(Membership, Membership.user_id == User.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    User.id == claims.user_id,
                    User.is_active.is_(True),
                    Membership.id == claims.membership_id,
                    Membership.tenant_id == claims.tenant_id,
                    Membership.is_active.is_(True),
                    Membership.role == claims.role,
                    Tenant.status == TenantStatus.ACTIVE,
                )
            )
        ).one_or_none()
    if row is None:
        return None
    user, membership, tenant = row
    return Principal(
        user_id=user.id,
        tenant_id=tenant.id,
        membership_id=membership.id,
        role=membership.role,
        display_name=user.display_name,
        email=user.email,
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
    )


async def membership_is_current(principal: Principal) -> bool:
    async with SessionFactory() as session:
        await set_tenant_context(session, principal.tenant_id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.tenant_id == principal.tenant_id,
                Membership.id == principal.membership_id,
                Membership.user_id == principal.user_id,
                Membership.role == principal.role,
                Membership.is_active.is_(True),
            )
        )
        return membership is not None


runtime: RealtimeRuntime | None = None


def get_realtime_runtime(settings: Settings) -> RealtimeRuntime:
    global runtime
    if runtime is None:
        runtime = RealtimeRuntime(settings)
    return runtime
