# MVP Completion - Implementation Plan

## Objective

Complete the remaining engineering hardening tasks required to confidently declare the Fullstack AI Platform MVP complete. These tasks focus on production readiness — environment consolidation, structured logging, request traceability, consistent error handling, rate limiting, static type checking, and CI quality gates — rather than new end-user features.

## Execution Mode

- Implement sequentially by phase.
- Use the **Python backend** as the production reference; align Node.js after Python patterns are stable.
- After each phase verification is complete, stop and request explicit user confirmation before starting the next phase.
- Every milestone must leave the application deployable.

## Phase Status

- Phase 0 - **Complete**
- Phase 1 - **Complete**
- Phase 2 - **Complete**
- Phase 3 - **Complete**
- Phase 4 - **Complete**
- Phase 5 - **Complete**
- Phase 6 - **Complete**
- Phase 7 - **Complete**
- Phase 8 - **Skipped** (deferred post-MVP; Node.js alignment tracked in `docs/plans/nodejs-backend-v1.md`)
- Phase 9 - **Complete**

## Scope

- In scope:
  - Python backend hardening (primary)
  - CI quality gate completion
  - Environment variable consolidation and validation
  - Structured logging with correlation IDs
  - Centralized error handling improvements
  - HTTP rate limiting middleware
  - Pyright standard mode adoption
  - Final MVP validation and documentation updates
  - Node.js backend alignment (logging, correlation IDs, rate limiting, error codes) after Python is complete
- Out of scope (V1):
  - Tool framework, tool registry, tool executor
  - Web search, prompt management, document ingestion
  - Embeddings, vector database, RAG
  - Full Node.js feature parity (auth, persistence, groq/anthropic) — tracked separately in `docs/plans/nodejs-backend-v1.md`

## Non-Negotiable Requirements

1. Python backend is the production reference; Node.js follows after Python patterns stabilize.
2. Implement one capability at a time; do not batch unrelated hardening changes.
3. Preserve backward compatibility for existing API contracts where practical.
4. Add tests alongside every implementation change.
5. Every phase below must be verifiable before moving to the next phase.
6. User confirmation is required between phases.
7. No sensitive data (tokens, secrets, message content) in logs.
8. Application must remain deployable after each phase.

## Current Baseline (as of plan creation)

| Area            | Current state                                                                                                           |
| --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Settings        | Pydantic-settings in `backend-python/app/core/config.py`; partial validation; no dev/prod config split beyond `APP_ENV` |
| Logging         | `logging.basicConfig(level=INFO)` in `main.py`; ad-hoc logger calls; no structured JSON                                 |
| Correlation IDs | Not implemented                                                                                                         |
| Error handling  | Consistent `{ error: { code, message } }` envelope via FastAPI handlers; gaps for DB and rate-limit categories          |
| Rate limiting   | Guest daily quota in `quota_service.py` only; no HTTP middleware; no `Retry-After` header                               |
| Pyright         | `typeCheckingMode = "basic"` in `pyproject.toml`                                                                        |
| CI              | Ruff lint, pyright, pytest present; **no format gate, no coverage threshold**                                           |
| Node.js         | Basic chat-only parity; no logging, correlation IDs, or HTTP rate limiting                                              |

---

## Phase 0 - Baseline Audit and Safety Net

### Tasks

- Document current startup behavior for Python backend (`make dev`, Docker compose).
- Inventory all environment variable sources:
  - `backend-python/.env.example`, `.env.required`
  - `backend-nodejs/.env.example`, `.env.required`
  - Root `.env.compose`, `docker-compose.yml`
  - Frontend `VITE_*` variables
- Record current error response shapes from representative endpoints (health, chat, auth, validation failure, quota exceeded).
- Capture current log output format (dev startup + one chat request).
- Run existing test suite and record baseline pass count and duration.
- Confirm CI workflow status (`.github/workflows/pr-quality.yml`).

### Verification Checklist

- Baseline inventory document section added to this plan or linked PR notes.
- Python backend starts cleanly with `.env.example` values.
- Existing pytest suite passes (`make test`).
- Current CI jobs documented with pass/fail status.

### Exit Criteria

- Team agrees on baseline and understands gaps before code changes begin.
- User confirms Phase 0 completion.

### Phase 0 Baseline Audit (2026-07-19)

Audit performed locally against the current `main`-equivalent workspace. No code changes were made.

#### Startup Behavior

