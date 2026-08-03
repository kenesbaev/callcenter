from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from teamora_api.config import get_settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.logging import get_logger
from teamora_api.realtime import event_envelope, normalize_requested_projects, replay_events
from teamora_api.realtime_hub import (
    REALTIME_DROPPED_SLOW_CLIENTS,
    REALTIME_RECONNECTS,
    REALTIME_REPLAYS,
    REALTIME_RESYNCS,
    authenticate_websocket,
    get_realtime_runtime,
    membership_is_current,
)

router = APIRouter(prefix="/realtime", tags=["realtime"])
log = get_logger()


async def _send(websocket: WebSocket, payload: dict[str, object]) -> None:
    settings = get_settings()
    try:
        await asyncio.wait_for(websocket.send_json(payload), timeout=settings.realtime_send_timeout_seconds)
    except TimeoutError:
        REALTIME_DROPPED_SLOW_CLIENTS.inc()
        await websocket.close(code=1013, reason="slow client")
        raise


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    settings = get_settings()
    principal = await authenticate_websocket(websocket, settings)
    if principal is None:
        await websocket.close(code=1008, reason="authentication required")
        return
    await websocket.accept()
    runtime = get_realtime_runtime(settings)
    try:
        connection = runtime.register(websocket, principal)
    except ValueError:
        await websocket.close(code=1008, reason="connection limit")
        return

    correlation_id = str(connection.id)
    cursor = 0
    projects: set[UUID] = set()
    last_membership_check = datetime.now(UTC)
    inbound_count = 0
    inbound_window = datetime.now(UTC)
    receive_task: asyncio.Task[object] | None = None
    try:
        try:
            initial = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.realtime_subscribe_timeout_seconds
            )
        except TimeoutError:
            await websocket.close(code=1008, reason="subscribe timeout")
            return
        if initial.get("type") != "subscribe":
            await websocket.close(code=1008, reason="subscribe required")
            return
        if len(json.dumps(initial, separators=(",", ":")).encode()) > settings.realtime_max_message_bytes:
            await websocket.close(code=1009, reason="message too large")
            return
        cursor = max(0, int(initial.get("cursor", 0)))
        projects = normalize_requested_projects(
            initial.get("project_ids", []), maximum=settings.realtime_max_subscriptions
        )
        if cursor:
            REALTIME_RECONNECTS.inc()
        await _send(
            websocket,
            {
                "type": "subscribed",
                "schema_version": 1,
                "cursor": cursor,
                "correlation_id": correlation_id,
            },
        )
        receive_task = asyncio.create_task(websocket.receive_json())

        while True:
            # Clear before the durable replay. A notification arriving while the
            # database is read remains set and triggers the next pass instead of
            # being lost between the query and the wait.
            connection.wake.clear()
            async with SessionFactory() as session:
                await set_tenant_context(session, principal.tenant_id)
                events, resync_required, scanned_cursor = await replay_events(
                    session,
                    principal=principal,
                    cursor=cursor,
                    project_ids=projects,
                    limit=settings.realtime_replay_batch_size,
                )
            if resync_required:
                REALTIME_RESYNCS.inc()
                await _send(websocket, {"type": "resync_required", "cursor": cursor})
                cursor = 0
            for event in events:
                await _send(websocket, {"type": "event", "event": event_envelope(event)})
                cursor = max(cursor, event.cursor)
                REALTIME_REPLAYS.inc()
                if (
                    event.event_type == "team.member_blocked"
                    and event.aggregate_id == principal.membership_id
                ):
                    await websocket.close(code=1008, reason="membership blocked")
                    return
            if scanned_cursor > cursor:
                cursor = scanned_cursor
                await _send(websocket, {"type": "cursor", "cursor": cursor})
            if len(events) == settings.realtime_replay_batch_size:
                continue

            now = datetime.now(UTC)
            if (now - last_membership_check).total_seconds() >= settings.realtime_auth_recheck_seconds:
                if not await membership_is_current(principal):
                    await websocket.close(code=1008, reason="membership changed")
                    return
                last_membership_check = now

            wake_task = asyncio.create_task(connection.wake.wait())
            done, _pending = await asyncio.wait(
                {receive_task, wake_task},
                timeout=settings.realtime_database_sweep_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wake_task not in done:
                wake_task.cancel()
                await asyncio.gather(wake_task, return_exceptions=True)
            if receive_task not in done:
                continue
            message = receive_task.result()
            receive_task = asyncio.create_task(websocket.receive_json())
            if not isinstance(message, dict):
                await websocket.close(code=1008, reason="invalid message")
                return
            if len(json.dumps(message, separators=(",", ":")).encode()) > settings.realtime_max_message_bytes:
                await websocket.close(code=1009, reason="message too large")
                return
            if (now - inbound_window).total_seconds() >= 60:
                inbound_count = 0
                inbound_window = now
            inbound_count += 1
            if inbound_count > settings.realtime_messages_per_minute:
                await websocket.close(code=1008, reason="rate limit")
                return
            message_type = message.get("type")
            if message_type == "ping":
                await _send(websocket, {"type": "pong", "cursor": cursor})
            elif message_type == "subscribe":
                projects = normalize_requested_projects(
                    message.get("project_ids", []), maximum=settings.realtime_max_subscriptions
                )
                requested_cursor = max(0, int(message.get("cursor", cursor)))
                cursor = min(cursor, requested_cursor) if requested_cursor else cursor
            elif message_type == "unsubscribe":
                projects = set()
            else:
                await websocket.close(code=1008, reason="unsupported message")
                return
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except (TypeError, ValueError):
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="invalid message")
    except Exception as exc:
        log.warning(
            "realtime_connection_closed",
            correlation_id=correlation_id,
            error_type=type(exc).__name__,
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="realtime error")
    finally:
        if receive_task is not None and not receive_task.done():
            receive_task.cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.gather(receive_task, return_exceptions=True), timeout=0.5)
        runtime.unregister(connection)
