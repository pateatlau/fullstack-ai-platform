# Post-MVP V2 Epic 05 Phase 0 — Baseline Audit

**Epic:** v2-05 Memory System
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-01
**Auditor:** AI Agent
**Status:** Complete (awaiting user confirmation to proceed to Phase 1)

---

## Executive Summary

Baseline audit before implementing Epic 05 (Memory System). All quality gates pass. Epic 04 (Voice) is complete and authorized. No memory subsystem exists yet — clean baseline. Platform is ready for Phase 1 (models, interfaces, migration).

**Key findings:**

- ✅ Backend gates pass: lint, format, typecheck, **1079 tests**, **89.57%** `app/` coverage, eval **5/5**
- ✅ Frontend gates pass: lint, format, **233 tests**, build successful
- ✅ Epic 04 complete: voice package at **93%** coverage; `VOICE_ENABLED` present (default `false`)
- ✅ V1 `SessionSummary` + `_maybe_summarize` operational; `build_context_messages` implemented but **not wired to live chat**
- ✅ pgvector enabled for **document chunks only** (migration `0003`); no memory tables
- ❌ `MEMORY_ENABLED` absent; `app/ai/memory/` does not exist (expected Phase 1+)

---

## 1. Epic 04 Phase 11 Status

**Finding:** Epic 04 Voice Interfaces Phase 11 validation complete; user confirmed; next epic authorized.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-04-voice-interfaces.md` — Phase 11 marked Completed; release summary published
- `app/ai/voice/` — 12 modules (STT, TTS, session, streaming, chat bridge, OpenAI provider)
- `app/routers/voice.py` — WebSocket voice endpoints
- `tests/ai/voice/` — 11 test modules; `tests/test_voice_router.py`
- Baseline from Epic 04: 1076 passed, 89.52%; current run: **1079 passed, 89.57%** (no regression)

**Recommendation:** Epic 05 implementation may proceed.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~836 ms
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 316 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~5.8 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 1079 passed, 0 failed
Coverage: 89.57% on app/ (≥80% required)
Duration: ~145 s
```

**Notable coverage:**

| Module | Coverage |
| ------ | -------- |
| `app/services/chat_service.py` | 90% |
| `app/services/unified_chat_service.py` | 59% (conditional RAG/tools/agent branches) |
| `app/ai/voice/` (aggregate) | 93% |
| `app/ai/rag/` | 90%+ on core paths |
| `app/ai/mcp/` | 93%+ |
| `app/ai/vectorstores/pgvector.py` | 92% |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 5/5 (prompt 2, retrieval 2, e2e 1)
Duration: ~2.8 s
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm run format:check  ✅ PASS
npm test -- --run     ✅ PASS — 39 files, 233 tests
npm run build         ✅ PASS — 479 kB JS bundle
```

No memory UI modules exist (`memoryClient`, `MemorySettingsPage` absent — expected Phase 9).

---

## 4. V1 Session Summary Inventory

| Component | Location | Status |
| --------- | -------- | ------ |
| `SessionSummary` ORM | `app/db/models.py` (`session_summaries` table) | ✅ Exists |
| `SqlChatStore.get_latest_summary` | `app/db/chat.py` | ✅ Exists |
| `SqlChatStore.add_summary` | `app/db/chat.py` | ✅ Exists |
| `SqlChatStore.list_messages_after_seq` | `app/db/chat.py` | ✅ Exists |
| `ChatService._maybe_summarize` | `app/services/chat_service.py` | ✅ Runs post-turn on persisted paths |
| `ChatService.build_context_messages` | `app/services/chat_service.py` | ✅ Implemented; **unit-tested only** |
| Live chat message assembly | `ChatService.complete_chat` / `stream_chat` | Uses **`request.messages`** (client-supplied history) |

**Wiring status:** Summaries are **persisted** via `_maybe_summarize` but **not injected** into LLM context on live chat paths. `build_context_messages()` assembles summary + tail messages deterministically and is tested in `tests/test_summarization_and_linking.py` — Phase 2 will wire it when `MEMORY_ENABLED=true`.

**Flag-off vs flag-on `_maybe_summarize` (documented for Phase 2):**

| Flag | `_maybe_summarize` behaviour |
| ---- | ---------------------------- |
| `MEMORY_ENABLED=false` (current) | Unchanged — existing V1 threshold-gated summarization after each turn |
| `MEMORY_ENABLED=true` (Phase 2+) | Gated via `ConversationSummaryService` wrapper; same `session_summaries` storage |

---

## 5. Chat Routing Split

| Path | Router | Service | When |
| ---- | ------ | ------- | ---- |
| Plain chat | `app/routers/chat.py` | `ChatService` | `use_web_search=false` and `use_documents=false` |
| Unified (RAG / tools) | `app/routers/chat.py` | `UnifiedChatService` | `_needs_unified_path()` → web search or documents |
| Streaming | `POST /api/chat/stream` | Same split via `UnifiedChatService.stream_execute` | SSE frames |
| Voice → chat | `app/ai/voice/chat_bridge.py` | Delegates to chat services | Epic 04 bridge |

**Memory integration target (Phase 8):** Both `ChatService` and `UnifiedChatService` — never routers or agent core directly.

---

## 6. pgvector & Document-Chunk Schema

| Item | Status |
| ---- | ------ |
| pgvector extension | ✅ Migration `0003_pgvector_embeddings.py` — `CREATE EXTENSION IF NOT EXISTS vector` |
| Embedding column | `document_chunks.embedding vector(1536)` with HNSW cosine index |
| Memory tables | ❌ None — no `memory_records`, `user_preferences` migrations |
| Alembic head | `0005_document_chunks_fts.py` (5 migrations total) |

Memory will use **separate tables** from `document_chunks` (Part I invariant: RAG independence).

---

## 7. Memory Subsystem Absence

Confirmed no prior memory implementation:

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/memory/` | ❌ Does not exist |
| `app/routers/memory.py` | ❌ Does not exist |
| `app/schemas/memory.py` | ❌ Does not exist |
| `MEMORY_ENABLED` in config | ❌ Not present |
| `memory_records` / `user_preferences` ORM | ❌ Not in `app/db/models.py` |
| `tests/ai/memory/` | ❌ Does not exist |

