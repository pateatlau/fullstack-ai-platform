# Python AI Service

**Production MVP backend** — FastAPI service deployed to Railway.

MVP hardening is complete (2026-07-19): structured logging, correlation IDs, centralized errors, HTTP rate limiting, Pyright standard mode, and CI quality gates. See `docs/plans/mvp-completion-implementation-plan.md` for the validation record.

## Capabilities

- Multi-provider chat (OpenAI, Gemini, Groq, Anthropic) — streaming and non-streaming
- Unified chat orchestration via `UnifiedChatService` — web search and document grounding toggles on `POST /api/chat` and `POST /api/chat/stream` (V1.1)
- Google OAuth login with app-issued JWT; guest identity and daily quota
- Public demo protection (V1.1.1): guest output token cap, optional authenticated daily upload quota — see [docs/ops/public-demo-protection.md](../docs/ops/public-demo-protection.md)
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
- `app/services/chat_service.py` — core chat completion, SSE framing, persistence
- `app/services/unified_chat_service.py` — V1.1 orchestration for toggled chat (streaming + non-streaming)
- `app/services/tool_chat_service.py` — tool loop (invoked by orchestrator)
- `app/providers/` — provider adapters and factory
- `app/ai/` — reusable AI framework (embeddings, vectorstores, prompts, tools, documents, rag, evaluation)
- `app/schemas/` — request/response/frame schemas
- `tests/` — unit and integration tests (**403** tests, **86.14%** coverage on `app/`, 2026-07-22)

## AI Module (`app/ai/`)

Phase 1 scaffold; **Phase 2** adds prompt infrastructure (`PromptManager`, versioned Jinja2 templates). Dependency direction:

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
| `app/ai/agent/` | Reusable agent runtime (planner, executor, streaming, adapters) |
| `app/ai/interfaces/` | Protocols added incrementally per phase |
| `app/ai/deps.py` | FastAPI dependency wiring for AI components |

### Prompt Infrastructure (Phase 2)

Production prompts live under `app/ai/prompts/{category}/` using the filename convention `{name}.v{version}.j2` (for example `chat/summarize_system.v1.j2`).

Render via the app-scoped DI singleton:

```python
from app.ai.deps import get_prompt_manager

content = get_prompt_manager().render(
    "chat", "summarize_system", "1", {}
)
```

All template variables are **required** — missing keys raise `PromptRenderError` (Jinja2 `StrictUndefined`). Regression snapshots and edge-case fixtures are in `tests/test_prompt_manager.py` and `tests/data/prompts/`.

### Tool Platform (Phase 3–4)

Generic tool lifecycle:

```text
Registry → Validation → Authorization → Execution → Normalization
```

**Phase 4** adds **web search** as the first production tool and wires a **non-streaming tool loop** into chat when `TOOLS_ENABLED=true`.

| Stage | Component | Responsibility |
| ----- | --------- | -------------- |
| Registry | `ToolRegistry` | Register, lookup, list tools; expose OpenAI-compatible schemas |
| Validation | `ToolValidator` | Validate call args against tool JSON Schema |
| Authorization | `ToolAuthorizer` | V1: authenticated users only; guests receive `forbidden` |
| Execution | `ToolExecutor` | Orchestrate lifecycle with timeout, logging, error normalization |
| Chat orchestration | `ToolChatService` | Composes `ChatService`; capped tool loop for `POST /api/chat` |

Wire via DI:

```python
from app.ai.deps import get_tool_registry, get_tool_executor, get_web_search_client
from app.routers.chat import get_tool_chat_service
```

