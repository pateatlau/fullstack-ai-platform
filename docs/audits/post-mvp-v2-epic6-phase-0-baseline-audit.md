# Post-MVP V2 Epic 06 Phase 0 — Baseline Audit

**Epic:** v2-06 Workflow Engine
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-04
**Auditor:** AI Agent
**Status:** Complete

---

## Executive Summary

Baseline audit before implementing Epic 06 (Workflow Engine). All quality gates pass. Epic 05 (Memory System) Phase 10 is complete and authorized. No workflow subsystem exists yet — clean baseline. Platform is ready for Phase 1 (models, interfaces, migration).

**Key findings:**

- ✅ Backend gates pass: lint, format, typecheck, **1305 tests**, **89.66%** `app/` coverage, eval **5/5**
- ✅ Frontend gates pass: lint, format, **251 tests** (41 files), build successful
- ✅ Epic 05 complete: `app/ai/memory/` (22 modules, 23 test files); `MEMORY_ENABLED` present (default `false`)
- ✅ All architectural dependencies verified: ToolExecutor, DefaultAgent, LLMProvider, PromptManager, retry, DI, feature flags
- ❌ `WORKFLOW_ENGINE_ENABLED` absent; `app/ai/workflow/` does not exist (expected Phase 1+)

---

## 1. Epic 05 Phase 10 Status

**Finding:** Epic 05 Memory System Phase 10 validation complete; release summary published; next epic authorized.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-05-memory-system.md` — Phases 0–10 marked Completed
- `docs/releases/post-mvp-v2-epic5-release-summary.md` — published 2026-08-04
- `app/ai/memory/` — 22 modules (manager, semantic retriever, lifecycle, pgvector provider, etc.)
- `app/routers/memory.py` — authenticated Memory REST API (route-level `503` when flag off)
- `tests/ai/memory/` — 23 test modules; `tests/test_memory_router.py`
- Baseline from Epic 05 Phase 10: 1305 passed, 89.68%; current run: **1305 passed, 89.66%** (no regression)

**Recommendation:** Epic 06 implementation may proceed.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~2.5 s
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 367 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~25 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 1305 passed, 0 failed
Coverage: 89.66% on app/ (≥80% required)
Duration: ~218 s
```

**Notable coverage:**

