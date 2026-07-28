# Post-MVP V1 - Implementation Plan

## Objective

Transform the completed MVP chatbot into a reusable AI platform by introducing modular, provider-agnostic AI infrastructure. V1 proves the architecture — centralized prompts, a generic tool platform with web search, document ingestion with pgvector, a **Generic RAG Framework** (domain-agnostic retrieval infrastructure), and an evaluation framework — while preserving MVP stability and adhering to YAGNI implementation constraints from `docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md`.

Domain-specific RAG applications (Customer Care, Community Service, Enterprise Knowledge Base, Legal Assistant, HR Assistant, etc.) are **out of scope for V1**; they are future consumers of the Generic RAG Framework via configuration and composition.

## Execution Mode

- Implement sequentially by phase.
- Use the **Python backend** as the production reference; Node.js AI infrastructure is out of scope for V1.
- After each phase verification is complete, stop and request explicit user confirmation before starting the next phase.
- Every milestone must leave the application deployable; existing chat, auth, and persistence flows must not regress.
- Follow **YAGNI**: introduce abstractions only when a second concrete implementation or a clear architectural need exists.
- Prefer the simplest implementation that satisfies the current milestone; document V2 extension points rather than building them.
- **Async-first**: ingestion, embedding batches, vector queries, and tool handlers are `async` end-to-end; use `asyncio.to_thread` only where an SDK is synchronous (same pattern as the Gemini LLM adapter).
- **Stateless AI services**: `RAGService`, `Retriever`, and tool execution hold no per-request state beyond injected dependencies.
- **No hidden magic**: favor explicit orchestration over automatic discovery, reflection, decorators, plugin loading, or implicit runtime behavior unless there is a demonstrated architectural need.

## Phase Workflow

Each phase follows this checkpoint sequence:

```text
Architecture Review
        ↓
Implementation
        ↓
Tests
        ↓
Regression Verification
        ↓
User Confirmation
```

- **Architecture Review**: confirm component boundaries, dependency direction, and YAGNI compliance before writing code.
- **Implementation**: build only what the phase scope defines.
- **Tests**: unit and integration tests alongside changes; coverage ≥ 80% on `app/`.
- **Regression Verification**: full MVP suite passes; feature flags off = no behavior change.
- **User Confirmation**: explicit approval before the next phase begins.

## V1 Locked Decisions

| Decision                  | Choice                                                     | Rationale                                                     |
| ------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------- |
| Frontend RAG UI           | **Separate route** (e.g. `/documents` or `/rag`)           | Clearer scope; lower chat regression risk                     |
| Document / RAG access     | **Auth-only** — guests cannot upload or call RAG endpoints | Simpler ownership model; documents scoped to `user_id` only   |
| Generic RAG phase split   | **Phase 8 (components) + Phase 9 (orchestration)**         | Smaller PRs; easier debugging and review                      |
| Evaluation phase order    | **After Phase 9 (RAG orchestration), before API**          | Tune chunk/top-K settings before exposing HTTP endpoints      |
| Tool + chat orchestration | **`ToolChatService` composes `ChatService`**               | Avoids further growth of an already large `ChatService`       |
| Streaming + tools (V1)    | **Disable tool calling when `stream=true`**                | Non-streaming tool loop only in V1                            |
| RAG responses (V1)        | **Non-streaming only**                                     | Simpler pipeline; streaming RAG deferred to V2                |
| Embedding storage schema  | **`embedding vector(N)` column on `document_chunks`**      | Single table; populated in Phase 7 migration                  |
| Embedding cache (V1)      | **Deferred**                                               | Document Redis path for V2; mocks suffice in tests            |
| Tool integration model    | **Provider-capability driven**                             | Adapters expose what each LLM supports; no assumed API parity |

## Phase Status

- Phase 0 - **Complete** (2026-07-20)
- Phase 1 - **Complete** (2026-07-20)
- Phase 2 - **Complete** (2026-07-20)
- Phase 3 - **Complete** (2026-07-20)
- Phase 4 - **Complete** (2026-07-20)
- Phase 5 - **Complete** (2026-07-20)
- Phase 6 - **Complete** (2026-07-21)
- Phase 7 - **Complete** (2026-07-21)
- Phase 8 - **Complete** (2026-07-21)
- Phase 9 - **Complete** (2026-07-21)
- Phase 10 - **Complete** (2026-07-21)
- Phase 11 - **Complete** (2026-07-21)
- Phase 12 - **Complete** (2026-07-21)
- Phase 13 - **Complete** (2026-07-21)

## Scope

- In scope:
  - `app/ai/` reusable AI framework (prompts, tools, documents, embeddings, vectorstores, rag, evaluation)
  - Prompt infrastructure (central repository, Jinja2 rendering, versioning, regression tests)
  - Tool platform lifecycle (registry → validation → authorization → execution → normalization)
  - Web search as the first tool
  - Knowledge platform (upload → parser → chunker → embeddings → vector store)
  - pgvector as the primary vector store implementation
  - **Generic RAG Framework** (retriever → context builder → prompt builder → LLM → response) — domain-agnostic; no business-specific logic in framework code
  - Evaluation framework (prompt, retrieval, end-to-end levels)
  - Configuration matrix extensions (embedding model, chunk size/overlap, top-K, temperature, etc.)
  - API endpoints and minimal frontend surfaces for document upload and generic RAG interaction (**authenticated users only**)
  - Tests, migrations, env templates, and documentation for all V1 capabilities
- Out of scope (V1):
  - Node.js AI infrastructure parity
  - Additional vector store backends (Chroma, Pinecone, Qdrant) — interface documented, not implemented
  - Future tools (calculator, weather, GitHub, SQL)
  - Domain-specific RAG applications (Customer Care, Legal, HR, etc.) — built on top of the framework post-V1
  - Generic RAG infrastructure enhancements deferred to V2 (hybrid retrieval, reranking, metadata filtering, query expansion, citations, context compression, parent-child retrieval)
  - V2 capabilities (MCP, memory, agents, workflow engine, voice, vision)
  - Plugin systems, generic factories for single implementations, unnecessary base classes
  - Business-specific abstractions or domain logic inside `app/ai/rag/`
  - Guest document upload or guest-scoped RAG corpora

## Non-Negotiable Requirements

1. Python backend is the production reference.
2. Dependency direction is enforced: Routers → Services → AI Framework → Providers → External APIs.
3. Lower layers never depend on higher layers.
4. Implement one capability at a time; do not batch unrelated subsystems.
5. Add tests alongside every implementation change; maintain ≥ 80% coverage on `app/`.
6. Every phase must be verifiable before moving to the next phase.
7. User confirmation is required between phases.
8. No sensitive data (API keys, tokens, document content, search queries) in logs.
9. Application must remain deployable after each phase; MVP chat/auth/persistence must keep working.
10. Do not introduce abstractions until at least two concrete implementations exist or architecture explicitly requires it.
11. The Generic RAG Framework (`app/ai/rag/`) must remain **domain-agnostic** — business knowledge lives in documents and prompts; domain-specific logic belongs in application services (`app/services/`), not in framework code.
12. Document and RAG endpoints require authentication — no guest-scoped document ownership in V1.

## Module Boundaries (`app/providers/` vs `app/ai/`)

| Layer        | Location                                               | Responsibility                                                            |
| ------------ | ------------------------------------------------------ | ------------------------------------------------------------------------- |
| LLM adapters | `app/providers/`                                       | Existing `LLMProvider` protocol and OpenAI/Gemini/Groq/Anthropic adapters |
| Embeddings   | `app/ai/embeddings/`                                   | `EmbeddingProvider` protocol + concrete adapters (OpenAI first)           |
| Vector store | `app/ai/vectorstores/`                                 | `VectorStore` protocol + `PgVectorStore`                                  |
| AI framework | `app/ai/` (prompts, tools, documents, rag, evaluation) | Domain-agnostic orchestration consumed by `app/services/`                 |

Rule: `app/services/` depends on `app/ai/` and `app/providers/`; `app/ai/` may call external APIs through its own provider adapters or reuse patterns from `app/providers/` without creating upward dependencies.

## Provider Capability Model

Tool integration is **provider-capability driven**, not API-shape driven. Each LLM adapter declares and implements only the capabilities its provider supports:

| Capability                   | V1 expectation                                         |
| ---------------------------- | ------------------------------------------------------ |
| Non-streaming chat           | All four providers                                     |
| Streaming chat               | All four providers                                     |
| Tool / function calling      | Primary provider first (Phase 0); others incrementally |
| Structured tool call parsing | Per-provider adapter logic                             |

Do not assume identical tool-calling behavior across OpenAI, Gemini, Groq, and Anthropic. `ToolChatService` depends on the `LLMProvider` protocol; each adapter maps normalized tool schemas to provider-native formats.

## External Service Retry Policy

Apply a consistent retry strategy for all external API calls (LLM, embeddings, web search):

| Setting      | Value                                                              |
| ------------ | ------------------------------------------------------------------ |
| Retry on     | HTTP 429, HTTP 503, network timeout, temporary connection failures |
| Max attempts | 3                                                                  |
| Backoff      | Exponential (e.g. 1s → 2s → 4s with jitter)                        |
| Do not retry | HTTP 4xx (except 429), validation errors, auth failures            |

Implement once in a shared utility (e.g. `app/core/retry.py`) and reuse from provider adapters and tool handlers — do not duplicate retry logic per service.

## Performance Targets (Engineering Guidance)

These are **soft targets** for development and regression detection, not hard SLA requirements:

| Operation                             | Target       |
| ------------------------------------- | ------------ |
| Document upload (max size)            | ≤ 10 MB      |
| Embedding generation (typical doc)    | ≤ 30 seconds |
| Top-K retrieval (end-to-end)          | < 150 ms     |
| Vector similarity search (DB query)   | < 100 ms     |
| Tool execution (web search)           | < 10 seconds |
| Complete RAG response (non-streaming) | < 8 seconds  |

