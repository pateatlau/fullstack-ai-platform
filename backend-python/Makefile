UV := uv

.PHONY: run lint format format-check test db-migrate db-revision db-reset db-seed

run:
	$(UV) run python -m uvicorn app.main:app --reload --port 8000

lint:
	$(UV) run python -m ruff check app tests

format:
	$(UV) run python -m black app tests

format-check:
	$(UV) run python -m black --check app tests

test:
	$(UV) run python -m pytest -q

db-migrate:
	$(UV) run alembic upgrade head

db-revision:
	$(UV) run alembic revision --autogenerate -m "$(m)"

db-reset:
	$(UV) run alembic downgrade base
	$(UV) run alembic upgrade head

db-seed:
	$(UV) run python -m app.db.seed