| Module | Coverage |
| ------ | -------- |
| `app/services/chat_service.py` | 93% |
| `app/services/unified_chat_service.py` | 60% (conditional RAG/tools/agent/memory branches) |
| `app/ai/memory/` (aggregate) | High — 23 test modules |
| `app/ai/voice/` (aggregate) | 93% (Epic 04 baseline) |
| `app/ai/rag/` | 90%+ on core paths |
| `app/ai/mcp/` | 93%+ |
| `app/ai/tools/` | ToolExecutor, registry, authorizer tested |
| `app/ai/agent/` | 12 test modules |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 5/5 (prompt 2, retrieval 2, e2e 1)
Duration: ~14 s
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm run format:check  ✅ PASS
npm test -- --run     ✅ PASS — 41 files, 251 tests
npm run build         ✅ PASS — 496 kB JS bundle
```

No workflow UI modules exist (`workflowClient`, `WorkflowsPage` absent — expected Phase 11).

---

## 4. Tool Platform Inventory

| Component | Location | Status |
| --------- | -------- | ------ |
| `ToolExecutor` | `app/ai/tools/executor.py` | ✅ Exists |
| `ToolAuthorizer` | `app/ai/tools/authorizer.py` | ✅ Exists (guest denial enforced) |
| `ToolRegistry` | `app/ai/tools/registry.py` | ✅ Exists |
| `ToolValidator` | `app/ai/tools/validator.py` | ✅ Exists |
| Tool registration | `app/ai/tools/registration.py` | ✅ Web search + MCP conditional registration |
| Production tools | `app/ai/tools/implementations/web_search.py` | ✅ Registered when `tools_enabled` |
| DI wiring | `app/ai/deps.py` → `get_tool_executor()` | ✅ Exists |

**Workflow integration target (Phase 10):** Register `WorkflowExecutionTool` in `registration.py` guarded by `settings.workflow_engine_enabled`.

---

## 5. Agent Framework Inventory

| Component | Location | Status |
| --------- | -------- | ------ |
| `DefaultAgent` | `app/ai/agent/runtime/default_agent.py` | ✅ Exists |
| `AgentRequest` | `app/ai/agent/models/request.py` | ✅ Exists |
| `AgentResponse` | `app/ai/agent/models/response.py` | ✅ Exists |
| Agent factory | `app/ai/agent/runtime/factory.py` | ✅ `create_default_agent()` |
| DI wiring | `app/ai/deps.py` → `get_agent_runtime()` | ✅ Exists |
| `AGENT_RUNTIME_ENABLED` | `app/core/config.py` | ✅ Default `false` |
| Tests | `tests/ai/agent/` | ✅ 12 test modules |

**Workflow integration target (Phase 6):** `AgentNodeExecutor` delegates to `DefaultAgent`; fails at run time if `AGENT_RUNTIME_ENABLED=false`.

---

## 6. LLM Provider & Prompt Inventory

| Component | Location | Status |
| --------- | -------- | ------ |
| `LLMProvider` (Protocol) | `app/providers/base.py` | ✅ Exists |
| `ProviderFactory` | `app/providers/factory.py` | ✅ Exists |
| `PromptManager` | `app/ai/prompts/manager.py` | ✅ Exists |
| DI wiring | `app/ai/deps.py` → `get_prompt_manager()` | ✅ Process-wide singleton |

**Workflow integration target (Phase 6):** `LLMNodeExecutor` uses `PromptManager` + `ProviderFactory`; prompt templates under `app/ai/prompts/workflow/` (Phase 6).

---

## 7. Memory Subsystem (Epic 05 — Predecessor)

| Item | Status |
| ---- | ------ |
| `MEMORY_ENABLED` | ✅ Present; default `false` |
| `app/ai/memory/` | ✅ 22 modules |
| Migration `0006_memory_tables.py` | ✅ Applied (Alembic head) |
| Memory REST API | ✅ `app/routers/memory.py` |
| Health field | ✅ `memory_enabled` in `GET /api/health` |
| Chat integration | ✅ `ChatService` + `UnifiedChatService` when flag on |
| Frontend | ✅ `MemorySettingsPage`, `memoryClient.ts` |

Memory remains operational; no conflicts with planned workflow tables (`workflow_definitions`, `workflow_runs`, `workflow_node_executions` — independent of `memory_records`).

---

## 8. Workflow Subsystem Absence

Confirmed no prior workflow implementation:

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/workflow/` | ❌ Does not exist |
| `app/routers/workflows.py` | ❌ Does not exist |
| `app/schemas/workflow.py` | ❌ Does not exist |
| `app/ai/tools/implementations/workflow_tool.py` | ❌ Does not exist |
| `WORKFLOW_ENGINE_ENABLED` in config | ❌ Not present |
| Workflow ORM models | ❌ Not in `app/db/models.py` |
| `alembic/versions/0007_workflow_tables.py` | ❌ Does not exist |
| `tests/ai/workflow/` | ❌ Does not exist |
| Frontend workflow modules | ❌ None |

---

## 9. Architecture Review

### 9.1 Part I Architectural Invariants — Verified as Preconditions

All invariants are **not yet exercised** (no workflow code) but **no conflicts** with existing architecture:

| Invariant | Precondition status |
| --------- | ------------------- |
| Orchestration boundary — `WorkflowManager` only | ✅ No bypass paths exist yet |
| No chat pipeline coupling | ✅ `ChatService`/`UnifiedChatService` have no workflow hooks |
| Reuse ToolExecutor / DefaultAgent / LLMProvider | ✅ All abstractions present and tested |
| Deterministic conditions — no `eval()`/`exec()` | ✅ N/A until Phase 4 |
| Checkpoint-per-transition | ✅ N/A until Phase 3; `WorkflowStore` pattern mirrors `MemoryProvider` |
| Idempotent resume | ✅ N/A until Phase 7–8 |
| Definition immutability post-run | ✅ N/A until Phase 2 |
| Provider replaceability — `WorkflowStore` Protocol | ✅ Pattern established by `MemoryProvider` |
| Auth-only workflows | ✅ `get_current_caller` + guest tool denial in place |
| Flag-off parity | ✅ Default behaviour preserved until `WORKFLOW_ENGINE_ENABLED` added (Phase 1) |
| Public APIs stable after Phase 1 | ✅ No workflow public APIs yet |

### 9.2 Workflow Integration Points (Future)

| Phase | Integration point |
| ----- | ----------------- |
| 1 | `app/ai/workflow/` package, `WorkflowStore`, migration `0007`, `WORKFLOW_ENGINE_ENABLED`, DI |
| 2 | `GraphValidator`, definition CRUD via `WorkflowManager` |
| 3 | `WorkflowExecutor`, `TaskNodeExecutor` → `ToolExecutor` |
| 4 | `ConditionEvaluator`, `RouterNodeExecutor` |
| 5 | `ForkNodeExecutor` / `JoinNodeExecutor` (asyncio) |
| 6 | `LLMNodeExecutor`, `AgentNodeExecutor` |
| 7 | `ApprovalNodeExecutor`, pause/resume |
| 8 | `RetryPolicy` → `app/core/retry.py`, crash recovery |
| 9 | `app/routers/workflows.py`, health `workflow_engine_enabled` |
| 10 | `WorkflowExecutionTool` → `registration.py` (tool-only chat surface) |
| 11 | Frontend `WorkflowsPage`, `workflowClient.ts` |