| Mode                            | Command                                                      | Notes                                                                                                                                                                         |
| ------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python backend (local dev)      | `cd backend-python && make run`                              | Runs `uv run python -m uvicorn app.main:app --reload --port 8000`. Plan references `make dev`; **no root or backend `dev` Make target exists** — actual target is `make run`. |
| Python backend (direct)         | `uv run python -m uvicorn app.main:app --reload --port 8000` | Documented Windows fallback in README.                                                                                                                                        |
| Docker Compose (Python profile) | `docker compose --profile python up --build`                 | Starts `postgres`, `backend-python`, and `frontend`. Backend health check: `GET /api/health`. See `DOCKER_COMPOSE.md`.                                                        |
| Docker Compose (Node profile)   | `docker compose --profile nodejs up --build`                 | Node backend only; post-MVP / paused per root README.                                                                                                                         |

**Python startup sequence observed:**

1. `logging.basicConfig(level=logging.INFO)` runs at import time (`app/main.py`).
2. `get_settings()` loads env via pydantic-settings (`.env` file + environment), validates provider API key, caches result.
3. FastAPI app created with lifespan hook (DB engine disposed on shutdown).
4. CORS middleware registered; routers mounted (`health`, `auth`, `chat`).
5. Uvicorn prints standard access logs (`INFO: Started server process …`, `Application startup complete.`).

**Settings load with `.env.example` placeholder values:** verified OK (`LLM_PROVIDER=openai`, `OPENAI_API_KEY=sk-placeholder`, `APP_ENV=development`).

#### Environment Variable Inventory

| Source                                        | Variables defined                                                                                                                                                                                                                                                                     | Gaps / drift vs Python `.env.example`                                                                                                                                           |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend-python/.env.example`                 | 24 vars: `LLM_PROVIDER`, all provider keys/models (`openai`, `gemini`, `groq`, `anthropic`), `CORS_ALLOWED_ORIGINS`, `APP_ENV`, `MAX_MESSAGE_LENGTH`, `REQUEST_TIMEOUT_SECONDS`, `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `JWT_*`, `GUEST_DAILY_MESSAGE_QUOTA`, `CHAT_PERSISTENCE_ENABLED` | Authoritative Python template.                                                                                                                                                  |
| `backend-python/.env.required`                | Same set as `.env.example` with REQUIRED/CONDITIONAL/Optional annotations                                                                                                                                                                                                             | Aligned with `.env.example`; adds `PYTHONUNBUFFERED` (not in `.env.example`).                                                                                                   |
| `backend-nodejs/.env.example`                 | 9 vars: `LLM_PROVIDER` (`openai` \| `gemini` only), provider keys/models, `CORS_*`, `APP_ENV`, `MAX_MESSAGE_LENGTH`, `REQUEST_TIMEOUT_SECONDS`                                                                                                                                        | Missing `groq`/`anthropic`, auth, DB, JWT, quota, persistence. Has no `PORT` (present in `.env.required`).                                                                      |
| `backend-nodejs/.env.required`                | 12 vars including `PORT`, `REQUEST_BODY_LIMIT_BYTES`                                                                                                                                                                                                                                  | `REQUEST_BODY_LIMIT_BYTES` not in Python config (hardcoded 16KB in `main.py`).                                                                                                  |
| `.env.compose`                                | 10 vars: `LLM_PROVIDER`, OpenAI/Gemini keys/models, `CORS_ALLOWED_ORIGINS=http://localhost`, `APP_ENV`, `MAX_MESSAGE_LENGTH`, `REQUEST_TIMEOUT_SECONDS`                                                                                                                               | Missing `groq`/`anthropic`, `DATABASE_URL`, auth/JWT vars, quota, persistence. CORS default differs from Python `.env.example` (`http://localhost` vs `http://localhost:5173`). |
| `docker-compose.yml` (`backend-python` block) | 12 inline env vars                                                                                                                                                                                                                                                                    | Missing `GROQ_*`, `ANTHROPIC_*`, `GOOGLE_CLIENT_ID`, `JWT_*`, `GUEST_DAILY_MESSAGE_QUOTA`, `CHAT_PERSISTENCE_ENABLED`. `CORS_ALLOWED_ORIGINS=http://localhost` (not `:5173`).   |
| `docker-compose.yml` (`backend-nodejs` block) | 11 inline env vars                                                                                                                                                                                                                                                                    | Same CORS drift; no `REQUEST_BODY_LIMIT_BYTES`.                                                                                                                                 |
| `frontend/.env.example`                       | `VITE_API_BASE_URL`, `VITE_GOOGLE_CLIENT_ID`                                                                                                                                                                                                                                          | Build-time vars only.                                                                                                                                                           |
| `frontend/.env.required`                      | Same + documentation for Docker/Vercel build args                                                                                                                                                                                                                                     | Aligned.                                                                                                                                                                        |

**Known drift to resolve in Phase 1:**

