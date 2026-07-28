# Production readiness report

Date: 2026-07-28. Verdict: **not production-ready**. Production is the target; no MVP readiness claim is used as a substitute for the gates below.

## Verified locally

- FastAPI registration, authentication, tenant/RBAC, operator versioning, text knowledge, development simulation, transcript, summary, usage and dashboard APIs.
- Customer creation/search, PostgreSQL `FOR UPDATE SKIP LOCKED` assignment, operator Mock call lifecycle, outcomes and callback tasks with tenant RLS and audit events. Mock telephony is rejected outside development/test.
- Operator workflow concurrency coverage proves that one customer cannot be claimed by two operators.
- PostgreSQL migration at `8d6dea3e8a52` head, no pending migration operations, forced RLS, non-superuser app role, and a cross-tenant negative integration test.
- Next.js production build and explicit unavailable states for unfinished routes.
- Gateway mocked tests for state, reconnect, barge-in, typed tools, timeout and transfer ordering.
- PostgreSQL, Redis and MinIO containers were healthy before the later Docker build failure.

## Blocking findings

1. Latest stable Next.js `16.2.12` is reported by npm with high-severity transitive PostCSS and Sharp findings. The first non-affected Next range is preview-only; remain on stable and upgrade when a patched stable release is available.
2. Full Compose image/health verification is blocked by a full C: drive and Docker Desktop containerd `input/output error`, followed by daemon RPC timeouts. No Docker data cleanup or daemon restart was attempted automatically.
3. No real OpenAI audio, SIP carrier, NAT, two-way media, payment, recording playback, backup/restore, load or penetration test has run. The internal CRM workflow exists, but external CRM connectors remain unverified.
4. ARI/External Media and Asterisk files are foundations, not an orchestrated end-to-end media bridge. Worker handlers intentionally dead-letter unwired jobs.
5. The development limiter is process-local; Redis per-tenant limits and distributed concurrent-call reservations are still required.

## Claims

Russian and English are product targets, not completed audio canaries. Uzbek remains beta. Karakalpak remains experimental behind `KARAKALPAK_EXPERIMENTAL=true`. Azure Speech, Yandex SpeechKit, Bitrix24, amoCRM and Google Sheets are unavailable. OpenAI credentials would mean configured, not verified.

## Next safe increment

After an operator frees disk space and restores Docker Desktop, run `npm ci`, rebuild services sequentially, rerun all health checks, then add Playwright for the vertical flow. Next implement the internal tenant-authorized tool endpoint plus Redis tenant rate/concurrency limits before connecting a real provider.
