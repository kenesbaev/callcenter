# Teamora Voice — implementation plan

Status: stages 0–4 foundation implemented; later operations remain gated. See `production-readiness.md`.

Observed status on 2026-07-26: stages 0–2 are implemented and integration-tested; stage 3 has a working vertical UI but Playwright remains pending; stage 4 passes mocked gateway tests but has no real audio canary; stages 5–7 contain foundations only and remain blocked by external and Docker findings.

## Non-negotiable rules

- The repository is created locally from scratch. No template or external repository is copied.
- No credentials, private keys, recordings, transcripts, or generated secrets are committed.
- Every business record is tenant-scoped. The tenant comes from the authenticated membership or from a verified DID mapping, never from an untrusted request body.
- PostgreSQL RLS is the database backstop; application-scoped repositories are the first guard. Cross-tenant tests are release-blocking.
- LLMs never receive SQL, arbitrary URL access, shell access, credentials, or an unbounded tool executor.
- Tools are server-owned, JSON-Schema validated, tenant-authorized, idempotent, timed out, redacted, and audited.
- `kaa` is always `experimental`; `uz` stays `beta` until STT/TTS quality gates pass.
- Development simulations and fake billing are visibly marked and impossible to enable in production.
- A configured adapter is not a verified integration. Live SIP, CRM, payment, STT/TTS, and OpenAI results require real canaries.

## Definition of done for every stage

1. The stage has a small, reviewable scope and its limitations are documented.
2. Format, lint, typecheck, unit tests, integration tests, and migration checks run where applicable.
3. Docker services used by the stage have health checks. A skipped health check is reported, never implied.
4. No placeholder control claims success. Incomplete actions are hidden, disabled with a reason, or marked `Coming later`.
5. README and this plan reflect the observed state.

## Stage 0 — architecture and product contract

Deliverables:

- `docs/implementation-plan.md`
- `docs/architecture.md` with system, sequence, handoff, tenancy, deployment, and lifecycle diagrams
- `docs/product-design.md`
- shared design tokens in `packages/ui`
- risk register and explicit production-readiness boundaries

Exit gate: documents agree on service boundaries, tenancy, security, languages, and the first vertical slice.

## Stage 1 — secure platform foundation

Deliverables:

- npm workspace monorepo and Python service projects
- local Docker Compose: web, API, gateway, worker, PostgreSQL, Redis, MinIO, Asterisk, Nginx, Mailpit, Prometheus, Grafana
- FastAPI configuration, structured logs, correlation ID, consistent errors, health endpoints, metrics
- SQLAlchemy async models and Alembic baseline
- Tenant, User, Membership, RBAC, refresh-token rotation, registration/login/logout/me
- PostgreSQL RLS policies and tenant context transaction helper
- security headers, CORS/trusted-host allowlists, rate-limit foundation
- auth, RBAC, tenant-isolation, migration, and health tests

Exit gate: a user can register a tenant, authenticate with HttpOnly cookies, and cannot read another tenant's records at API or database policy level.

## Stage 2 — first vertical product slice

Deliverables:

- AI operator create/list with immutable draft/version/publish boundary
- text knowledge entry and tenant-scoped retrieval
- customer and call domain
- local-only text call simulator guarded by `APP_ENV=development` and `ENABLE_CALL_SIMULATOR=true`
- transcript segments with per-segment language
- deterministic development summary and usage calculation
- dashboard aggregates with no production demo numbers
- typed tool registry with safe MVP tools and audited executions

Exit gate: registration → tenant → AI operator → knowledge → simulated call → transcript → summary → dashboard works through real APIs and PostgreSQL.

## Stage 3 — web experience

Deliverables:

- Next.js App Router, strict TypeScript, Tailwind, TanStack Query, React Hook Form, Zod, Recharts, accessible SVG icons
- localized landing page without invented customers or testimonials
- auth screens and onboarding
- app shell, dashboard, AI operator, knowledge, simulator, conversations, and detail views backed by the API
- route inventory for future pages; unavailable surfaces are explicitly labeled
- loading, empty, validation, error, and keyboard-focus states

Exit gate: critical vertical flow is usable at 1280 px and basic tablet widths; component and Playwright smoke tests pass.

## Stage 4 — realtime voice foundation

Deliverables:

- provider contracts for realtime voice, STT, TTS, telephony, CRM, object storage, and notifications
- OpenAI Realtime GA WebSocket adapter selected by `OPENAI_REALTIME_MODEL`
- mocked provider and deterministic session state machine tests
- Asterisk ARI client boundary and External Media adapter (WebSocket when supported, RTP fallback documented)
- input/output audio framing, server VAD events, barge-in, timeout, reconnect policy, graceful shutdown
- server-side typed tool calls; no browser-side privileged tool execution
- call event, transcript, summary, transfer, and usage persistence contracts

Exit gate: mocked bidirectional session passes state, interruption, tool, transfer, reconnect, and timeout tests. Live OpenAI remains unavailable without credentials and a real audio canary.

## Stage 5 — telephony and operations

Deliverables:

- PJSIP trunk example with environment substitution and no default production password
- inbound DID route, ARI Stasis flow, bridge/external media lifecycle, queue transfer, CDR and recording controls
- MinIO private buckets, per-tenant prefixes, signed URLs, access audit, pause/resume recording contract
- worker jobs for summaries, safe CRM retries, notifications, retention deletion/anonymization, and usage rollups
- generic webhook CRM implementation; Bitrix24, amoCRM, Azure Speech, Yandex SpeechKit, and Google Sheets remain unavailable adapters until completed and credentialed

Exit gate: local mocked SIP/media tests pass. Real carrier and NAT tests remain a stated external gate.

## Stage 6 — commercial and platform operations

Deliverables:

- analytics, usage limits, plans, subscriptions and invoices
- development-only fake billing provider with an unmistakable label
- platform tenant/health/usage/incidents/feature-flag surfaces
- Prometheus dashboards, alerts, OpenTelemetry-ready interfaces and incident runbooks
- retention, export, deletion and anonymization workflows

Exit gate: calculations are test-covered and tenant/billing roles cannot access conversation content.

## Stage 7 — production-readiness audit

Deliverables:

- dependency and container scan, secrets scan, SAST, threat-model review
- load tests for API, concurrent voice sessions, Redis coordination and database pools
- backup/restore and disaster-recovery exercise
- TLS, firewall, SIP allowlist, fail2ban and fraud-control verification
- real provider canaries for each claimed language and integration
- signed production-readiness report with unresolved risks

Exit gate: no production-ready claim until the security, load, restore, SIP, recording, and language quality gates have evidence.

## Current external gates

- Live OpenAI: `OPENAI_API_KEY`, account/model access, network and recorded latency/quality evidence.
- Live SIP: carrier credentials, DID, allowlisted IPs, public TLS/NAT/firewall, and two-way audio test.
- `uz`: provider-specific STT/TTS benchmark and human evaluation before promotion from beta.
- `kaa`: remains behind `KARAKALPAK_EXPERIMENTAL=true` and requires a dedicated corpus plus native-speaker review.
- CRM/payment: real credentials, webhook verification, field mapping, idempotency and end-to-end canaries.

## High-priority risks

| Risk                          | Impact   | Mitigation and gate                                                             |
| ----------------------------- | -------- | ------------------------------------------------------------------------------- |
| Cross-tenant data leak        | Critical | Application scope + PostgreSQL RLS + negative tests + redacted logs             |
| SIP fraud or exposed RTP      | Critical | Provider allowlist, channel caps, TLS/SRTP where supported, firewall, alerts    |
| Hallucinated business facts   | High     | Retrieval/tool-only facts, no-answer handoff, evidence IDs in tool results      |
| Tool privilege escalation     | Critical | Server registry, tenant binding, per-operator allowlist, schema, timeout, audit |
| Sensitive recording content   | Critical | Disclosure, pause, private encrypted storage, signed URL, retention/delete      |
| Realtime latency/jitter       | High     | Metrics, bounded buffers, codec tests, barge-in and carrier-region testing      |
| Provider/model drift          | High     | Provider interfaces, environment model selection, contract tests, canaries      |
| Language quality overclaim    | High     | Status badges and Language Lab evidence; `kaa` never production by default      |
| Retry duplicates side effects | High     | Retry safe reads only; idempotency keys for writes and webhooks                 |
| Cost runaway                  | High     | tenant minute/concurrency limits, usage reservation, hard stops and alerts      |
