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
	py -3.12 -m pytest services/api/tests services/worker/tests

migrate:
	py -3.12 -m alembic -c services/api/alembic.ini upgrade head

migration-check:
	py -3.12 -m alembic -c services/api/alembic.ini check

health:
	docker compose ps