- **`web_search`** — Tavily-backed handler (`app/ai/tools/implementations/web_search.py`); registered at startup when `TOOLS_ENABLED=true`.
- **Multi-provider tool calling (V1.1a)** — `complete_chat_with_tools` on all four LLM adapters (OpenAI, Gemini, Groq, Anthropic). Per-request web search via `use_web_search=true` on `POST /api/chat` when `TOOLS_ENABLED=true` (V1.1b). Capability flags are exposed on `GET /api/health` under `capabilities.by_provider` (see `app/providers/capabilities.py`).
- **Unified chat (V1.1b)** — `UnifiedChatService` orchestrates document grounding (`use_documents`) and web search (`use_web_search`) toggles on non-streaming `POST /api/chat`. Plain chat is unchanged when both toggles are off.
- **Streaming policy (V1.1c)** — `POST /api/chat/stream` routes through `UnifiedChatService.stream_execute` when `use_web_search=true` and/or `use_documents=true` (when respective flags are on). Document retrieval completes **before** the first `delta` frame; optional SSE `retrieval_complete` signals retrieval phase to clients. Web search emits `tool_start` / `tool_end` during the tool loop, then streams the final answer. Combined toggles run in order: retrieval → tool loop → stream. Plain streaming (no toggles) still uses `ChatService.stream_chat`.
- **Agent runtime (V2 Epic 1)** — When `AGENT_RUNTIME_ENABLED=true`, unified web-search chat (non-streaming and streaming) routes through `app/ai/agent/adapters/` instead of the legacy `ToolChatService` tool loop. RAG document grounding still runs in `UnifiedChatService` before agent handoff. Default is **off** so V1.1 behaviour is unchanged unless the flag is set. Release summary: [docs/releases/post-mvp-v2-epic1-release-summary.md](../docs/releases/post-mvp-v2-epic1-release-summary.md).
- **Retry policy** — shared utility in `app/core/retry.py` (HTTP 429/503, timeouts; max 3 attempts with exponential backoff).
- **Metrics** — `tool_calls_total`, `tool_errors_total` (Phase 3); `search_latency_ms` on each web search; `stream_tool_rounds` on unified streaming tool path (Phase 4); `retrieval_latency_ms` and `time_to_first_delta_ms` on unified streaming document path (Phase 5).

Unit tests register the stub **`echo`** tool in fixtures only (`tests/test_tool_platform.py`). Tool arguments and search queries are never logged.

#### Multi-provider tool calling (V1.1a)

| Provider  | `supports_streaming` | `supports_tool_calling` | Notes |
| --------- | -------------------- | ----------------------- | ----- |
| OpenAI    | yes                  | yes                     | Reference adapter; OpenAI-compatible tool schema |
| Gemini    | yes                  | yes                     | `google-genai` function calling; prompt-based streaming unchanged |
| Groq      | yes                  | yes                     | OpenAI-compatible chat completions API |
| Anthropic | yes                  | yes                     | Messages API `tool_use` / `tool_result` blocks |

Query capabilities via **`GET /api/health`** — response includes `capabilities.by_provider` with all `ProviderCapabilities` fields (`supports_streaming`, `supports_tool_calling`, and deferred V2 flags defaulting to `false`). Option A (extend health) was chosen over a separate config route to keep the contract minimal for Phase 3 frontend gating.

When tools are enabled but the resolved provider lacks tool support, `ToolChatService` returns **422** `validation_error` with message `Tool calling is not supported for provider '<name>'.` — no silent plain-chat fallback.

Non-streaming unified path: `POST /api/chat` with optional `use_web_search` / `use_documents` (authenticated users only). Global flags gate capabilities; request toggles default off.

Streaming unified path (V1.1c): `POST /api/chat/stream` with `use_web_search=true` and/or `use_documents=true` when flags are on — pre-stream document retrieval (when documents toggle on), optional `retrieval_complete` SSE, tool loop with SSE lifecycle events (when web search on), then streamed final answer. Empty corpus returns a graceful static streamed message without an LLM call.

Known limitations: model-specific restrictions (e.g. Groq model availability) follow each provider's SDK constraints.

### Unified chat orchestration (V1.1b)

Canonical non-streaming pipeline (`UnifiedChatService.execute` in `app/services/unified_chat_service.py`):

