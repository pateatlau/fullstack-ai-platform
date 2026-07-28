# Post-MVP V2 Epic 01 — Phase 0 Baseline Audit

**Epic:** v2-01 Agent Framework
**Audit date:** 2026-07-23
**Auditor:** Cursor agent (Phase 0 execution)
**Git commit:** `05c8e59` — `chore: improve local dev with make backend, Postgres on 5433, Vite /api proxy, and durable CORS handling`
**Depends on:** V1.1.1 (confirmed complete)

---

## Executive summary

Phase 0 baseline audit for Epic 01 (Agent Framework). **Backend quality gates pass.** **Frontend lint, format, and build pass**, but **2 Vitest tests fail** (RAG `feature_disabled` handling regressed by recent `request.ts` proxy-error mapping). **No `app/ai/agent/` package exists** — safe to scaffold in Phase 1. Orchestration inventory documented below.

| Gate area | Result |
| --------- | ------ |
| V1.1.1 complete | ✅ Confirmed |
| Backend lint / format / typecheck | ✅ Pass |
| Backend test-cov (≥80%) | ✅ Pass — 458 tests, 87.23% |
| Backend eval CLI | ✅ Pass — 5/5 |
| Frontend lint / format / build | ✅ Pass |
| Frontend Vitest | ⚠️ **165/167 pass** (2 failures) |
| `app/ai/agent/` conflicts | ✅ None |

**Recommendation:** Resolve or explicitly accept the 2 frontend RAG test failures before marking Phase 0 acceptance complete. Backend baseline is green and ready for Phase 1.

---

## V1.1.1 completion confirmation

| Evidence | Location |
| -------- | -------- |
| README status section | `README.md` — "Post-MVP V1.1.1 Status (Complete — 2026-07-22)" |
| Release summary | `docs/releases/post-mvp-v1.1.1-release-summary.md` |
| Validation record | `docs/plans/post-mvp-v1.1.1-implementation-plan.md` (Phase 10 Completion Record) |

All V1.1.1 polish items marked **Done** in README. No open V1.1.1 phases remain.

---

## Quality gate results

### Backend (`backend-python/`)

Commands run from `backend-python/` on 2026-07-23.

| Command | Result | Notes |
| ------- | ------ | ----- |
| `make lint` | ✅ Pass | Ruff — all checks passed |
| `make format-check` | ✅ Pass | 166 files already formatted |
| `make typecheck` | ✅ Pass | Pyright — 0 errors |
| `make test-cov` | ✅ Pass | 458 passed; **87.23%** coverage on `app/` (gate ≥80%) |
| `make eval` | ✅ Pass | 5 passed, 0 failed, 0 skipped |

**Eval detail:**

| Level | Passed | Failed | Skipped |
| ----- | ------ | ------ | ------- |
| prompt | 2 | 0 | 0 |
| retrieval | 2 | 0 | 0 |
| e2e | 1 | 0 | 0 |

Report: `backend-python/.eval/eval-report.json`

### Frontend (`frontend/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `npm run lint` | ✅ Pass | ESLint clean |
| `npm run format:check` | ✅ Pass | Prettier clean |
| `npm test -- --run` | ⚠️ **165 pass / 2 fail** | See failures below |
| `npm run build` | ✅ Pass | `tsc -b && vite build` succeeded |

**Failing tests:**

| Test file | Test name | Root cause (observed) |
| --------- | --------- | --------------------- |
| `src/api/ragClient.test.ts` | maps 503 feature_disabled to RagApiError with friendly message | `parseErrorEnvelope()` treats HTTP 503 as Vite proxy "backend unavailable" before parsing JSON body; `RagApiError.code` is `undefined` |
| `src/components/RagAskPanel.test.tsx` | shows disabled message when RAG returns feature_disabled | Same — UI shows generic backend-unavailable alert instead of RAG disabled status banner |

**Likely origin:** Commit `05c8e59` added `backendUnavailableMessage()` in `frontend/src/api/request.ts`, mapping 503 (among others) to a proxy error string. RAG endpoint legitimately returns 503 with `{ error: { code: "feature_disabled", ... } }` when `RAG_ENABLED=false`.