---

## 8. Architecture Review

### 8.1 Part I Architectural Invariants — Verified as Preconditions

All invariants are **not yet exercised** (no memory code) but **no conflicts** with existing architecture:

- Orchestration boundary — chat services are the correct hook points; routers delegate only
- RAG independence — separate pgvector tables planned; RAG uses `document_chunks` only
- Session summary reuse — `session_summaries` exists; no parallel summary table needed
- Provider replaceability — `EmbeddingProvider` + `PgVectorStore` patterns exist for reference
- Auth-only memory — guest paths exist in chat; memory will gate on authenticated caller
- Flag-off parity — default behaviour preserved until `MEMORY_ENABLED` added (Phase 1)

### 8.2 Memory Integration Points (Future)

| Phase | Integration point |
| ----- | ----------------- |
| 2 | `ChatService.build_context_messages` activation; `ConversationSummaryService` |
| 3 | `MemoryManager` async extraction (manager API) |
| 7 | `app/routers/memory.py` REST API |
| 8 | `ChatService` + `UnifiedChatService` retrieve/inject/post-process |
| 8 | `MemoryPromptInjector` → `PromptManager`; RAG `PromptBuilder.build(instructions=...)` |
| 9 | Frontend `MemorySettingsPage`, `memoryClient` |

### 8.3 Extension Points to Reuse

| Component | Location |
| --------- | -------- |
| `ChatService`, `_maybe_summarize`, `build_context_messages` | `app/services/chat_service.py` |
| `UnifiedChatService`, `execute()`, `stream_execute()` | `app/services/unified_chat_service.py` |
| `SessionSummary`, chat store summary methods | `app/db/models.py`, `app/db/chat.py` |
| RAG `PromptBuilder` (optional `instructions`) | `app/ai/rag/prompt_builder.py` |
| `PromptManager`, chat templates | `app/ai/prompts/` |
| `EmbeddingProvider`, `create_embedding_provider` | `app/ai/embeddings/`, `app/ai/interfaces/embedding_provider.py` |
| `PgVectorStore` (patterns only) | `app/ai/vectorstores/pgvector.py` |
| Feature flags | `app/core/config.py` |
| DI factories | `app/ai/deps.py` |
| Voice → chat bridge | `app/ai/voice/chat_bridge.py` |

---

## 9. Dependency Verification

| Dependency | Status | Notes |
| ---------- | ------ | ----- |
| PostgreSQL | ✅ | `database_url` default `postgresql+asyncpg://...@localhost:5433/chatbot` |
| pgvector | ✅ | Extension + HNSW index on document chunks |
| Embedding provider | ✅ | `embedding_provider=openai`, `text-embedding-3-small`, dims **1536** |
| `EmbeddingProvider` protocol | ✅ | `app/ai/interfaces/embedding_provider.py` |
| `create_embedding_provider` | ✅ | `app/ai/embeddings/factory.py` |
| Feature flag infrastructure | ✅ | Pattern established (`voice_enabled`, `mcp_enabled`, etc.) |
| DI wiring | ✅ | `app/ai/deps.py` — 117 statements, factories for RAG/tools/voice |
| `MEMORY_ENABLED` | ❌ | Phase 1 deliverable |

---

## 10. Platform Readiness

Verified via comprehensive test suite (no live manual smoke in this audit):

