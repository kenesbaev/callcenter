# K-Line

Мультитенантная платформа AI Call Center для компаний. Публичный продукт и интерфейс используют бренд K-Line; внутренние имена пакетов `@teamora/*` сохранены временно для совместимости.

Production is the target, not an MVP label. The registration → tenant → AI operator → knowledge → transcript → summary → dashboard flow and the customer → locked assignment → operator call → outcome → callback workflow are implemented against PostgreSQL with tenant RLS and automated tests. Mock telephony is restricted to development/test environments. Live SIP, OpenAI audio, recording, backup/restore, load, failover and security readiness are not claimed until their external verification gates pass. See the [production-readiness report](docs/production-readiness.md).

## Repository map

```text
apps/web                    Next.js control plane and landing page
services/api                FastAPI, auth, tenant domain and analytics
services/voice-gateway      Realtime media/session gateway
services/worker             background jobs and retention
packages/contracts          shared TypeScript contracts
packages/config             validated shared TypeScript configuration
packages/ui                 design tokens and reusable UI
infrastructure              Asterisk, Nginx, PostgreSQL and monitoring
docs                        architecture, security and operations
tests                       cross-service and E2E tests
```

## Project-scoped CRM workflow

CRM data is separated first by tenant and then by project. Every tenant receives a
default project during registration or migration. Customers, customer phone numbers,
operator calls, callbacks, AI operators, and call-flow scripts carry a required
`project_id`. A phone number is unique inside one project and may be reused in another
project of the same company.

The operator Dialer uses a PostgreSQL lease with `FOR UPDATE SKIP LOCKED`, a unique
lease token, a 15-minute expiry, and a browser heartbeat. Assigned callbacks remain
owned by their operator; stale new-customer leases can be reclaimed. Tenant, project,
and operator concurrent-call limits are checked transactionally before a Mock call is
created. See [the migration plan](docs/MIGRATION_PLAN.md) for completed and pending work.

The extended operator workspace keeps the customer card, published script runtime, call history,
tasks and state-machine controls in `/app/dialer`. `POST /api/v1/dialer/complete-and-next` saves the
outcome and allocates the next customer atomically with an idempotent replay response. Runtime and
recovery details are documented in [the Dialer workspace guide](docs/dialer-workspace.md).
Team roles, invitations and presence are documented in
[the team access guide](docs/team-access.md).
Durable Worker jobs, large customer imports, private-object reconciliation, and
two-stage retention are documented in
[the background jobs and retention guide](docs/BACKGROUND_JOBS_RETENTION.md).

The standalone web image compiles Next.js rewrites during the Docker build. Its
Dockerfile therefore defaults `API_INTERNAL_URL` to the Compose service address
`http://api:8000`; deployments with a different internal API hostname must override
that build argument.

Start with [the implementation plan](docs/implementation-plan.md) and [architecture](docs/architecture.md).

## Требования

- Node.js 22+
- Python 3.12
- Docker Desktop with Compose
- `make` is optional on Windows; the commands in the Makefile can be run directly in PowerShell.

## Первый запуск в Windows PowerShell

1. Запустите Docker Desktop и дождитесь состояния `running`: `docker desktop status`.
2. Скопируйте `.env.example` в `.env` и заполните только локальные значения. Не используйте development secrets в production.
3. Установите зависимости: `npm install` и `py -3.12 -m pip install -e "services/api[dev]" -e "services/worker[dev]"`.
4. Запустите инфраструктуру: `docker compose up -d postgres redis minio minio-init`.
5. Примените миграции: `py -3.12 -m alembic -c services/api/alembic.ini upgrade head`.
6. Запустите API: `py -3.12 -m uvicorn teamora_api.main:app --app-dir services/api --reload --port 8000`.
7. В отдельном окне запустите web: `npm run dev:web`.
8. Откройте `http://localhost:3000` и перейдите к регистрации.

Проверка инфраструктуры:

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

Если readiness возвращает `503` с `database_unavailable`, Docker/PostgreSQL ещё не готовы. Форма регистрации сохранит введённые значения и покажет понятную ошибку вместо общего `The request could not be completed`.

The API does not silently migrate on startup. Apply migrations with the migration-owner URL; the normal application role intentionally cannot create schema objects.

The development Call Simulator additionally requires `APP_ENV=development` and `ENABLE_CALL_SIMULATOR=true`. It is always labeled “Simulation — not a phone call”.

## Verification

Точные формулы Overview/Analytics, правила timezone, denominators и data-quality flags описаны в
[`docs/analytics-metrics.md`](docs/analytics-metrics.md). Обе страницы используют общий backend
query-layer; расходы SIP/DID не входят в оценку AI. Индексную миграцию аналитики и сохранность
старых звонков проверяет `py -3.12 scripts/run_analytics_migration_test.py`.

