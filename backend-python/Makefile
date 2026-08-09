UV := uv

.PHONY: run lint typecheck format format-check test test-cov eval db-migrate db-revision db-reset db-seed

# Bind IPv4 loopback explicitly so local Vite (proxy → 127.0.0.1:8000) cannot
# race a Docker-published *:8000 that "localhost" may resolve to via IPv6.
run:
	$(UV) run python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

lint:
	$(UV) run python -m ruff check app tests

typecheck:
	$(UV) run pyright app tests

format:
	$(UV) run python -m ruff format app tests

format-check:
	$(UV) run python -m ruff format --check app tests

test:
	$(UV) run python -m pytest -q --cov=app --cov-report=term-missing:skip-covered

test-cov:
	$(UV) run python -m pytest -q \
		--cov=app \
		--cov-report=term-missing \
		--cov-report=xml:coverage.xml \
		--cov-fail-under=80

eval:
	# --level all requires AGENT_RUNTIME_ENABLED, WORKFLOW_ENGINE_ENABLED, Postgres, pgvector
	$(UV) run python -m app.ai.evaluation.cli --level all

db-migrate:
	$(UV) run alembic upgrade head

db-revision:
	$(UV) run alembic revision --autogenerate -m "$(m)"

db-reset:
	$(UV) run alembic downgrade base
	$(UV) run alembic upgrade head

db-seed:
	$(UV) run python -m app.db.seed