### 9.3 Extension Points to Reuse

| Component | Location |
| --------- | -------- |
| `ToolExecutor`, `ToolAuthorizer`, `ToolRegistry` | `app/ai/tools/` |
| `DefaultAgent`, `AgentRequest`/`AgentResponse` | `app/ai/agent/` |
| `LLMProvider`, `ProviderFactory` | `app/providers/` |
| `PromptManager` | `app/ai/prompts/` |
| `retry_async`, `is_retryable_exception` | `app/core/retry.py` |
| `MemoryProvider` / `MemoryManager` pattern | `app/ai/memory/` (reference only — separate domain) |
| Feature flags | `app/core/config.py` |
| DI factories | `app/ai/deps.py` |
| Tool registration | `app/ai/tools/registration.py` |
| `get_current_caller`, `CallerContext` | `app/core/caller.py` |
| Health endpoint pattern | `app/routers/health.py` (`memory_enabled` precedent) |

---

## 10. Dependency Verification

| Dependency | Status | Notes |
| ---------- | ------ | ----- |
| Epic 05 Memory (predecessor) | ✅ | All phases complete; stable chat/memory pipeline |
| PostgreSQL | ✅ | `database_url` default `postgresql+asyncpg://...@localhost:5433/chatbot` |
| Alembic migrations | ✅ | Head: `0006_memory_tables.py` (6 migrations); next: `0007_workflow_tables` |
| `app/core/retry.py` | ✅ | `retry_async`, `is_retryable_exception` available |
| `EmbeddingProvider` / pgvector | ✅ | Operational (RAG + Memory); workflow tables independent |
| `ProviderFactory` / `LLMProvider` | ✅ | Multi-provider support (OpenAI, Gemini, Groq, Anthropic) |
| Feature flag infrastructure | ✅ | Pattern: `agent_runtime_enabled`, `memory_enabled`, `voice_enabled`, `mcp_enabled` |
| DI wiring | `app/ai/deps.py` | ✅ ~517 lines; factories for agent, tools, memory, voice, RAG |
| `WORKFLOW_ENGINE_ENABLED` | ❌ | Phase 1 deliverable |

---

## 11. Platform Readiness

Verified via comprehensive test suite (no live manual smoke in this audit):

| Capability | Evidence |
| ---------- | -------- |
| Chat (plain + persistence) | `tests/test_chat_persistence.py`, `test_summarization_and_linking.py`, `test_chat_service_memory.py` |
| Streaming SSE | `tests/test_unified_chat.py`, `app/routers/chat.py` 88% cov |
| RAG | `tests/test_rag_api.py`, `tests/test_rag_service.py`, `tests/ai/rag/` |
| Tools / web search | `tests/test_unified_chat.py`, `app/ai/tools/` |
| MCP | `tests/ai/mcp/` (12 test files) |
| Agent runtime | `tests/ai/agent/` (12 test files); `AGENT_RUNTIME_ENABLED=false` default |
| Memory | `tests/ai/memory/` (23 test files), `tests/test_memory_router.py` |
| Voice | `tests/ai/voice/` (11 test files), `tests/test_voice_router.py` |

All subsystems operational at Epic 05 baseline; no blockers for additive workflow layer.

---

## 12. Codebase Inventory

### Backend paths (reuse checklist)

| Path | Status |
| ---- | ------ |
| `app/services/chat_service.py` | ✅ 464 stmts, 93% cov |
| `app/services/unified_chat_service.py` | ✅ 397 stmts |
| `app/services/tool_chat_service.py` | ✅ 151 stmts |
| `app/routers/chat.py` | ✅ 167 stmts |
| `app/ai/tools/executor.py` | ✅ Task node target |
| `app/ai/tools/registration.py` | ✅ WorkflowExecutionTool registration target (Phase 10) |
| `app/ai/agent/runtime/default_agent.py` | ✅ Agent node target |
| `app/providers/factory.py` | ✅ LLM node target |
| `app/ai/prompts/manager.py` | ✅ LLM node prompt rendering |
| `app/core/retry.py` | ✅ Node retry wrapper target (Phase 8) |
| `app/ai/memory/` | ✅ WorkflowStore pattern reference |
| `app/ai/deps.py` | ✅ DI pattern for workflow factories |
| `app/core/config.py` | ✅ Feature flags + workflow config (Phase 1) |
| `app/core/caller.py` | ✅ Owner-scoped REST API auth |