Log stage latencies in structured logs; flag warnings when targets are exceeded in dev/staging. Phase 10 evaluation CLI can compare latency metrics against these baselines.

## Observability

### Logging (existing)

Structured logging with correlation IDs; no sensitive content in logs.

### Metrics (V1 minimum — structured log counters or simple in-process counters)

Document in `backend-python/README.md`; emit via structured log fields or a lightweight metrics helper:

| Metric                     | Purpose                     |
| -------------------------- | --------------------------- |
| `rag_requests_total`       | RAG ask volume              |
| `rag_request_duration_ms`  | End-to-end RAG latency      |
| `retrieval_latency_ms`     | Retriever stage latency     |
| `embedding_latency_ms`     | Embedding batch latency     |
| `vector_search_latency_ms` | pgvector query latency      |
| `tool_calls_total`         | Tool invocations by name    |
| `tool_errors_total`        | Tool failures by name       |
| `search_latency_ms`        | Web search provider latency |
| `documents_ingested_total` | Successful ingestions       |
| `documents_failed_total`   | Failed ingestions           |

V1 does not require Prometheus/Grafana — log-structured metrics are sufficient. Design metric names now for V2 export compatibility.

## Current Baseline (as of plan creation)

| Area               | Current state                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------------- |
| MVP hardening      | Complete (env, logging, correlation IDs, errors, rate limiting, pyright standard, CI gates)                         |
| `app/ai/`          | **Does not exist** — no AI framework module yet                                                                     |
| Prompts            | Hardcoded strings in `chat_service.py` (summarization system/user prompts); seed data in `db/seed.py`               |
| Tools              | Not implemented                                                                                                     |
| Documents / RAG    | Not implemented — Generic RAG Framework not yet built                                                               |
| Embeddings         | Not implemented                                                                                                     |
| Vector store       | Not implemented; Postgres exists for chat persistence (no pgvector extension)                                       |
| Evaluation         | Not implemented                                                                                                     |
| Providers          | LLM adapters only (`openai`, `gemini`, `groq`, `anthropic`) via `LLMProvider` protocol                              |
| Config             | `Settings` in `app/core/config.py` — LLM, auth, quota, logging, rate limits; no AI/RAG settings yet                 |
| Tests              | **170 passed**, 89.49% coverage on `app/`, 4.65s duration (Phase 0 verified 2026-07-20)                             |
| Frontend           | Chat UI only; no document upload or RAG mode                                                                        |
| Recommended libs   | Jinja2, PyMuPDF, python-docx, pgvector listed in spec; not yet in dependencies                                      |
| Request body limit | Global **16 KB** middleware on all POST/PUT/PATCH — blocks document upload until Phase 11 adds route-specific limit |
| Docker Postgres    | `postgres:16-alpine` — **does not include pgvector**; image change required before Phase 7                          |
| CORS               | `allow_methods=["GET", "POST"]` only — **DELETE** must be added for document removal (Phase 11)                     |

### Hardcoded Prompt Inventory (to migrate in Phase 2)

Phase 0 grep audit (2026-07-20): searched `backend-python/app/services/` and `backend-python/app/routers/` for system-role strings, `"You are"`, `"Summarize"`, and similar prompt patterns. **No additional hardcoded prompts found** beyond the table below. Routers contain no prompt strings.

| Location                              | Prompt purpose                                         |
| ------------------------------------- | ------------------------------------------------------ |
| `chat_service._build_summary_input`   | Summarization system prompt                            |
| `chat_service._build_summary_input`   | Summarization user prompt template                     |
| `chat_service.build_context_messages` | Summary injection system prefix                        |
| `db/seed.py`                          | Demo system message (`"You are a helpful assistant."`) |

---

## Phase 0 - Baseline Audit and V1 Readiness

### Objectives

Establish a verified starting point, confirm MVP stability, and document gaps before any V1 code changes.

### Tasks

- Confirm MVP completion status (`docs/plans/mvp-completion-implementation-plan.md` Phase 9).
- Run full quality gate locally:
  - `make lint`, `make format-check`, `make typecheck`, `make test-cov`
- Record baseline test count, coverage percentage, and duration.
- Inventory all hardcoded prompts (table above); confirm no other prompt strings in services/routers.
- Verify Postgres connectivity and Alembic migration state (`alembic current`).
- Confirm Docker Compose Python profile starts cleanly.
- **Verify pgvector readiness:**
  - Confirm `postgres:16-alpine` lacks pgvector extension.
  - Plan Docker image change (e.g. `pgvector/pgvector:pg16`) or custom init script for Phase 7.
  - Verify pgvector availability on staging/hosted Postgres provider.
- **Audit request body limit impact:**
  - Document that global 16 KB limit (`request_body_limit_bytes`) will reject document uploads.
  - Plan separate `document_upload_max_bytes` setting and route-specific middleware exemption (Phase 1 config, Phase 11 implementation).
- **Record primary LLM provider** for tool-calling implementation order (value of `LLM_PROVIDER` in dev/staging).
- Document current API surface (`/api/health`, `/api/auth/*`, `/api/chat/*`) for regression comparison.
- Create a V1 feature flag strategy:
  - Decide default-off vs default-on for RAG/tools endpoints during incremental rollout.
  - Plan env vars: `RAG_ENABLED`, `TOOLS_ENABLED` (or equivalent).
- Review spec constraints (`YAGNI`, dependency direction) with team; agree on pgvector-only for V1.

### Success Criteria

- All MVP quality gates pass locally.
- Baseline test count, coverage, and duration recorded in this plan.
- Hardcoded prompt inventory complete with no surprises.
- pgvector Docker/staging remediation plan documented.
- Body limit impact and route-specific exemption plan documented.
- Primary LLM provider for Phase 4 recorded.
- Feature flag defaults and env var names agreed.

### Verification Checklist

- All MVP quality gates pass locally.
- Baseline metrics recorded in this plan (Phase 0 section below).
- Hardcoded prompt inventory complete.
- Postgres + Alembic healthy.
- pgvector Docker/staging plan documented.
- Body limit impact and remediation plan documented.
- Primary LLM provider for Phase 4 tool calling recorded.
- Feature flag approach documented.

### Exit Criteria

- Team agrees on baseline and V1 starting point.
- User confirms Phase 0 completion.

### Phase 0 Baseline Record (verified 2026-07-20)

#### MVP completion status

- `docs/plans/mvp-completion-implementation-plan.md` Phase 9 is **Complete** (Phases 0–7 and 9 complete; Phase 8 skipped/deferred).
- MVP hardening baseline confirmed; no V1 feature code introduced in this audit.

#### Quality gate results

Commands run from `backend-python/` (Makefile lives there; root README documents `cd backend-python && make …`):

| Gate             | Command             | Result                                                                  |
| ---------------- | ------------------- | ----------------------------------------------------------------------- |
| Lint             | `make lint`         | All checks passed (Ruff)                                                |
| Format           | `make format-check` | 60 files already formatted                                              |
| Typecheck        | `make typecheck`    | 0 errors, 0 warnings (Pyright)                                          |
| Tests + coverage | `make test-cov`     | **170 passed**, **89.49%** coverage on `app/`, **4.65s** total duration |

Coverage gate: `--cov-fail-under=80` satisfied. Warnings only: `InsecureKeyLengthWarning` for short test JWT secret (expected in dev tests).

#### Postgres and Alembic

| Check             | Result                                            |
| ----------------- | ------------------------------------------------- |
| Connectivity      | OK — local Postgres reachable at `localhost:5432` |
| `alembic current` | `0001_init_chat_persistence (head)`               |
| Migration state   | Healthy — at head revision                        |

Command: `cd backend-python && uv run alembic current`

#### Docker Compose Python profile

| Check               | Result                                                                    |
| ------------------- | ------------------------------------------------------------------------- |
| Command             | `docker compose --profile python up -d --build` (from repository root)    |
| Services            | `postgres`, `backend-python`, `frontend` started; all healthchecks passed |
| `/api/health`       | `200` — `{"status":"ok","provider":"openai","version":"0.1.0"}`           |
| `/api/health/ready` | `200` — `{"status":"ok","db":"ok"}`                                       |

Startup warnings (expected in dev, relevant to V1 auth testing):

- `Using default JWT_SECRET; override before deploying outside development.`
- `GOOGLE_CLIENT_ID is not set; POST /api/auth/google will return auth_not_configured.`

No blocking env gaps for chat or persistence smoke tests.

#### pgvector readiness

| Environment                           | pgvector available?           | Evidence                                                            |
| ------------------------------------- | ----------------------------- | ------------------------------------------------------------------- |
| Docker Compose (`postgres:16-alpine`) | **No**                        | `CREATE EXTENSION vector` fails — control file not present in image |
| Local Postgres (`localhost:5432`)     | **No**                        | Same error when extension not installed on host Postgres            |
| Staging (Railway/hosted)              | **Pending manual validation** | No staging credentials or DB access in this audit session           |

**Phase 7 remediation plan (Docker):** replace `postgres:16-alpine` in `docker-compose.yml` with `pgvector/pgvector:pg16` (or equivalent pgvector-enabled image). Add Alembic migration step: `CREATE EXTENSION IF NOT EXISTS vector;`. Add `pgvector` Python dependency in Phase 7.

**Staging verification (before Phase 7):** connect to staging Postgres and run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

If Railway/managed Postgres blocks the extension, document provider-specific enablement or choose a pgvector-capable plan before Phase 7.

#### Request body limit impact and remediation

| Item                   | Current state                                                      |
| ---------------------- | ------------------------------------------------------------------ |
| Global limit           | `request_body_limit_bytes = 16384` (16 KB) in `app/core/config.py` |
| Middleware             | Applied to all `POST`/`PUT`/`PATCH` in `app/main.py`               |
| Document upload impact | **Will reject** uploads > 16 KB with `413` / `validation_error`    |