```text
Validate Request (auth for toggles, flags, provider capability)
        ↓
Build Conversation Context
        ↓
[use_documents?] → Retriever.retrieve → ContextBuilder.build → merge via chat/document_context.v1.j2
        ↓
[use_web_search?] → register web_search (this request only) → ToolChatService tool loop
        ↓
ChatService.complete_chat (or tool loop terminal completion)
        ↓
Persist Conversation
        ↓
Return Response (+ optional retrieved_chunks, tools_used)
```

| Service | Responsibility |
| ------- | -------------- |
| `UnifiedChatService` | Request orchestration; composes chat, tools, and retrieval components |
| `ChatService` | Provider resolution, plain completion, persistence hooks |
| `ToolChatService` | Tool loop only (invoked when `use_web_search=true`) |
| RAG retrieval stack | Generic retrieval/context only — no chat logic in `app/ai/rag/` |
| `RAGService` | Standalone `/api/rag/ask` unchanged |

**Request fields** on `POST /api/chat` and `POST /api/chat/stream` (optional, default `false`):

| Field | Behavior when `true` (and flag on, authenticated) |
| ----- | --------------------------------------------------- |
| `use_web_search` | Per-request Tavily `web_search` tool loop via `ToolChatService` |
| `use_documents` | Pre-retrieval from caller's corpus; context merged before LLM call |

**Routing:** `POST /api/chat` delegates to `UnifiedChatService` when either toggle is `true`; otherwise existing plain `ChatService` path (zero regression when toggles off).

**Guests:** toggles return the same denial message as V1 tool policy (`_GUEST_TOOL_DENIED_MESSAGE` style) without invoking retrieval or tools.

**Flags off:** when `RAG_ENABLED=false` or `TOOLS_ENABLED=false`, the corresponding toggle is ignored (no-op); plain chat proceeds.

**Health:** `GET /api/health` exposes `rag_enabled`, `tools_enabled`, and `capabilities.by_provider` for frontend toggle gating.

Example:

```bash
curl -sS -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"Summarize my uploaded notes"}],
    "use_documents": true,
    "provider": "openai",
    "model": "gpt-4o-mini"
  }'
```

### Document Ingestion (Phase 5)

Parse, chunk, and persist uploaded documents for authenticated users only (no HTTP upload route until Phase 11).

| Component | Location | Responsibility |
| --------- | -------- | -------------- |
| Parsers | `app/ai/documents/parsers/` | PDF (PyMuPDF), DOCX (python-docx), Markdown/plain text |
| Router | `select_parser(mime_type, filename)` | Simple if/else routing — not a plugin system |
| Chunker | `app/ai/documents/chunkers/recursive.py` | Character-based chunks with overlap from settings |
| Pipeline | `app/ai/documents/pipeline.py` | In-memory parse → chunk orchestration |
| Service | `app/services/document_service.py` | Auth-only ownership, status lifecycle, DB persistence |

Supported file types: **PDF**, **DOCX**, **`.md`**, **`.txt`**.

Settings (service-layer validation in Phase 5; HTTP enforcement in Phase 11):

| Setting | Env var | Default |
| ------- | ------- | ------- |
| Chunk size | `CHUNK_SIZE` | `1000` |
| Chunk overlap | `CHUNK_OVERLAP` | `200` |
| Upload max bytes | `DOCUMENT_UPLOAD_MAX_BYTES` | `10485760` (10 MB) |

Phase 5 scope: parse + chunk + persist text chunks only. `DocumentService` keeps this text-only path (DB `embedding` column NULL). Full vector ingest uses `KnowledgeService` (Phase 7).

### Embeddings (Phase 6)

In-memory embedding generation — vectors attach to pipeline `DocumentChunk` instances after chunking. `DocumentService` does not embed; use `KnowledgeService` for parse → chunk → embed → persist (Phase 7).

| Setting | Env var | Default |
| ------- | ------- | ------- |
| Embedding provider | `EMBEDDING_PROVIDER` | `openai` |
| Embedding model | `EMBEDDING_MODEL` | `text-embedding-3-small` |
| Embedding dimensions | `EMBEDDING_DIMENSIONS` | `1536` |
| Embedding batch size | `EMBEDDING_BATCH_SIZE` | `100` |

