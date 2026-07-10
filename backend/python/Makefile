UV := uv

.PHONY: run lint format format-check test

run:
	$(UV) run uvicorn app.main:app --reload --port 8000

lint:
	$(UV) run ruff check app tests

format:
	$(UV) run black app tests

format-check:
	$(UV) run black --check app tests

test:
	$(UV) run pytest -q