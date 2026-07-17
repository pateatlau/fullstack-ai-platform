# Python AI Service

FastAPI backend for the chatbot project.

Current backend scope includes Phase 7-10 work:

- typed non-streaming error envelopes
- typed streaming SSE error frames
- provider timeout normalization
- request-size and schema validation
- Gemini, Groq, and Anthropic provider integration behind the shared provider interface
- automated endpoint and provider tests

## Tech Stack

- FastAPI
- uvicorn
- pydantic-settings
- OpenAI SDK
- Google GenAI SDK
- Groq SDK
- Anthropic SDK

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

Environment resolution note:

- Dependencies declared in `pyproject.toml` (including `groq`) are resolved in the uv-managed project environment.
- Running plain `python` may use a different interpreter (for example system or Conda), where `groq` is not installed.
- Use `uv run ...` (or the provided `make` targets) to ensure commands run against this project's environment.

Quick verification:

```bash
python -c "import groq"         # may fail if this is not the uv interpreter
uv run python -c "import groq"  # expected to succeed after uv sync
```

Then fill in the real key for the selected provider before starting the server.

- If `LLM_PROVIDER=openai`, set `OPENAI_API_KEY`
- If `LLM_PROVIDER=gemini`, set `GEMINI_API_KEY`
- If `LLM_PROVIDER=groq`, set `GROQ_API_KEY`
- If `LLM_PROVIDER=anthropic`, set `ANTHROPIC_API_KEY`

The app now fails fast during settings load if the selected provider key is missing.

## Shortcut Commands

```bash
make run
make lint
make format
make format-check
make test
```

These Make targets intentionally run tooling through `python -m ...` so commands always resolve from the project virtual environment and not from any global Python/Conda PATH entries.

If a command works with `uv run ...` but fails with plain `python`, you are almost certainly on the wrong interpreter.

## Windows Note

On native Windows, `make` is not installed by default.

- If you use WSL2, the documented `make` commands should work as-is.
- If you stay on native Windows, install GNU Make (for example with Chocolatey or Scoop), or run the equivalent commands directly:

```bash
uv run python -m uvicorn app.main:app --reload --port 8000
uv run python -m ruff check app tests
uv run python -m black app tests
uv run python -m black --check app tests
uv run python -m pytest -q
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
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-20b
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
CORS_ALLOWED_ORIGINS=http://localhost:5173
APP_ENV=development
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
```

Additional behavior tied to these settings:

- `LLM_PROVIDER` controls the default adapter resolved by `ProviderFactory`
- `OPENAI_MODEL` defaults to `gpt-4o-mini`
- `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`
- `GROQ_MODEL` defaults to `openai/gpt-oss-20b`
- `ANTHROPIC_MODEL` defaults to `claude-haiku-4-5-20251001`
- `REQUEST_TIMEOUT_SECONDS` caps provider completion and stream iteration time
- `CORS_ALLOWED_ORIGINS` must include the deployed frontend origin in non-local environments

## Provider Selection

- Default provider comes from `LLM_PROVIDER`.
- You can also pass `provider` and `model` in each chat request.
- OpenAI, Gemini, Groq, and Anthropic use the same `/api/chat` and `/api/chat/stream` contracts.
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
uv run python -m pytest
uv run python -m ruff check app tests
```

Current backend tests cover:

- `GET /api/health`
- `POST /api/chat` success path
- `POST /api/chat` validation and provider-error paths
- `POST /api/chat/stream` happy path, provider error, and client disconnect behavior
- Gemini, Groq, and Anthropic provider completion/streaming behavior and env-driven provider selection

## Manual Smoke Checklist

Use this checklist after changing provider wiring or env configuration:

1. Set `LLM_PROVIDER` to one supported provider and configure only its matching API key.
2. Start the backend and confirm `GET /api/health` reports the selected provider.
3. Submit `POST /api/chat` with the provider omitted and confirm the backend uses that provider's default model.
4. Submit `POST /api/chat/stream` with the provider omitted and confirm the stream starts, emits deltas, and ends with the same default model.
5. Repeat steps 2-4 for OpenAI, Gemini, Groq, and Anthropic.
6. Confirm that a mismatched provider/model pair returns a validation error before any provider request is made.

## Deployment Notes

This backend is the active MVP production backend and is deployed to Railway.

Recommended commands:

```bash
uv sync
uv run python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Railway notes:

- Prefer Dockerfile deploy mode for this service.
- In Railway service settings, set root directory to `backend-python`.
- If using Nixpacks instead, use:
  - Build command: `uv sync --no-dev`
  - Start command: `uv run python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `CORS_ALLOWED_ORIGINS` to the exact frontend origin(s), comma-separated, with no trailing slash.
- CORS exposes the `X-Guest-Token` response header (`expose_headers=["X-Guest-Token"]`) so
  the frontend can read the minted guest token cross-origin. `allow_credentials` stays
  `False` (Bearer token transport, not cookies).

### Google OAuth Web Client Configuration

Google login requires one Google Cloud OAuth 2.0 **Web client**. Only the client ID is
used (`GOOGLE_CLIENT_ID` here, `VITE_GOOGLE_CLIENT_ID` on the frontend) — never the client
secret.

Authorized JavaScript origins must exactly match each frontend origin (scheme + host, no
path, no trailing slash), one-to-one with `CORS_ALLOWED_ORIGINS`:

- Local: `http://localhost:5173`
- Staging: the exact staging Vercel frontend origin (see [CD_STAGING.md](../CD_STAGING.md)) — pending real hostname
- Production: the exact production Vercel frontend origin (see [CD_PRODUCTION.md](../CD_PRODUCTION.md)) — pending real hostname

Authorized redirect URIs are not required for the Google Identity Services ID-token flow
used by this app.

Full per-environment env var contract (Railway `GOOGLE_CLIENT_ID`/`APP_ENV`/`JWT_SECRET`,
Vercel `VITE_GOOGLE_CLIENT_ID`, and the matching Google Cloud origin) lives in the
"Authentication (Google OAuth) Environment Contract" section of
[CD_STAGING.md](../CD_STAGING.md) and [CD_PRODUCTION.md](../CD_PRODUCTION.md).

Before deployment, the operator must prepare:

- a reachable Git remote
- a Railway account
- production provider secrets
- production `CORS_ALLOWED_ORIGINS`
- production `GOOGLE_CLIENT_ID` and a matching authorized JavaScript origin on the Google OAuth Web client

The full manual deployment prerequisite checklist and runbook live in [../docs/plans/chatbot-v1.md](../docs/plans/chatbot-v1.md).
