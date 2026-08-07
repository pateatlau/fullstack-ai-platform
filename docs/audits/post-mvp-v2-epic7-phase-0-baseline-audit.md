# Post-MVP V2 Epic 07 Phase 0 — Baseline Audit

**Epic:** v2-07 Observability & Evaluation
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-07
**Auditor:** AI Agent
**Status:** Complete

---

## Executive Summary

Baseline audit before implementing Epic 07 (Observability & Evaluation). All quality gates pass. Epic 06 (Workflow Engine) Phase 12 is complete and release summary is published. No observability subsystem exists yet — clean baseline. Platform is ready for Phase 1 (tracing & metrics foundation).

**Key findings:**

- ✅ Backend gates pass: lint, format, typecheck, **1551 tests**, **89.05%** `app/` coverage, eval **5/5**
- ✅ Frontend gates pass: lint, **268 tests** (43 files), build successful
- ✅ Epic 06 complete: `app/ai/workflow/` (33 modules, 23 test files); `WORKFLOW_ENGINE_ENABLED` present (default `false`)
- ✅ All architectural dependencies verified: logging, correlation middleware, usage store, providers, evaluation CLI, DI, feature flags
- ❌ `OBSERVABILITY_ENABLED` absent; `app/ai/observability/` does not exist (expected Phase 1+)
- ❌ OpenTelemetry SDK packages not yet in `pyproject.toml` (Phase 1 deliverable)
- ⚠️ Epic 06 release doc lists "User authorizes Epic 07" unchecked — user has requested Phase 0 implementation

---

## 1. Epic 06 Phase 12 Status

**Finding:** Epic 06 Workflow Engine Phase 12 validation complete; release summary published.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-06-workflow-engine.md` — Phases 0–12 marked Completed
- `docs/releases/post-mvp-v2-epic6-release-summary.md` — published 2026-08-05
- `app/ai/workflow/` — 33 modules (manager, executor, nodes, postgres store, retry, etc.)
- `app/routers/workflows.py` — authenticated Workflow REST API (route-level `503` when flag off)
- `tests/ai/workflow/` — 23 test modules; workflow suite **241 passed**
- Baseline from Epic 06 Phase 12: 1551 passed, 89.05%; current run: **1551 passed, 89.05%** (no regression)

**Recommendation:** Epic 07 implementation may proceed.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~1.2 s
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 429 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~7.5 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 1551 passed, 0 failed
Coverage: 89.05% on app/ (≥80% required)
Duration: ~178 s
```

**Notable coverage:**

