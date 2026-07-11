UV := uv

.PHONY: run lint format format-check test

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