**Remediation plan:**

- **Phase 1:** add `document_upload_max_bytes` setting (default `10485760` / 10 MB per performance targets).
- **Phase 11:** exempt `/api/documents/upload` (and similar document routes) from the global 16 KB middleware; enforce upload size via route-specific validation using `document_upload_max_bytes`.

MVP chat JSON payloads remain under 16 KB; no MVP behavior change until document routes ship.

#### Primary LLM provider for Phase 4 tool calling

| Source                        | `LLM_PROVIDER` value                                 |
| ----------------------------- | ---------------------------------------------------- |
| `backend-python/.env.example` | `openai`                                             |
| `.env.compose`                | `openai`                                             |
| Local `backend-python/.env`   | Not present in repo                                  |
| Staging                       | **Pending manual validation** (no hosted env access) |

**Phase 4 implementation order:** implement tool-calling methods on the **OpenAI** adapter first (`complete_chat_with_tools`), then extend Gemini, Groq, and Anthropic incrementally per the provider capability model. Rationale: OpenAI is the dev/compose default, has mature function-calling APIs, and matches existing test/provider patterns.

#### Current API surface (regression baseline)

All routes on Python backend (`app/main.py` routers). Error envelope: `{ "error": { "code", "message", "request_id" } }`. Correlation ID returned via `X-Request-Id` header.

| Method | Path                      | Auth / caller                   | Streaming                     | Rate limit | Notes                                                                                                              |
| ------ | ------------------------- | ------------------------------- | ----------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------ |
| GET    | `/api/health`             | None                            | N/A                           | Exempt     | Liveness; returns provider + version                                                                               |
| GET    | `/api/health/ready`       | None                            | N/A                           | Exempt     | Readiness; DB `SELECT 1`                                                                                           |
| POST   | `/api/auth/google`        | Google ID token in body         | No                            | Yes        | Returns app JWT; optional `X-Guest-Token` for guest linking; `503 auth_not_configured` if `GOOGLE_CLIENT_ID` unset |
| POST   | `/api/chat`               | Guest (default) or Bearer JWT   | No                            | Yes        | Non-streaming completion; guest quota via `X-Guest-Quota-Remaining`                                                |
| POST   | `/api/chat/stream`        | Guest or Bearer JWT             | **SSE** (`text/event-stream`) | Yes        | Pre-flight errors as HTTP; stream after quota/session checks                                                       |
| GET    | `/api/chat/sessions`      | Caller when persistence enabled | No                            | Yes        | Empty list if persistence off                                                                                      |
| POST   | `/api/chat/sessions`      | Caller when persistence enabled | No                            | Yes        | `201` creates session                                                                                              |
| GET    | `/api/chat/sessions/{id}` | Caller when persistence enabled | No                            | Yes        | Session transcript                                                                                                 |

**Rate limits:** sliding window 60s — anonymous 30 req/min (IP + guest token bucket), authenticated 120 req/min (JWT subject). **429** `rate_limit_exceeded` with `Retry-After` header.

**Body size:** oversize request → **413** `validation_error`.

**CORS:** `allow_methods=["GET", "POST"]` only — **DELETE** must be added in Phase 11 for document removal.

#### V1 feature flag strategy

| Env var (Phase 1 settings)        | Purpose                           | Default     | Rollout behavior                                                            |
| --------------------------------- | --------------------------------- | ----------- | --------------------------------------------------------------------------- |
| `RAG_ENABLED` → `rag_enabled`     | RAG HTTP endpoints + pipeline     | **`false`** | When off: no RAG routes registered (or return disabled); MVP chat unchanged |
| `TOOLS_ENABLED` → `tools_enabled` | Tool registry + `ToolChatService` | **`false`** | When off: standard `ChatService` path; streaming always skips tools in V1   |

**Policy:** default-**off** for both flags in all environments until the phase that introduces each capability is verified. Enable incrementally in dev → staging → production. Production fail-fast for missing RAG/tools secrets only when the respective flag is `true` (Phase 1).

#### Spec constraint review (Phase 0 agreement)

Reviewed `docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md` constraints; documented for V1:

| Constraint                            | Agreement                                                             |
| ------------------------------------- | --------------------------------------------------------------------- |
| Python backend = production reference | Yes — Node.js AI infrastructure out of scope for V1                   |
| pgvector-only vector store (V1)       | Yes — no Chroma/Pinecone/Qdrant implementations in V1                 |
| Generic RAG Framework domain-agnostic | Yes — business logic in services/prompts/documents, not `app/ai/rag/` |
| YAGNI / incremental milestones        | Yes — one capability per phase; no speculative abstractions           |
| Dependency direction                  | Yes — Routers → Services → AI Framework → Providers → External APIs   |

---

## Phase 1 - AI Module Foundation and Configuration Matrix

### Objectives

Create the `app/ai/` directory skeleton, extend settings for the configuration matrix, and establish dependency injection patterns without premature abstractions.

### Target Structure (initial scaffold)

```text
backend-python/app/ai/
├── __init__.py
├── interfaces/          # Protocols added incrementally per phase
├── prompts/
│   ├── chat/
│   ├── evaluation/
│   ├── rag/
│   ├── shared/
│   ├── system/
│   └── tools/
├── tools/
├── documents/
│   ├── chunkers/
│   └── parsers/
├── embeddings/
├── vectorstores/
├── rag/
└── evaluation/
```

### Configuration Matrix Extensions

Add to `app/core/config.py` (with `.env.example` / `.env.required` updates):

| Setting                       | Purpose                                      | Default (dev)            |
| ----------------------------- | -------------------------------------------- | ------------------------ |
| `embedding_provider`          | Embedding backend selection                  | `openai`                 |
| `embedding_model`             | Model name for embeddings                    | `text-embedding-3-small` |
| `embedding_dimensions`        | Vector dimension (must match model)          | `1536`                   |
| `chunk_size`                  | Target chunk character/token size            | `1000`                   |
| `chunk_overlap`               | Overlap between consecutive chunks           | `200`                    |
| `rag_top_k`                   | Retrieval result count                       | `5`                      |
| `rag_default_prompt_template` | Default RAG template (category/name/version) | `rag/answer/v1`          |
| `rag_context_max_chars`       | Context budget for RAG (character-based V1)  | `8000`                   |
| `rag_enabled`                 | Feature flag for RAG endpoints               | `false`                  |
| `tools_enabled`               | Feature flag for tool execution              | `false`                  |
| `default_temperature`         | LLM temperature default                      | `0.7`                    |
| `default_max_tokens`          | LLM max tokens default                       | (provider default)       |
| `document_upload_max_bytes`   | Max upload size for `/api/documents/upload`  | `10485760` (10 MB)       |
| `web_search_provider`         | Search API backend                           | `tavily`                 |
| `web_search_api_key`          | Search provider API key                      | `None`                   |
| `web_search_max_results`      | Max results per search                       | `5`                      |

Note: LLM provider selection already exists via `llm_provider` in `Settings`. Vector store is fixed to `pgvector` in V1 — document in README rather than adding a factory for a single backend.

### Tasks

- Create `app/ai/` package with subdirectories per spec (empty `__init__.py` files only).
- Extend `Settings` with configuration matrix fields; validate embedding provider key when RAG enabled.
- Add production fail-fast rules for RAG/tools secrets when respective flags are enabled.
- Create `app/ai/deps.py` (or extend existing DI) for wiring AI components into services.
- Document folder responsibilities, **module boundaries**, retry policy, and observability metric names in `backend-python/README.md`.
- Add placeholder tests confirming settings load and feature flags default safely.

### Success Criteria

- `app/ai/` directory tree exists with documented responsibilities.
- All new settings load with safe defaults; existing `.env.example` unchanged in behavior.
- App starts with `RAG_ENABLED=false` and `TOOLS_ENABLED=false`.
- All 170 existing tests pass.
- New settings validation tests pass.

### Verification Checklist

- `app/ai/` tree exists with documented responsibilities.
- Settings load with existing `.env.example`; new vars optional with safe defaults.
- App starts with `RAG_ENABLED=false` and `TOOLS_ENABLED=false` (no behavior change).
- Existing 170 tests still pass.
- New settings validation tests pass.

### Exit Criteria

- AI module scaffold and configuration matrix are in place.
- No MVP behavior change with feature flags off.
- User confirms Phase 1 completion.

### Phase 1 Completion Record (verified 2026-07-20)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **180 passed** (170 baseline + 10 Phase 1 settings tests), **89.65%** coverage on `app/`, **4.82s** duration |
| Feature flags | `RAG_ENABLED=false`, `TOOLS_ENABLED=false` — MVP chat/auth/persistence unchanged                             |
| Scaffold      | `app/ai/` directory tree + `deps.py` DI skeleton; no business logic                                          |

---

## Phase 2 - Prompt Infrastructure

### Objectives

Centralize all prompts in a versioned repository with Jinja2 rendering, variable injection, and regression tests. Remove hardcoded prompt strings from business logic.

### Components

| Component        | Responsibility                                           |
| ---------------- | -------------------------------------------------------- |
| PromptRepository | Load prompt templates from `app/ai/prompts/` by category |
| PromptRenderer   | Jinja2 rendering with typed variable injection           |
| PromptManager    | Facade: resolve name + version → render → return string  |

### PromptManager Lifecycle (locked)

| Property         | Requirement                                                  |
| ---------------- | ------------------------------------------------------------ |
| State            | **Stateless** — no per-request mutable state                 |
| Thread safety    | Safe for concurrent async requests                           |
| Instantiation    | **Singleton via DI** (`app/ai/deps.py`)                      |
| Template cache   | Load and cache parsed templates after startup                |
| Runtime mutation | Templates are **immutable at runtime** — no hot reload in V1 |

### Prompt Categories (per spec)