| Module | Coverage |
| ------ | -------- |
| `app/services/chat_service.py` | 93% |
| `app/services/unified_chat_service.py` | 60% (conditional RAG/tools/agent/memory branches) |
| `app/ai/workflow/` (aggregate) | High — 23 test modules, 241 tests |
| `app/ai/memory/` (aggregate) | High — 23 test modules |
| `app/ai/voice/` (aggregate) | 83%+ on router; 11 test modules |
| `app/ai/rag/` | 90%+ on core paths |
| `app/ai/mcp/` | 93%+ |
| `app/ai/tools/` | ToolExecutor, registry, authorizer tested |
| `app/ai/agent/` | 13 test modules |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 5/5 (prompt 2, retrieval 2, e2e 1)
Duration: ~3.5 s
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm test -- --run     ✅ PASS — 43 files, 268 tests
npm run build         ✅ PASS — 514 kB JS bundle
```

No observability UI modules exist (`observabilityClient`, `ObservabilityPage` absent — expected Phase 9).

---

## 4. Logging & Correlation Inventory

| Component | Location | Status | Epic 07 role |
| --------- | -------- | ------ | ------------ |
| `StructuredLogger`, `sanitize_value`, `bind_context`, `clear_context` | `app/core/logging.py` | ✅ Exists | Span/metric attribute redaction; trace/span-id log correlation (Phase 1) |
| `correlation_id_middleware`, `get_request_id`, `REQUEST_ID_HEADER` | `app/middleware/correlation_id.py` | ✅ Exists | Extend to bind `trace_id`/`span_id` when enabled (Phase 1) |
| `LogContext` TypedDict | `app/core/logging.py` | ✅ Exists | Will gain optional `trace_id`/`span_id` keys in log context |

Current middleware binds only `request_id`; no OTel trace context yet.

---

## 5. Usage & Cost Persistence Inventory

| Component | Location | Status | Epic 07 role |
| --------- | -------- | ------ | ------------ |
| `SqlUsageStore.record()` | `app/db/usage.py` | ✅ Exists | Extend with `cost_usd`/`pricing_version` (Phase 5) |
| `UsageEvent` ORM | `app/db/models.py` | ✅ Exists | Add nullable `cost_usd`, `pricing_version` columns (Phase 5) |
| `ProviderUsage` | `app/providers/base.py` | ✅ Exists | Source for `CostCalculator` (Phase 5) |
| Usage write call sites | `app/services/chat_service.py` | ✅ 3× `record()` | Cost computed at write time when enabled |
| DI wiring | `app/db/deps.py` → `get_usage_store()` | ✅ Exists | Unchanged pattern |

**Current `usage_events` schema (no cost columns yet):**

| Column | Type |
| ------ | ---- |
| `id`, `session_id`, `user_id`, `guest_id`, `message_id` | UUID / FK |
| `kind`, `provider`, `model`, `token_source` | text |
| `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms` | int nullable |
| `request_id` | text nullable |
| `created_at` | timestamptz |

---

## 6. Evaluation Framework Inventory

| Component | Location | Status |
| --------- | -------- | ------ |
| `EvalLevel` | `app/ai/evaluation/datasets.py` | ✅ `Literal["prompt", "retrieval", "e2e"]` |
| `EvalCase`, `EvalDataset`, `load_dataset()` | `app/ai/evaluation/datasets.py` | ✅ Exists |
| Runners | `app/ai/evaluation/runners.py` | ✅ Prompt/Retrieval/E2E |
| Metrics | `app/ai/evaluation/metrics.py` | ✅ Exists |
| Report | `app/ai/evaluation/report.py` | ✅ Exists |
| CLI | `app/ai/evaluation/cli.py` | ✅ `make eval` entry point |
| Sample dataset | `tests/data/evaluation/sample.yaml` | ✅ 5 cases |
| `AgentEvalRunner`, `WorkflowEvalRunner` | — | ❌ Phase 7 |
| `RegressionChecker`, `baseline-report.json` | — | ❌ Phase 8 |

---

## 7. Provider & DI Inventory

| Component | Location | Status | Epic 07 role |
| --------- | -------- | ------ | ------------ |
| `LLMProvider` (Protocol) | `app/providers/base.py` | ✅ Exists | Wrapped by `TracingLLMProvider` (Phase 2) |
| `ProviderFactory.get_provider()` | `app/providers/factory.py` | ✅ Exists | Single install point for tracing wrapper (Phase 2) |
| `PromptManager.render()` | `app/ai/prompts/manager.py` | ✅ Exists | `prompt_span` target (Phase 2) |
| DI factories | `app/ai/deps.py` | ✅ ~550 lines | Observability DI wiring (Phases 1, 6) |
| `get_current_caller`, `CallerContext` | `app/core/caller.py` | ✅ Exists | Owner-scoped Observability REST API (Phase 6) |

---

## 8. Pipeline Entry Points (Span Instrumentation Targets)

Confirmed call sites each span helper will wrap (Phases 2–4):

| Span helper | Span name(s) | Instrumentation target | File |
| ----------- | ------------ | ---------------------- | ---- |
| `llm_span` | `llm.complete` | `ProviderFactory.get_provider()` → `TracingLLMProvider` | `app/providers/factory.py` |
| `prompt_span` | `prompt.render` | `PromptManager.render()` | `app/ai/prompts/manager.py` |
| `tool_span` | `tool.execute` | `ToolExecutor.execute()` | `app/ai/tools/executor.py` |
| `agent_span` | `agent.iteration`, `agent.tool_call`, `agent.reflection` | `DefaultAgent.run()` iteration loop | `app/ai/agent/runtime/default_agent.py` |
| `rag_span` | `rag.retrieve` | `Retriever.retrieve()` | `app/ai/rag/retriever.py` (+ hybrid retriever) |
| `memory_span` | `memory.retrieve` | `MemoryManager.retrieve_context()` / related retrieval | `app/ai/memory/manager.py` |
| `memory_span` | `memory.extract` | `MemoryManager._run_extraction_pipeline()` | `app/ai/memory/manager.py` |
| `voice_span` | `voice.session` | `VoiceSessionManager` lifecycle | `app/ai/voice/session.py` |
| `workflow_span` | `workflow.run`, `workflow.node` | `WorkflowExecutor.execute_run()` + per-node attempts | `app/ai/workflow/engine/executor.py` |

**Note:** Part I references `WorkflowExecutor.step()`; the implemented method is `execute_run()` — Phase 4 should instrument the actual executor API without renaming production code.

---

## 9. Observability Subsystem Absence

Confirmed no prior observability implementation:

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/observability/` | ❌ Does not exist |
| `app/routers/observability.py` | ❌ Does not exist |
| `app/schemas/observability.py` | ❌ Does not exist |
| `OBSERVABILITY_ENABLED` in config | ❌ Not present |
| `CostCalculator`, `ModelPricingTable` | ❌ Not present |
| `TracerRegistry`, `MeterRegistry` | ❌ Not present |
| Span helpers (`llm_span`, etc.) | ❌ Not present |
| `usage_events.cost_usd` / `pricing_version` | ❌ Not in schema |
| `alembic/versions/0008_observability_usage_cost.py` | ❌ Does not exist |
| `tests/ai/observability/` | ❌ Does not exist |
| `tests/data/evaluation/baseline-report.json` | ❌ Does not exist |
| Frontend observability modules | ❌ None |
| OpenTelemetry packages in `pyproject.toml` | ❌ Not added |

