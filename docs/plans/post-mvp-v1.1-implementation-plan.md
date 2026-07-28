# Post-MVP V1.1 - Implementation Plan

## Objective

Extend the completed V1 AI platform with a **unified chat experience**: web search and document-grounded answers available from the main chatbot, across all four LLM providers, with streaming support. V1.1 delivers provider parity and UX consolidation while preserving V1 stability — chat persistence, auth, guest flow, and the Generic RAG Framework remain unchanged at their core.

V1 proved the architecture (tools, ingestion, pgvector, generic RAG, evaluation). V1.1 proves the **product integration** — one chat surface for plain chat, web search, and document-grounded answers.

## Relationship to V1

| V1 delivered (complete 2026-07-21)                                | V1.1 extends                                |
| ----------------------------------------------------------------- | ------------------------------------------- |
| Web search tool (Tavily), non-streaming, OpenAI-primary tool loop | All four providers; streaming tool loop     |
| `RAGService.ask` (non-streaming), global `LLM_PROVIDER` only      | Per-request provider/model; streaming RAG   |
| `/documents` route + `/api/rag/ask` (separate from chat)          | RAG + web search toggles on main chat (`/`) |
| Streaming chat (no tools when streaming is on)                    | Streaming with tools and document context   |
| **Interim (pre-V1.1):** `CHAT_STREAMING_ENABLED=false` → main chat uses non-streaming `/api/chat`; web search works when `TOOLS_ENABLED=true` | Per-request toggles; streaming tools/RAG (V1.1c) |

Reference: [post-mvp-v1-implementation-plan.md](./post-mvp-v1-implementation-plan.md) (Phase 13 Completion Record).

## Execution Mode