- `IngestionPipeline.embed()` and `parse_chunk_embed()` require an injected `EmbeddingProvider`; `DocumentService` continues parse + chunk only (DB `embedding` column stays NULL).
- Structured logs emit `embedding_latency_ms` and batch/text counts — never chunk content or vector values.
- V2 embedding cache (Redis) is deferred; see comment in `app/ai/embeddings/factory.py`.
- When `RAG_ENABLED=true` and `EMBEDDING_PROVIDER=openai`, startup requires `OPENAI_API_KEY` (existing validation in `Settings.validate_rag_requirements()`).

### Vector store (Phase 7)

Local dev Postgres must use the pgvector image (`pgvector/pgvector:pg16` in root `docker-compose.yml`, `python` profile). Host Postgres is supported if the `vector` extension is installed.

Alembic migration `0003_pgvector_embeddings`:

- `CREATE EXTENSION IF NOT EXISTS vector`
- `document_chunks.embedding` → `vector(1536)` (matches `EMBEDDING_DIMENSIONS`)
- HNSW index `ix_document_chunks_embedding_hnsw` with cosine distance (`vector_cosine_ops`)

If migration fails on an old volume from `postgres:16-alpine`, reset with `docker compose --profile python down -v` then bring Postgres back up before `make db-migrate`.

| Component | Scope (Phase 7) |
| --------- | ---------------- |
| `PgVectorStore` | `upsert`, `similarity_search` (cosine, `user_id`-scoped), `delete_by_document` |
| `KnowledgeService` | `ingest_document`, `delete_document` only — **no search** (retrieval is Phase 8 `Retriever`) |
| `IngestionPipeline.persist` | Writes embeddings after in-memory embed |

Structured logs: `vector_search_latency_ms` (result count only), `documents_ingested_total`, `documents_failed_total`. Never log chunk text or embedding values.

V2 may add async ingest queue (return `document_id` + status immediately); V1 ingest is synchronous end-to-end.

Wire via DI:

```python
from app.ai.deps import (
    get_document_service,
    get_embedding_provider,
    get_ingestion_pipeline,
    get_ingestion_pipeline_with_embeddings,
    get_knowledge_service,
    get_vector_store,
)
```

### Generic RAG retrieval (Phase 8)

Domain-agnostic retrieval-side components in `app/ai/rag/` — no LLM orchestration in this phase (`RAGService` is Phase 9).

| Component | Responsibility |
| --------- | -------------- |
| `Retriever` | Embed query → `VectorStore.similarity_search` → ranked `ScoredChunk` list (`user_id`-scoped) |
| `ContextBuilder` | Numbered context blocks with `rag_context_max_chars` budget; drops lowest-scoring chunks when over budget |
| `PromptBuilder` | Render configurable RAG template via `PromptManager` (default `rag/answer/v1`) |

Settings:

| Setting | Env var | Default |
| ------- | ------- | ------- |
| Top-K retrieval | `RAG_TOP_K` | `5` |
| Context char budget | `RAG_CONTEXT_MAX_CHARS` | `8000` |
| Default RAG template | `RAG_DEFAULT_PROMPT_TEMPLATE` | `rag/answer/v1` |

Structured logs emit `retrieval_latency_ms` and result **count** only — never question text, chunk content, or embedding values.

Components are independently testable with mocked dependencies. Domain-specific assistants compose these via prompts and application services (Phase 9+) — not by adding business logic to `app/ai/rag/`.

Wire via DI:

```python
from app.ai.deps import (
    get_context_builder,
    get_prompt_builder,
    get_rag_service,
    get_retriever,
)
```

### Generic RAG orchestration (Phase 9)

`RAGService.ask` wires the Phase 8 components into a complete non-streaming pipeline:

```text
Question → Retriever → ContextBuilder → PromptBuilder → LLM → RAGResponse
```

