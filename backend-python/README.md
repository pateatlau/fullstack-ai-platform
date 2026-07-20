# Python AI Service

**Production MVP backend** — FastAPI service deployed to Railway.

MVP hardening is complete (2026-07-19): structured logging, correlation IDs, centralized errors, HTTP rate limiting, Pyright standard mode, and CI quality gates. See `docs/plans/mvp-completion-implementation-plan.md` for the validation record.

## Capabilities

- Multi-provider chat (OpenAI, Gemini, Groq, Anthropic) — streaming and non-streaming
- Google OAuth login with app-issued JWT; guest identity and daily quota
- Chat persistence (sessions, messages, usage) when `CHAT_PERSISTENCE_ENABLED=true`
- Typed error envelopes and SSE error frames with `request_id`
- Request-size and schema validation; provider timeout normalization
- Structured JSON logging in production; correlation IDs on every response
- HTTP rate limiting (per-minute) with `Retry-After` on 429

## Tech Stack

- FastAPI
- uvicorn
- pydantic-settings
- OpenAI SDK
- Google GenAI SDK
- Groq SDK
- Anthropic SDK

Dev tooling: Ruff (lint and format), Pyright (static type checking, standard mode), pytest, pytest-cov

## Layout

- `app/main.py` — app creation, middleware (CORS, rate limit, logging, correlation ID)
- `app/core/config.py` — environment-driven settings with startup validation
- `app/core/logging.py` — structured logging (JSON in production)
- `app/core/errors.py` — centralized error envelope and exception handlers
- `app/middleware/correlation_id.py` — `X-Request-ID` propagation
- `app/middleware/rate_limit.py` — in-memory sliding-window rate limits
- `app/routers/health.py` — `GET /api/health`, `GET /api/health/ready`
- `app/routers/auth.py` — `POST /api/auth/google`
- `app/routers/chat.py` — chat endpoints and session APIs
- `app/services/chat_service.py` — orchestration, SSE framing, persistence
- `app/providers/` — provider adapters and factory
- `app/ai/` — reusable AI framework (embeddings, vectorstores, prompts, tools, documents, rag, evaluation)
- `app/schemas/` — request/response/frame schemas
- `tests/` — unit and integration tests (180 tests, ~90% coverage on `app/`)

## AI Module (`app/ai/`)

Phase 1 scaffold only — implementations arrive in later phases. Dependency direction:

```text
Routers → Services → AI Framework (`app/ai/`) → Providers → External APIs
```

### Folder responsibilities

| Path | Responsibility |
| ---- | -------------- |
| `app/ai/prompts/` | Versioned Jinja2 prompt templates by category (`chat/`, `rag/`, `tools/`, etc.) |
| `app/ai/tools/` | Tool registry, validation, authorization, execution, normalization |
| `app/ai/documents/` | Upload parsing and chunking (`parsers/`, `chunkers/`) |
| `app/ai/embeddings/` | Embedding provider adapters |
| `app/ai/vectorstores/` | Vector store adapters (V1: pgvector only) |
| `app/ai/rag/` | Generic RAG framework (retriever, context builder, orchestration) |
| `app/ai/evaluation/` | Prompt, retrieval, and end-to-end evaluation helpers |
| `app/ai/interfaces/` | Protocols added incrementally per phase |
| `app/ai/deps.py` | FastAPI dependency wiring for AI components |

### Module boundaries

| Layer | Location | Responsibility |
| ----- | -------- | -------------- |
| LLM adapters | `app/providers/` | Existing `LLMProvider` protocol and OpenAI/Gemini/Groq/Anthropic adapters |
| Embeddings | `app/ai/embeddings/` | Embedding provider protocol + concrete adapters |
| Vector store | `app/ai/vectorstores/` | Vector store protocol + `PgVectorStore` (V1) |
| AI framework | `app/ai/` | Domain-agnostic orchestration consumed by `app/services/` |

V1 uses **pgvector only** for vector storage — no vector-store factory for alternate backends.

### Configuration matrix (env vars)