### Alembic migrations

| Migration | Purpose |
| --------- | ------- |
| `0001_init_chat_persistence.py` | Chat sessions, messages, users |
| `0002_documents_and_chunks.py` | Document storage |
| `0003_pgvector_embeddings.py` | pgvector extension |
| `0004_upload_quota_counters.py` | Upload quotas |
| `0005_document_chunks_fts.py` | Full-text search |
| `0006_memory_tables.py` | Memory records, preferences |
| `0007_workflow_tables.py` | **Planned Phase 1** |

### Planned create (not present)

| Path | Phase |
| ---- | ----- |
| `app/ai/workflow/**` | 1–8 |
| `app/routers/workflows.py` | 9 |
| `app/schemas/workflow.py` | 9 |
| `app/ai/tools/implementations/workflow_tool.py` | 10 |
| `app/ai/prompts/workflow/**` | 6 |
| `alembic/versions/0007_workflow_tables.py` | 1 |
| `frontend/src/api/workflowClient.ts` | 11 |
| `frontend/src/pages/WorkflowsPage.tsx` | 11 |

---

## 13. Implementation Assumptions (from Part I)

| Assumption | Value |
| ---------- | ----- |
| Master flag | `WORKFLOW_ENGINE_ENABLED=false` (default) |
| Storage | PostgreSQL only (`workflow_definitions`, `workflow_runs`, `workflow_node_executions`) |
| Execution model | Single-process in-process `asyncio`; checkpoint after every node transition |
| Graph model | Directed acyclic graph; cycles rejected at validation |
| Chat integration | Tool-only via `WorkflowExecutionTool`; **no** `ChatService`/`UnifiedChatService` hooks |
| Agent nodes | Require `AGENT_RUNTIME_ENABLED=true` at run time |
| Auth | Workflow definitions and runs owner-scoped; authenticated users only |
| Approval timeout | `workflow_approval_timeout_hours=0` (indefinite) |
| Alembic next | `0007_workflow_tables.py` |
| CI | Fakes for unit/integration gates; no live workflow execution in CI by default |

---

## 14. Implementation Readiness Checklist

- [x] Epic 05 Phase 10 complete / authorized for Epic 06
- [x] All required dependencies available (Postgres, ToolExecutor, Agent, LLM, retry, DI)
- [x] Implementation order matches Part II (Phases 1–12)
- [x] No architectural conflicts identified
- [x] No workflow implementation already exists
- [x] Extension points identified and documented
- [x] Baseline quality metrics recorded
- [x] Baseline audit published
- [x] No functional code changes in this phase

---

## 15. Git Status

```bash
Branch: feat/v2-epic-06-workflow-engine-phase-00
Working tree: audit doc + epic plan (no code changes)
Recent: Epic 05 Phase 10 release (#148); Memory settings UI (#147); chat integration (#146)
```

---

## 16. Completion Record

| Metric | Result |
| ------ | ------ |
| Lint | ✅ PASS |
| Format check | ✅ PASS |
| Typecheck | ✅ PASS |
| Unit / integration tests | ✅ **1305 passed**, 0 failed |
| Coverage (`app/`) | ✅ **89.66%** |
| Evaluation suite | ✅ **5/5** passed |
| Frontend tests | ✅ **251** passed (41 files) |
| Frontend build | ✅ PASS |
| Platform readiness | ✅ Confirmed |
| Workflow subsystem | ❌ None (expected) |
| Baseline audit published | ✅ This document |

---

## 17. Recommendations

1. **Proceed to Phase 1** — all gates green; clean workflow baseline
2. **Add `WORKFLOW_ENGINE_ENABLED=false`** in Phase 1 before any behaviour change
3. **Mirror `MemoryProvider`/`WorkflowStore` pattern** — separate workflow tables; do not extend memory or RAG tables
4. **Keep chat decoupled** — register `WorkflowExecutionTool` only in Phase 10; never wire into chat services
5. **Reuse platform abstractions** — Task nodes via `ToolExecutor`; Agent nodes via `DefaultAgent`; retry via `app/core/retry.py`

---

## 18. Phase 0 Exit Criteria

- [x] Audit published
- [x] Baseline recorded
- [x] Quality gates passed
- [x] Architecture verified
- [x] No implementation blockers
- [ ] User confirmation to proceed to Phase 1

**Next phase:** Phase 1 — Models, Interfaces & Migration
**Branch:** `feat/v2-epic-06-workflow-engine-phase-00`

---

**Audit completed:** 2026-08-04T21:30:00+05:30