| Behavior | Detail |
| -------- | ------ |
| Empty corpus | Short-circuits without an LLM call; returns a generic framework message |
| Response | Answer text + retrieved chunk metadata (scores, IDs) for debugging — not a citations UI |
| Metrics | `rag_requests_total`, `rag_request_duration_ms`, retrieval/included counts, top score, latency breakdown |
| Streaming | Standalone `/api/rag/ask` streaming **deferred**; chat document grounding streams via `UnifiedChatService` (V1.1) |

**Extension philosophy** — domain-specific assistants compose the framework; they do not modify `app/ai/rag/`:

| Future consumer | Corpus | Prompt template | Application service |
| --------------- | ------ | --------------- | ------------------- |
| Customer Care RAG | Customer documents | `rag/customer_care.v1.j2` | `CustomerCareRAGService` in `app/services/` |
| Enterprise Knowledge Assistant | Internal docs | Enterprise prompt template | App service with org scoping |
| Legal / HR / Community Service | Domain corpus | Domain prompt template | Matching app service |

Business knowledge lives in **documents** and **prompt templates**; framework code stays domain-agnostic. HTTP exposure is documented in **Knowledge and RAG API (Phase 11)** below.

### Knowledge and RAG API (Phase 11)

Auth-only REST endpoints for document management and generic RAG. Guests receive **401** on all routes below.

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| POST | `/api/documents/upload` | Bearer JWT | Upload and synchronously ingest a document |
| GET | `/api/documents` | Bearer JWT | List caller's documents (newest first) |
| GET | `/api/documents/{id}` | Bearer JWT | Document metadata and status |
| DELETE | `/api/documents/{id}` | Bearer JWT | Delete document and vectors |
| POST | `/api/rag/ask` | Bearer JWT | Generic RAG question → answer |

**Upload limits:** `DOCUMENT_UPLOAD_MAX_BYTES` (default 10 MB) applies only to `/api/documents/upload`. Chat and auth routes still use the global `REQUEST_BODY_LIMIT_BYTES` (16 KB).

**RAG feature flag:** set `RAG_ENABLED=true` for `/api/rag/ask`; otherwise the endpoint returns **503** with code `feature_disabled`. Document upload/list/delete work independently of `RAG_ENABLED`.

**RAG provider selection:** `LLM_PROVIDER` is the default when the request omits `provider`. Each `POST /api/rag/ask` body may optionally include `provider` (`openai` | `gemini` | `groq` | `anthropic`) and `model` (must match the configured model for that provider). Retrieval and embeddings still use the env-configured embedding provider (OpenAI by default) — selecting a different LLM provider does not change the embedding backend in V1.1.

**CORS:** `DELETE` is allowed for browser-based document removal in Phase 12.

Example requests (replace `$TOKEN` with a bearer JWT from `POST /api/auth/google`):

```bash
# Upload
curl -X POST http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sample.pdf"

# List
curl http://localhost:8000/api/documents \
  -H "Authorization: Bearer $TOKEN"

# Ask (requires RAG_ENABLED=true)
curl -X POST http://localhost:8000/api/rag/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarize my uploaded documents."}'

# Ask with per-request provider override (optional)
curl -X POST http://localhost:8000/api/rag/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"Summarize my uploaded documents.","provider":"groq","model":"openai/gpt-oss-20b"}'
```

### Evaluation framework (Phase 10)

Objective quality measurement at three levels — **prompt**, **retrieval**, and **end-to-end** — via a CLI harness. Run eval before API exposure to tune chunk size, overlap, top-K, embedding model, and prompt templates.

| Level | Runner | Measures |
| ----- | ------ | -------- |
| Prompt | `PromptEvalRunner` | Template rendering correctness / regression snapshots |
| Retrieval | `RetrievalEvalRunner` | Precision and recall vs labeled chunk sets |
| End-to-end | `EndToEndEvalRunner` | Correctness, faithfulness, hallucination, latency |

**Run evaluation:**

