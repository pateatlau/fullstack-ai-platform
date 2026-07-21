UV := uv

.PHONY: run lint typecheck format format-check test test-cov eval db-migrate db-revision db-reset db-seed

run:
	$(UV) run python -m uvicorn app.main:app --reload --port 8000

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
