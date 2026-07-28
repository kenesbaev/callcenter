# K-Line CallCenter migration plan

Updated: 2026-07-28

## Target architecture

```text
Next.js web application
        ↓ REST / WebSocket
FastAPI control plane
        ↓
PostgreSQL / Redis / MinIO
        ↓
Voice Gateway / TelephonyProvider
        ↓
Asterisk / UzCloud SIP
        ↓
OpenAI Realtime
```

The repository already uses Next.js and FastAPI as separate services. There is no
runtime Prisma layer in the current codebase, so no Prisma client remains to be removed.
The migration is an incremental extension of the existing SQLAlchemy/Alembic domain,
not a rewrite.

## Completed foundation

- tenant-aware JWT/cookie authentication, RBAC, CSRF, audit logging, and PostgreSQL RLS;
- SQLAlchemy 2 models and versioned Alembic migrations;
- customer, Mock call, call result, callback, transcript, analytics, and AI operator APIs;
- operator Dialer with PostgreSQL `FOR UPDATE SKIP LOCKED` assignment;
- Next.js CRM pages that call FastAPI only;
- Redis, PostgreSQL, and MinIO local services in Docker Compose;
- Voice Gateway provider contracts and mocked OpenAI Realtime session tests.

## Project migration implemented in this stage

Migration `c24a8f6e2d10` performs the following
without deleting existing CRM data:

1. Create `projects` and `project_users` with tenant RLS.
2. Create one `Основной проект` for every existing tenant.
3. Assign active existing memberships to that project.
4. Backfill required `project_id` values on customers, contacts, calls, callbacks,
   AI operators, and call flows.
5. Enforce composite tenant/project foreign keys.
6. Change phone uniqueness from tenant-wide to project-wide.
7. Add a unique Dialer lease token.

New tenant registration creates the default project and owner assignment inside the
same database transaction.

## Dialer corrections implemented in this stage

- callbacks assigned to another user are excluded from operator queue selection;
- operators see and complete only their own callback tasks;
- selected callbacks move to `in_progress` and return to `pending` on explicit release;
- API serialization happens before commit, preserving transaction-local RLS context;
- heartbeat renews the 15-minute lease;
- stale lease tokens cannot start calls, renew leases, or release clients;
- active and result-pending calls protect the customer from reassignment;
- expired new-customer leases can be reclaimed safely;
- tenant, project, and operator call limits are enforced under database row locks.

## Next implementation order

1. Project working-hours editor and operator assignment UI.
2. Project-specific custom customer fields and CSV/XLSX import mapping.
3. Versioned visual call-script editor linked to `call_flows`.
4. Configurable call-result catalogs and validation rules.
5. Expanded task lifecycle: priority, ownership changes, reminders, SLA, and audit trail.
6. Advanced Dialer workspace with script branching and “Save and next client”.
7. Mock telephony scenarios for busy, no-answer, failure, transfer, and channel queues.
8. Extract inline Mock behavior behind a Python `TelephonyProvider` interface.
9. Implement Asterisk ARI originate, bridge, External Media, recording, and webhook flow.
10. Connect UzCloud SIP credentials through encrypted secret references and run carrier
    canaries before enabling production telephony.

## Production gates still open

- real Asterisk ARI event and media WebSockets;
- RTP/audio resampling between Asterisk and OpenAI Realtime;
- reconnect and recovery persistence for real voice sessions;
- SIP fraud controls, public TLS/NAT/firewall verification, and UzCloud canaries;
- recording upload, signed playback URLs, retention jobs, backup/restore, and load tests;
- CI release pipeline and deployment manifests.

The application must continue to label Mock calls as development calls until all real
telephony gates have evidence. No SIP password, API key, token, or other secret belongs
in the frontend or Git history.