```bash
make eval
# or
uv run python -m app.ai.evaluation.cli --level all
uv run python -m app.ai.evaluation.cli --level prompt
uv run python -m app.ai.evaluation.cli --level retrieval --dataset tests/data/evaluation/sample.yaml
uv run python -m app.ai.evaluation.cli --level e2e --use-judge
```

- Sample dataset: `tests/data/evaluation/sample.yaml` (3–5 cases covering all levels).
- JSON baseline report (for Phase 13 comparison): `.eval/eval-report.json` by default (`--output` to override).

**Phase 13 / V1 validation:** Re-run `make eval` before release and compare against the committed baseline in `.eval/eval-report.json`. Phase 13 verified 5/5 cases pass (prompt=2, retrieval=2, e2e=1) with mean retrieval latency 57.5 ms and e2e latency 49 ms on the sample dataset.
- **Offline mode:** fake embeddings + mocked LLM — no live API key required for prompt-level runs; retrieval/e2e need local Postgres with pgvector.
- Optional `--use-judge` enables LLM-as-judge faithfulness/hallucination via `app/ai/prompts/evaluation/judge.v1.j2`.

**Tuning workflow:** re-run eval after changing env vars (`LLM_PROVIDER`, `RAG_TOP_K`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `EMBEDDING_MODEL`, prompt templates) and compare JSON reports to pick better settings before Phase 11 API exposure.

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
| Chat streaming flag | `CHAT_STREAMING_ENABLED` | `true` |
| Default temperature | `DEFAULT_TEMPERATURE` | `0.7` |
| Default max tokens | `DEFAULT_MAX_TOKENS` | provider default (`None`) |
| Document upload max | `DOCUMENT_UPLOAD_MAX_BYTES` | `10485760` (10 MB) |
| Web search provider | `WEB_SEARCH_PROVIDER` | `tavily` |
| Web search API key | `WEB_SEARCH_API_KEY` | unset |
| Web search max results | `WEB_SEARCH_MAX_RESULTS` | `5` |

LLM provider selection remains `LLM_PROVIDER` in `app/core/config.py`.

### Feature flags

- `RAG_ENABLED=false` (default) — document toggle and `/api/rag/ask` disabled; plain chat unchanged.
- `TOOLS_ENABLED=false` (default) — web search toggle disabled; no tool registration at startup.
- `TOOLS_ENABLED=true` — registers `web_search`; enables `use_web_search` on non-streaming and streaming chat when authenticated. Requires `WEB_SEARCH_API_KEY`.
- `RAG_ENABLED=true` — enables `use_documents` on chat and `/api/rag/ask`. Requires embedding provider secrets (e.g. `OPENAI_API_KEY` when `EMBEDDING_PROVIDER=openai`).
- `CHAT_STREAMING_ENABLED=true` (default) — `POST /api/chat/stream` serves SSE; set `false` to return **503** `feature_disabled` on the stream route and use non-streaming `POST /api/chat` instead. Exposed on `GET /api/health` as `chat_streaming_enabled` for the frontend.
- **Request toggles** (`use_web_search`, `use_documents`) are JSON body fields, not env vars. They are no-ops when the corresponding flag is off or the caller is a guest.
- When a flag is `true`, startup fails fast if required secrets are missing.
- With both `RAG_ENABLED` and `TOOLS_ENABLED` off and toggles off, behavior matches the MVP/V1 baseline.

### External service retry policy

Documented for later phases — implemented in `app/core/retry.py` and reused from web search (Phase 4+):

| Setting | Value |
| ------- | ----- |
| Retry on | HTTP 429, HTTP 503, network timeout, temporary connection failures |
| Max attempts | 3 |
| Backoff | Exponential (e.g. 1s → 2s → 4s with jitter) |
| Do not retry | HTTP 4xx (except 429), validation errors, auth failures |

### Observability metrics (V1 + V1.1)

Structured log counters and fields:

