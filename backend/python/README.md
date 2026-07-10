# Backend (FastAPI)

FastAPI backend for the chatbot project.

Current backend scope includes Phase 7-10 work:

- typed non-streaming error envelopes
- typed streaming SSE error frames
- provider timeout normalization
- request-size and schema validation
- Gemini provider integration behind the shared provider interface
- automated endpoint and provider tests

## Tech Stack

- FastAPI
- uvicorn
- pydantic-settings
- OpenAI SDK
- Google GenAI SDK

## Layout

- `app/main.py` - app creation, CORS, router wiring
- `app/core/config.py` - environment-driven settings
- `app/routers/health.py` - `GET /api/health`
- `app/routers/chat.py` - `POST /api/chat`, `POST /api/chat/stream`
- `app/services/chat_service.py` - orchestration, SSE framing
- `app/providers/base.py` - provider protocol
- `app/providers/openai_provider.py` - OpenAI adapter
- `app/providers/gemini_provider.py` - Gemini adapter
- `app/providers/factory.py` - provider selection
- `app/schemas/chat.py` - request/response/frame schemas
- `tests/` - test doubles and provider tests

## Setup

```bash
cp .env.example .env
uv sync
```

Then fill in the real key for the selected provider before starting the server.

- If `LLM_PROVIDER=openai`, set `OPENAI_API_KEY`
- If `LLM_PROVIDER=gemini`, set `GEMINI_API_KEY`

The app now fails fast during settings load if the selected provider key is missing.

## Shortcut Commands

```bash
make run
make lint
make format
make format-check
make test
```

## Run

```bash
make run
```

## Environment Variables

From `.env.example`:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.1-flash-lite
CORS_ALLOWED_ORIGINS=http://localhost:5173
APP_ENV=development
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
```

Additional behavior tied to these settings:

- `LLM_PROVIDER` controls the default adapter resolved by `ProviderFactory`
- `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`
- `REQUEST_TIMEOUT_SECONDS` caps provider completion and stream iteration time
- `CORS_ALLOWED_ORIGINS` must include the deployed frontend origin in non-local environments

## Provider Selection

- Default provider comes from `LLM_PROVIDER`.
- You can also pass `provider` and `model` in each chat request.
- OpenAI and Gemini use the same `/api/chat` and `/api/chat/stream` contracts.
- Health responses report the active default provider.

## Endpoints

- `GET /` - welcome message
- `GET /api/health` - status + active provider + app version
- `POST /api/chat` - non-streaming completion
- `POST /api/chat/stream` - SSE streaming completion

## Error Behavior

Non-streaming errors return:

```json
{
  "error": {
    "code": "provider_error",
    "message": "Upstream provider failed."
  }
}
```

Known backend error codes:

- `validation_error`
- `provider_timeout`
- `provider_rate_limited`
- `provider_error`
- `internal_error`

For `/api/chat/stream`, those errors are emitted as SSE `error` frames after the stream starts.
Validation failures are returned as JSON responses before streaming begins.

## Validation Guards

- request body size is capped before request handling
- `messages` must be non-empty
- message content is trimmed and length-limited
- `model` cannot be blank
- `temperature` is constrained to a safe numeric range

## Quick Checks

```bash
curl -s http://localhost:8000/api/health
```

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say hello"}]}'
```

## Tests

```bash
make test
```

Gemini provider-focused test:

```bash
uv run pytest -q tests/providers/test_gemini_provider.py
```

Full backend validation:

```bash
uv run pytest
uv run ruff check app tests
```

Current backend tests cover:

- `GET /api/health`
- `POST /api/chat` success path
- `POST /api/chat` validation and provider-error paths
- `POST /api/chat/stream` happy path, provider error, and client disconnect behavior
- Gemini provider completion/streaming behavior and env-driven provider selection

## Deployment Notes

This backend is intended to deploy to Render as a Python web service.

Recommended commands:

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Before deployment, the operator must prepare:

- a reachable Git remote
- a Render account
- production provider secrets
- production `CORS_ALLOWED_ORIGINS`

The full manual deployment prerequisite checklist and runbook live in [../docs/plans/chatbot-v1.md](../docs/plans/chatbot-v1.md).