- `chat/` — general chat system prompts
- `rag/` — RAG question-answering, context injection templates
- `tools/` — tool-use system instructions
- `evaluation/` — eval judge/scoring prompts
- `system/` — shared system-level instructions
- `shared/` — reusable partials/macros

### Tasks

- Add `Jinja2` to `pyproject.toml` dependencies.
- Implement `PromptRepository`:
  - Load `.jinja2` (or `.j2`) templates from category subdirectories.
  - Support version suffix in filename (e.g. `summarize.v1.j2`) or metadata sidecar.
  - Cache parsed templates after first load (startup or lazy on first render).
- Implement `PromptRenderer` with strict undefined handling (fail on missing variables).
- Implement `PromptManager` as DI singleton with method:
  - `render(category, name, version, variables) -> str`
  - Skip `list_prompts()` until an admin UI or eval CLI needs it (YAGNI).
- Migrate hardcoded prompts to templates:
  - `chat/summarize_system.v1.j2`
  - `chat/summarize_user.v1.j2`
  - `chat/context_summary_prefix.v1.j2`
  - `chat/default_system.v1.j2` — migrate `db/seed.py` demo system message content here
- Refactor `ChatService._build_summary_input` and `build_context_messages` to use `PromptManager`.
- Update `db/seed.py` to load demo system message from template or reference the same string source.
- Add prompt regression tests:
  - Snapshot rendered output for fixed variable inputs.
  - Missing variable raises clear error.
  - Version resolution returns expected template.
- Add `tests/data/prompts/` fixtures for edge cases.

### Success Criteria

- Zero hardcoded prompt strings in `chat_service.py` and `db/seed.py`.
- Summarization behavior unchanged (existing tests pass).
- Prompt regression snapshots pass.
- `PromptManager` is DI singleton; template cache verified by test.

### Verification Checklist

- No hardcoded prompt strings remain in `chat_service.py` or `db/seed.py` (grep confirms).
- Summarization behavior unchanged (existing summarization tests pass).
- Prompt regression tests pass.
- Prompt templates are human-readable and documented.

### Exit Criteria

- All production prompts live in `app/ai/prompts/` with versioning.
- Business logic uses `PromptManager` exclusively.
- User confirms Phase 2 completion.

### Phase 2 Completion Record (verified 2026-07-20)

| Item             | Result                                                                                                      |
| ---------------- | ----------------------------------------------------------------------------------------------------------- |
| Quality gates    | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                              |
| Tests            | **194 passed** (≥180 Phase 1 baseline; +11 prompt tests), **90.10%** coverage on `app/`, **5.39s** duration |
| Prompt migration | Four templates under `app/ai/prompts/chat/`; zero hardcoded prompts in `chat_service.py` or `db/seed.py`    |
| MVP regression   | Summarization/context tests pass; feature flags off — no behavior change                                    |

---

## Phase 3 - Tool Platform Core

### Objectives

Build the generic tool execution framework following the spec lifecycle without implementing specific tools yet.

### Tool Lifecycle (spec)

```text
LLM → Registry → Validation → Authorization → Execution → Normalization → Return Result
```

### Components

| Component      | Responsibility                                              |
| -------------- | ----------------------------------------------------------- |
| ToolDefinition | Pydantic schema: name, description, parameters, handler ref |
| ToolRegistry   | Register, lookup, list available tools                      |
| ToolValidator  | Validate tool call arguments against schema                 |
| ToolAuthorizer | Check caller permissions for tool invocation                |
| ToolExecutor   | Orchestrate lifecycle with timeout, logging, error handling |
| ToolResult     | Normalized result envelope (success/error, data, metadata)  |

### Tasks

- Define `ToolDefinition` and `ToolCall` Pydantic schemas in `app/ai/tools/schemas.py`.
- Define `ToolHandler` protocol (async `execute(args, context) -> ToolResult`).
- Implement `ToolRegistry`:
  - `register(tool: ToolDefinition, handler: ToolHandler)`
  - `get(name) -> ToolDefinition | None`
  - `list_tools() -> list[ToolDefinition]`
  - `get_schemas_for_llm() -> list[dict]` (OpenAI-compatible function definitions)
- Implement `ToolValidator` — validate call args against tool JSON schema.
- Implement `ToolAuthorizer`:
  - V1 policy: authenticated users may invoke all registered tools; guests denied.
  - Return standardized `forbidden` error when denied.
- Implement `ToolExecutor`:
  - Accept `ToolCall`, run validation → authorization → execution → normalization.
  - Configurable timeout (reuse `request_timeout_seconds` or add `tool_timeout_seconds`).
  - Structured logging: tool name, latency_ms, success/failure, request_id (no query/content).
  - Emit `tool_calls_total` / `tool_errors_total` metric fields.
  - Map exceptions to `ToolResult` error envelope.
- Wire registry and executor into DI (`app/ai/deps.py`).
- Add unit tests for each lifecycle stage with a **stub tool** (echo/ping):
  - Registration and lookup
  - Validation failure (missing required arg)
  - Authorization denial for guest
  - Execution timeout
  - Normalized error on handler exception

### Success Criteria

- Stub tool executes through full lifecycle in isolation.
- Guest callers denied; invalid args rejected before handler runs.
- Tool metrics logged on success and failure.
- Chat tests unaffected with `TOOLS_ENABLED=false`.

### Verification Checklist

- Stub tool executes end-to-end through full lifecycle.
- Guest callers receive authorization denial.
- Invalid args return validation error without executing handler.
- Tool execution logs include name and latency; no sensitive args logged.
- Existing chat tests unaffected (`TOOLS_ENABLED=false`).

### Exit Criteria

- Tool platform core is independently testable with stub tool.
- No web search or LLM integration yet.
- User confirms Phase 3 completion.

### Phase 3 Completion Record (verified 2026-07-20)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **208 passed** (194 Phase 2 baseline + 14 tool platform tests), **89.74%** coverage on `app/`, **6.92s** duration |
| Tool platform | `ToolRegistry`, `ToolValidator`, `ToolAuthorizer`, `ToolExecutor` + stub `echo` handler; DI wired in `deps.py` |
| MVP regression | Feature flags off — chat/auth/persistence unchanged; no startup tool registration                            |

---

## Phase 4 - Web Search Tool and Chat Integration

### Objectives

Implement web search as the first real tool and integrate tool calling into the chat flow when `TOOLS_ENABLED=true`.

### V1 Streaming Policy (locked)

- **Non-streaming chat (`stream=false`)**: tool loop enabled when `tools_enabled=true`.
- **Streaming chat (`stream=true`)**: tool calling **disabled** — behave as standard MVP streaming chat regardless of `tools_enabled`. Return no error; silently skip tools in V1.

### Tasks

- Choose initial search provider (recommended: **Tavily** — common for AI apps; abstract behind handler).
- Add search provider settings validation when `tools_enabled=true`.
- Implement `WebSearchTool` in `app/ai/tools/implementations/web_search.py`:
  - Input schema: `query` (required), optional `max_results`
  - Output: normalized list of `{ title, url, snippet }`
  - Apply shared retry policy for provider HTTP calls
  - Handle rate limits and timeouts gracefully
  - Emit `search_latency_ms` metric field
- Register web search in application startup when `tools_enabled=true`.
- Extend `LLMProvider` protocol with tool-calling methods (e.g. `complete_chat_with_tools`) — implement on **primary provider first** (recorded in Phase 0), then extend others incrementally per **provider capability model**:
  - Pass tool schemas to provider API
  - Parse tool call responses from provider
  - **Do not** modify all four providers in one PR
- Create **`ToolChatService`** that **composes** `ChatService` (do not further inflate `ChatService`):
  1. Send messages + tool schemas to LLM
  2. If LLM returns tool call → execute via `ToolExecutor`
  3. Append tool result to conversation
  4. Re-invoke LLM for final answer
  5. Cap tool call iterations (e.g. max 3 per request)
- Wire router to delegate non-streaming requests to `ToolChatService` when `tools_enabled=true`.
- Add tool-use prompts in `app/ai/prompts/tools/` (when to search, how to cite).
- Add integration tests:
  - Mock search API; verify tool call → search → response flow
  - Verify iteration cap prevents infinite loops
  - Verify tools disabled returns standard chat (no regression)
  - Verify `stream=true` skips tool loop (no regression on streaming tests)
- Document `WEB_SEARCH_API_KEY` in env templates.

### Success Criteria

- Web search returns normalized results in mocked integration test.
- Non-streaming chat with tools produces grounded answer.
- Streaming chat unaffected; tools silently skipped.
- Tool execution completes within 10s target in dev (mocked provider).
- MVP chat behavior identical when `TOOLS_ENABLED=false`.

### Verification Checklist

- Web search returns normalized results via stubbed/mocked API in tests.
- Chat with `TOOLS_ENABLED=true` can invoke web search and produce grounded answer (non-streaming).
- Chat with `TOOLS_ENABLED=false` behaves identically to MVP.
- Streaming chat ignores tools in V1 (documented behavior; tests confirm).
- Tool call iteration cap enforced.

### Exit Criteria

- Web search is the first production tool integrated into chat (non-streaming path).
- Feature flag controls rollout.
- User confirms Phase 4 completion.

### Phase 4 Completion Record (verified 2026-07-20)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **224 passed** (208 Phase 3 baseline + 16 Phase 4 tests), **87.29%** coverage on `app/`, **7.20s** duration |
| Web search    | `web_search` tool (Tavily), shared `app/core/retry.py`, startup registration when `TOOLS_ENABLED=true`       |
| Chat integration | `ToolChatService` composes `ChatService`; OpenAI `complete_chat_with_tools`; non-streaming tool loop only |
| MVP regression | Feature flags off — chat/auth/persistence unchanged; streaming silently skips tools                         |

---

## Phase 5 - Document Parsing and Chunking

### Objectives

Implement the ingestion front half of the knowledge platform: parse and chunk documents. Persist chunks in Postgres (text only; embeddings added in Phases 6–7).