```powershell
npm run format:check
npm run lint
npm run typecheck
npm test
py -3.12 -m ruff format --check services/api services/worker
py -3.12 -m ruff check services/api services/worker
py -3.12 -m mypy services/api/teamora_api services/worker/teamora_worker
py -3.12 scripts/run_api_tests.py
Push-Location services/worker; py -3.12 -m pytest; Pop-Location
py -3.12 scripts/run_e2e_tests.py
py -3.12 -m alembic -c services/api/alembic.ini check
docker compose ps
npm audit --omit=dev
```

`scripts/run_api_tests.py` refuses staging/production and non-local database URLs,
creates a separate `*_pytest` PostgreSQL database, applies Alembic to it, runs the API
suite through the restricted application role, and drops the database even when tests
fail. Direct API pytest execution is fail-closed unless `APP_ENV=test`, `DATABASE_URL`
and `TEST_DATABASE_URL` identify the same explicitly local test database. Optional
`TEST_DATABASE_URL` and `TEST_MIGRATION_DATABASE_URL` values may override the derived
URLs without being committed.

`scripts/run_e2e_tests.py` creates and later removes an isolated local PostgreSQL
database, then starts Playwright development servers on ports `8100` and `3100`.
Direct Playwright execution is fail-closed unless `E2E_DATABASE_URL` and
`E2E_MIGRATION_DATABASE_URL` reference a local database ending in `_test` or
`_pytest`. The runner uses the installed Microsoft Edge channel locally and checks
the complete vertical flow plus the five dashboard sections. PostgreSQL, Redis and
MinIO must already be healthy. In CI, install the official Playwright browser first
with `npx playwright install --with-deps chromium`.

The current stable Next.js release has upstream high-severity transitive audit findings. Do not deploy until a patched stable version is adopted and reverified.

## Project configuration

`/app/projects` is the project control center for tenant owners and managers. It
configures status, language/timezone overrides, tenant-owned outbound and inbound
numbers, project AI/call-flow links, a tenant knowledge source, assigned operators,
working hours, recording/disclosure overrides, concurrent-call limits, retry policy,
and callback defaults. Operators and analysts can read only the projects allowed by
their role and project assignment; unavailable management controls are not rendered.

Projects are archived instead of deleted. New projects receive an empty result catalog
which owners and managers can populate with project-specific result definitions.
Run `py -3.12 scripts/run_project_migration_test.py` to verify that a project created at
the previous Alembic head survives the Stage 2 migration with its existing values and
receives its result catalog. The script only accepts local PostgreSQL and always removes
its isolated test database.

## Customer management

`/app/customers` provides the tenant owner and manager with a project-scoped customer
workspace: profile fields, multiple normalized phone numbers and e-mail addresses,
primary contacts, tags, responsible project operator, the next contact date, archive
and restore actions, search, filters and pagination. Project custom-field definitions
support text, textarea, number, boolean, date, datetime, select and multiselect values;
the API validates every value against the customer's own tenant and project.

CSV/XLSX import uses a two-step preview/commit flow. Preview validates the file and
mapping without creating customers. Commit requires an `Idempotency-Key`, skips invalid
rows, and only updates duplicates when the user explicitly selects the update rule.
Synchronous imports are limited to 2 MB and 500 data rows per sheet. The background
path supports configured files up to 25 MB and 100,000 rows by default, streams rows
into tenant-scoped staging, and applies all customer changes in one atomic finalization
transaction. Progress and recovery are available after reload.

Client contacts are stored in `customer_contacts` and are unique within their tenant,
project, kind and normalized value. `phone_numbers.e164` remains globally unique because
that separate table contains telephony DID/SIP routing numbers. Run
`py -3.12 scripts/run_customer_migration_test.py` to verify preservation of a legacy
customer, its contact, call history and custom-field values across the Stage 3 migration.
The script only accepts a local PostgreSQL test database and removes it after completion.

## Call-flow editor

`/app/projects/{project_id}/flow` provides a tenant- and project-scoped scenario editor.
It stores typed nodes in immutable version snapshots, supports validated language codes,
branch transitions, field references, safe action definitions and a read-only preview.
Language codes are normalized to lowercase BCP 47 form: `ka` is Georgian, while
Karakalpak uses `kaa` (including variants such as `kaa-latn` and `kaa-cyrl`). Existing
data is never rewritten from `ka` unless its Karakalpak provenance is explicitly known.
Only a draft can be edited. Publishing runs full graph validation in one transaction;
published versions are immutable, and a subsequent edit starts as a copied draft with
an optimistic `lock_version` check. A Call records its exact published
`call_flow_version_id`; a project without a published flow continues through Dialer
without a script.