| Metric | Purpose |
| ------ | ------- |
| `rag_requests_total` | RAG ask volume |
| `rag_request_duration_ms` | End-to-end RAG latency |
| `retrieval_latency_ms` | Retriever stage latency (standalone RAG + unified streaming) |
| `time_to_first_delta_ms` | Unified streaming UX latency (V1.1) |
| `stream_tool_rounds` | Tool iterations per unified streaming request (V1.1) |
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
DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot
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
- `DOCUMENT_UPLOAD_MAX_BYTES` (default 10 MB) is enforced on `POST /api/documents/upload` (see Knowledge and RAG API section)

## Provider Selection

- Default provider comes from `LLM_PROVIDER`.
- You can also pass `provider` and `model` in each chat request.
- OpenAI, Gemini, Groq, and Anthropic use the same `/api/chat` and `/api/chat/stream` contracts.
- Health responses report the active default provider.

## Endpoints

- `GET /` - welcome message
- `GET /api/health` - status + active provider + app version
- `GET /api/health/ready` - readiness probe (DB `SELECT 1`; returns `{"status":"ok","db":"ok"}`)
- `POST /api/chat` - non-streaming completion
- `POST /api/chat/stream` - SSE streaming completion (plain chat, document grounding, and/or web search toggles)

### Chat session API (V1.1.1)

Requires `CHAT_PERSISTENCE_ENABLED=true`. Session list/create/resume unchanged from V1.1; V1.1.1 adds delete and auto-title behavior.

| Method | Path | Auth | Purpose |
| ------ | ---- | ---- | ------- |
| GET | `/api/chat/sessions` | Bearer JWT | List caller's sessions (newest activity first) |
| POST | `/api/chat/sessions` | Bearer JWT | Create empty session (`title: null` until first chat turn) |
| GET | `/api/chat/sessions/{id}` | Bearer JWT | Session metadata + messages |
| DELETE | `/api/chat/sessions/{id}` | Bearer JWT | Delete session and cascade children (**204**); **403** for guests; **404** if not owned |

**Delete cascade:** DB foreign keys remove `chat_messages`, `session_summaries`, and `usage_events` for the session. Frontend confirms deletion and selects the most recent remaining session, or creates a new empty session when the list is empty.

**Auto-title (V1.1.1):** On the first persisted user message when `session.title IS NULL`, `derive_session_title()` in `app/core/text_utils.py` sets the title — first line only, whitespace collapsed, ~50 characters. Existing titles are never overwritten. Empty sessions created via `POST /api/chat/sessions` display **"New chat"** in the UI until the first turn.

### Public demo protection (V1.1.1)

Config-driven caps for public deployments. See [docs/ops/public-demo-protection.md](../docs/ops/public-demo-protection.md) for the operator checklist (rate limits, token caps, upload quotas, provider spending alerts).

| Setting | Dev default | Public demo note |
| ------- | ----------- | ---------------- |
| `GUEST_MAX_OUTPUT_TOKENS` | `4096` | Lower to `512` (or use `DEMO_MODE_STRICT=true`) |
| `AUTHENTICATED_DAILY_UPLOAD_QUOTA` | unset (unlimited) | e.g. `20` uploads per UTC day |
| `GUEST_DAILY_UPLOAD_QUOTA` | `5` | Future-proof if guest upload is enabled |
| `DEMO_MODE_STRICT` | `false` | `true` tightens guest tokens and upload quota defaults |
| `RATE_LIMIT_ANONYMOUS_PER_MINUTE` | `30` | Review for production demo profile |
| `RATE_LIMIT_AUTHENTICATED_PER_MINUTE` | `120` | Review for production demo profile |

Guests remain denied `use_web_search` and `use_documents` (V1.1 policy unchanged).

### SSE event protocol (`POST /api/chat/stream`)

Frames are Server-Sent Events: `event: <name>` plus a JSON `data:` line. Additive events — older clients may ignore unknown types.