Structured log metrics (V1/V2) exist in services (e.g. `retrieval_latency_ms`, workflow log fields) — these are **not** OTel/Prometheus instrumentation and remain unchanged until Epic 07 phases wire the new layer.

---

## 10. Architecture Review

### 10.1 Part I Architectural Invariants — Understood as Preconditions

| Invariant | Precondition status |
| --------- | ------------------- |
| Provider-agnostic OTel API only; no vendor APM SDKs | ✅ No conflicting SDKs in codebase |
| `OBSERVABILITY_ENABLED=false` → no-op Tracer/Meter, zero behaviour change | ✅ Flag absent; default-off pattern established by other flags |
| Zero content leakage on spans/metrics | ✅ `sanitize_value` exists; policy documented |
| Fail-open instrumentation | ✅ Pattern exists elsewhere (memory extraction catches all) |
| Bounded metric cardinality | ✅ Policy documented; no metrics exist yet |
| Reuse V1 evaluation harness in place | ✅ `app/ai/evaluation/` ready for extension |
| Reuse `usage_events` table (additive columns only) | ✅ Table exists; no cost columns yet |
| `TracingLLMProvider` at `ProviderFactory` only | ✅ Factory is single switch point |
| Public APIs stable after Phase 1 | ✅ No observability public APIs yet |
| Flag-off parity with Epic 06 behaviour | ✅ All pipelines operational at baseline |

### 10.2 Epic 07 Integration Points (by Phase)

