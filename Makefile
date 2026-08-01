.PHONY: install dev up down format lint typecheck test migrate migration-check health

install:
	npm install
	py -3.12 -m pip install -e "services/api[dev]" -e "services/worker[dev]"

dev:
	docker compose up --build

up:
	docker compose up -d --build

down:
	docker compose down

format:
	npm run format
	py -3.12 -m ruff format services/api services/worker

lint:
	npm run lint
	py -3.12 -m ruff check services/api services/worker

typecheck:
	npm run typecheck
	py -3.12 -m mypy services/api/teamora_api services/worker/teamora_worker

test:
	npm test
	py -3.12 scripts/run_api_tests.py
	py -3.12 scripts/run_project_migration_test.py
	py -3.12 scripts/run_customer_migration_test.py
	py -3.12 scripts/run_call_flow_migration_test.py
	py -3.12 scripts/run_call_result_migration_test.py
	py -3.12 scripts/run_task_migration_test.py
	py -3.12 scripts/run_telephony_migration_test.py
	py -3.12 scripts/run_call_state_migration_test.py
	py -3.12 scripts/run_dialer_migration_test.py
	py -3.12 scripts/run_team_migration_test.py
	cd services/worker && py -3.12 -m pytest
	py -3.12 scripts/run_e2e_tests.py

migrate:
	py -3.12 -m alembic -c services/api/alembic.ini upgrade head

migration-check:
	py -3.12 -m alembic -c services/api/alembic.ini check

health:
	docker compose ps