### Supported Document Types (V1)

- PDF (PyMuPDF)
- DOCX (python-docx)
- Markdown (.md)
- Plain text (.txt)

### DB Schema (locked for V1)

```text
documents
  id, user_id (FK, NOT NULL — auth-only), filename, mime_type, status, created_at

document_chunks
  id, document_id (FK), chunk_index, content, metadata_json
  embedding vector(N) NULL until Phase 7 migration populates column
```

Status enum: `pending`, `processing`, `ready`, `failed`

### Components

| Component        | Responsibility                                                          |
| ---------------- | ----------------------------------------------------------------------- |
| DocumentParser   | Protocol: bytes + mime → structured text + metadata                     |
| PdfParser        | PyMuPDF implementation                                                  |
| DocxParser       | python-docx implementation                                              |
| TextParser       | Markdown and plain text                                                 |
| `select_parser`  | Route by mime/extension (simple if/else function — not a plugin system) |
| Chunker          | Protocol: text → list of chunks with metadata                           |
| RecursiveChunker | Character/token-based chunking with overlap                             |

### Tasks

- Add `pymupdf`, `python-docx` to dependencies.
- Define `ParsedDocument` and `DocumentChunk` dataclasses/Pydantic models:
  - Metadata: `source`, `page` (if applicable), `chunk_index`, `tags` (optional)
- Implement parsers for each document type with unit tests per parser.
- Implement `select_parser(mime_type, filename) -> DocumentParser` (if/else router).
- Implement `RecursiveChunker`:
  - Configurable `chunk_size` and `chunk_overlap` from settings
  - Preserve page numbers for PDF where possible
- Implement `IngestionPipeline` orchestrator:
  - `parse(file_bytes, filename) -> ParsedDocument`
  - `chunk(document) -> list[DocumentChunk]`
- Add DB models and Alembic migration per schema above (`embedding` column nullable, no index yet).
- Add `DocumentService` in `app/services/` for ownership (`user_id` only) and status tracking.
- Enforce MIME/extension allowlist and max file size via `document_upload_max_bytes` (service-layer validation for Phase 5 tests; HTTP enforcement in Phase 11).
- Add tests with fixtures in `tests/data/documents/` (sample PDF, DOCX, MD, TXT).

### Success Criteria

- PDF, DOCX, MD, and TXT fixtures parse correctly.
- Chunks respect size/overlap settings with deterministic output.
- Chunks persist in Postgres with `user_id` ownership.
- Existing chat functionality unaffected.
- All tests pass.

### Verification Checklist

- Each parser extracts text correctly from fixture files.
- Chunker respects size/overlap settings; consecutive chunks overlap as configured.
- Pipeline produces deterministic chunk sequences for fixed input.
- DB migration applies cleanly; document records persist with `user_id` ownership.
- No embeddings or vector index yet (chunks stored as text only; `embedding` column NULL).

### Exit Criteria

- Documents can be parsed and chunked with metadata; chunks persisted in Postgres.
- User confirms Phase 5 completion.

### Phase 5 Completion Record (verified 2026-07-20)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **241 passed** (16 Phase 5 tests added), **88.35%** coverage on `app/`, **7.87s** duration |
| Ingestion     | Parsers (PDF/DOCX/MD/TXT), `RecursiveChunker`, `IngestionPipeline`, `DocumentService`, Alembic `0002` migration |
| MVP regression | Feature flags off — chat/auth/persistence/tools unchanged; no new HTTP routes                              |

---

## Phase 6 - Embeddings Provider

### Objectives

Add embedding generation with provider abstraction, starting with a single concrete implementation.

### Tasks

- Define `EmbeddingProvider` protocol in `app/ai/interfaces/`:
  - `async embed_texts(texts: list[str]) -> list[list[float]]`
  - `dimensions -> int`
- Implement `OpenAIEmbeddingProvider` in `app/ai/embeddings/` (first concrete implementation):
  - Use `embedding_model` and `embedding_dimensions` from settings
  - Batch requests (configurable batch size, e.g. 100)
  - Apply shared retry policy
  - Handle rate limits and timeouts
  - Emit `embedding_latency_ms` metric field
- Extend `IngestionPipeline`:
  - After chunking → generate embeddings → attach vectors in memory
- Add structured logging in pipeline (count, latency, no content) — **no separate `EmbeddingService` pass-through** unless orchestration complexity warrants it.
- **Defer in-memory/distributed embedding cache to V2** — document Redis upgrade path only.
- Add unit tests with mocked embedding API.
- Add integration test: chunk list → embeddings with correct dimensions.

### Success Criteria

- Embeddings match configured dimensions.
- Batch edge cases (empty, single text) handled.
- Pipeline attaches vectors in memory after chunking.
- Embedding latency logged per batch.
- Provider key validated when RAG enabled.

### Verification Checklist

- Embeddings returned with dimension matching settings.
- Batch embedding handles empty list and single text edge cases.
- Ingestion pipeline attaches embedding vectors to chunks in memory.
- Provider key validated when RAG enabled.

### Exit Criteria

- Embedding generation works for OpenAI provider.
- No vector storage yet — embeddings computed but not indexed in DB.
- User confirms Phase 6 completion.

### Phase 6 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `uv run alembic upgrade head` — all pass |
| Tests         | **259 passed** (241 Phase 5 baseline + 18 Phase 6 tests), **88.39%** coverage on `app/`, **8.46s** duration |
| Embeddings    | `EmbeddingProvider` protocol, `OpenAIEmbeddingProvider` (batch/retry/latency), `IngestionPipeline.embed` in memory only |
| MVP regression | Feature flags off — chat/auth/persistence/tools/document ingest unchanged; DB `embedding` column NULL          |

---

## Phase 7 - Vector Store (pgvector)

### Objectives

Store and query document embeddings using pgvector as the primary V1 vector store.

### Tasks

- Update Docker Compose Postgres image to pgvector-enabled image (planned in Phase 0, e.g. `pgvector/pgvector:pg16`).
- Enable `pgvector` extension in Alembic migration (`CREATE EXTENSION IF NOT EXISTS vector`).
- Add `pgvector` Python dependency.
- Alembic migration on `document_chunks`:
  - Ensure `embedding vector(N)` column exists (from Phase 5); backfill not needed for new ingests
  - Add HNSW index on `embedding` using cosine distance (document IVFFlat tradeoffs if HNSW unavailable)
- Define `VectorStore` protocol in `app/ai/interfaces/`:
  - `upsert(chunks_with_embeddings) -> None`
  - `similarity_search(query_embedding, top_k, user_id) -> list[ScoredChunk]`
  - `delete_by_document(document_id) -> None`
- Implement `PgVectorStore` (single concrete implementation — no factory until second backend needed):
  - Cosine similarity search
  - **`user_id` filtering on all queries** (auth-only; no guest scoping)
  - Emit `vector_search_latency_ms` metric field
- Extend `IngestionPipeline` to persist embeddings via `PgVectorStore.upsert`.
- Add **`KnowledgeService`** orchestration — **ingestion lifecycle only**:
  - `ingest_document(user_id, file) -> document_id`
  - `delete_document(user_id, document_id) -> None`
  - Do **not** add `KnowledgeService.search()` — retrieval belongs in `Retriever` (Phase 8).
- **Future-proof service interface** (design note only — no V2 implementation):
  - V1: synchronous `ingest_document` returns when parse → chunk → embed → store completes.
  - V2 evolution: `ingest_document` returns immediately with `document_id` + `status=pending`; background worker processes queue; client polls status or receives notification.
  - Keep method signatures and return types compatible with async status tracking (return `document_id` + status enum from day one).
- Add integration tests:
  - Insert known vectors; verify top-K retrieval ordering
  - Owner isolation: user A cannot retrieve user B's chunks
  - Delete removes vectors
  - Vector search completes within 100 ms target on fixture dataset (dev)

### Success Criteria

- pgvector extension enabled locally and in migration.
- Ingestion end-to-end: parse → chunk → embed → store.
- Top-K retrieval ordering correct on fixture vectors.
- `user_id` isolation enforced.
- `KnowledgeService` exposes ingest/delete only; no search method.
- Existing chat unaffected.

### Verification Checklist

- pgvector extension enabled locally (Docker) and in migration; idempotent in dev.
- Similarity search returns expected neighbors for fixture embeddings.
- `user_id`-scoped queries enforced at store level.
- Ingestion end-to-end: parse → chunk → embed → store.

### Exit Criteria

- pgvector is the production vector store for V1.
- Knowledge platform ingestion pipeline is complete.
- User confirms Phase 7 completion.

### Phase 7 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `uv run alembic upgrade head` — all pass |
| Tests         | **270 passed** (259 Phase 6 baseline + 11 Phase 7 tests), **88.38%** coverage on `app/`, **8.83s** duration |
| Vector store  | Docker `pgvector/pgvector:pg16`, migration `0003`, `VectorStore` protocol, `PgVectorStore`, HNSW cosine index |
| Ingestion     | `IngestionPipeline.persist`, `KnowledgeService` ingest/delete only (no search)                               |
| MVP regression | Feature flags off — chat/auth/persistence/tools/document text-only ingest unchanged                          |

---

## Phase 8 - Generic Retrieval Infrastructure

### Objectives

Implement the retrieval-side components of the **Generic RAG Framework** — domain-agnostic building blocks for embed → search → context assembly → prompt rendering. **No LLM orchestration or end-to-end pipeline in this phase.**

### Purpose

- Components are **domain-independent** and independently testable.
- Business knowledge belongs in **documents** and **prompts**, not in framework code.
- No domain-specific classes, enums, or branching inside `app/ai/rag/`.

### Components

| Component      | Responsibility                                                     |
| -------------- | ------------------------------------------------------------------ |
| Retriever      | Embed query → vector search → ranked chunks (domain-agnostic)      |
| ContextBuilder | Assemble retrieved chunks into LLM context window (generic format) |
| PromptBuilder  | Render configurable RAG prompt template with question + context    |