| Event                | When emitted | Payload fields |
| -------------------- | ------------ | -------------- |
| `retrieval_complete` | After document retrieval, before tool loop or final stream | `type`, `id`, `chunk_count`, `timestamp` |
| `start`              | Final answer streaming begins | `type`, `id`, `session_id?`, `timestamp` |
| `delta`              | Token/chunk of assistant text | `type`, `id`, `content`, `timestamp` |
| `end`                | Stream complete | `type`, `id`, `finish_reason`, `timestamp` |
| `error`              | Provider, retrieval, or server failure after stream started | `type`, `id`, `code`, `message`, `timestamp` |
| `tool_start`         | Before a tool handler runs (streaming web search) | `type`, `id`, `tool_name`, `call_id`, `timestamp` |
| `tool_end`           | After tool handler completes (no result body) | `type`, `id`, `tool_name`, `call_id`, `success`, `timestamp` |

Example (document retrieval then streamed answer):

```text
event: retrieval_complete
data: {"type":"retrieval_complete","id":"resp_abc","chunk_count":3,"timestamp":"2026-07-21T12:00:00Z"}

event: start
data: {"type":"start","id":"resp_abc","session_id":null,"timestamp":"2026-07-21T12:00:00Z"}

event: delta
data: {"type":"delta","id":"resp_abc","content":"Based on your documents, ","timestamp":"2026-07-21T12:00:01Z"}

event: end
data: {"type":"end","id":"resp_abc","finish_reason":"stop","timestamp":"2026-07-21T12:00:02Z"}
```

Example (web search then streamed answer):

```text
event: tool_start
data: {"type":"tool_start","id":"resp_abc","tool_name":"web_search","call_id":"call_1","timestamp":"2026-07-21T12:00:00Z"}

event: tool_end
data: {"type":"tool_end","id":"resp_abc","tool_name":"web_search","call_id":"call_1","success":true,"timestamp":"2026-07-21T12:00:01Z"}

event: start
data: {"type":"start","id":"resp_abc","session_id":null,"timestamp":"2026-07-21T12:00:01Z"}

event: delta
data: {"type":"delta","id":"resp_abc","content":"Based on recent results, ","timestamp":"2026-07-21T12:00:02Z"}

event: end
data: {"type":"end","id":"resp_abc","finish_reason":"stop","timestamp":"2026-07-21T12:00:03Z"}
```

Provider strategy for streaming tools: **two-phase** — non-streaming `complete_chat_with_tools` for tool-call detection iterations, then `stream_chat` for the final answer (same pattern as the V1.1 plan).

Streaming RAG policy: retrieval and context merge complete **before** the first `delta` frame (no mid-stream retrieval in V1.1). When both toggles are on, order is retrieval → tool loop → stream. Retrieval failures emit an SSE `error` frame with code `retrieval_error`. Structured logs include `retrieval_latency_ms` and `time_to_first_delta_ms` on unified streaming paths.

## Observability

- **Logging:** JSON in production (`APP_ENV=production`), readable text in development. Fields include `request_id`, route, method, status, latency, provider, and model. Sensitive values are redacted.
- **Correlation IDs:** Every response includes `X-Request-ID`. Pass the header on inbound requests to continue a trace.
- **Rate limiting:** Anonymous callers (IP or guest token bucket) and authenticated callers (JWT bucket) have separate per-minute limits. `/api/health` and `/api/health/ready` are exempt.
- **V1.1 unified streaming path** — structured log fields on `UnifiedChatService.stream_execute`:
  - `retrieval_latency_ms` — document pre-retrieval duration
  - `time_to_first_delta_ms` — time from stream start to first answer `delta`
  - `stream_tool_rounds` — tool loop iterations before final stream
  - `chunk_count` on retrieval completion log line
- **Tool platform** — `tool_calls_total`, `tool_errors_total`, `search_latency_ms` (Phase 3–4)
- **RAG** — `rag_requests_total`, `rag_request_duration_ms`, retrieval counts (standalone `/api/rag/ask`)

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

Current suite (2026-07-22): **403 passed**, **86.14%** coverage on `app/` (20.10s).

Coverage includes health, auth, chat (streaming and non-streaming), unified chat toggles, tools, RAG, persistence, logging, correlation IDs, errors, rate limiting, and provider adapters (OpenAI, Gemini, Groq, Anthropic).

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