| Capability | Evidence |
| ---------- | -------- |
| Chat (plain + persistence) | `tests/test_chat_persistence.py`, `test_summarization_and_linking.py`, `app/services/chat_service.py` 90% cov |
| Streaming SSE | `tests/test_unified_chat.py`, `app/routers/chat.py` 88% cov |
| RAG | `tests/test_rag_api.py`, `tests/test_rag_service.py`, `tests/ai/rag/` |
| Tools / web search | `tests/test_unified_chat.py`, `app/ai/tools/` |
| MCP | `tests/ai/mcp/` (12 test files) |
| Agent runtime | `tests/ai/agent/`, `AGENT_RUNTIME_ENABLED=false` default |
| Voice | `tests/ai/voice/`, `tests/test_voice_router.py`; package 93% cov |

All subsystems operational at Epic 04 baseline; no blockers for additive memory layer.

---

## 11. Codebase Inventory

### Backend paths (reuse checklist)

| Path | Status |
| ---- | ------ |
| `app/services/chat_service.py` | ✅ 411 stmts, 90% cov |
| `app/services/unified_chat_service.py` | ✅ 390 stmts |
| `app/services/tool_chat_service.py` | ✅ 151 stmts |
| `app/routers/chat.py` | ✅ 165 stmts |
| `app/ai/rag/prompt_builder.py` | ✅ Supports optional `instructions` |
| `app/ai/embeddings/factory.py` | ✅ OpenAI provider |
| `app/ai/vectorstores/pgvector.py` | ✅ Document chunk store |
| `app/ai/deps.py` | ✅ DI pattern for AI components |
| `app/core/config.py` | ✅ Feature flags + embedding settings |
| `app/ai/voice/chat_bridge.py` | ✅ Voice → chat integration |

### Planned create (not present)

| Path | Phase |
| ---- | ----- |
| `app/ai/memory/**` | 1–8 |
| `app/routers/memory.py` | 7 |
| `app/schemas/memory.py` | 7 |
| `alembic/versions/000N_memory_tables.py` | 1 |
| `frontend/src/api/memoryClient.ts` | 9 |
| `frontend/src/pages/MemorySettingsPage.tsx` | 9 |

---

## 12. Implementation Assumptions (from Part I)

| Assumption | Value |
| ---------- | ----- |
| Master flag | `MEMORY_ENABLED=false` (default) |
| Memory provider | `"pgvector"` |
| Project scope v1 | `project_id` ≡ `chat_session_id` |
| Summary storage | Reuse `session_summaries` — no duplicate tables |
| Preferences | `user_preferences` table, no embeddings |
| Durable memory extraction | Async (in-process); sync summaries |
| Auth | Memory API and retrieval — authenticated users only |
| Embedding dimensions | 1536 (locked to existing RAG config) |
| CI | Fake embeddings; no live extraction in unit gates |

---

## 13. Implementation Readiness Checklist

- [x] All required dependencies available (Postgres, pgvector, embeddings)
- [x] Implementation order matches Part II (Phases 1–10)
- [x] No architectural conflicts identified
- [x] No memory implementation already exists
- [x] Extension points identified and documented
- [x] Baseline quality metrics recorded
- [x] Baseline audit published
- [x] No functional code changes in this phase

---

## 14. Git Status

```bash
Branch: feat/v2-epic-05-memory-system-phase-00
Working tree: clean (audit doc only change pending)
Recent: Epic 05 plan merged (#136); voice AudioWorklet migration (#137)
```

---

## 15. Completion Record

| Metric | Result |
| ------ | ------ |
| Lint | ✅ PASS |
| Format check | ✅ PASS |
| Typecheck | ✅ PASS |
| Unit / integration tests | ✅ **1079 passed**, 0 failed |
| Coverage (`app/`) | ✅ **89.57%** |
| Voice package coverage | ✅ **93%** `app/ai/voice/` |
| Evaluation suite | ✅ **5/5** passed |
| Frontend tests | ✅ **233** passed (39 files) |
| Frontend build | ✅ PASS |
| Platform readiness | ✅ Confirmed |
| Memory subsystem | ❌ None (expected) |
| Baseline audit published | ✅ This document |

---

## 16. Recommendations

1. **Proceed to Phase 1** — all gates green; clean memory baseline
2. **Add `MEMORY_ENABLED=false`** in Phase 1 before any behaviour change
3. **Reuse `PgVectorStore` patterns** — separate `memory_records` table, do not extend `document_chunks`
4. **Wire `build_context_messages` in Phase 2 only** — preserve flag-off parity until then
5. **Orchestrate from chat services only** — Phase 8 integration; no router/agent shortcuts

---

## 17. Phase 0 Exit Criteria

- [x] Audit published
- [x] Baseline recorded
- [x] Quality gates passed
- [x] Architecture verified
- [x] No implementation blockers
- [ ] User confirmation to proceed to Phase 1

**Next phase:** Phase 1 — Models, Interfaces & Migration
**Branch:** `feat/v2-epic-05-memory-system-phase-00`

---

**Audit completed:** 2026-08-01T08:30:00+05:30