**V1.1.1 baseline comparison:** Release summary recorded 163/164 Vitest pass (pre-existing `healthClient` URL mismatch). Current suite has 167 tests; the 2 RAG failures appear **new** relative to V1.1.1 sign-off.

---

## Orchestration inventory

Epic 01 Phase 8 references `ToolChatService._run_tool_loop` as parity reference. Epic 01 reuses components listed in Part II § Reuse existing components.

### Chat orchestration chain

```text
UnifiedChatService
  ├── ChatService          (base LLM chat, persistence, SSE streaming)
  ├── ToolChatService      (non-streaming tool loop; wraps ChatService)
  └── RAG components       (Retriever, ContextBuilder — pre-handoff in UnifiedChatService)
```

### File inventory

#### `backend-python/app/services/unified_chat_service.py` (1,042 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Canonical chat orchestrator for unified toggles (`use_web_search`, `use_documents`); stream + non-stream |
| **Key class** | `UnifiedChatService` |
| **Public methods** | `execute()` — non-streaming unified path; `stream_execute()` — SSE streaming unified path |
| **Internal helpers** | `_run_stream_tool_loop`, `_execute_stream_tool_call`, `_stream_provider_answer`, `_stream_static_content`, `_stream_guest_denial`, `_guest_denial_response`, `_empty_corpus_response` |
| **Dependencies** | `ChatService`, `ToolChatService`, `Retriever`, `ContextBuilder`, `PromptManager`, `Settings`, SSE frame models from `app/schemas/chat.py`, `format_sse` / `normalize_chat_error` from `chat_service.py` |
| **Agent integration point (Phase 11)** | Branch on `AGENT_RUNTIME_ENABLED` before delegating to legacy path; RAG stays here before agent handoff |

#### `backend-python/app/services/tool_chat_service.py` (445 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Non-streaming chat with capped LLM tool-calling loop |
| **Key class** | `ToolChatService` |
| **Public methods** | `complete_chat()` |
| **Core loop** | `_run_tool_loop()` — **Phase 8 parity reference** |
| **Tool execution** | `_execute_tool_call()` → `ToolExecutor.execute()` |
| **Constants** | `_TOOL_ITERATION_LIMIT_MESSAGE`, `_GUEST_TOOL_DENIED_MESSAGE` |
| **Exports** | `ChatActivityCallback`, `_assistant_tool_call_message()` (used by UnifiedChatService streaming) |
| **Default** | `max_tool_iterations=3` |

#### `backend-python/app/services/chat_service.py` (1,320 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Base chat orchestration — provider calls, persistence, quota, usage, SSE streaming |
| **Key class** | `ChatService` |
| **Protocols** | `ChatStore`, `UsageStore`, `QuotaChecker`, `ClosableAsyncIterator` |
| **Error types** | `ChatServiceError`, `ProviderTimeoutError`, `ProviderRateLimitedError`, `ProviderError`, `EmptyProviderResponseError`, `SessionNotFoundError`, `ProviderNotAllowedError`, `NewChatForbiddenError`, `DbUnavailableError` |
| **Shared utilities** | `normalize_chat_error()`, `format_sse()` — **reuse in Phase 11 adapter only** (not in agent core) |
| **Public methods** | `complete_chat()`, `stream_chat()`, `prepare_stream()`, session CRUD, `build_context_messages()`, guest quota helpers |
| **Coverage** | 90% (411 stmts, 41 miss) |

#### `backend-python/app/ai/tools/executor.py` (184 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Tool lifecycle: registry → validation → auth → execution → normalization |
| **Key class** | `ToolExecutor` |
| **Public methods** | `execute(call, context) → ToolResult` |
| **Pipeline** | Registry lookup → `ToolValidator.validate()` → `ToolAuthorizer.authorize()` → handler `execute()` with timeout → `_finalize()` with latency metadata |
| **Agent reuse** | Phase 7 wraps this exclusively — no duplicate validation/auth |
| **Coverage** | 96% |