| Setting | Env var | Default |
| ------- | ------- | ------- |
| Embedding provider | `EMBEDDING_PROVIDER` | `openai` |
| Embedding model | `EMBEDDING_MODEL` | `text-embedding-3-small` |
| Embedding dimensions | `EMBEDDING_DIMENSIONS` | `1536` |
| Chunk size | `CHUNK_SIZE` | `1000` |
| Chunk overlap | `CHUNK_OVERLAP` | `200` |
| RAG top-K | `RAG_TOP_K` | `5` |
| RAG default template | `RAG_DEFAULT_PROMPT_TEMPLATE` | `rag/answer/v1` |
| RAG context budget | `RAG_CONTEXT_MAX_CHARS` | `8000` |
| RAG feature flag | `RAG_ENABLED` | `false` |
| Tools feature flag | `TOOLS_ENABLED` | `false` |
| Default temperature | `DEFAULT_TEMPERATURE` | `0.7` |
| Default max tokens | `DEFAULT_MAX_TOKENS` | provider default (`None`) |
| Document upload max | `DOCUMENT_UPLOAD_MAX_BYTES` | `10485760` (10 MB) |
| Web search provider | `WEB_SEARCH_PROVIDER` | `tavily` |
| Web search API key | `WEB_SEARCH_API_KEY` | unset |
| Web search max results | `WEB_SEARCH_MAX_RESULTS` | `5` |

LLM provider selection remains `LLM_PROVIDER` in `app/core/config.py`.

### Feature flags

- `RAG_ENABLED=false` (default) — no RAG routes or pipeline; MVP chat unchanged.
- `TOOLS_ENABLED=false` (default) — standard `ChatService` path; no tool execution.
- When a flag is `true`, startup fails fast if required secrets are missing (`OPENAI_API_KEY` for RAG with `EMBEDDING_PROVIDER=openai`; `WEB_SEARCH_API_KEY` for tools).
- With both flags off, no new secrets are required and behavior matches the MVP baseline.

### External service retry policy

Documented for later phases — implement in `app/core/retry.py` and reuse from adapters:

| Setting | Value |
| ------- | ----- |
| Retry on | HTTP 429, HTTP 503, network timeout, temporary connection failures |
| Max attempts | 3 |
| Backoff | Exponential (e.g. 1s → 2s → 4s with jitter) |
| Do not retry | HTTP 4xx (except 429), validation errors, auth failures |

### Observability metrics (V1)

Structured log counters (implementation in later phases):

| Metric | Purpose |
| ------ | ------- |
| `rag_requests_total` | RAG ask volume |
| `rag_request_duration_ms` | End-to-end RAG latency |
| `retrieval_latency_ms` | Retriever stage latency |
| `embedding_latency_ms` | Embedding batch latency |
| `vector_search_latency_ms` | pgvector query latency |
| `tool_calls_total` | Tool invocations by name |
| `tool_errors_total` | Tool failures by name |
| `search_latency_ms` | Web search provider latency |
| `documents_ingested_total` | Successful ingestions |
| `documents_failed_total` | Failed ingestions |

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
make typecheck
make format
make format-check
make test
make test-cov
```

These Make targets run through `uv run ...` so commands always resolve from the project virtual environment and not from any global Python/Conda PATH entries.

If a command works with `uv run ...` but fails with plain `python`, you are almost certainly on the wrong interpreter.

## Windows Note

On native Windows, `make` is not installed by default.

- If you use WSL2, the documented `make` commands should work as-is.
- If you stay on native Windows, install GNU Make (for example with Chocolatey or Scoop), or run the equivalent commands directly:

```bash
uv run python -m uvicorn app.main:app --reload --port 8000
uv run python -m ruff check app tests
uv run pyright app tests
uv run python -m ruff format app tests
uv run python -m ruff format --check app tests
uv run python -m pytest -q --cov=app --cov-report=term-missing:skip-covered
uv run python -m pytest -q --cov=app --cov-report=term-missing --cov-fail-under=80
```

## Type Checking

Static analysis is enforced with [Pyright](https://github.com/microsoft/pyright), configured in `pyproject.toml` under `[tool.pyright]`. The project uses `typeCheckingMode = "standard"` for `app/` and `tests/`.

Formatting uses [Ruff format](https://docs.astral.sh/ruff/formatter/) (`line-length = 88`, `target-version = py312`), configured under `[tool.ruff]` in the same file. CI runs `make format-check` on every Python PR.

Pyright aligns with the Pylance language server in Cursor/VS Code, so local editor diagnostics and CI should report the same issues.

```bash
make typecheck
# or
uv run pyright app tests
```

Coverage is configured in `pyproject.toml` under `[tool.coverage]`. Source is `app/`; `app/db/seed.py` is omitted (CLI entrypoint).

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
LOG_LEVEL=INFO
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
REQUEST_BODY_LIMIT_BYTES=16384
RATE_LIMIT_ANONYMOUS_PER_MINUTE=30
RATE_LIMIT_AUTHENTICATED_PER_MINUTE=120
DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot
GOOGLE_CLIENT_ID=...
JWT_SECRET=...
GUEST_DAILY_MESSAGE_QUOTA=20
CHAT_PERSISTENCE_ENABLED=true
# AI / RAG (optional — defaults shown; see AI Module section)
# RAG_ENABLED=false
# TOOLS_ENABLED=false
# EMBEDDING_PROVIDER=openai
# WEB_SEARCH_API_KEY=...
```