### Design Constraints

- Retriever must not depend on document type beyond generic metadata filters.
- Retriever must not depend on business domain.
- Prompt construction uses `PromptManager`; default from `rag_default_prompt_template` setting.
- Context assembly is formatting and budgeting only — no domain interpretation.

### Tasks

- Implement `Retriever`:
  - Embed user question via `EmbeddingProvider`
  - Query `VectorStore.similarity_search` with `rag_top_k` and `user_id`
  - Return scored chunks with metadata (no domain filtering beyond owner scope)
  - Emit `retrieval_latency_ms` metric field
- Implement `ContextBuilder`:
  - Format chunks into numbered context blocks (generic structure)
  - Enforce `rag_context_max_chars` budget (character-based V1; reuse `usage_service` token estimators or add tiktoken if needed)
  - Truncate lowest-scoring chunks if over budget
- Implement `PromptBuilder`:
  - Use `PromptManager` with configurable template (default: `rag/answer.v1.j2`)
  - Variables: `question`, `context`, optional `instructions` (supplied by caller, not hardcoded domain rules)
- Add **generic** RAG prompt in `app/ai/prompts/rag/answer.v1.j2` (default answer template only).
- Add unit tests per component with mocked dependencies:
  - Retriever returns ranked chunks for known query
  - ContextBuilder truncates when over budget
  - PromptBuilder renders template with question + context
  - Empty retrieval handled gracefully at component level

### Success Criteria

- Retriever returns correct ranked chunks against fixture vectors.
- Context budgeting truncates lowest-scoring chunks correctly.
- PromptBuilder renders expected output for fixed inputs.
- Each component testable in isolation with mocks.
- No domain-specific logic in `app/ai/rag/` (grep confirms).
- No `RAGService` or LLM calls in this phase.

### Verification Checklist

- Retriever unit tests pass with mocked embedding and vector store.
- Context budget truncation works without error.
- PromptBuilder renders `rag/answer.v1.j2` with test variables.
- No domain-specific logic in `app/ai/rag/` (code review / grep confirms).
- Existing chat flow unaffected.

### Exit Criteria

- Retrieval infrastructure components work independently.
- Components are domain-agnostic and documented.
- No LLM orchestration yet (Phase 9).
- User confirms Phase 8 completion.

### Phase 8 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **290 passed** (270 Phase 7 baseline + 20 Phase 8 tests), **88.58%** coverage on `app/`, **9.33s** duration |
| Retrieval     | `Retriever`, `ContextBuilder`, `PromptBuilder` in `app/ai/rag/`; generic prompt `rag/answer.v1.j2`           |
| Observability | `retrieval_latency_ms` on retriever calls; result count only in logs                                         |
| MVP regression | Feature flags off — chat/auth/persistence/tools/knowledge ingest unchanged                                     |

---

## Phase 9 - Generic RAG Orchestration

### Objectives

Wire retrieval infrastructure into a complete **Generic RAG Framework** pipeline: question → retriever → context builder → prompt builder → LLM → response. Domain-specific assistants are future consumers via configuration and application services.

### Pipeline (spec)

```text
Question → Retriever → Context Builder → Prompt Builder → LLM → Response
```

### Architectural Responsibility

| In scope (framework)              | Out of scope (application services)       |
| --------------------------------- | ----------------------------------------- |
| Retrieval (embed query, search)   | Customer support logic / ticket workflows |
| Context assembly (format, budget) | Community service policies                |
| Prompt construction (templates)   | Legal reasoning / compliance rules        |
| Response generation (LLM call)    | HR workflows / enterprise business rules  |

### Design Constraints

- **V1 responses are non-streaming only** — streaming RAG deferred to V2.
- Prompt template override supported via caller (for future domain services).
- No domain-specific logic inside `app/ai/rag/`.

### Tasks

- Implement `RAGService.ask(user_id, question, prompt_template?, ...) -> RAGResponse`:
  - Orchestrate retriever → context builder → prompt builder → LLM (`complete_chat`, non-streaming)
  - Return response + retrieved chunk metadata (for debugging; not full citations UI)
  - Accept optional prompt template override
- Add structured logging and metrics: `rag_requests_total`, `rag_request_duration_ms`, retrieval count, top score, latency breakdown.
- Add integration test: ingest fixture doc → `RAGService.ask` → response references doc content.
- Add integration test: empty corpus → graceful "no relevant documents" response.
- Document extension philosophy in README (Customer Care, Legal, HR patterns via prompts + app services).

### Extension Philosophy (document in code comments / README)

Future domain-specific systems reuse this framework without modifying `app/ai/rag/`:

- **Customer Care RAG** — customer docs + `rag/customer_care.v1.j2` + `CustomerCareRAGService`
- **Enterprise Knowledge Assistant** — internal docs + enterprise prompt template + app service scoping
- **Legal / HR / Community Service** — same pattern: corpus + prompts + application service

### Out of Scope (document only — generic infrastructure for V2)

- Hybrid retrieval, metadata filtering, query expansion, reranking, citations, multi-document retrieval, retrieval strategy plugins, context compression, parent-child retrieval

### Success Criteria

- Full pipeline: question → retrieval → prompt → LLM → response works on fixture doc.
- Empty retrieval returns graceful generic response.
- End-to-end integration tests pass.
- Complete RAG response within 8s target in dev (mocked LLM).
- No domain logic in `app/ai/rag/`.
- Chat unaffected when `RAG_ENABLED=false`.

### Verification Checklist

- Framework response uses retrieved context (verified via fixture doc content in test).
- Empty retrieval returns graceful generic "no relevant documents" response.
- Context budget truncation works without error.
- No domain-specific logic in `app/ai/rag/` (code review / grep confirms).
- Prompt template selection is configurable from caller.
- Existing chat flow unaffected when `RAG_ENABLED=false`.

### Exit Criteria

- Generic RAG Framework works end-to-end in service layer.
- Framework is domain-agnostic and documented as reusable infrastructure.
- No HTTP endpoints yet (Phase 11).
- User confirms Phase 9 completion.

### Phase 9 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass                               |
| Tests         | **303 passed** (290 Phase 8 baseline + 13 Phase 9 tests), **88.78%** coverage on `app/`, **9.95s** duration |
| Orchestration | `RAGService.ask` wires `Retriever` → `ContextBuilder` → `PromptBuilder` → `LLMProvider.complete_chat`        |
| Observability | `rag_requests_total`, `rag_request_duration_ms`, retrieval/included counts, top score, latency breakdown     |
| MVP regression | Feature flags off — chat/auth/persistence/tools/knowledge ingest unchanged                                     |

---

## Phase 10 - Evaluation Framework

### Objectives

Build objective quality measurement at three levels: prompt, retrieval, and end-to-end. Run **before API exposure** so chunk/top-K settings can be tuned on fixtures.

### Evaluation Levels (spec)

1. **Prompt** — template rendering correctness, regression snapshots
2. **Retrieval** — precision, recall against labeled chunk sets
3. **End-to-end** — faithfulness, correctness, hallucination, latency

### Metrics (V1 minimum)

| Metric        | Level      | Implementation approach               |
| ------------- | ---------- | ------------------------------------- |
| Latency       | All        | Timed pipeline stages                 |
| Precision     | Retrieval  | Relevant retrieved / total retrieved  |
| Recall        | Retrieval  | Relevant retrieved / total relevant   |
| Faithfulness  | End-to-end | LLM-as-judge or heuristic overlap     |
| Correctness   | End-to-end | Exact/fuzzy match vs expected answer  |
| Hallucination | End-to-end | Judge prompt flags unsupported claims |

### Tasks

- Create `app/ai/evaluation/` module:
  - `datasets.py` — load eval cases from YAML/JSON in `tests/data/evaluation/`
  - `metrics.py` — precision, recall, latency helpers; compare against performance targets
  - `runners.py` — prompt, retrieval, e2e runners
  - `report.py` — summary output (console + JSON file)
- Define eval case schema:
  ```yaml
  id: string
  question: string
  expected_answer: string | null
  relevant_chunk_ids: list[string] | null
  document_fixture: string | null
  ```
- Implement `PromptEvalRunner` — extend Phase 2 regression tests into eval harness.
- Implement `RetrievalEvalRunner`:
  - Ingest fixture docs → query via `Retriever` → compare retrieved IDs to `relevant_chunk_ids`
  - Compute precision/recall
- Implement `EndToEndEvalRunner`:
  - Full Generic RAG Framework pipeline → compare response to expected
  - Optional LLM-as-judge using `evaluation/judge.v1.j2` prompt
- Add CLI entry point:
  ```bash
  cd backend-python && uv run python -m app.ai.evaluation.cli --level all
  ```
  Or `make eval` target in Makefile.
- Add eval prompts in `app/ai/prompts/evaluation/`.
- Add sample eval dataset (3–5 cases) in `tests/data/evaluation/`.
- Document how to compare providers/models/prompts/chunk settings by re-running eval with different env.

### Success Criteria

- CLI runs all three eval levels and prints summary.
- Retrieval eval computes precision/recall on fixtures.
- End-to-end eval produces pass/fail per case.
- Latency metrics reported against performance targets.
- Eval runs without external network when mocks configured.
- Baseline metrics recorded for Phase 13.

### Verification Checklist

- CLI runs all three eval levels and prints summary.
- Retrieval eval computes precision/recall on fixture dataset.
- End-to-end eval produces pass/fail per case.
- Eval runs do not require external network when mocks configured.
- CI optional: add eval job as non-blocking workflow (or `make eval` local-only for V1).

### Exit Criteria

- Evaluation framework measures quality at prompt, retrieval, and e2e levels.
- Sample dataset demonstrates workflow; baseline metrics recorded for Phase 13.
- User confirms Phase 10 completion.