- Implement sequentially by phase (V1.1a → V1.1b → V1.1c).
- Use the **Python backend** as the production reference; Node.js remains out of scope.
- After each phase verification is complete, stop and request explicit user confirmation before starting the next phase.
- Every milestone must leave the application deployable; existing chat, auth, persistence, and `/documents` flows must not regress.
- Introduce a lightweight **`UnifiedChatService`** orchestration layer in Phase 3; keep `ChatService`, `ToolChatService`, and RAG retrieval components focused on their own responsibilities (see [Architecture Principles](#architecture-principles)).
- **Async-first**: tool execution, retrieval, and streaming remain async end-to-end.
- Feature flags (`RAG_ENABLED`, `TOOLS_ENABLED`, `CHAT_STREAMING_ENABLED`) continue to gate rollout; new chat toggles are no-ops when flags are off. `CHAT_STREAMING_ENABLED` defaults to `true` (V1 streaming UX unchanged).

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

## Architecture Principles

These principles govern V1.1 design and implementation. They align the plan with a clean bridge to V2 without scope creep.

### Canonical chat execution pipeline

All chat paths (plain, tools, document grounding, streaming, non-streaming) follow the same orchestration order. Response delivery differs only at the final step (stream vs complete).

```text
Validate Request
        ↓
Build Conversation Context
        ↓
Retrieve Documents (optional — context provider)
        ↓
Register Tools (optional)
        ↓
Invoke LLM Provider (+ tool loop when tools registered)
        ↓
Stream / Complete Response  ← response renderer only
        ↓
Persist Conversation
        ↓
Return Response
```

When **both** web search and document grounding are enabled, execution order is deterministic:

```text
Conversation Context
        ↓
Document Retrieval
        ↓
Tool Registration
        ↓
Tool Execution (if requested by LLM)
        ↓
Final LLM Response
```

Document this pipeline in `backend-python/README.md` and the architecture spec during Phase 3.

### Context sources model

Treat RAG as a **context provider**, not a separate execution path. Chat orchestration merges context sources before the LLM call:

```text
Context Sources                    Merged Context
─────────────────                  ──────────────
• System Prompt          ─┐
• Conversation History   ─┼──→  messages[]  ──→  LLM Provider
• Retrieved Documents    ─┤
• (Future: Memory)       ─┤
• (Future: User Profile) ─┘
```

Retrieval and context assembly live in the service layer (`UnifiedChatService` + RAG retrieval components). The Generic RAG Framework (`app/ai/rag/`) remains retrieval-only — no chat logic, UI logic, or business logic.

### Service responsibilities

| Service | Responsibility |
| ------- | -------------- |
| `UnifiedChatService` | Request orchestration: validate, build context, coordinate retrieval/tools, invoke provider, persist, return |
| `ChatService` | Core chat completion, streaming primitives, persistence hooks, provider resolution |
| `ToolChatService` | Tool loop execution (invoked by orchestrator when tools are registered) |
| RAG retrieval stack (`Retriever`, `ContextBuilder`, etc.) | Generic retrieval and context generation only |
| `RAGService` | Standalone `/api/rag/ask` pipeline (unchanged role; per-request provider in Phase 2) |
| `ProviderFactory` | Provider resolution |

Do not leak orchestration logic into provider adapters or RAG framework modules. Do not expand `ToolChatService` into a god service — new cross-cutting chat behavior belongs in `UnifiedChatService`.

### Single pipeline, dual response modes

Avoid parallel execution paths such as `chat()`, `stream_chat()`, `tool_chat()`, `stream_tool_chat()`, `rag_chat()`, `stream_rag_chat()`. `UnifiedChatService` exposes one execution entry point; the final step selects a **response renderer**:

- **Non-streaming** — accumulate completion, return `ChatResponseSchema`
- **Streaming** — emit SSE frames (`start`, `delta`, `tool_start`, `tool_end`, `end`, `error`)

Phases 4–5 extend the same pipeline; they do not fork orchestration.

### Provider capabilities abstraction

Establish the full capability model now; implement only what V1.1 needs:

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_streaming: bool
    supports_tool_calling: bool
    supports_json_mode: bool
    supports_reasoning: bool
    supports_image_input: bool
    supports_image_output: bool
    supports_audio: bool
    supports_embeddings: bool
```

Phase 1 sets values per provider (streaming and tool calling are the V1.1-active flags; others default to `False` until needed). Frontend gates tool toggle on `supports_tool_calling`.

### Backward compatibility

Existing endpoints remain fully functional throughout V1.1:

- `POST /api/chat`
- `POST /api/chat/stream`
- `POST /api/rag/ask`
- `/documents` (upload, list, delete)

New chat toggles and orchestration are **additive**. Routers delegate to `UnifiedChatService` when toggles or flags require it; plain chat behavior is unchanged when toggles are off.

### V1.1 scope boundary

Complete the platform; do not transform it into V2. Explicitly defer: MCP, agent frameworks, LangGraph, multi-agent workflows, memory, hybrid search, reranking, citations UI, async ingestion, background workers.

---

## V1.1 Locked Decisions

| Decision                   | Choice                                                                             | Rationale                                                          |
| -------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Release packaging          | **Single V1.1 release**, three sub-tracks (1.1a → 1.1b → 1.1c)                     | Ship provider parity before UX unification; streaming last         |
| Web search in chat         | **LLM tool** (`web_search`), enabled via request toggle                            | LLM decides when to search; matches V1 tool platform               |
| Document grounding in chat | **Pre-retrieval context provider** when toggle on (not a tool)                      | Predictable latency; fits merged-context model                     |
| Chat orchestration         | **`UnifiedChatService`** — single pipeline, dual response renderers                 | Avoid god service; streaming/non-streaming share orchestration     |
| RAG integration pattern    | **Extend chat request** via context provider; `/api/rag/ask` unchanged              | Backward-compatible; standalone RAG remains for direct/debug use |
| `/documents` route         | **Retained** as document management UI                                             | Upload/list/delete stays; "ask" moves to main chat                 |
| Streaming + tools (V1.1c)  | **New SSE event types** for tool lifecycle                                         | Extend existing `start` / `delta` / `end` / `error` frames         |
| Streaming RAG (V1.1c)      | **Retrieve before stream starts**; stream LLM answer only                          | Avoid mid-stream context injection complexity in V1.1              |
| Provider tool calling      | **Capability flags per provider**                                                  | Disable tool toggle in UI when provider lacks support              |
| RAG provider selection     | **Per-request `provider` / `model`** on chat and `/api/rag/ask`                    | Match chat's existing provider resolution                          |
| Guest users                | **No tools, no document grounding** (auth-only, same as V1 RAG)                    | Consistent with V1 auth policy                                     |
| Non-streaming fallback     | **Env-level `CHAT_STREAMING_ENABLED=false`** (implemented pre-V1.1); **request-level** toggles in V1.1b+ | Deploy-time tools/web-search path today; per-request `use_web_search` / `use_documents` later; streaming remains additive |

## Phase Status

- Phase 0 - **Complete** (2026-07-21)
- Phase 1 - **Complete** (2026-07-21)
- Phase 2 - **Complete** (2026-07-21)
- Phase 3 - **Complete** (2026-07-21)
- Phase 4 - **Complete** (2026-07-22)
- Phase 5 - **Complete** (2026-07-22)
- Phase 6 - **Complete** (2026-07-22)

## Scope

### In scope

- Multi-provider tool calling validation and hardening (OpenAI, Gemini, Groq, Anthropic)
- Provider capability model exposed to frontend (tool-calling support per provider)
- Per-request provider/model on `RAGService` and `/api/rag/ask`
- Chat request extensions: `use_web_search`, `use_documents` toggles
- Pre-retrieval document grounding in chat orchestration (non-streaming, then streaming)
- Web search tool loop on all supported providers (non-streaming, then streaming)
- SSE extensions for tool lifecycle events during streaming
- Frontend chat UI: toggles, tool/RAG status indicators, streaming event handling
- Document upload accessible from chat context (link or embedded panel)
- Tests, documentation, and env template updates for all V1.1 capabilities
- Full regression: V1 chat, auth, persistence, `/documents` management, eval CLI

### Out of scope (V1.1)

- Node.js backend parity
- Additional tools (calculator, weather, GitHub, SQL)
- RAG as an LLM-invoked tool (deferred — pre-retrieval chosen for V1.1)
- Mid-stream retrieval / dynamic re-retrieval during streaming
- Hybrid retrieval, reranking, citations UI, query expansion (V2 RAG enhancements)
- Async document ingestion queue (V2)
- Guest-scoped document corpora
- MCP, agents, memory, workflow engine (V2)
- Removing `/api/rag/ask` or `/documents` route (deprecated usage only; routes remain)

## Non-Negotiable Requirements

1. Python backend is the production reference.
2. Dependency direction unchanged: Routers → Services → AI Framework → Providers → External APIs.
3. Generic RAG Framework (`app/ai/rag/`) remains **domain-agnostic** — chat integration orchestrates in `app/services/` via `UnifiedChatService`; no chat logic in RAG framework.
4. Feature flags off (`RAG_ENABLED=false`, `TOOLS_ENABLED=false`, `CHAT_STREAMING_ENABLED=true`) = MVP + V1 behavior unchanged.
5. Add tests alongside every change; maintain ≥ 80% coverage on `app/`.
6. No sensitive data (API keys, tokens, document content, search queries) in logs.
7. User confirmation required between phases.
8. Document and RAG grounding require authentication — guests receive clear messaging, not silent failure.
9. Existing `/documents` upload/list/delete API and tests must keep passing.
10. Evaluation CLI (`make eval`) must continue to pass; extend only if new settings affect eval fixtures.

## Current Baseline (post-Phase 6; verified 2026-07-22)

| Area             | Current state                                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------------------------------- |
| V1 status        | **Complete** — Phase 13 verified 2026-07-21                                                                          |
| Backend tests    | **403 passed**, **86.14%** coverage on `app/`, **20.10s**                                                           |
| Frontend tests   | **122 passed** (Vitest)                                                                                              |
| Tool calling     | `complete_chat_with_tools` **implemented on all four providers**; non-streaming + **streaming** integration tests per provider |
| Capability model | `ProviderCapabilities` + `get_capabilities()` in `app/providers/capabilities.py`; exposed on `GET /api/health` as `capabilities.by_provider` |
| Tool chat path   | `ToolChatService` on non-streaming `POST /api/chat`; `UnifiedChatService.stream_execute` on streaming path when `use_web_search=true` and/or `use_documents=true` |
| Streaming toggle | **`CHAT_STREAMING_ENABLED`** (default `true`): when `false`, `POST /api/chat/stream` returns **503** `feature_disabled`; UI uses non-streaming `POST /api/chat`. Exposed on `GET /api/health` as `chat_streaming_enabled`. |
| Streaming policy | `POST /api/chat/stream` runs **document pre-retrieval** when `use_documents=true` and `RAG_ENABLED=true` (SSE `retrieval_complete`, then stream); **web search tool loop** when `use_web_search=true` and `TOOLS_ENABLED=true` (SSE `tool_start` / `tool_end`); combined toggles: retrieval → tools → stream; plain streaming unchanged when toggles off |
| Web search       | Tavily-backed; per-request tool loop via `use_web_search=true` on non-streaming and streaming chat when `TOOLS_ENABLED=true` |
| RAG              | `RAGService.ask` resolves **per-request `provider` / `model`** via `ProviderFactory.get_provider(name, settings)` inside `ask()`; falls back to `LLM_PROVIDER` when omitted |
| RAG API          | `POST /api/rag/ask` accepts optional `provider` / `model` on request body (non-streaming; standalone streaming deferred) |
| Chat UI          | `/` — streaming by default; **document and web search toggles use streaming** when `CHAT_STREAMING_ENABLED=true` |
| Documents UI     | `/documents` — upload, list, delete; standalone RAG ask de-emphasized (link to chat)                         |
| SSE frames       | `start`, `delta`, `end`, `error`, **`retrieval_complete`**, **`tool_start`**, **`tool_end`**                                                   |
| Eval CLI         | 5/5 pass on sample dataset; report at `backend-python/.eval/eval-report.json` (timestamp 2026-07-21T23:04:45Z)   |
| Release summary  | [docs/releases/post-mvp-v1.1-release-summary.md](../releases/post-mvp-v1.1-release-summary.md)                     |
| Architecture spec | V1.1 sections marked in [post-MVP-V1-Architecture-and-Technical-Design-Specs.md](../references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md) |

### V1 gaps addressed by V1.1

| Gap                  | V1 state                                          | V1.1 target                                    |
| -------------------- | ------------------------------------------------- | ---------------------------------------------- |
| Web search providers | Effectively OpenAI-validated in integration tests | All four providers tested and capability-gated |
| Web search streaming | Disabled when streaming is on (`CHAT_STREAMING_ENABLED=true`) | Tool loop works in streaming path (V1.1c)      |
| RAG providers        | Global env `LLM_PROVIDER` only                    | Per-request provider/model                     |
| RAG streaming        | Non-streaming only                                | Stream answer after retrieval                  |
| Chat + RAG + search  | Separate routes/endpoints                         | Unified on `/` with toggles                    |

---

## Phase 0 - Baseline Audit and V1.1 Readiness

**Status:** Complete (2026-07-21)

### Objectives

Confirm V1 completion, record baseline metrics, and document the starting API surface and provider adapter state before any V1.1 code changes.

### Tasks

- Confirm V1 Phase 13 completion record in [post-mvp-v1-implementation-plan.md](./post-mvp-v1-implementation-plan.md).
- Run full quality gates locally:
  - Backend: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
  - Frontend: `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build`
- Record baseline test counts, coverage, and duration (backend + frontend).
- Inventory provider adapter tool-calling implementations:
  - `app/providers/openai_provider.py` — `complete_chat_with_tools`
  - `app/providers/gemini_provider.py` — `complete_chat_with_tools`
  - `app/providers/groq_provider.py` — `complete_chat_with_tools`
  - `app/providers/anthropic_provider.py` — `complete_chat_with_tools`
- Document existing provider tests under `tests/providers/` — note OpenAI-only tool tests today.
- Confirm streaming tool skip policy when streaming is on: `test_streaming_skips_tools_even_when_enabled` in `test_phase4_chat_tools.py`.
- Confirm **`CHAT_STREAMING_ENABLED`** behavior (pre-V1.1): health field, stream route 503 when off, UI transport switch, composer/bubble pending labels, tools on non-stream path when `TOOLS_ENABLED=true`.
- Confirm RAG provider wiring: `get_rag_service` in `app/ai/deps.py` uses global provider.
- Document current chat request schema (no `use_web_search` / `use_documents`).
- Agree V1.1 feature flag behavior: existing flags unchanged (`CHAT_STREAMING_ENABLED` defaults `true`); request toggles on chat are ignored when `RAG_ENABLED` / `TOOLS_ENABLED` are off.
- Review V1.1 locked decisions with team.

### Success Criteria

- All V1 quality gates pass locally.
- Baseline metrics recorded in Phase 0 section below.
- Provider adapter inventory complete.
- V1.1 locked decisions agreed.

### Verification Checklist

- Backend and frontend quality gates pass.
- Baseline test counts and coverage recorded.
- Provider tool-calling code paths documented.
- RAG global-provider limitation documented.
- Streaming tool skip behavior confirmed by test reference (when `CHAT_STREAMING_ENABLED=true`).
- `CHAT_STREAMING_ENABLED` interim behavior documented in baseline table.

### Exit Criteria

- Team agrees on V1.1 starting point.
- User confirms Phase 0 completion.

### Phase 0 Baseline Record

Verified **2026-07-21**. Full audit: [docs/audits/post-mvp-v1.1-phase-0-baseline-audit.md](../audits/post-mvp-v1.1-phase-0-baseline-audit.md).

| Item                 | Result |
| -------------------- | ------ |
| Backend tests        | **344 passed**, **11.78s** — `make lint`, `make format-check`, `make typecheck`, `make test-cov` all pass |
| Backend coverage     | **87.99%** on `app/` (≥80% gate) |
| Frontend tests       | **109 passed**, **2.21s** — lint, format, Vitest, build all pass |
| Eval CLI             | **5/5** passed (prompt=2, retrieval=2, e2e=1); report at `backend-python/.eval/eval-report.json` (timestamp 2026-07-21T16:22:49Z) |
| Docker Compose smoke | Pass — `docker compose --profile python up -d --build`; `/api/health` ok (`chat_streaming_enabled: true`); `/api/health/ready` db ok |

#### Phase 0 fixes applied

| Fix | Reason |
| --- | ------ |
| `tests/conftest.py`: `setdefault("CHAT_STREAMING_ENABLED", "true")` | Local `.env` had streaming disabled; 9 streaming tests failed with 503. Test isolation only — product default unchanged (`true` in `config.py`). |

#### Regression vs V1 Phase 13

| Metric | Phase 13 | Phase 0 | Notes |
| ------ | -------- | ------- | ----- |
| Backend tests | 342 | 344 | +2 streaming-toggle tests |
| Backend coverage | 88.25% | 87.99% | Still ≥80% |
| Frontend tests | 106 | 109 | +3 streaming UI/health tests |
| Eval | 5/5 | 5/5 | Pass count unchanged |

---

## Phase 1 - Multi-Provider Tool Calling (V1.1a)

### Objectives

Validate and harden web search tool execution across all four LLM providers on the **non-streaming** chat path. Introduce a provider capability model so callers know which providers support tool calling.

### Design

| Component              | Responsibility                                                                                        |
| ---------------------- | ----------------------------------------------------------------------------------------------------- |
| `ProviderCapabilities` | Full capability dataclass (see [Architecture Principles](#architecture-principles)); Phase 1 implements `supports_streaming` and `supports_tool_calling` per provider |
| Provider adapters      | Harden `complete_chat_with_tools` parsing for Gemini, Groq, Anthropic edge cases                      |
| `ToolChatService`      | Tool loop only — no new orchestration; called by `UnifiedChatService` in Phase 3                      |
| Tool tests             | Per-provider unit tests mirroring `test_openai_tool_calling.py`                                       |

### Tasks

- Add `ProviderCapabilities` in `app/providers/capabilities.py`:
  - Dataclass with all capability fields (`supports_streaming`, `supports_tool_calling`, `supports_json_mode`, `supports_reasoning`, `supports_image_input`, `supports_image_output`, `supports_audio`, `supports_embeddings`)
  - `get_capabilities(provider: ProviderName) -> ProviderCapabilities` — V1.1 sets streaming + tool calling per provider; remaining fields default `False`
  - Document known limitations (e.g. model-specific restrictions) in README
- Harden Gemini, Groq, Anthropic `complete_chat_with_tools`:
  - Normalize tool call IDs, argument JSON parsing, empty tool call lists
  - Map provider-native tool schemas consistently (reuse OpenAI patterns where possible)
- Add provider unit tests:
  - `tests/providers/test_gemini_tool_calling.py`
  - `tests/providers/test_groq_tool_calling.py`
  - `tests/providers/test_anthropic_tool_calling.py`
  - Cover: tool call parsed, direct answer (no tools), malformed arguments
- Extend `ToolChatService` (tool loop only — no orchestration expansion):
  - When request provider lacks tool support and tools would run → return clear `validation_error` or degrade gracefully (document chosen behavior)
  - Ensure tool loop uses `request.provider` resolution (same as `ChatService._resolve_provider`)
- Add integration tests in `test_phase4_chat_tools.py` (or new file):
  - Parameterized or separate cases for each provider with `FakeProvider` / mocked adapters
  - Web search end-to-end on non-streaming `POST /api/chat` for each provider
- Expose capability endpoint or include in existing health/config response (choose one):
  - Option A: extend `GET /api/health` with `capabilities.by_provider` (nested capability object per provider)
  - Option B: new lightweight `GET /api/config/capabilities` (auth not required)
  - Document choice in README
- Update `backend-python/README.md` with multi-provider tool calling notes.

### Success Criteria

- Web search tool loop completes on non-streaming chat for all four providers (mocked in CI).
- Provider-specific tool calling unit tests pass.
- Unsupported provider returns clear error when tools requested (no silent fallback to plain chat without notice).
- Existing OpenAI tool tests still pass.
- MVP regression: streaming still skips tools (unchanged until Phase 4).

### Verification Checklist

- Per-provider tool calling unit tests pass.
- Integration test: tools enabled, non-streaming, each provider invokes web search mock.
- Capability lookup documented and tested.
- `make test-cov` ≥ 80% on `app/`.
- Feature flags off — no behavior change.

### Exit Criteria

- Web search works on non-streaming chat for all four providers.
- User confirms Phase 1 completion.

### Phase 1 Completion Record

Verified **2026-07-21**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **364 passed**, **13.87s** (+20 vs Phase 0 baseline 344) |
| Backend coverage | **86.70%** on `app/` (≥80% gate; −1.29pp vs Phase 0 87.99%, new provider adapter paths) |
| Frontend regression | **113 passed**, lint/format/build pass (no intentional frontend changes) |
| Eval CLI | **5/5** passed; report timestamp 2026-07-21T17:11:09Z |
| Provider adapters | Gemini, Groq, Anthropic `complete_chat_with_tools` hardened; V1 `NotImplementedError` stubs removed |
| Capability endpoint | **Option A** — `GET /api/health` extended with `capabilities.by_provider` |
| Unsupported provider | `ToolChatService` returns **422** `validation_error`: `Tool calling is not supported for provider '<name>'.` |
| Streaming policy | `test_streaming_skips_tools_even_when_enabled` passes unchanged |
| Feature flags off | `TOOLS_ENABLED=false` — standard chat path unchanged (existing tests pass) |
| README | Multi-provider tool calling section added to `backend-python/README.md` |

#### Files created/updated (Phase 1)

| File | Change |
| ---- | ------ |
| `app/providers/capabilities.py` | **Created** — `ProviderCapabilities`, `get_capabilities()`, `capabilities_by_provider()` |
| `app/providers/gemini_provider.py` | Hardened `complete_chat_with_tools` |
| `app/providers/groq_provider.py` | Hardened `complete_chat_with_tools` |
| `app/providers/anthropic_provider.py` | Hardened `complete_chat_with_tools` |
| `app/services/tool_chat_service.py` | Capability check before tool loop |
| `app/routers/health.py` | `capabilities.by_provider` on health response |
| `tests/providers/test_capabilities.py` | **Created** |
| `tests/providers/test_gemini_tool_calling.py` | **Created** |
| `tests/providers/test_groq_tool_calling.py` | **Created** |
| `tests/providers/test_anthropic_tool_calling.py` | **Created** |
| `tests/test_health.py` | Capabilities assertions |
| `tests/test_phase4_chat_tools.py` | Per-provider integration tests |
| `tests/test_tool_chat_service.py` | Unsupported-provider validation test |
| `backend-python/README.md` | Multi-provider tool calling documentation |

---

## Phase 2 - RAG Per-Request Provider (V1.1a)

### Objectives

Allow RAG requests to specify **provider and model** per request (matching chat), instead of relying solely on global `LLM_PROVIDER`.

### Design

| Location          | Change                                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| `RAGAskRequest`   | Add optional `provider: ProviderName`, `model: str`                                                          |
| `RAGService.ask`  | Accept `provider` / `model`; resolve via `ProviderFactory` per call                                          |
| `get_rag_service` | Inject provider factory or resolve provider inside `ask()` — avoid binding single global provider at DI time |
| `/api/rag/ask`    | Pass through new fields                                                                                      |
| Eval runners      | Unchanged default (env provider); optional override in eval config later                                     |

### Tasks

- Extend `app/schemas/rag.py`:
  - Add `provider: ProviderName | None = None`
  - Add `model: str | None = None`
  - Reuse provider/model validation from `ChatRequestSchema` (extract shared validator if needed — YAGNI: duplicate only if trivial)
- Refactor `RAGService.ask`:
  - Add `provider: ProviderName | None`, `model: str | None` parameters
  - Resolve provider/model like `ChatService._resolve_provider` (fallback to settings defaults)
  - Call `ProviderFactory.get_provider(resolved_name, settings)` per request
- Update `app/ai/deps.py` `get_rag_service`:
  - Stop binding a single `LLMProvider` instance at construction **or** pass factory and resolve in `ask()`
- Update `app/routers/rag.py` to forward `provider` / `model`.
- Update `frontend/src/types/rag.ts` and `ragClient.ts` if standalone `/documents` RAG panel should allow provider selection (optional for Phase 2; required before Phase 3 if panel kept).
- Add tests:
  - `test_rag_service.py`: ask with each provider (mocked LLM)
  - `test_rag_api.py`: request body with provider/model; validation errors for missing API keys
  - Regression: omitting provider uses settings default
- Update `.env.example` documentation — RAG no longer tied exclusively to `LLM_PROVIDER`.

### Success Criteria

- `/api/rag/ask` accepts optional `provider` and `model`.
- RAG completion uses the requested provider's adapter.
- Omitting provider/model falls back to settings defaults (backward compatible).
- All existing RAG tests pass with updated wiring.
- Eval CLI still passes.

### Verification Checklist

- Unit tests for per-provider RAG ask (mocked).
- API integration test with provider override.
- Backward compatibility: request without provider still works.
- No change to retrieval/embedding path (still OpenAI embeddings by default).

### Exit Criteria

- RAG supports all four LLM providers per request.
- User confirms Phase 2 completion.

### Phase 2 Completion Record

Verified **2026-07-21**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **380 passed**, **14.73s** (+16 vs Phase 1 baseline 364) |
| Backend coverage | **87.28%** on `app/` (≥80% gate; +0.58pp vs Phase 1 86.70%) |
| Frontend regression | **113 passed**, lint/format/build pass (no frontend changes) |
| Eval CLI | **5/5** passed; report timestamp 2026-07-21T17:39:49Z |
| DI refactor | **Option A** — removed `llm_provider` from `RAGService.__init__`; `ProviderFactory.get_provider(name, settings)` called per request in `_resolve_provider` |
| Provider resolution | Mirrors `ChatService._resolve_provider`: allowed set check, API key validation, settings fallback |
| Missing API key | **422** `validation_error` via `ChatServiceError` (e.g. `ANTHROPIC_API_KEY is not set`) |
| Invalid provider/model | **422** `validation_error` at schema validation (same allowed-model set as chat) |
| Backward compatibility | Requests omitting `provider` / `model` use `LLM_PROVIDER` + default model env vars |
| Retrieval/embedding | Unchanged — OpenAI embeddings via `create_embedding_provider(get_settings())` |
| Phase 1 regression | Multi-provider tool calling, capabilities, streaming tool skip policy unchanged |
| Frontend RAG panel | Deferred to Phase 3; backend contract ready |
| Docs | `.env.example` and `backend-python/README.md` updated with per-request RAG provider selection |

#### Files created/updated (Phase 2)

| File | Change |
| ---- | ------ |
| `app/schemas/rag.py` | Added optional `provider` / `model` with chat-aligned validation |
| `app/ai/rag/service.py` | Per-request `_resolve_provider`; removed constructor-bound LLM |
| `app/ai/deps.py` | `get_rag_service` no longer binds global provider at DI time |
| `app/routers/rag.py` | Forwards `provider` / `model` to `RAGService.ask` |
| `app/ai/evaluation/runners.py` | Patches `ProviderFactory.get_provider` for eval LLM double |
| `tests/test_rag_service.py` | Per-provider unit tests, settings default, missing-key, empty-corpus metadata |
| `tests/test_rag_api.py` | Provider override, backward compat, invalid model combo API tests |
| `tests/test_evaluation_runners.py` | Updated `_rag_service` mock signature |
| `backend-python/.env.example` | RAG per-request provider docs |
| `backend-python/README.md` | RAG provider selection + curl example |

---

## Phase 3 - Unified Chat Integration (V1.1b)

### Objectives

Integrate web search and document-grounded answers into the **main chat** (`POST /api/chat` and frontend `/`) via explicit user toggles, without removing the `/documents` management route.

### Design

#### Request extensions

Add to `ChatRequestSchema`:

| Field            | Type   | Default | Behavior                                                                              |
| ---------------- | ------ | ------- | ------------------------------------------------------------------------------------- |
| `use_web_search` | `bool` | `false` | When `TOOLS_ENABLED` and authenticated: register web search tool for this request     |
| `use_documents`  | `bool` | `false` | When `RAG_ENABLED` and authenticated: pre-retrieve from user's corpus before LLM call |

When both flags off, behavior identical to pre-V1.1 chat.

#### Orchestration — `UnifiedChatService`

Introduce `app/services/unified_chat_service.py` as the canonical chat orchestrator. It composes existing services and follows the [canonical execution pipeline](#canonical-chat-execution-pipeline):

```text
Validate Request
        ↓
Build Conversation Context (system prompt + history)
        ↓
[use_documents?] → Retriever → ContextBuilder → merge into messages
        ↓
[use_web_search?] → register web_search tool → ToolChatService tool loop
        ↓
ChatService.complete_chat (or tool loop terminal completion)
        ↓
Persist Conversation
        ↓
Return Response (+ optional metadata: retrieved_chunks, tools_used)
```

- **Document grounding:** When `use_documents=true`, run retrieval via RAG stack components (`Retriever.retrieve` → `ContextBuilder.build`) and merge into messages via `PromptManager` (reuse `rag/answer.v1.j2` context section or new `chat/document_context.v1.j2` partial). This is a context provider step — not a separate RAG execution path.
- **Web search:** When `use_web_search=true`, register `web_search` for this request and delegate tool loop to `ToolChatService`.
- **Combined toggles:** Document retrieval runs before tool registration (see [Architecture Principles](#architecture-principles)).
- **Guests:** `use_web_search` and `use_documents` → `401` or validation error with message matching V1 guest tool denial pattern.
- **Plain chat:** When both toggles off, router may bypass `UnifiedChatService` and use existing `ChatService` / `ToolChatService` path for zero regression — or route through orchestrator with no-op steps (choose simpler option in implementation; document in Phase 3).
- **Router wiring:** `POST /api/chat` delegates to `UnifiedChatService.execute(..., mode="complete")` when toggles or unified path required; otherwise existing behavior.

#### Response extensions (non-streaming)

Extend `ChatResponseSchema` optionally:

| Field              | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `retrieved_chunks` | Chunk metadata when `use_documents=true`           |
| `tools_used`       | List of tool names invoked (e.g. `["web_search"]`) |

Keep fields optional for backward compatibility.

#### Frontend

- Add toggles to `Composer` or chat settings bar:
  - "Web search" (disabled when guest or `TOOLS_ENABLED=false` or provider lacks capability)
  - "My documents" (disabled when guest or `RAG_ENABLED=false`)
- Pass toggles in `ChatRequest` from `chatClient.ts`.
- Show subtle indicators on assistant messages when tools or retrieval were used (optional metadata from response).
- Document management:
  - Add link/button from chat to `/documents` for upload/manage
  - Or embed compact upload widget in chat sidebar (minimal — link is sufficient for V1.1)
- Remove or de-emphasize standalone RAG ask on `/documents` (keep upload/list/delete; RAG panel optional/hidden with link to chat).

### Tasks

- Add `UnifiedChatService` in `app/services/unified_chat_service.py`:
  - Compose `ChatService`, `ToolChatService`, retriever/context builder (injected)
  - Implement non-streaming `execute()` following canonical pipeline
  - Add `get_unified_chat_service` dependency in `app/routers/chat.py`
- Extend `ChatRequestSchema` and frontend `ChatRequest` type with `use_web_search`, `use_documents`.
- Implement document context provider step (retrieval + message merge) in orchestrator (non-streaming only in this phase).
- Wire `use_web_search` to `ToolChatService` via orchestrator (Phase 1 multi-provider support).
- Extend `ChatResponseSchema` with optional `retrieved_chunks`, `tools_used`.
- Update `app/routers/chat.py` to route through `UnifiedChatService` when either toggle is true (or always, with no-op steps when toggles off).
- Add prompt template for document context injection in chat (if not reusing RAG template directly).
- Frontend: toggles, disabled states, guest/sign-in prompts, API client updates.
- Add backend integration tests:
  - Chat with `use_documents=true` returns grounded answer (fixture doc ingested)
  - Chat with `use_web_search=true` invokes search tool
  - Both toggles together
  - Guest rejected appropriately
  - Flags off → toggles ignored or 503
- Add frontend Vitest tests for toggles and request payload.
- Update README and root docs with unified chat usage.

### Success Criteria

- Authenticated user can enable web search and/or document grounding from main chat (non-streaming).
- `/documents` upload/list/delete still works.
- `/api/rag/ask` still works (standalone/debug).
- Guest users see clear UX when toggles unavailable.
- Existing chat flow unchanged when toggles off.
- Frontend and backend tests pass.

### Verification Checklist

- Integration tests for both toggles and combined usage.
- Guest and feature-flag-off cases covered.
- `/documents` regression tests pass.
- Chat persistence unaffected when toggles used (session messages saved correctly).
- No domain logic added to `app/ai/rag/`.

### Exit Criteria

- Unified non-streaming chat experience works end-to-end.
- User confirms Phase 3 completion.

### Phase 3 Completion Record

Verified **2026-07-21**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **389 passed**, **14.53s** (+9 vs Phase 2 baseline 380) |
| Backend coverage | **86.68%** on `app/` (≥80% gate) |
| Frontend regression | **116 passed**, lint/format/build pass (+3 vs Phase 2 baseline 113) |
| Eval CLI | **5/5** passed |
| Orchestrator routing | **Option A** — route through `UnifiedChatService` when `use_web_search` or `use_documents`; plain `ChatService` when both off |
| Document context merge | **Option A** — `chat/document_context.v1.j2` partial via `PromptManager`; retrieve → build → system message merge |
| Guest/flag-off behavior | Guest + toggle → **200** with `_GUEST_TOOL_DENIED_MESSAGE` (no LLM/retrieval); flags off → toggles **ignored** (no-op plain chat) |
| Tool registration | Per-request `allowed_tool_names={web_search}` on `ToolChatService`; global `TOOLS_ENABLED` gates capability |
| Phase 1 regression | Multi-provider tool calling + capabilities unchanged |
| Phase 2 regression | RAG per-request provider + `/api/rag/ask` unchanged |
| Streaming policy | `test_streaming_skips_tools_even_when_enabled` passes unchanged |
| Docs | `backend-python/README.md` canonical pipeline + toggles; root `README.md` brief V1.1b summary |

#### Files created/updated (Phase 3)

| File | Change |
| ---- | ------ |
| `app/services/unified_chat_service.py` | New orchestrator with non-streaming `execute()` |
| `app/services/tool_chat_service.py` | Per-request `allowed_tool_names`; `tools_used` on response |
| `app/schemas/chat.py` | `use_web_search`, `use_documents`, `retrieved_chunks`, `tools_used` |
| `app/routers/chat.py` | Unified routing + NDJSON progress for `use_web_search` |
| `app/routers/health.py` | `rag_enabled` on health response |
| `app/ai/prompts/chat/document_context.v1.j2` | Document context partial for chat merge |
| `tests/test_unified_chat.py` | Integration + persistence unit tests |
| `tests/test_phase4_chat_tools.py` | Per-request `use_web_search` on tool tests |
| `tests/test_health.py` | Expect `rag_enabled` |
| `frontend/src/components/Composer.tsx` | Web search / My documents toggles |
| `frontend/src/pages/ChatPage.tsx` | Toggle wiring; non-streaming when toggles on |
| `frontend/src/api/chatClient.ts`, `types/chat.ts` | Toggle + metadata fields |
| `frontend/src/hooks/useChatStreamingEnabled.ts` | `ragEnabled`, capabilities |
| `frontend/src/pages/DocumentsPage.tsx` | De-emphasized standalone RAG ask |
| `backend-python/README.md`, root `README.md` | Unified chat documentation |

---

## Phase 4 - Streaming Tool Loop (V1.1c)

### Objectives

Enable web search (and future tools) during **SSE streaming** chat. Extend the event protocol so clients can show tool-in-progress state.

### Design

#### SSE protocol extensions

Add new frame types alongside existing `start`, `delta`, `end`, `error`:

| Event        | Payload                                                | When                                                                                                                        |
| ------------ | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `tool_start` | `{ type, id, tool_name, call_id, timestamp }`          | Before tool handler executes                                                                                                |
| `tool_end`   | `{ type, id, tool_name, call_id, success, timestamp }` | After tool handler completes (no result body — avoid leaking search content in stream metadata; optional truncated summary) |

Tool execution remains **blocking between stream segments**: stream model text until tool call → execute tool → resume streaming next completion. True parallel tool streaming is out of scope.

#### Service changes

Extend **`UnifiedChatService`** with streaming response renderer — same orchestration pipeline as non-streaming; do **not** add `ToolStreamingChatService` or parallel RAG stream paths:

- `execute(..., mode="stream")` runs the canonical pipeline through tool/retrieval steps, then yields SSE frames
- When `use_web_search=true` and streaming: run iterative tool loop within the unified pipeline
- Each iteration: call provider streaming API until tool call detected **or** completion
- On tool call: emit `tool_start` → execute → emit `tool_end` → append tool result to messages → next iteration
- Provider adapters: evaluate need for `stream_chat_with_tools`:
  - **Preferred V1.1 approach:** use non-streaming `complete_chat_with_tools` for tool-call detection iterations, then `stream_chat` for final answer — simpler, two-phase
  - **Alternative:** native streaming tool APIs where available — only if required for UX; document tradeoff
- Cap tool iterations (reuse existing limit from `ToolChatService`).
- Remove or update V1 policy test `test_streaming_skips_tools_even_when_enabled` — replace with positive streaming tool tests (applies when `CHAT_STREAMING_ENABLED=true`; non-stream tool path unchanged when streaming is off).
- `POST /api/chat/stream` delegates to `UnifiedChatService.execute(..., mode="stream")` when toggles require unified path.

#### Frontend

- Extend `ChatChunk` type with `tool_start` / `tool_end` variants.
- Update `useChatStream` to handle new events (show "Searching the web…" indicator).
- Ensure stop/cancel aborts in-flight tool execution where possible.

### Tasks

- Define `ToolStartFrame`, `ToolEndFrame` in `app/schemas/chat.py`.
- Add streaming response renderer to `UnifiedChatService` (document chosen provider strategy).
- Update `POST /api/chat/stream` router to use unified orchestrator when `use_web_search=true` (or when any unified-path toggle is on).
- Add integration tests:
  - Stream with web search → `tool_start` / `tool_end` events emitted
  - Stream without tools → unchanged behavior
  - Stop/cancel during tool execution
  - Iteration limit reached mid-stream
- Frontend: parse new SSE events; UI indicators; tests.
- Update README SSE documentation with new event types.
- Log `tool_calls_total` during streaming (existing metric).

### Success Criteria

- Streaming chat with `use_web_search=true` completes web search and streams final answer.
- Clients receive `tool_start` / `tool_end` events.
- Streaming without tools unchanged.
- Stop/cancel works without leaving orphan tool requests (best effort).
- All four providers supported on streaming tool path (mocked in CI).

### Verification Checklist

- SSE protocol documented with examples.
- Integration tests for streaming + tools.
- Frontend handles new events without breaking existing streams.
- MVP streaming regression tests pass (plain chat stream).

### Exit Criteria

- Streaming web search works on main chat.
- User confirms Phase 4 completion.

### Phase 4 Completion Record

Verified **2026-07-22**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **399 passed**, **18.64s** (+10 vs Phase 3 baseline 389) |
| Backend coverage | **85.93%** on `app/` (≥80% gate) |
| Frontend regression | **120 passed**, lint/format/build pass (+4 vs Phase 3 baseline 116) |
| Eval CLI | **5/5** passed |
| SSE frames | `ToolStartFrame`, `ToolEndFrame` in `app/schemas/chat.py`; documented in `backend-python/README.md` with examples |
| Streaming renderer | **Option B** — `UnifiedChatService.stream_execute(...)` (separate method; same orchestration as non-streaming) |
| Provider strategy | **Two-phase** — `complete_chat_with_tools` for tool iterations, `stream_chat` for final answer |
| Stream router | **Option A** — route through `UnifiedChatService` when `_effective_web_search(request, settings)`; else `ChatService.stream_chat` |
| `use_documents` on stream | **Option B** — backend returns **422** `validation_error`; frontend keeps documents on non-streaming path |
| Guest/flag-off behavior | Guest + `use_web_search` on stream → SSE denial message (same `_GUEST_TOOL_DENIED_MESSAGE`); flags off → toggles ignored |
| Integration tests | Positive streaming tool tests replace `test_streaming_skips_tools_even_when_enabled`; per-provider, iteration limit, cancel, plain stream regression |
| Frontend SSE | `ChatChunk` + `useChatStream` handle `tool_start` / `tool_end`; ChatPage shows "Searching the web…" via `searching_web` waiting variant |
| Phase 3 regression | Non-streaming unified chat + NDJSON progress unchanged |
| Phase 1–2 regression | Multi-provider tools + RAG per-request provider unchanged |
| Docs | `backend-python/README.md` SSE table + streaming policy; root `README.md` brief V1.1c note |

#### Files created/updated (Phase 4)

| File | Change |
| ---- | ------ |
| `app/schemas/chat.py` | `ToolStartFrame`, `ToolEndFrame` |
| `app/services/unified_chat_service.py` | `stream_execute` streaming renderer + tool loop SSE |
| `app/services/chat_service.py` | `format_sse` accepts tool frames |
| `app/routers/chat.py` | Stream route unified when `use_web_search`; 422 for `use_documents` on stream |
| `tests/test_phase4_chat_tools.py` | Positive streaming tool integration tests |
| `frontend/src/types/chat.ts` | `tool_start` / `tool_end` chunk variants |
| `frontend/src/hooks/useChatStream.ts` | Tool lifecycle callbacks |
| `frontend/src/hooks/useChatStream.test.ts` | SSE tool frame parsing test |
| `frontend/src/pages/ChatPage.tsx` | Streaming transport with web search; tool activity indicator |
| `frontend/src/components/Composer.tsx` | Updated toggle UX copy |
| `frontend/src/api/sseParser.test.ts` | Tool frame parse test |
| `backend-python/README.md`, root `README.md` | SSE docs + streaming policy |
| `docs/plans/post-mvp-v1.1-implementation-plan.md` | Phase 4 completion record + baseline |

---

## Phase 5 - Streaming RAG in Chat (V1.1c)

### Objectives

Stream LLM answers when `use_documents=true` on the main chat path. Retrieval and context assembly complete **before** the first `delta` frame.

### Design

Extend the **same `UnifiedChatService` pipeline** with document context provider step before streaming begins:

```text
Validate → Build Context → [Retrieve Documents] → [Register Tools] → stream_chat → Persist
```

- **No mid-stream retrieval** in V1.1 — retrieval latency is pre-stream (acceptable; log `retrieval_latency_ms`).
- Optional: extend `StartFrame` with `retrieved_chunk_count` or emit `retrieval_complete` event — choose one for UI ("Searching your documents…").
- Reuse Phase 3 context-merge logic; streaming differs only in the final response renderer.

### Tasks

- Extend `UnifiedChatService` streaming renderer to run document context provider step when `use_documents=true` (reuse Phase 3 merge logic).
- Stream LLM response via `ChatService.stream_chat` after context injection.
- Optional SSE event `retrieval_complete` or metadata on `start` frame for frontend status.
- Include `retrieved_chunks` in final persistence metadata if chat store supports it (optional — document if deferred).
- Add integration tests:
  - Stream with `use_documents=true` → answer references fixture doc
  - Stream with both `use_documents` and `use_web_search`
  - Empty corpus → graceful streamed message
  - Retrieval failure → error frame
- Frontend: "Searching your documents…" state before first delta.
- Extend `RAGService` with `ask_stream` **only if** standalone `/api/rag/ask` streaming is desired in V1.1:
  - **Default:** chat-only streaming RAG; standalone endpoint remains non-streaming unless explicitly scoped
- Performance: log end-to-end latency; first delta target ≤ 8s soft target (retrieval + TTFT).

### Success Criteria

- Streaming chat with document grounding works end-to-end.
- Combined streaming: documents + web search in one request.
- Empty corpus handled gracefully in stream.
- Standalone `/api/rag/ask` non-streaming still works.
- Eval CLI passes (no regression).

### Verification Checklist

- Integration tests for streaming RAG and combined toggles.
- Frontend streaming UX for retrieval phase.
- Latency logged (retrieval_ms, time_to_first_delta).
- Chat persistence saves completed streamed messages correctly.

### Exit Criteria

- Streaming document-grounded chat works on main chat.
- User confirms Phase 5 completion.

### Phase 5 Completion Record

Verified **2026-07-22**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **403 passed**, **19.18s** (+4 vs Phase 4 baseline 399) |
| Backend coverage | **86.17%** on `app/` (≥80% gate) |
| Frontend regression | **122 passed**, lint/format/build pass (+2 vs Phase 4 baseline 120) |
| Eval CLI | **5/5** passed |
| Retrieval status SSE | **Option A** — `RetrievalCompleteFrame` (`type: "retrieval_complete"`, `chunk_count`) emitted after retrieval, before tool loop or final stream |
| Stream router | **Option A** — route through `UnifiedChatService.stream_execute` when `_effective_web_search` OR `_effective_documents`; else `ChatService.stream_chat` |
| Combined toggle order | retrieval → tool loop → stream final answer (deterministic) |
| `use_documents` on stream | Enabled — pre-stream retrieval, context merge, then streamed answer |
| Empty corpus | Static streamed `EMPTY_CORPUS_MESSAGE` via `_stream_static_content` (no LLM call) |
| Retrieval failure | SSE `error` frame with code `retrieval_error` |
| Latency logging | `retrieval_latency_ms`, `time_to_first_delta_ms` on unified streaming path |
| `retrieved_chunks` persistence | **Deferred** — `_persist_stream_result` does not yet store chunk metadata on stream path |
| `RAGService.ask_stream` | **Deferred** per plan default — standalone `/api/rag/ask` remains non-streaming |
| Integration tests | Positive streaming RAG tests replace 422 test; combined toggles, empty corpus, retrieval failure, guest denial |
| Frontend streaming UX | Document toggle uses streaming transport; "Searching your documents…" via `retrieval_complete` / `streamingRetrievalActive` |
| Phase 4 regression | Streaming web search + `tool_start` / `tool_end` unchanged |
| Phase 3 regression | Non-streaming unified chat + NDJSON progress unchanged |
| Phase 1–2 regression | Multi-provider tools + RAG per-request provider unchanged |
| Docs | `backend-python/README.md` streaming RAG policy + `retrieval_complete` SSE; root `README.md` brief note |

#### Files created/updated (Phase 5)

| File | Change |
| ---- | ------ |
| `app/schemas/chat.py` | `RetrievalCompleteFrame` |
| `app/services/unified_chat_service.py` | Document pre-retrieval in `stream_execute`; latency logging |
| `app/services/chat_service.py` | `SseFrame` union includes `RetrievalCompleteFrame` |
| `app/routers/chat.py` | Removed 422 guard; unified path when documents and/or web search effective |
| `tests/test_unified_chat.py` | Streaming RAG integration tests |
| `tests/test_phase4_chat_tools.py` | Removed 422 test |
| `frontend/src/types/chat.ts` | `retrieval_complete` chunk variant |
| `frontend/src/hooks/useChatStream.ts` | `onRetrievalComplete` callback |
| `frontend/src/hooks/useChatStream.test.ts` | Retrieval SSE parsing test |
| `frontend/src/pages/ChatPage.tsx` | Streaming transport with document toggle; retrieval indicator |
| `frontend/src/pages/ChatPage.sessions.test.tsx` | Streaming document-grounded chat test |
| `frontend/src/components/Composer.tsx` | Removed non-streaming-only document copy |
| `backend-python/README.md`, root `README.md` | Streaming RAG docs |
| `docs/plans/post-mvp-v1.1-implementation-plan.md` | Phase 5 completion record + baseline |

---

## Phase 6 - Final V1.1 Validation

### Objectives

Verify the complete V1.1 platform meets the definition of done through systematic validation.

### Validation Checklist

| Area                  | Verification                                                                           |
| --------------------- | -------------------------------------------------------------------------------------- |
| V1 regression         | Chat, auth, persistence, `/documents` management unchanged when request toggles off and `CHAT_STREAMING_ENABLED=true` (default streaming UX) |
| Provider parity       | Web search tool loop on all four providers (non-streaming + streaming)                 |
| RAG providers         | Per-request provider on `/api/rag/ask` and chat                                        |
| Unified chat          | Toggles work on `/` for authenticated users                                            |
| Streaming protocol    | New SSE events documented and tested                                                   |
| Guest policy          | Guests cannot use tools or document grounding                                          |
| Feature flags         | `RAG_ENABLED` / `TOOLS_ENABLED` / `CHAT_STREAMING_ENABLED` gate behavior               |
| Generic RAG Framework | Still domain-agnostic; no business logic in `app/ai/rag/`                              |
| Chat orchestration    | Single pipeline via `UnifiedChatService`; streaming/non-streaming share orchestration   |
| Evaluation            | `make eval` passes                                                                     |
| Performance           | Stage latencies logged; spot-check soft targets                                        |
| Observability         | Tool and RAG metrics emitted in streaming paths                                        |
| Tests                 | Backend ≥ 80% coverage; frontend Vitest green                                          |
| CI                    | All quality gates green                                                                |
| Documentation         | README, env templates, SSE docs, release summary                                       |

### Tasks

- Run full manual QA script covering validation checklist.
- Run evaluation CLI; compare to V1 baseline.
- Run V1 regression suite (chat, auth, stream, persistence, rate limit, documents API).
- Manual smoke: unified chat with each provider + toggles.
- Docker Compose smoke test.
- Update documentation:
  - `docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md` — mark V1.1 sections
  - `backend-python/README.md` — canonical chat pipeline, SSE events, toggles, capabilities, service responsibilities
  - Root `README.md` — V1.1 capability summary
  - `.env.example` — any new settings
- Create V1.1 release summary at `docs/releases/post-mvp-v1.1-release-summary.md`.
- Record validation results in Phase 6 section below.

### Success Criteria

- Every validation checklist row verified and recorded.
- No P0/P1 issues open for V1.1 scope.
- CI green; Docker smoke test passes.

### Exit Criteria

- V1.1 declared complete per Definition of Done.
- User confirms Phase 6 completion.

### Phase 6 Completion Record

Verified **2026-07-22**.

| Item | Result |
| ---- | ------ |
| Backend quality gates | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Backend tests | **403 passed**, **20.10s**, **86.14%** coverage on `app/` (≥80% gate) |
| Frontend quality gates | **122 passed**; `npm run lint` (1 exhaustive-deps warning), format, build pass |
| Eval CLI | **5/5** passed; timestamp 2026-07-21T23:04:45Z (unchanged pass count vs Phase 5) |
| V1 regression suite | **122** targeted tests pass (auth, persistence, sessions, rate limit, guest quota, documents, unified chat, tools) |
| Provider parity | **8** provider-scoped tool tests pass (mocked) |
| RAG providers | **8** provider-scoped RAG/unified tests pass |
| Streaming protocol | Integration tests assert `retrieval_complete`, `tool_start`, `tool_end` ordering; frontend `useChatStream.test.ts` |
| Guest policy | **3** guest denial tests pass |
| Feature flags | `test_chat_stream_disabled_returns_503`; unified chat flag-off no-op tests pass |
| Generic RAG framework | `rg` audit — no `UnifiedChatService` / toggle references in `app/ai/rag/` |
| Chat orchestration | Single `UnifiedChatService.execute` / `stream_execute`; grep + tests confirm |
| Docker Compose smoke | `docker compose --profile python up --build -d` — health **200**, frontend **200**, SSE `start` frame on stream (provider_error with placeholder keys) |
| Manual QA | Backend/frontend integration tests + Docker API smoke; browser guest UI verified (no toggles visible; login unavailable in compose env) |
| Documentation | Architecture spec V1.1 sections; `backend-python/README.md` observability; root `README.md` V1.1 section; `.env.example` V1.1 comments |
| Release summary | [docs/releases/post-mvp-v1.1-release-summary.md](../releases/post-mvp-v1.1-release-summary.md) created |
| P0/P1 blockers | None open |
| Known deferrals | Standalone RAG streaming; stream `retrieved_chunks` persistence — confirmed non-blocking |

#### Files created/updated (Phase 6)

| File | Change |
| ---- | ------ |
| `docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md` | V1.1 scope, pipeline, toggles, SSE, flags, deferrals |
| `backend-python/README.md` | Test counts; V1.1 observability log fields |
| `README.md` | Post-MVP V1.1 status section |
| `backend-python/.env.example` | V1.1 flag and toggle documentation |
| `docs/releases/post-mvp-v1.1-release-summary.md` | **Created** |
| `docs/plans/post-mvp-v1.1-implementation-plan.md` | Phase 6 completion record + baseline |

---

## Suggested Task Breakdown (PR-Friendly)

1. **PR 1:** Phase 0 audit notes + baseline record updates.
2. **PR 2:** Phase 1 provider capabilities + Gemini/Groq/Anthropic tool tests + hardening.
3. **PR 3:** Phase 1 integration tests + capability endpoint.
4. **PR 4:** Phase 2 RAG per-request provider (schema, service, API, tests).
5. **PR 5:** Phase 3 backend — `UnifiedChatService` + chat request toggles (non-streaming).
6. **PR 6:** Phase 3 frontend — toggles, chat client, `/documents` UX adjustments.
7. **PR 7:** Phase 4 — `UnifiedChatService` streaming renderer + SSE tool events + frontend.
8. **PR 8:** Phase 5 streaming RAG + combined toggle streaming.
9. **PR 9:** Phase 6 validation fixes + documentation + release summary.

---

## Risk Register and Mitigation

| Risk                                                 | Impact | Mitigation                                                                       |
| ---------------------------------------------------- | ------ | -------------------------------------------------------------------------------- |
| Provider tool-calling API differences                | High   | Capability flags; per-provider tests; clear UI disable states                    |
| Streaming + tool loop complexity                     | High   | Phase 4 isolated; consider non-streaming tool detection + streaming final answer |
| Chat regression from orchestration changes           | High   | Toggles default off; `UnifiedChatService` composes existing services; full regression each phase |
| ToolChatService scope creep                          | Medium | New orchestration only in `UnifiedChatService`; `ToolChatService` remains tool loop only           |
| Pre-retrieval adds latency before stream             | Medium | Log metrics; UI "searching documents" state; soft 8s target                      |
| Combined toggles (search + docs) inflate token usage | Medium | Context budget unchanged; document truncation via `ContextBuilder`               |
| Guest confusion with disabled toggles                | Low    | Clear sign-in prompts; match V1 guest tool denial messaging                      |
| Scope creep into V2 (citations, hybrid search)       | Medium | Locked decisions; reject mid-stream retrieval and RAG-as-tool for V1.1           |
| `/documents` RAG panel duplication                   | Low    | De-emphasize panel; primary ask in chat                                          |
| SSE client backward compatibility                    | Medium | New events additive; old clients ignore unknown events                           |

---

## Performance Targets (V1.1 additions)

Inherit V1 soft targets. Add:

| Operation                                    | Target                                 |
| -------------------------------------------- | -------------------------------------- |
| Time to first delta (streaming + documents)  | ≤ 8 seconds (includes retrieval)       |
| Time to first delta (streaming + web search) | ≤ 12 seconds (includes one tool round) |
| Pre-retrieval before stream                  | < 150 ms (same as V1 retrieval target) |

Log `time_to_first_delta_ms`, `retrieval_latency_ms`, and `tool_rounds` on streaming requests.

---

## Observability (V1.1 additions)

| Metric                      | Purpose                               |
| --------------------------- | ------------------------------------- |
| `chat_use_web_search_total` | Chat requests with web search toggle  |
| `chat_use_documents_total`  | Chat requests with document toggle    |
| `stream_tool_rounds`        | Tool iterations per streaming request |
| `time_to_first_delta_ms`    | Streaming UX latency                  |

Emit via structured log fields (same pattern as V1); no Prometheus requirement.

---

## V2 Extension Points (Document Only — Do Not Implement in V1.1)

- RAG as LLM-invoked tool (`search_documents`) for agentic multi-step retrieval
- Mid-stream re-retrieval and dynamic context updates
- Standalone streaming `/api/rag/ask` endpoint
- Citations UI with chunk excerpts and source links
- Hybrid retrieval, reranking, query expansion
- Additional tools and MCP integration
- Async document ingestion queue
- Guest-scoped document corpora

---

## Definition of Done

Post-MVP V1.1 is complete when **all** of the following are true:

- Web search tool loop works on **all four LLM providers** for non-streaming and streaming chat.
- Document-grounded answers available from **main chat** via `use_documents` toggle (non-streaming and streaming).
- Web search available from **main chat** via `use_web_search` toggle (non-streaming and streaming).
- RAG supports **per-request provider/model** on `/api/rag/ask` and chat orchestration.
- SSE protocol extended with tool lifecycle events; frontend handles them.
- Provider capability model (`ProviderCapabilities` dataclass) prevents tool use on unsupported providers with clear errors.
- `UnifiedChatService` orchestrates chat with single pipeline for streaming and non-streaming.
- `/documents` route retained for upload/list/delete; chat is primary ask surface.
- Guests cannot use tools or document grounding; authenticated users only.
- Feature flags (`RAG_ENABLED`, `TOOLS_ENABLED`, `CHAT_STREAMING_ENABLED`) control rollout; request toggles off + feature flags off = V1 behavior (`CHAT_STREAMING_ENABLED=true` preserves default streaming UX).
- Generic RAG Framework remains domain-agnostic.
- Evaluation CLI passes; no regression in V1 test suites.
- Coverage ≥ 80% on `app/`; frontend Vitest green.
- Documentation and V1.1 release summary updated.

---

## Final Acceptance Gate

All items must be true:

- Phases 0–6 completion records filled and verified.
- Unified chat UX functional on `/` for authenticated users.
- Streaming tools and streaming RAG work individually and combined.
- V1 capabilities unchanged when new toggles are off.
- No V2 features implemented beyond documented extension points.
- User confirms V1.1 completion.