Preview never creates calls or changes customers, callbacks, tasks or transfers. The
action nodes are deliberately marked inert until their execution stages are implemented.
Run `py -3.12 scripts/run_call_flow_migration_test.py` to verify preservation and
project backfill of pre-Stage-4 flows, versions, project links and call history. The
script only accepts local PostgreSQL and removes its isolated database after completion.

## Project call results

`/app/projects/{project_id}/results` manages a tenant- and project-scoped result catalog.
Owners and managers can create, translate, order, deactivate, archive and restore result
definitions; assigned operators receive only active definitions in Dialer. Definitions
use the system categories `successful`, `intermediate`, `unreachable` and
`unsuccessful`, while their codes, labels, colors and workflow rules remain configurable.

Saving `POST /api/v1/calls/{call_id}/result` with `result_definition_id` records an
immutable outcome snapshot (definition ID, code, localized label, translations,
category and color). Later definition renames or category changes therefore do not
rewrite history or analytics. The deprecated fixed `result` code remains temporarily
accepted for older clients. `Idempotency-Key` prevents duplicate outcome events, notes
and generated tasks. Run `py -3.12 scripts/run_call_result_migration_test.py` to verify
known and unknown legacy results across upgrade and downgrade in an isolated database.

## Tasks and callbacks

`/app/tasks` is the unified tenant- and project-scoped workspace for callbacks,
follow-ups, manual work and system tasks. Tasks have UTC due times, computed overdue
state in the project timezone, priorities, assignment, an explicit state machine and
an immutable `task_events` history. Owners and managers see and manage their project
queues; operators see their assigned tasks and eligible unassigned work; analysts have
read-only access. The compatibility route `/app/callbacks` and `/api/v1/callbacks`
continue to use the same `callback_tasks` records.

Mutating task APIs accept `Idempotency-Key`. Dialer locks due callback tasks and their
customers with `FOR UPDATE SKIP LOCKED`, prioritizes overdue assigned work, and never
issues a future callback before `due_at`. Result-driven callback and follow-up creation,
replacement and do-not-call cancellation are synchronized without deleting completed
history. Run `py -3.12 scripts/run_task_migration_test.py` to verify preservation of a
legacy callback and its call/outcome links through Stage 6 upgrade and downgrade.

## Unified telephony control plane

Call routers now use a project-aware `TelephonyService`. Development/test calls use a
deterministic `MockTelephonyProvider`; configured project phone numbers select the
authenticated Voice Gateway and shared Asterisk ARI adapter. Commands and provider
events are idempotent, audited and tenant-scoped.

See [docs/telephony-layer.md](docs/telephony-layer.md) for the service boundary,
command/event contracts, environment variable names and live verification checklist.
The ARI adapter is implemented but still requires live Asterisk/SIP verification; no
carrier or OpenAI voice claim is made. The isolated preservation check is
`py -3.12 scripts/run_telephony_migration_test.py`.

## Canonical call state machine

Every `Call.status` transition now passes through the transactional
`CallStateService`; routers, Mock, simulator and provider webhooks cannot assign a
status independently. Optimistic `state_version`, canonical timestamps, normalized
hangup causes, deterministic out-of-order event handling and a provider reconciliation
endpoint keep FastAPI as the source of truth. The Dialer uses canonical states, starts
its timer at `answered_at`, stops at `ended_at`, disables impossible controls and does
not replace newer state with an older polling response.

See [docs/call-state-machine.md](docs/call-state-machine.md) for the transition table,
provider event rules, migration mapping and live verification boundary. Run
`py -3.12 scripts/run_call_state_migration_test.py` for the isolated
upgrade/downgrade preservation check.

## Versioned knowledge and deterministic RAG

`/app/knowledge` manages project-scoped draft and immutable published knowledge-base
revisions, safe TXT/Markdown/PDF/DOCX ingestion through the existing Worker and private
MinIO, deterministic chunking, pgvector plus PostgreSQL lexical retrieval, exact
citations, and pinned revisions for calls and Simulator sessions. Scanned PDFs are
reported as `needs_ocr`; no OCR, generative LLM, or external embedding request is made
in this stage.

See [docs/KNOWLEDGE_RAG.md](docs/KNOWLEDGE_RAG.md) for the lifecycle, upload security,
ranking formula, language rules, RBAC/RLS boundary, environment variable names, and
live-verification limitations. Run `py -3.12 scripts/run_knowledge_migration_test.py`
for the isolated upgrade/downgrade preservation check.

## Language readiness

| Language           | Status            | Rule                                                                       |
| ------------------ | ----------------- | -------------------------------------------------------------------------- |
| Russian (`ru`)     | Production target | still requires deployment/provider canaries                                |
| English (`en`)     | Production target | still requires deployment/provider canaries                                |
| Uzbek (`uz`)       | Beta              | promote only after real STT/TTS quality review                             |
| Karakalpak (`kaa`) | Experimental      | requires `KARAKALPAK_EXPERIMENTAL=true`; never production-ready by default |