Additional behavior tied to these settings:

- `LLM_PROVIDER` controls the default adapter resolved by `ProviderFactory`
- `OPENAI_MODEL` defaults to `gpt-4o-mini`
- `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`
- `GROQ_MODEL` defaults to `openai/gpt-oss-20b`
- `ANTHROPIC_MODEL` defaults to `claude-haiku-4-5-20251001`
- `LOG_LEVEL` controls root logger verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- `REQUEST_BODY_LIMIT_BYTES` caps incoming JSON body size (default 16384, aligned with Node)
- `REQUEST_TIMEOUT_SECONDS` caps provider completion and stream iteration time
- `RATE_LIMIT_ANONYMOUS_PER_MINUTE` / `RATE_LIMIT_AUTHENTICATED_PER_MINUTE` — HTTP per-minute limits (default 30 / 120)
- `CORS_ALLOWED_ORIGINS` must include the deployed frontend origin in non-local environments
- When `APP_ENV` is not `development`, startup fails fast unless `JWT_SECRET`, `DATABASE_URL`, and `GOOGLE_CLIENT_ID` are explicitly set
- In `development`, insecure defaults emit startup warnings instead of failing
- `RAG_ENABLED` / `TOOLS_ENABLED` default to `false`; enabling either requires the corresponding secrets at startup
- `DOCUMENT_UPLOAD_MAX_BYTES` (default 10 MB) is configured now; HTTP enforcement on `/api/documents/upload` arrives in Phase 11

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

## Observability

- **Logging:** JSON in production (`APP_ENV=production`), readable text in development. Fields include `request_id`, route, method, status, latency, provider, and model. Sensitive values are redacted.
- **Correlation IDs:** Every response includes `X-Request-ID`. Pass the header on inbound requests to continue a trace.
- **Rate limiting:** Anonymous callers (IP or guest token bucket) and authenticated callers (JWT bucket) have separate per-minute limits. `/api/health` and `/api/health/ready` are exempt.

## Error Behavior

Non-streaming errors return:

```json
{
  "error": {
    "code": "provider_error",
    "message": "Upstream provider failed.",
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

Error responses include the `X-Request-ID` header matching `error.request_id`.

Known backend error codes:

- `validation_error`
- `invalid_google_token`, `invalid_access_token`, `auth_not_configured`
- `provider_not_allowed`, `session_not_found`
- `quota_exceeded` — guest daily message limit (business logic)
- `rate_limit_exceeded` — HTTP per-minute limit (middleware; includes `Retry-After`)
- `provider_timeout`, `provider_rate_limited`, `provider_error`
- `database_error`
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
make test       # pytest with coverage report (no threshold)
make test-cov   # enforce 80% minimum on app/ (matches CI)
```

CI and local quality gates:

```bash
make lint && make format-check && make typecheck && make test-cov
```

Current suite (2026-07-20): **180 passed**, **~90%** coverage on `app/`.

Coverage includes health, auth, chat (streaming and non-streaming), persistence, logging, correlation IDs, errors, rate limiting, and provider adapters (OpenAI, Gemini, Groq, Anthropic).

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