- CORS defaults inconsistent across templates (`http://localhost:5173` vs `http://localhost`).
- Docker compose Python service missing auth/DB/JWT/quota/persistence/groq/anthropic vars.
- `REQUEST_BODY_LIMIT_BYTES` exists in Node but is hardcoded in Python `main.py`.
- No `LOG_LEVEL`, rate-limit, or `request_body_limit_bytes` settings yet (planned Phase 1/5).

#### Error Response Shapes (Representative Endpoints)

Standard envelope (no `request_id` yet):

```json
{ "error": { "code": "<string>", "message": "<string>" } }
```

| Endpoint / scenario                    | HTTP | `error.code`            | Sample message                                                     |
| -------------------------------------- | ---- | ----------------------- | ------------------------------------------------------------------ |
| `GET /api/health` (success)            | 200  | —                       | `{"status":"ok","provider":"<llm_provider>","version":"0.1.0"}`    |
| `GET /api/health/ready` (success)      | 200  | —                       | `{"status":"ok","db":"ok"}`                                        |
| `GET /api/health/ready` (DB down)      | 503  | —                       | `{"status":"error","db":"down"}` — **not** standard error envelope |
| `POST /api/chat` validation failure    | 422  | `validation_error`      | e.g. `"messages: List should have at least 1 item…"`               |
| `POST /api/chat` body too large        | 413  | `validation_error`      | `"Request body exceeds the 16384 byte limit…"`                     |
| `POST /api/chat` provider failure      | 502  | `provider_error`        | `"Upstream provider failed."`                                      |
| `POST /api/chat` provider timeout      | 504  | `provider_timeout`      | `"Upstream provider timed out."`                                   |
| `POST /api/chat` provider rate limited | 429  | `provider_rate_limited` | `"Upstream rate limit hit, please retry shortly."`                 |
| `POST /api/chat` guest quota exceeded  | 429  | `quota_exceeded`        | (from tests; business-logic quota, not HTTP middleware)            |
| `POST /api/auth/google` invalid token  | 401  | `invalid_google_token`  | `"The Google ID token could not be verified."`                     |
| `POST /api/auth/google` not configured | 503  | `auth_not_configured`   | `"Authentication is not configured on the server."`                |
| Unhandled exception                    | 500  | `internal_error`        | `"Unexpected server error."`                                       |
| HTTP rate limiting                     | —    | —                       | **Not implemented** (Phase 5)                                      |

Additional codes in codebase (not in plan table): `session_not_found` (404), `provider_not_allowed` (403), `new_chat_forbidden` (403), `db_unavailable` (503), `invalid_access_token` (401).

**Gaps vs Phase 4 target:** no `request_id` in envelope; readiness/DB errors use ad-hoc shape; no dedicated `database_error` handler for SQLAlchemy exceptions; no HTTP `rate_limit_exceeded` code.

#### Log Output Format

**Development startup** (uvicorn + stdlib logging):