### Phase 10 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass                  |
| Tests         | **323 passed** (303 Phase 9 baseline + 20 Phase 10 tests), **87.64%** coverage on `app/`, **10.66s** duration |
| Eval CLI      | All three levels run via `make eval`; sample dataset 5/5 pass; baseline JSON at `.eval/eval-report.json`      |
| Baseline eval | Prompt 2/2 pass; retrieval mean precision/recall 1.000; e2e correctness/faithfulness pass; latencies within soft targets |
| MVP regression | Feature flags off — chat/auth/persistence/tools/knowledge ingest/RAG orchestration unchanged                     |

---

## Phase 11 - Knowledge and Generic RAG API Endpoints

### Objectives

Expose document management and generic RAG interaction via REST API with auth, validation, and error handling consistent with MVP patterns. API routers and application services orchestrate the Generic RAG Framework; they must not embed domain-specific business logic — V1 exposes a generic `/api/rag/ask` endpoint suitable as the foundation for future domain-specific services.

**Auth-only:** all endpoints require authenticated user; guests receive 401.

### Endpoints (proposed)

| Method | Path                    | Purpose                                            |
| ------ | ----------------------- | -------------------------------------------------- |
| POST   | `/api/documents/upload` | Upload and ingest document                         |
| GET    | `/api/documents`        | List caller's documents                            |
| GET    | `/api/documents/{id}`   | Document metadata and status                       |
| DELETE | `/api/documents/{id}`   | Delete document and vectors                        |
| POST   | `/api/rag/ask`          | Generic RAG question → response (framework-backed) |

Note: unified chat+RAG via `POST /api/chat` is **out of scope for V1** — RAG lives on dedicated endpoints and frontend route.

### Tasks

- Create `app/schemas/documents.py` and `app/schemas/rag.py` request/response DTOs.
- **Route-specific upload body limit:**
  - Exempt `/api/documents/upload` from global 16 KB middleware cap.
  - Enforce `document_upload_max_bytes` on upload route only; keep existing limit for chat/auth.
- Update CORS `allow_methods` to include **`DELETE`** for document removal.
- Create `app/routers/documents.py`:
  - Multipart upload with MIME/extension allowlist and `document_upload_max_bytes` enforcement
  - **Synchronous ingestion for V1** with explicit request timeout; return 408/504 on overrun
  - Require authenticated user (`CallerContext` with `user_id`); guests → 401
  - Emit `documents_ingested_total` / `documents_failed_total` on completion
- Create `app/routers/rag.py`:
  - `POST /api/rag/ask` — generic question body; optional `prompt_template` for template selection
  - Delegate to `RAGService` (framework); no domain logic in router
  - Require authenticated user; guests → 401
  - Guard with `rag_enabled` setting → 503 `feature_disabled` when off
- Register routers in `main.py`; respect rate limiting and correlation ID middleware.
- Map errors to centralized envelope (validation, not found, ingestion failure, feature disabled, payload too large).
- Add API integration tests via `TestClient`:
  - Upload → list → ask → delete lifecycle (authenticated)
  - Guest access returns 401 on all document/RAG endpoints
  - Unauthorized cross-user access returns 403/404
  - Feature flag off returns 503
  - Oversized upload returns 413 with clear message
- Update `.env.example`, `backend-python/README.md` with new endpoints and settings.

### Success Criteria

- Authenticated upload → list → ask → delete lifecycle works via API.
- Guest callers receive 401 on all document/RAG routes.
- Upload accepts files up to 10 MB; chat route still enforces 16 KB.
- RAG ask returns framework response for ingested fixture doc.
- Error envelopes match MVP format with `request_id`.
- Existing chat endpoints unaffected.

### Verification Checklist

- Document upload and ingestion work via API for authenticated users.
- RAG ask returns framework-generated response for ingested document (generic, not domain-specific).
- `user_id` isolation enforced on all document/RAG endpoints.
- Guest callers receive 401 (not 403) on protected routes.
- Upload route accepts files up to `document_upload_max_bytes`; chat route still enforces 16 KB limit.
- Error envelopes match MVP format with `request_id`.
- Rate limiting applies to new endpoints.

### Exit Criteria

- Knowledge and generic RAG capabilities are accessible via HTTP API (auth-only).
- User confirms Phase 11 completion.

### Phase 11 Completion Record (verified 2026-07-21)

| Item          | Result                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------ |
| Quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass                  |
| Tests         | **342 passed** (323 Phase 10 baseline + 19 Phase 11 tests), **88.25%** coverage on `app/`, **12.72s** duration |
| API surface   | `POST /api/documents/upload`, `GET /api/documents`, `GET /api/documents/{id}`, `DELETE /api/documents/{id}`, `POST /api/rag/ask` |
| Auth policy   | Auth-only document/RAG access; guests receive 401; upload route uses `DOCUMENT_UPLOAD_MAX_BYTES`; CORS allows DELETE |
| MVP regression | Feature flags off — chat/auth/persistence/tools/evaluation unchanged                                             |

---

## Phase 12 - Frontend Integration

### Objectives

Add minimal UI for document upload and generic RAG interaction on a **dedicated route** (e.g. `/documents` or `/rag`) without modifying the existing chat experience. The UI consumes the generic `/api/rag/ask` endpoint — no domain-specific assistant branding or workflows in V1.

### Tasks

- Add new route/page (e.g. `/documents`):
  - Document upload component (file picker, progress, error display)
  - Documents list panel (filename, status, delete action)
  - "Ask your documents" RAG input and response display
- Hide route from navigation (or show login prompt) when user is not authenticated.
- Extend `frontend/src/api/` clients for documents and RAG endpoints.
- Respect auth tokens and `X-Request-ID` forwarding (existing patterns).
- Handle feature-disabled responses gracefully (hide UI or show message when API returns 503).
- Add Vitest tests for new API clients and key components.
- Update frontend `.env.example` if new `VITE_*` flags needed.
- Add nav link to documents/RAG page for authenticated users only.

### Success Criteria

- Authenticated user can upload, list, delete, and RAG-ask on dedicated route.
- Guest users redirected to login or see hidden nav.
- Existing chat page and flow unaffected.
- Frontend tests pass.

### Verification Checklist

- Authenticated user can upload, list, delete documents, and ask RAG questions on dedicated route.
- Guest users cannot access upload/RAG (redirect to login or hidden nav).
- Existing chat page and flow unaffected.
- Frontend tests pass.

### Exit Criteria

- Minimal frontend surfaces V1 knowledge platform and Generic RAG Framework on a separate route.
- User confirms Phase 12 completion.

### Phase 12 Completion Record (verified 2026-07-21)

| Item               | Result                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| Quality gates      | `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build` — all pass                  |
| Frontend tests     | **106 passed** (Vitest; 90 Phase 11 baseline + 16 Phase 12)                                                  |
| Route              | `/documents` (auth-only upload/list/delete/RAG ask); `/` chat unchanged                                    |
| API clients        | `documentsClient.ts`, `ragClient.ts` with Bearer auth, error envelope parsing, 503 `feature_disabled` handling |
| Backend regression | `make lint`, `make test-cov`, `make eval` — unchanged (**342** tests, **88.25%** coverage on `app/`)     |

---

## Phase 13 - Final V1 Validation

### Objectives

Verify the complete V1 platform meets the definition of done through systematic validation.

### Validation Checklist

| Area                  | Verification                                                                        |
| --------------------- | ----------------------------------------------------------------------------------- |
| MVP regression        | Chat, auth, streaming, persistence unchanged                                        |
| Prompt infrastructure | No hardcoded prompts; regression tests pass                                         |
| Tool platform         | Web search tool executes full lifecycle                                             |
| Knowledge platform    | Upload → parse → chunk → embed → store works                                        |
| Vector store          | pgvector retrieval with user_id isolation                                           |
| Generic RAG Framework | End-to-end question → response with ingested doc; no domain logic in `app/ai/rag/`  |
| RAG domain-agnostic   | Framework code free of business-specific logic; prompts/docs carry domain knowledge |
| Evaluation            | CLI runs all levels; sample dataset passes                                          |
| Performance           | Stage latencies logged; spot-check against soft targets                             |
| Observability         | Metric fields emitted for RAG, retrieval, tools, ingestion                          |
| Configuration         | Matrix settings documented and validated at startup                                 |
| API                   | New endpoints follow error/logging/rate-limit patterns; auth-only enforced          |
| Frontend              | Separate route: upload and generic RAG UI functional                                |
| Tests                 | Full pytest suite passes; coverage ≥ 80%                                            |
| CI                    | All quality gates green                                                             |
| Documentation         | README, env templates, architecture docs updated                                    |
| Deployment            | Docker Compose (pgvector image) / staging deploy succeeds                           |

### Tasks

- Run full manual QA script covering validation checklist.
- Run evaluation CLI against sample dataset; record baseline metrics.
- Run MVP regression test suite (chat, auth, stream, persistence, rate limit, errors).
- Deploy to staging and smoke test document upload + RAG ask (authenticated).
- Spot-check performance targets on staging (upload, retrieval, RAG response).
- Update documentation:
  - `docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md` → mark implemented sections
  - `backend-python/README.md` — AI module, endpoints, eval CLI, module boundaries, metrics, retry policy
  - Root README — V1 capability summary
  - `.env.example` / `.env.required` — complete variable list
- Record validation results in this plan (Phase 13 section).
- Create V1 release summary.

### Success Criteria

- Every validation checklist row verified and recorded.
- Eval CLI baseline metrics recorded.
- Performance spot-check documented (pass or noted exceptions).
- No P0/P1 issues open for V1 scope.
- CI green; staging deploy successful.

### Verification Checklist

- Every row in validation checklist verified and recorded.
- No P0/P1 issues open for V1 scope.
- CI green on main.
- Staging deployment successful.

### Exit Criteria

- V1 is declared complete per Definition of Done below.
- User confirms Phase 13 completion.