| Phase | Integration point |
| ----- | ----------------- |
| 1 | `app/ai/observability/` package, `TracerRegistry`/`MeterRegistry`, `OBSERVABILITY_ENABLED`, OTel deps, logging correlation |
| 2 | `TracingLLMProvider` in `ProviderFactory`; `prompt_span` in `PromptManager` |
| 3 | `tool_span` in `ToolExecutor`; `agent_span` in `DefaultAgent` |
| 4 | `rag_span`, `memory_span`, `voice_span`, `workflow_span` in respective entry points |
| 5 | `CostCalculator`, migration `0008`, metrics instruments, `SqlUsageStore` extension |
| 6 | `app/routers/observability.py`, `GET /metrics`, health `observability_enabled` |
| 7 | `AgentEvalRunner`, `WorkflowEvalRunner`, extended `EvalLevel` |
| 8 | `RegressionChecker`, `baseline-report.json`, CLI flags |
| 9 | Frontend `ObservabilityPage`, `observabilityClient.ts` |
| 10 | Full validation & release |

### 10.3 Components to Reuse (DO NOT REIMPLEMENT)

Per Part II **Reuse Existing Components** — all verified present:

- `StructuredLogger`, `sanitize_value`, `bind_context` — `app/core/logging.py`
- `correlation_id_middleware` — `app/middleware/correlation_id.py`
- `SqlUsageStore`, `UsageEvent` — `app/db/usage.py`, `app/db/models.py`
- `ProviderUsage`, `LLMProvider`, `ProviderFactory` — `app/providers/`
- `PromptManager` — `app/ai/prompts/manager.py`
- `ToolExecutor`, registry, validator, `ToolAuthorizer` — `app/ai/tools/`
- `DefaultAgent` — `app/ai/agent/`
- `WorkflowManager`, `WorkflowExecutor` — `app/ai/workflow/`
- RAG `Retriever` — `app/ai/rag/`
- Memory manager/extraction — `app/ai/memory/`
- Voice session — `app/ai/voice/`
- Evaluation harness — `app/ai/evaluation/`
- Feature flags — `app/core/config.py`
- DI — `app/ai/deps.py`

---

## 11. Dependency Verification

| Dependency | Status | Notes |
| ---------- | ------ | ----- |
| Epic 06 Workflow (predecessor) | ✅ | All phases complete; stable platform |
| PostgreSQL | ✅ | Default `postgresql+asyncpg://...@localhost:5433/chatbot` |
| Alembic migrations | ✅ | Head: `0007_workflow_tables.py` (7 migrations); next: `0008_observability_usage_cost` |
| `usage_events` table | ✅ | Exists; no cost columns |
| `ProviderFactory` / `LLMProvider` | ✅ | OpenAI, Gemini, Groq, Anthropic |
| Feature flag infrastructure | ✅ | `agent_runtime_enabled`, `memory_enabled`, `voice_enabled`, `workflow_engine_enabled`, etc. |
| OpenTelemetry SDK | ❌ | Phase 1 — `opentelemetry-api`, `opentelemetry-sdk`, OTLP HTTP exporter, Prometheus reader |
| `OBSERVABILITY_ENABLED` | ❌ | Phase 1 deliverable |
| Python | ✅ | `requires-python = ">=3.12"` in `pyproject.toml` |

---

## 12. Platform Readiness

Verified via comprehensive test suite (no live manual smoke in this audit):

| Capability | Evidence |
| ---------- | -------- |
| Chat (plain + persistence) | `tests/test_chat_persistence.py`, `test_summarization_and_linking.py`, `test_chat_service_memory.py` |
| Streaming SSE | `tests/test_unified_chat.py`, `tests/test_chat_stream.py` — **26 passed** |
| RAG | `tests/test_rag_api.py`, `tests/test_rag_service.py`, `tests/ai/rag/` |
| Tools / web search | `tests/test_unified_chat.py`, `app/ai/tools/` |
| MCP | `tests/ai/mcp/` |
| Agent runtime | `tests/ai/agent/` — 13 test modules; `AGENT_RUNTIME_ENABLED=false` default |
| Memory | `tests/ai/memory/` — 23 test modules, `tests/test_memory_router.py` |
| Voice | `tests/ai/voice/` — 11 test modules, `tests/test_voice_router.py` |
| Workflow | `tests/ai/workflow/` — **241 passed**; router **23**; tool **11** |

All subsystems operational at Epic 06 baseline; no blockers for additive observability layer.

---