```
INFO:     Started server process [PID]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Single chat request** (observed with `LLM_PROVIDER=groq`):

```
INFO:httpx:HTTP Request: POST https://api.groq.com/openai/v1/chat/completions "HTTP/1.1 403 Forbidden"
INFO:     127.0.0.1:57371 - "POST /api/chat HTTP/1.1" 502 Bad Gateway
```

Characteristics:

- Plain text, not JSON.
- Uvicorn access log format: `INFO: <client> - "<METHOD> <path> HTTP/1.1" <status>`.
- Third-party `httpx` logs at INFO (noisy; no suppression).
- No correlation ID, structured fields, or redaction layer.
- `logging.basicConfig(level=logging.INFO)` only — no `LOG_LEVEL` setting.

#### Test Suite Baseline

| Metric     | Value                                                      |
| ---------- | ---------------------------------------------------------- |
| Command    | `cd backend-python && make test`                           |
| Result     | **130 passed**, 0 failed                                   |
| Duration   | **4.16s** (pytest), ~5.1s wall clock                       |
| Warnings   | 11 (`InsecureKeyLengthWarning` from short test JWT secret) |
| Test files | 15 files under `backend-python/tests/`                     |

#### CI Workflow Status (`.github/workflows/pr-quality.yml`)

| Job                 | Trigger                     | Steps                                                       | Format gate        | Coverage gate |
| ------------------- | --------------------------- | ----------------------------------------------------------- | ------------------ | ------------- |
| `changes`           | Always                      | Path filter for frontend / nodejs / python                  | —                  | —             |
| `frontend-pr`       | `frontend/**` changed       | npm ci, lint, test, build                                   | No (lint only)     | No            |
| `backend-nodejs-pr` | `backend-nodejs/**` changed | npm ci, lint, test, build                                   | No                 | No            |
| `backend-python-pr` | `backend-python/**` changed | uv sync, alembic migrate, **lint**, **typecheck**, **test** | **No** (not in CI) | **No**        |

**Local quality gate status (2026-07-19):**

| Gate      | Command                               | Result          |
| --------- | ------------------------------------- | --------------- |
| Lint      | `make lint` (Ruff)                    | Pass            |
| Typecheck | `make typecheck` (Pyright basic mode) | Pass (0 errors) |
| Format    | `make format-check` (Black)           | Pass            |
| Tests     | `make test`                           | Pass (130)      |

**Recent CI runs:** last 5 `PR Quality Checks` workflow runs on GitHub reported **success** (most recent: 2026-07-19).

**Pre-commit (`.pre-commit-config.yaml`):** Python hooks run Ruff check (with `--fix`), Black format (auto-fix, not `--check`), and Pyright. Format is enforced locally via pre-commit but **not** as a `--check` gate in CI.

#### Baseline Gap Summary (confirms plan assumptions)

| Area            | Current state (verified)                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Settings        | Pydantic-settings; provider key validation; JWT secret check for non-dev; no `LOG_LEVEL`, body limit setting, or rate-limit settings |
| Logging         | `basicConfig(INFO)`; plain text; no JSON, correlation IDs, or redaction                                                              |
| Correlation IDs | Not implemented; no `X-Request-ID`                                                                                                   |
| Error handling  | Consistent `{ error: { code, message } }` for most paths; readiness/DB uses different shape; no `request_id`                         |
| Rate limiting   | Guest daily quota only (`quota_service.py`); no HTTP middleware or `Retry-After`                                                     |
| Pyright         | `typeCheckingMode = "basic"` — passes cleanly                                                                                        |
| CI              | Lint + typecheck + test present; **no format-check step, no coverage threshold**                                                     |
| Node.js         | Basic chat; no structured logging, correlation IDs, or HTTP rate limiting                                                            |

---

## Phase 1 - Environment Refinement

### Objectives

Consolidate configuration, validate required settings at startup, separate development and production behavior, and remove duplicate environment variables.

### Tasks

- Audit and reconcile env templates:
  - Align `backend-python/.env.example`, `.env.required`, `.env.compose`, and `docker-compose.yml` python service block.
  - Resolve known drift (e.g. `CORS_ALLOWED_ORIGINS` defaults, missing auth/DB/JWT vars in compose).
- Extend `backend-python/app/core/config.py`:
  - Add `request_body_limit_bytes` setting (currently hardcoded 16KB in `main.py`; align with Node's `REQUEST_BODY_LIMIT_BYTES`).
  - Add `log_level` setting with validated enum (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
  - Add rate-limit settings placeholders (`rate_limit_anonymous_per_minute`, `rate_limit_authenticated_per_minute`) for Phase 5.
  - Strengthen startup validation for production (`APP_ENV != development`): required provider keys, `DATABASE_URL`, `GOOGLE_CLIENT_ID` when auth routes are enabled.
- Introduce environment-aware defaults:
  - Development: permissive defaults, human-readable config warnings.
  - Production: fail-fast on missing secrets and insecure defaults.
- Replace hardcoded body limit in `main.py` with settings value.
- Update documentation:
  - `backend-python/README.md` env section.
  - Root README deployment/env references if present.

### Verification Checklist

- Single authoritative env variable list with consistent naming across templates.
- App fails fast with clear error when required production vars are missing.
- App starts successfully in development with `.env.example`.
- Docker compose profile starts with aligned env vars.
- Unit tests for settings validation (invalid JWT secret in production, missing provider key).

### Exit Criteria

- Configuration is centralized, validated, and documented.
- No duplicate or conflicting env variable definitions remain.
- User confirms Phase 1 completion.

---

## Phase 2 - Structured Logging

### Objectives

Replace ad-hoc logging with structured logging that supports production diagnostics.

### Tasks

- Create `backend-python/app/core/logging.py`:
  - Configure root logger based on `APP_ENV` and `LOG_LEVEL`.
  - **Production:** JSON formatter with fields: `timestamp`, `level`, `message`, `logger`.
  - **Development:** human-readable console formatter with color optional.
  - Suppress noisy third-party loggers as needed.
- Define log context schema (TypedDict or dataclass) for request-scoped fields:
  - `request_id`, `user_id`, `route`, `method`, `status_code`, `latency_ms`, `provider`, `model`.
- Create helper `get_logger(name)` and `bind_context(**kwargs)` for structured extra fields.
- Wire logging setup in `main.py` lifespan/startup (replace `logging.basicConfig`).
- Update key call sites to use structured logging:
  - `app/services/chat_service.py` (provider, model, latency).
  - `app/services/quota_service.py`.
  - `app/routers/chat.py`, `auth.py`.
- Add redaction guard: never log API keys, JWT tokens, or full message content.
- Add tests:
  - JSON output shape in production mode.
  - Log level filtering.
  - Redaction behavior.

### Verification Checklist

- Production mode emits valid JSON log lines.
- Development mode emits readable single-line logs.
- Chat request produces logs with provider, model, route, and latency fields.
- No secrets or message bodies appear in log output.
- Existing tests pass; new logging tests pass.

### Exit Criteria

- Structured logging is active across primary request paths.
- User confirms Phase 2 completion.

---

## Phase 3 - Request / Correlation IDs

### Objectives

Generate a unique request ID for every request and propagate it through the request lifecycle.

### Tasks

- Create `backend-python/app/middleware/correlation_id.py`:
  - Accept incoming `X-Request-ID` header if present and valid (UUID format); otherwise generate a new UUID.
  - Store request ID in `contextvars.ContextVar` for access in services and loggers.
  - Attach `X-Request-ID` to every response header.
  - Bind request ID into structured log context automatically.
- Register middleware in `main.py` (early in stack, before route handlers).
- Include `request_id` in error response envelope (extend `ErrorResponseSchema` if needed).
- Update frontend API client (if applicable) to forward `X-Request-ID` on retries for traceability.
- Add tests:
  - Generated ID when header absent.
  - Preserved ID when valid header provided.
  - Response header present on success and error paths.
  - Request ID appears in log context.

### Verification Checklist

- Every API response includes `X-Request-ID`.
- Logs for a single request share the same `request_id`.
- Error responses include `request_id` in envelope (when schema extended).
- Middleware does not break streaming (SSE) responses.

### Exit Criteria

- Correlation IDs are end-to-end traceable from request to log to error response.
- User confirms Phase 3 completion.

---

## Phase 4 - Centralized Error Handling

### Objectives

Return a consistent error envelope for all failure categories with no unhandled exception leaks.

### Error Categories

| Category              | HTTP Status | Error Code (example)    |
| --------------------- | ----------- | ----------------------- |
| Validation            | 422         | `validation_error`      |
| Authentication        | 401         | `authentication_failed` |
| Authorization         | 403         | `forbidden`             |
| Rate limiting         | 429         | `rate_limit_exceeded`   |
| Quota exceeded        | 429         | `quota_exceeded`        |
| Provider failure      | 502/503     | `provider_error`        |
| Provider rate limited | 429         | `provider_rate_limited` |
| Database              | 503         | `database_error`        |
| Unexpected            | 500         | `internal_error`        |

### Tasks

- Audit all exception paths in routers and services for unhandled cases.
- Add or extend exception classes in `app/core/errors.py` (or equivalent) for missing categories.
- Register FastAPI exception handlers in `main.py` for:
  - SQLAlchemy / DB connection errors → 503 `database_error`.
  - Rate limit exceptions (placeholder handler for Phase 5).
  - Ensure all handlers include `request_id` from context.
- Standardize error envelope shape across all handlers:

  ```json
  {
    "error": {
      "code": "string",
      "message": "string",
      "request_id": "uuid"
    }
  }
  ```

- Map existing `AuthError`, `ChatServiceError` hierarchy to standardized codes.
- Ensure 413 payload-too-large uses the same envelope (currently custom middleware).
- Add tests for each error category (unit + integration via `TestClient`).

### Verification Checklist

- All listed error categories return consistent envelope shape.
- No stack traces leak to client in production.
- `request_id` present in all error responses.
- Existing chat/auth/quota error tests still pass.

### Exit Criteria

- Centralized error handling covers all failure paths with consistent responses.
- User confirms Phase 4 completion.

---

## Phase 5 - Rate Limiting

### Objectives

Protect public endpoints while keeping authenticated usage practical.

### Suggested Policy

| Caller                  | Limit        | Window     |
| ----------------------- | ------------ | ---------- |
| Anonymous (guest token) | 30 requests  | per minute |
| Authenticated (JWT)     | 120 requests | per minute |

(Values configurable via settings from Phase 1.)

### Tasks

- Choose implementation approach:
  - **Recommended:** in-memory sliding window for MVP (single-instance deploy).
  - Document Redis upgrade path for multi-instance production.
- Create `backend-python/app/middleware/rate_limit.py`:
  - Identify caller tier from request (guest vs authenticated vs anonymous).
  - Apply configurable limits from settings.
  - Return 429 with standardized error envelope and `Retry-After` header (seconds until reset).
  - Exempt health/readiness endpoints (`/api/health`, `/api/health/ready`).
- Integrate with Phase 4 error handler (`rate_limit_exceeded`).
- Log rate-limit events at WARNING level with request_id and caller tier.
- Distinguish HTTP rate limiting from existing guest daily quota (`quota_service.py`):
  - HTTP rate limit: requests per minute (middleware).
  - Guest quota: messages per day (business logic) — keep both.
- Add tests:
  - Anonymous limit enforced.
  - Authenticated higher limit enforced.
  - `Retry-After` header present and reasonable.
  - Health endpoints exempt.

### Verification Checklist

- Anonymous callers receive 429 after exceeding per-minute limit.
- Authenticated callers have higher effective limit.
- Error response matches centralized envelope with `rate_limit_exceeded` code.
- `Retry-After` header present on 429 responses.
- Guest daily quota still works independently.

### Exit Criteria

- HTTP rate limiting is active and configurable.
- User confirms Phase 5 completion.

---

## Phase 6 - Static Type Checking (Pyright)

### Objectives

Adopt Pyright standard mode and resolve all type issues.

### Tasks

- Update `backend-python/pyproject.toml`:
  - Set `typeCheckingMode = "standard"`.
  - Add explicit `[tool.ruff]` section with project conventions (line length, target version).
- Run `make typecheck` and triage all errors by module.
- Fix type issues in priority order:
  1. `app/core/` (config, security, caller)
  2. `app/routers/`
  3. `app/services/`
  4. `app/schemas/`
  5. `tests/`
- Add type annotations to public functions missing them.
- Resolve `reportMissingTypeStubs` issues with targeted `# type: ignore` only when justified and documented.
- Align formatter choice:
  - **Decision:** adopt Ruff format (replace Black) OR keep Black — pick one and align Makefile, pre-commit, and CI.
  - Update `.pre-commit-config.yaml`, `Makefile`, and CI accordingly.
- Document type checking expectations in `backend-python/README.md`.

### Verification Checklist

- `make typecheck` passes with zero errors under standard mode.
- Chosen formatter check passes locally (`make format-check`).
- No unjustified `any` types introduced.
- All existing tests still pass.

### Exit Criteria

- Pyright standard mode is clean.
- Formatter tooling is consistent across local and CI.
- User confirms Phase 6 completion.

---

## Phase 7 - CI Quality Gates

### Objectives

Enforce full quality gates in CI so regressions cannot merge.

### Required Gates

| Gate       | Tool                   | Status                       |
| ---------- | ---------------------- | ---------------------------- |
| Lint       | Ruff check             | Present                      |
| Format     | Ruff format (or Black) | **Add**                      |
| Type check | Pyright standard       | Present (upgrade from basic) |
| Tests      | pytest                 | Present                      |
| Coverage   | pytest-cov             | **Add**                      |

### Tasks

- Add `pytest-cov` to dev dependencies.
- Create coverage config (`.coveragerc` or `pyproject.toml` `[tool.coverage]`):
  - Source: `app/`
  - Omit: tests, migrations boilerplate.
  - Initial threshold: **80%** (adjust if baseline is lower; document rationale).
- Update `Makefile`:
  - `make test` → run with coverage report.
  - `make test-cov` → enforce threshold locally.
  - `make format-check` → CI-ready format verification.
- Update `.github/workflows/pr-quality.yml` backend-python job:
  - Add format check step.
  - Add coverage threshold step (`pytest --cov=app --cov-fail-under=80`).
  - Ensure pyright runs after Phase 6 standard mode upgrade.
- Align `.pre-commit-config.yaml` with CI (same tools, same versions).
- Optionally add Node.js format check (`npm run format:check`) if script exists.
- Document required checks for branch protection in README or devops doc.

### Verification Checklist

- CI pipeline runs lint + format + typecheck + test + coverage on Python PRs.
- PR with intentional lint/type/coverage failure is blocked.
- Pre-commit hooks match CI tooling.
- Coverage report artifact generated (optional upload to PR comment).

### Exit Criteria

- All CI quality gates pass on a clean branch.
- User confirms Phase 7 completion.

---

## Phase 8 - Node.js Backend Alignment

### Objectives

Port hardened patterns from Python to Node.js for consistency (not full feature parity).

### Tasks

- Port structured logging to `backend-nodejs/src/core/logging.ts`:
  - JSON in production, readable in development.
  - Same field schema as Python.
- Port correlation ID middleware to `backend-nodejs/src/middleware/correlationId.ts`:
  - `X-Request-ID` generation/propagation.
  - Context binding for loggers.
- Port rate limiting middleware to `backend-nodejs/src/middleware/rateLimit.ts`:
  - Same tier policy and `Retry-After` behavior.
  - Configurable via `backend-nodejs/src/core/config.ts`.
- Extend error handling in `backend-nodejs/src/core/errors.ts`:
  - Add missing error codes matching Python.
  - Include `request_id` in error envelope.
- Align env templates with Python naming where applicable.
- Add vitest tests for middleware and error shapes.
- Update Node CI job with format check if applicable.

### Verification Checklist

- Node backend starts and serves chat with structured logs.
- Correlation ID present in response headers and logs.
- Rate limiting returns consistent 429 envelope.
- Node vitest suite passes.
- Error envelope shape matches Python contract.

### Exit Criteria

- Node.js backend reflects Python hardening patterns for logging, tracing, errors, and rate limiting.
- User confirms Phase 8 completion.

---

## Phase 9 - Final MVP Validation

### Objectives

Verify the complete MVP meets the definition of done through systematic end-to-end validation.

### Validation Checklist

| Area                | Verification                                                    |
| ------------------- | --------------------------------------------------------------- |
| Clean startup       | Python + Node + frontend start without errors in dev and Docker |
| Authentication      | Google OAuth login, JWT issuance, guest token flow              |
| Multi-provider chat | OpenAI, Gemini, Groq, Anthropic provider switching              |
| Streaming           | SSE chat streaming works with correlation IDs                   |
| Persistence         | Chat history saved and retrieved when enabled                   |
| Logging             | JSON logs in production mode with expected fields               |
| Correlation IDs     | `X-Request-ID` on all responses; traceable in logs              |
| Error responses     | Consistent envelope for all error categories                    |
| Rate limiting       | 429 with `Retry-After` under load test                          |
| Tests               | Full pytest + vitest suites pass                                |
| CI                  | All quality gates green on main branch                          |
| Deployment          | Staging/production deploy succeeds                              |
| Documentation       | Env, logging, error codes, and deployment docs updated          |

### Tasks

- Run full manual QA script covering the validation checklist.
- Run load test (simple script or `hey`/`ab`) against rate-limited endpoints.
- Deploy to staging environment and smoke test.
- Update documentation:
  - `docs/references/mvp-completion-strategy.md` → mark complete or archive.
  - `backend-python/README.md`, root README.
  - `docs/plans/devops-implementation-plan.md` → reflect current CI state.
- Create MVP completion summary (changelog entry or release note).

### Verification Checklist

- Every row in validation checklist verified and recorded.
- No P0/P1 issues open for MVP scope.
- CI green on main.
- Staging deployment successful.

### Exit Criteria

- MVP is declared complete per Definition of Done below.
- User confirms Phase 9 completion.

### Phase 9 Final Validation (2026-07-19)

Validation performed locally after Phases 0–7. **Phase 8 (Node.js alignment) was skipped** per product decision; Python backend remains the production reference for MVP.

#### Validation Checklist Results

| Area                       | Verification                      | Result         | Evidence                                                                           |
| -------------------------- | --------------------------------- | -------------- | ---------------------------------------------------------------------------------- |
| Clean startup              | Python app import; Compose config | **Pass**       | `from app.main import app` OK; `docker compose config --quiet` exit 0              |
| Clean startup (Docker run) | Full stack boot                   | **Not re-run** | Compose profiles documented; manual smoke recommended before prod promotion        |
| Authentication             | Google login, JWT, guest token    | **Pass**       | `tests/test_auth.py`, `tests/test_summarization_and_linking.py`                    |
| Multi-provider chat        | Provider switching                | **Pass**       | Provider unit tests + `tests/test_config.py` provider validation                   |
| Streaming                  | SSE with correlation IDs          | **Pass**       | `tests/test_chat_stream.py`, `tests/test_correlation_id.py`                        |
| Persistence                | History save/resume               | **Pass**       | `tests/test_chat_persistence.py` (Postgres integration; skips when DB unavailable) |
| Logging                    | JSON in production                | **Pass**       | `tests/test_logging.py` (production formatter, redaction, structured fields)       |
| Correlation IDs            | `X-Request-ID` on responses       | **Pass**       | `tests/test_correlation_id.py`; frontend retry forwards header                     |
| Error responses            | Consistent envelope               | **Pass**       | `tests/test_errors.py` + existing chat/auth/quota tests                            |
| Rate limiting              | 429 + `Retry-After` under burst   | **Pass**       | `tests/test_rate_limit.py` + burst load check (5 allowed / 3 blocked at limit=5)   |
| Tests (Python)             | Full pytest suite                 | **Pass**       | **167 passed**, 89% coverage on `app/`                                             |
| Tests (Frontend)           | Vitest                            | **Pass**       | **90 passed** (1 ESLint warning, 0 errors)                                         |
| Tests (Node.js)            | Vitest (baseline, not hardened)   | **Pass**       | **26 passed** — unchanged chat-only backend                                        |
| CI quality gates           | Lint, format, typecheck, coverage | **Pass**       | `make lint`, `make format-check`, `make typecheck`, `make test-cov`                |
| CI workflow                | PR job definitions                | **Pass**       | `.github/workflows/pr-quality.yml` includes format + coverage steps                |
| Deployment                 | Staging/production CD             | **Not re-run** | CD workflows exist (devops plan D1/D2); re-verify on next merge to `main`          |
| Documentation              | Env, logging, errors, CI          | **Pass**       | Updated in this phase (see below)                                                  |

#### Automated Gate Summary (2026-07-19)

```
backend-python:  lint ✓  format-check ✓  typecheck ✓  test-cov ✓ (89% ≥ 80%)
frontend:         lint ✓ (1 warning)       test ✓ (90)
backend-nodejs:   lint ✓                   test ✓ (26)
```

#### Documentation Updates (Phase 9)

- `docs/references/mvp-completion-strategy.md` — marked **complete**; Node alignment deferred
- `docs/plans/mvp-completion-implementation-plan.md` — this validation record
- `docs/plans/devops-implementation-plan.md` — Python CI gate list updated
- `backend-python/README.md`, root `README.md` — MVP status and quality gates

#### MVP Completion Summary

**Delivered (Python production reference):**

- Consolidated env config with startup validation (Phase 1)
- Structured JSON logging with redaction (Phase 2)
- Request correlation IDs on all responses (Phase 3)
- Centralized error envelope for all failure categories (Phase 4)
- HTTP rate limiting with `Retry-After` (Phase 5)
- Pyright standard mode (Phase 6)
- CI gates: lint, format, typecheck, tests, 80% coverage (Phase 7)

**Deferred post-MVP:**

- Node.js backend alignment (Phase 8) → `docs/plans/nodejs-backend-v1.md`
- V1 AI capabilities (tools, RAG, etc.) — out of scope

**Recommended follow-ups before production promotion:**

1. Re-run staging CD workflow and manual chat smoke test on `main`
2. Confirm branch protection required checks match updated Python job steps
3. Complete Node.js hardening when resuming dual-backend parity

---

## Suggested Task Breakdown (PR-Friendly)

1. **PR 1:** Phase 0 audit notes + Phase 1 env refinement.
2. **PR 2:** Phase 2 structured logging.
3. **PR 3:** Phase 3 correlation IDs + Phase 4 error handling.
4. **PR 4:** Phase 5 rate limiting.
5. **PR 5:** Phase 6 pyright standard + formatter alignment.
6. **PR 6:** Phase 7 CI quality gates (coverage, format).
7. **PR 7:** Phase 8 Node.js alignment.
8. **PR 8:** Phase 9 validation fixes + documentation.

---

## Risk Register and Mitigation

| Risk                                                 | Impact | Mitigation                                                                           |
| ---------------------------------------------------- | ------ | ------------------------------------------------------------------------------------ |
| Structured logging breaks log aggregation            | Medium | Validate JSON schema early; test with sample CloudWatch/Datadog ingest               |
| Correlation ID middleware breaks SSE streaming       | High   | Dedicated streaming test in Phase 3; verify flush behavior                           |
| Rate limiting too aggressive for demo usage          | Medium | Configurable limits; conservative defaults; document tuning                          |
| Pyright standard mode reveals large type debt        | Medium | Fix incrementally by module; do not suppress broadly                                 |
| Formatter migration (Black → Ruff) causes large diff | Low    | Single dedicated PR; run once across codebase                                        |
| Coverage threshold blocks merge on legacy gaps       | Medium | Measure baseline first; set threshold slightly below baseline then ratchet up        |
| Env consolidation breaks Docker compose              | High   | Test compose startup in Phase 1 verification                                         |
| Node alignment diverges from Python patterns         | Low    | Port only after Python patterns are verified; share test fixtures for envelope shape |

---

## Definition of Done

The MVP is complete when **all** of the following are true:

- Infrastructure hardening is finished for the **Python production reference** (Phases 1–7; Phase 8 Node alignment deferred post-MVP).
- CI quality gates pass (lint, format, pyright standard, pytest, coverage threshold).
- Production deployment succeeds (staging CD re-verification recommended after final merge).
- Documentation is updated (env vars, logging, error codes, deployment, CI gates).
- Core chat functionality remains stable (auth, multi-provider, streaming, persistence).
- The project is ready to serve as the foundation for V1 AI capabilities.

---

## Final Acceptance Gate

All items must be true:

- Centralized, validated environment configuration with aligned templates.
- Structured JSON logging in production with correlation IDs on every request.
- Consistent error envelope across all failure categories.
- HTTP rate limiting active with `Retry-After` on 429 responses.
- Pyright standard mode passes with zero errors.
- CI enforces lint, format, typecheck, tests, and coverage threshold.
- Node.js backend alignment **deferred** — tracked separately; not blocking MVP (2026-07-19).
- Final validation checklist completed and recorded (Phase 9 section above).
- User confirms MVP completion.
