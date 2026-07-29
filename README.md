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

## Language readiness

| Language           | Status            | Rule                                                                       |
| ------------------ | ----------------- | -------------------------------------------------------------------------- |
| Russian (`ru`)     | Production target | still requires deployment/provider canaries                                |
| English (`en`)     | Production target | still requires deployment/provider canaries                                |
| Uzbek (`uz`)       | Beta              | promote only after real STT/TTS quality review                             |
| Karakalpak (`kaa`) | Experimental      | requires `KARAKALPAK_EXPERIMENTAL=true`; never production-ready by default |
