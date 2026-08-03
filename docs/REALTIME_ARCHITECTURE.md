# K-Line realtime architecture

## Boundary and source of truth

PostgreSQL and the existing REST API remain the source of truth. A WebSocket
message is a small invalidation signal; it is never a replacement for a complete
`Call`, task, operator, or analytics response.

The delivery path is:

```text
business transaction
  -> realtime_events (same PostgreSQL transaction)
  -> Worker publisher (FOR UPDATE SKIP LOCKED)
  -> Redis kline:realtime:v1 notification
  -> every FastAPI WebSocket instance
  -> authorized browser subscription
  -> TanStack Query refetch of canonical REST state
```

FastAPI also performs a bounded PostgreSQL sweep for connected clients. This
recovers events when Redis was unavailable between two successful connections.
Redis is a low-latency notification bus, not durable event storage.

## Delivery guarantees

- Delivery is **at least once**, never exactly once.
- A crash after Redis publish and before the PostgreSQL status update can produce
  a duplicate. The browser deduplicates by `event_id`.
- The database identity `cursor` provides ordering. Aggregate consumers also
  reject stale `aggregate_version` values through their existing REST state.
- Events expire after the configured retention window (24 hours by default).
- A cursor older than retained tenant events receives `resync_required`; the
  browser invalidates all realtime-backed REST queries and continues from the
  retained window. A non-zero cursor with no retained events is also treated
  conservatively as requiring a full resync.
- Publisher retries use bounded exponential backoff. Publishing is idempotent;
  duplicate Redis notifications do not duplicate business state.

## Authentication and authorization

`/api/v1/realtime/ws` uses the existing same-origin HttpOnly `tv_access` cookie.
No JWT or invitation token is accepted in the URL. The handshake validates the
`Origin`, user, tenant, membership, role, tenant status, and active flags against
PostgreSQL. Membership and role are revalidated while the socket is open.

Requested tenant or membership identifiers are never trusted. Requested project
subscriptions are intersected with current `ProjectUser` access. Targeted Dialer
events are restricted to one membership. Operators receive only their calls,
their assigned or unassigned tasks, assigned projects, and colleague status
signals for shared projects. Owner, manager, and analyst access continues to use
the existing backend RBAC and RLS rules.

Role changes close the connection at the next authorization recheck. Project
assignment changes take effect on the next database replay query. Blocking a
membership invalidates the cookie-backed session and closes the connection.

## Event envelope

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "cursor": 123,
  "event_type": "call.state_changed",
  "occurred_at": "RFC3339 UTC timestamp",
  "project_id": "uuid or null",
  "aggregate_type": "call",
  "aggregate_id": "uuid",
  "aggregate_version": 4,
  "payload": { "status": "active" }
}
```

Payloads are scalar, bounded invalidation metadata. Passwords, tokens, provider
credentials, recordings, transcripts, and full telephone numbers are rejected.

Supported version 1 event families are calls, transfers, Dialer assignments,
tasks/callbacks, team presence/status, and `analytics.invalidated`. Analytics
responses are never transported over WebSocket.

## Connection lifecycle and limits

The client subscribes after the cookie-authenticated handshake, resumes from its
local durable cursor, sends ping messages, and reconnects with exponential
backoff plus jitter. Server limits cover subscribe timeout, message size,
messages per minute, projects per subscription, sockets per membership, and
outbound send time. The server does not allocate an unbounded per-socket queue:
it streams one bounded replay batch from PostgreSQL and closes a slow client on
the configured send timeout.

Each browser tab loads the persisted cursor once, then advances an independent
in-memory cursor. This prevents one tab from suppressing delivery in another
tab while retaining durable reconnect progress. Browser `offline` closes the
socket and enables fallback polling; `online` reconnects and performs the REST
resync before normal realtime processing resumes.

After reconnect the client first performs a REST resync, then applies replayed
signals. Logout is broadcast to other tabs with `BroadcastChannel`; tenant/user
changes dispose the old client. Presence heartbeat remains the independent
business heartbeat from Stage 10 and is not inferred from WebSocket traffic.

## Polling fallback

When connected, operational screens keep only a low-frequency consistency
refresh. During disconnect or reconnect they use bounded fallback polling.
Realtime signals invalidate the existing TanStack Query keys and therefore do
not overwrite unsaved Dialer comments, result selection, or scenario state.

## Nginx and configuration

Nginx exposes only the same-origin `/api/v1/realtime/ws` route with Upgrade
headers, disabled buffering, bounded request size, and timeouts. The internal
Voice Gateway route remains unavailable publicly.

Configuration names (values are deployment-specific):

- `REDIS_URL`
- `DATABASE_URL`
- `CORS_ORIGINS`
- `REALTIME_RETENTION_HOURS`
- `REALTIME_REPLAY_BATCH_SIZE`
- `REALTIME_MAX_SUBSCRIPTIONS`
- `REALTIME_CONNECTIONS_PER_MEMBERSHIP`
- `REALTIME_MESSAGES_PER_MINUTE`
- `REALTIME_MAX_MESSAGE_BYTES`
- `REALTIME_DATABASE_SWEEP_SECONDS`
- `REALTIME_AUTH_RECHECK_SECONDS`

`NEXT_PUBLIC_REALTIME_URL` exists only for the isolated local Playwright runner,
where Next.js and FastAPI use different ports. Normal Docker/Nginx operation is
same-origin and does not configure it.

## Observability and current limits

Prometheus exposes connection, replay, resync, reconnect, and slow-client
counters. Worker publisher heartbeat exposes publish totals, failures, lag, and
the oldest unpublished event through readiness diagnostics without making REST
unavailable during a brief Redis outage.

This stage is verified only with Mock telephony. It does not add live SIP, ARI,
WebRTC, OpenAI Realtime audio, or a production deployment.