## 13. Alembic Migrations

| Migration | Purpose |
| --------- | ------- |
| `0001_init_chat_persistence.py` | Chat sessions, messages, users, `usage_events` |
| `0002_documents_and_chunks.py` | Document storage |
| `0003_pgvector_embeddings.py` | pgvector extension |
| `0004_upload_quota_counters.py` | Upload quotas |
| `0005_document_chunks_fts.py` | Full-text search |
| `0006_memory_tables.py` | Memory records, preferences |
| `0007_workflow_tables.py` | Workflow definitions, runs, node executions |
| `0008_observability_usage_cost.py` | **Planned Phase 5** — `cost_usd`, `pricing_version` on `usage_events` |

---

## 14. Implementation Assumptions (from Part I)

| Assumption | Value |
| ---------- | ----- |
| Master flag | `OBSERVABILITY_ENABLED=false` (default) |
| Tracing SDK | OpenTelemetry API/SDK only; console exporter default; OTLP when endpoint set |
| Sampling | `ParentBased(TraceIdRatioBased(otel_traces_sample_ratio))`; default ratio `1.0` (dev) |
| Cost | Static versioned `ModelPricingTable`; approximate; `NULL` when unknown |
| Metrics | Prometheus exposition at `GET /metrics`; low-cardinality labels only |
| Evaluation | Extend existing harness; add `agent`/`workflow` levels; git-tracked baseline |
| Dashboard | DB-native cost/usage summary only; no trace explorer |
| Alembic next | `0008_observability_usage_cost.py` |
| CI | Fakes + in-memory OTel exporters for unit tests |

---

## 15. Implementation Readiness Checklist

- [x] Epic 06 Phase 12 complete / authorized for Epic 07 (release published; user requested Phase 0)
- [x] All required dependencies available (Postgres, providers, usage store, evaluation, DI)
- [x] OpenTelemetry packages identified for Phase 1 (not yet installed)
- [x] Implementation order matches Part II (Phases 1–10)
- [x] No architectural conflicts identified
- [x] No observability implementation already exists
- [x] Extension points and call sites identified
- [x] Baseline quality metrics recorded
- [x] Baseline audit published
- [x] No functional code changes in this phase

---

## 16. Git Status

```bash
Branch: feat/v2-epic-07-observability-and-evaluation-phase-00
Working tree: audit doc + epic plan updates (+ generated .eval/eval-report.json from make eval)
```

---

## 17. Completion Record

| Metric | Result |
| ------ | ------ |
| Lint | ✅ PASS |
| Format check | ✅ PASS |
| Typecheck | ✅ PASS |
| Unit / integration tests | ✅ **1551 passed**, 0 failed |
| Coverage (`app/`) | ✅ **89.05%** |
| Evaluation suite | ✅ **5/5** passed |
| Frontend tests | ✅ **268** passed (43 files) |
| Frontend build | ✅ PASS |
| Workflow integration | ✅ **241** passed |
| Platform readiness | ✅ Confirmed |
| Observability subsystem | ❌ None (expected) |
| Baseline audit published | ✅ This document |

---

## 18. Recommendations

1. **Proceed to Phase 1** — all gates green; clean observability baseline
2. **Add `OBSERVABILITY_ENABLED=false`** before any behaviour change
3. **Install OTel packages in Phase 1** — pin for Python ≥3.12
4. **Instrument `WorkflowExecutor.execute_run()`** — map Part I `step()` naming to actual API in Phase 4
5. **Keep flag-off parity** — no spans, metrics, or cost columns populated when disabled

---

## 19. Phase 0 Exit Criteria

- [x] Audit published
- [x] Baseline recorded
- [x] Quality gates passed
- [x] Architecture verified
- [x] No implementation blockers
- [ ] User confirmation to proceed to Phase 1

**Next phase:** Phase 1 — Tracing & Metrics Foundation
**Branch:** `feat/v2-epic-07-observability-and-evaluation-phase-00`

---

**Audit completed:** 2026-08-07T11:45:00+05:30