### Phase 13 Completion Record (verified 2026-07-21)

| Item | Result |
| ---- | ------ |
| Quality gates (backend) | `make lint`, `make format-check`, `make typecheck`, `make test-cov` — all pass |
| Quality gates (frontend) | `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build` — all pass |
| Backend tests | **342 passed**, **88.25%** coverage on `app/`, **12.35s** duration (matches Phase 12 baseline) |
| Frontend tests | **106 passed** (Vitest, 3.16s) |
| Eval CLI | 5/5 passed (prompt=2, retrieval=2, e2e=1); report at `backend-python/.eval/eval-report.json` |
| Eval vs Phase 10 | Pass count unchanged; mean retrieval latency 14 ms → 57.5 ms (within 150 ms soft target); precision/recall 1.0 |
| Grep audits | No hardcoded prompts in `app/services/` or `app/routers/`; no domain terms in `app/ai/rag/` |
| Docker Compose smoke | `docker compose --profile python up -d --build` — `/api/health` ok, `/api/health/ready` db ok; guest upload 401 |
| Staging smoke | **Pending** — no staging credentials in validation session; local Docker satisfies deployment checklist partially |
| CI | Local gates match `.github/workflows/pr-quality.yml` (pgvector `pg16` service); remote main CI not re-run in this session |
| Manual QA | Steps 1–3, 6–9 covered by Vitest + pytest integration tests; steps 4–5 covered by `test_documents_api`, `test_rag_api`, `test_knowledge_service` |
| Documentation | Architecture spec V1 status, root/backend README, env templates, release summary updated |
| Fixes applied | None — no P0/P1 blockers found during validation |

#### Validation Checklist (recorded)

| Area | Status | Evidence |
| ---- | ------ | -------- |
| MVP regression | Pass | 342 backend tests with default flags; chat/auth/stream/persistence/rate-limit tests pass |
| Prompt infrastructure | Pass | `rg` no matches; `test_prompt_manager.py` in suite |
| Tool platform | Pass | `test_tool_platform.py`, `test_tool_chat*.py`, `test_web_search_tool.py` |
| Knowledge platform | Pass | `test_document_pipeline.py`, `test_knowledge_service.py`, `test_documents_api.py` |
| Vector store | Pass | `test_vector_store.py`, `test_pgvector*`; Docker/CI use `pgvector/pgvector:pg16` |
| Generic RAG Framework | Pass | `test_rag_service.py`, `test_rag_api.py` |
| RAG domain-agnostic | Pass | `rg -i` no domain terms in `app/ai/rag/` |
| Evaluation | Pass | `make eval` — all levels on `sample.yaml` |
| Performance | Pass (documented) | Eval latencies within soft targets; retrieval mean 57.5 ms (< 150 ms), e2e 49 ms (< 8 s) |
| Observability | Pass | Metric fields verified in code + tests (see `backend-python/README.md`) |
| Configuration | Pass | `config.py` fail-fast; `.env.example` / `.env.required` aligned |
| API | Pass | Phase 11 integration tests; auth-only 401; 503 when RAG disabled |
| Frontend | Pass | `/documents` route tests; 106 Vitest tests; build succeeds |
| Tests | Pass | ≥ 342 backend, ≥ 80% coverage, 106 frontend |
| CI | Pass (local parity) | Workflow matches local gates; staging/main CI pending merge verification |
| Documentation | Pass | All required docs updated |
| Deployment | Pass (local) | Docker Compose smoke ok; staging pending |

---

## Suggested Task Breakdown (PR-Friendly)

1. **PR 1:** Phase 0 audit notes + Phase 1 AI scaffold and config matrix.
2. **PR 2:** Phase 2 prompt infrastructure + migration of summarization and seed prompts.
3. **PR 3:** Phase 3 tool platform core + stub tool tests.
4. **PR 4:** Phase 4 web search tool + `ToolChatService` (primary LLM provider).
5. **PR 5:** Phase 5 document parsers, chunkers, DB models.
6. **PR 6:** Phase 6 embeddings provider + pipeline extension.
7. **PR 7:** Phase 7 pgvector store + `KnowledgeService` ingestion + Docker pgvector image.
8. **PR 8:** Phase 8 generic retrieval infrastructure (retriever, context builder, prompt builder).
9. **PR 9:** Phase 9 generic RAG orchestration (`RAGService` + integration tests).
10. **PR 10:** Phase 10 evaluation framework + CLI.
11. **PR 11:** Phase 11 API endpoints (documents + generic RAG) + upload body limit + CORS DELETE.
12. **PR 12:** Phase 12 frontend integration (separate `/documents` route).
13. **PR 13:** Phase 13 validation fixes + documentation.

---

## Risk Register and Mitigation

| Risk                                               | Impact | Mitigation                                                                                                       |
| -------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------- |
| Premature abstraction slows delivery               | High   | Enforce YAGNI rule; one pgvector impl, one embedding provider first; refactor when needed                        |
| pgvector extension unavailable in hosted Postgres  | High   | Verify extension in Phase 0 (staging) and Phase 7 (Docker image change); document V2 fallback                    |
| Local Docker lacks pgvector (`postgres:16-alpine`) | High   | Switch to `pgvector/pgvector:pg16` in Phase 7; plan in Phase 0                                                   |
| Global 16 KB body limit blocks document upload     | High   | Route-specific limit in Phase 11; config in Phase 1                                                              |
| Tool calling inconsistent across LLM providers     | High   | Provider-capability model; primary provider first (Phase 0 records which)                                        |
| Large Generic RAG phase hard to debug              | Medium | Split Phase 8 (components) and Phase 9 (orchestration)                                                           |
| RAG retrieval quality poor on small chunks         | Medium | Configurable chunk size/overlap; eval framework (Phase 10) to tune before API exposure                           |
| Domain logic leaks into Generic RAG Framework      | High   | Code review gate; non-negotiable #11; domain logic only in `app/services/` and prompts                           |
| Document ingestion OOM on large PDFs               | Medium | `document_upload_max_bytes`; sync timeout in Phase 11; async ingestion in V2 if needed                           |
| Web search API costs / rate limits                 | Medium | Shared retry policy; configurable max results; monitor usage in logs                                             |
| MVP regression from shared ChatService changes     | High   | `ToolChatService` composition; feature flags default off; full suite every phase                                 |
| Streaming + tools complexity                       | Medium | V1 policy: disable tools when `stream=true`; document and test explicitly                                        |
| Embedding dimension mismatch                       | Medium | Validate model dimensions in settings; migration enforces vector column size                                     |
| Prompt migration changes summarization behavior    | Medium | Regression snapshots in Phase 2; compare before/after on fixture sessions                                        |
| CORS missing DELETE breaks document removal        | Medium | Add DELETE to CORS in Phase 11                                                                                   |
| Inconsistent retry logic across services           | Medium | Shared `app/core/retry.py`; document policy in plan                                                              |
| Scope creep into V2 or domain-specific features    | Medium | Document extension points; reject hybrid search, citations, MCP, domain abstractions until appropriate milestone |

---

## V2 Extension Points (Document Only — Do Not Implement in V1)

Per spec, reserve but do not build:

- MCP integration
- Long-term memory
- Agentic workflows / multi-agent
- Workflow engine
- Voice and vision modalities
- Additional vector stores (Chroma, Pinecone, Qdrant)
- Generic RAG infrastructure: hybrid retrieval, metadata filtering, query expansion, reranking, citations, multi-document retrieval, retrieval strategy plugins, context compression, parent-child retrieval
- Domain-specific RAG applications (Customer Care, Legal, HR, etc.) — built as application-layer consumers post-V1
- Additional tools: calculator, weather, GitHub, SQL
- Embedding cache (Redis), streaming RAG, guest-scoped document corpora, unified chat+RAG endpoint
- Async document ingestion (queue + background worker + status polling)

---

## Definition of Done

Post-MVP V1 is complete when **all** of the following are true:

- Centralized prompt management with Jinja2 rendering and regression tests; no hardcoded prompts in business logic.
- Generic tool platform with full lifecycle (registry → validation → authorization → execution → normalization).
- Web search integrated as the first production tool (non-streaming chat path).
- Knowledge platform ingests PDF, DOCX, Markdown, and TXT through parse → chunk → embed → store.
- pgvector is the primary vector store with `user_id`-scoped similarity search.
- **Generic RAG Framework** delivers end-to-end question → retrieved context → LLM response; framework code remains domain-agnostic.
- Evaluation framework measures prompt, retrieval, and end-to-end quality with runnable CLI (run before API ship).
- Configuration matrix settings are validated, documented, and feature-flagged.
- API endpoints follow MVP patterns (auth, errors, correlation IDs, rate limiting); document/RAG endpoints are auth-only.
- Minimal frontend exposes document upload and generic RAG on a **separate route**.
- Observability metric fields and performance targets documented; stage latencies logged.
- Shared retry policy applied to external service calls.
- CI quality gates pass (lint, format, typecheck, pytest, coverage ≥ 80%).
- MVP chat, auth, streaming, and persistence remain stable.
- Documentation updated for all V1 capabilities.

---

## Final Acceptance Gate

All items must be true:

- `app/ai/` framework implemented with clean dependency direction.
- Prompt infrastructure operational with versioned templates; `PromptManager` lifecycle documented.
- Tool platform executes web search through full lifecycle.
- Documents ingest through parse → chunk → embed → pgvector store pipeline.
- Generic RAG Framework returns grounded responses for uploaded documents; no domain-specific logic in `app/ai/rag/`.
- Evaluation CLI produces metrics for all three levels.
- Feature flags (`RAG_ENABLED`, `TOOLS_ENABLED`) control rollout safely.
- Document/RAG endpoints require authentication; guests cannot upload or query corpora.
- No V2 features or domain-specific RAG abstractions implemented beyond documented extension points.
- Phase 13 validation checklist completed and recorded.
- User confirms V1 completion.