#### `backend-python/app/providers/base.py` (94 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Provider-agnostic LLM contract (Protocol-only dependency for agent core) |
| **Key types** | `ProviderUsage`, `ProviderCompletion`, `ProviderToolCall`, `ProviderToolCompletion`, `ProviderChunk`, `ChatMessageInput` |
| **Protocol** | `LLMProvider` — `stream_chat()`, `complete_chat()`, `complete_chat_with_tools()` |
| **Coverage** | 100% |

#### `backend-python/app/schemas/chat.py` (223 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Chat request/response schemas and SSE frame models |
| **Request/response** | `ChatRequestSchema` (incl. `use_web_search`, `use_documents`), `ChatResponseSchema`, `ChatMessageSchema` |
| **SSE frames** | `StartFrame`, `DeltaFrame`, `EndFrame`, `ErrorFrame`, `ToolStartFrame`, `ToolEndFrame`, `RetrievalCompleteFrame` |
| **NDJSON frames** | `ChatActivityFrame`, `ChatCompleteFrame` |
| **Agent Phase 5/11** | SSE frame names must match for `sse_frame_from_agent_event()` and chat adapter |
| **Coverage** | 97% |

#### `backend-python/app/core/retry.py` (73 lines)

| Item | Detail |
| ---- | ------ |
| **Role** | Shared async retry utility for external HTTP/API calls |
| **Exports** | `retry_async()`, `is_retryable_exception()`, `is_retryable_http_status()` |
| **Defaults** | `DEFAULT_MAX_ATTEMPTS=3`, `DEFAULT_BASE_DELAY_SECONDS=1.0` |
| **Retryable** | 429/503 HTTP, timeout, connection, network errors |
| **Agent Phase 4** | Agent retry wraps this — no duplicate backoff |
| **Coverage** | 91% |

---

## Agent package conflict check

| Check | Result |
| ----- | ------ |
| `app/ai/agent/` directory | **Does not exist** |
| `tests/ai/agent/` directory | **Does not exist** |
| `AGENT_RUNTIME_ENABLED` in config | **Not present** (expected — Phase 1) |
| Code references to `app.ai.agent` | **None found** |

No naming conflicts or partial implementations detected. Phase 1 scaffold is clear.

---

## Baseline metrics vs epic plan

Epic plan baseline (post-V1.1.1, 2026-07-22) vs this audit (2026-07-23):

| Metric | Epic plan baseline | This audit | Delta |
| ------ | ------------------ | ---------- | ----- |
| Backend tests | 453 passed | **458 passed** | +5 |
| Backend `app/` coverage | 87.14% | **87.23%** | +0.09pp |
| Frontend tests | (not recorded in epic) | **165/167 pass** | 2 fail |
| Eval CLI | (not recorded in epic) | **5/5 pass** | — |
| Agent code | None | None | — |

Coverage delta likely reflects CORS module and related tests added in `05c8e59`.

---

## Phase 0 acceptance checklist

| Criterion | Status |
| --------- | ------ |
| All quality gates pass | ⚠️ Backend ✅; frontend tests ❌ (2 failures) |
| Orchestration inventory documented with file paths | ✅ |
| No repository code changes | ✅ (audit doc only) |
| Audit doc published | ✅ |
| Baseline recorded | ✅ (completion record below) |
| User confirmed Phase 0 | ⏳ Pending |

---

## Completion record

| Metric | Result |
| ------ | ------ |
| Backend tests / coverage | **458 passed**, **87.23%** `app/` |
| Frontend tests | **165 passed / 2 failed** (167 total) |
| Eval CLI | **5 passed**, 0 failed, 0 skipped |
| Git commit | `05c8e59` |
| Agent code present | No |
| Frontend lint / format / build | Pass |

---

## Open items for user

1. **Frontend RAG test failures** — Accept as known baseline regression from `05c8e59`, or fix before Phase 1?
2. **Phase 0 sign-off** — Confirm audit is sufficient to proceed to Phase 1 (Scaffold, Models, Interfaces).
