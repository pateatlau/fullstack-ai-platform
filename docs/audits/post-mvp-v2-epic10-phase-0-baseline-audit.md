# Post-MVP V2 Epic 10 Phase 0 — Baseline Audit

**Epic:** v2-10 Background Jobs
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-12
**Auditor:** AI Agent
**Status:** Complete
**Git commit (validation base):** `44550fc` — Epic 09 complete; Epic 10 not started

---

## Executive Summary

Baseline audit before implementing Epic 10 (Background Jobs). All quality gates pass. Epic 09 is complete with release summary published. **No Epic 10 Background Jobs code exists** — `app/ai/jobs/` absent, `BACKGROUND_JOBS_ENABLED` absent, no `background_jobs` migration. All five deferred-work closure targets are inventoried (`TODO(epic-9)`/`TODO(epic-10)` markers, config-only timeout/retention fields, lazy-only HITL expiry). Extension points for reuse (`schedule_run_task`, `SyncIndexingRunner`, `AgentApprovalService`, workflow CAS) verified present and documented as **DO NOT MODIFY** (Epic 06/05 in-process tasks) or **wire in Phases 3–6**.

**Key findings:**

- ✅ Backend gates pass: lint, format-check, typecheck, **2004 tests**, **88.73%** `app/` coverage (≥80% gate met), eval **15/15** (`--level all`) + **5/5** (`--level hitl`) + **3/3** (`--level plugin`)
- ✅ Frontend gates pass: lint, **303 tests** (50 files), build successful
- ✅ Epic 09 complete: `app/ai/hitl/` operational; `HITL_ENABLED` present (default `false`); release summary published
- ✅ Extension points verified: workflow/memory background tasks, `IndexingJob`/`SyncIndexingRunner`, HITL store/service, `ApprovalNodeExecutor`, config timeout/retention fields
- ✅ CHECK constraints: `agent_tool_approvals.status` includes `expired`/`cancelled`; `chat_messages.status` and `workflow_node_executions.decision` do **not** yet include `expired`
- ❌ `BACKGROUND_JOBS_ENABLED` absent; `app/ai/jobs/` does not exist (expected Phase 1+)
- ⚠️ Alembic head is `0011_hitl_lifecycle_audit`; **next revision is 0012** (epic doc Files index still references `0011_background_jobs.py` — update in Phase 1)
- ✅ **Phase 0 authorized** (user requested Phase 0 implementation)
- ⬜ **Phase 1 not authorized** — explicit user confirmation pending

---

## 1. Epic 09 Phase 10 Status

**Finding:** Epic 09 Human-in-the-Loop Phases 0–10 complete; release summary published.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-09-human-in-the-loop.md` — Phases 0–10 marked **Completed**
- `docs/releases/post-mvp-v2-epic9-release-summary.md` — published 2026-08-12
- `app/ai/hitl/` — policy, store, service, models, exceptions
- `app/routers/approvals.py` — unified audit REST API
- Epic 09 plan baseline: 1912 passed, 88.95%; Phase 0 run: **2004 passed, 88.73%** (+92 tests since Epic 09 Phase 10; coverage −0.22 pp; ≥80% gate met)

**Recommendation:** Epic 10 **Phase 0** is complete. Do not start Phase 1 until the user explicitly confirms.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~1 s
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 572 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~9 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 2004 passed, 0 failed
Coverage: 88.73% on app/ (≥80% required)
Duration: ~210 s
```

**Notable coverage:**

| Module | Coverage |
| ------ | -------- |
| `app/ai/hitl/` (package) | 83.8% |
| `app/ai/workflow/` | High — extensive test suite |
| `app/ai/plugins/` | High |
| `app/services/unified_chat_service.py` | 60% (conditional branches) |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 15/15 (--level all)
  prompt: 5, retrieval: 3, e2e: 2, agent: 1, workflow: 4

Command: uv run python -m app.ai.evaluation.cli --level hitl
Result: ✅ PASS — 5/5

Command: uv run python -m app.ai.evaluation.cli --level plugin
Result: ✅ PASS — 3/3
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm test -- --run     ✅ PASS — 50 files, 303 tests
npm run build         ✅ PASS — 557 kB JS bundle
```

No jobs dashboard modules exist (`jobsClient`, `JobsPage` absent — expected Phase 10).

---

## 4. Extension Point Inventory

### 4.1 Workflow background tasks (`app/ai/workflow/engine/background.py`)

| Symbol | Purpose | Epic 10 role |
| ------ | ------- | ------------ |
| `schedule_run_task()` | Fire-and-forget workflow run execution | **DO NOT MODIFY** — out of migration scope |
| `_ACTIVE_RUN_IDS` | In-process run tracking | **DO NOT MODIFY** |
| `reconcile_orphaned_runs()` | Startup orphan reconciliation | **DO NOT MODIFY** — Epic 10 claim-and-lease supersedes this pattern for _new_ jobs only |

### 4.2 Memory background tasks (`app/ai/memory/background_tasks.py`)

| Symbol | Purpose | Epic 10 role |
| ------ | ------- | ------------ |
| `schedule_extraction_task()` | Memory extraction asyncio task retention | **DO NOT MODIFY** |
| `schedule_lifecycle_task()` | Lifecycle processing | **DO NOT MODIFY** |

### 4.3 RAG indexing (`app/ai/interfaces/indexing_job.py`, `app/ai/rag/indexing/`)

| Component | Location | Current state | Epic 10 closure |
| --------- | -------- | ------------- | --------------- |
| `IndexingJob` protocol | `indexing_job.py` | `submit()` / `get_status()` | Unchanged public API |
| `SyncIndexingRunner` | `sync_runner.py` | In-process default; `KnowledgeService` injects it | Remains default when `rag_indexing_runner="sync"` |
| `QueueIndexingRunner` | — | **Absent** | Phase 5 deliverable |
| TODO markers | `indexing/__init__.py:5`, `sync_runner.py:7` | `TODO(epic-9): QueueIndexingRunner / workers / retries / durable job store.` | Close in Phase 5 |

### 4.4 HITL timeout & orphan sweep (`app/ai/hitl/`, `app/core/config.py`)

| Config field | Default | Enforced today | Epic 10 handler |
| ------------ | ------- | -------------- | --------------- |
| `hitl_approval_timeout_hours` | `0` (disabled) | **Lazy only** — `AgentToolApprovalStore.get_for_owner()` CAS-flips `pending→expired` on read/decide; no proactive sweep for untouched rows (`store.py:131–132`) | `hitl_approval_expiry_sweep` (Phase 3) |
| `workflow_approval_timeout_hours` | `0` (disabled) | **Not enforced** — `approval_node.py:33` `TODO(epic-10)` | `hitl_approval_expiry_sweep` (Phase 3) |
| `workflow_run_retention_days` | `90` | **Not enforced** — config + test default only | `workflow_run_retention_cleanup` (Phase 4) |

**Orphaned snapshot sweep:** Documented in Epic 09 Part I; no background handler yet. `AgentApprovalService.decide()` runs Stages 2–4 synchronously on approve; crash between Stage 1 and Stage 4 leaves `approved` rows with non-null pause snapshots — **Phase 3** `hitl_orphaned_snapshot_sweep`.

**Epic 09 comments referencing Epic 10:**

- `app/ai/hitl/models.py:113` — proactive background sweep deferred
- `app/ai/hitl/store.py:132` — no proactive sweep for untouched rows
- `backend-python/README.md:588–589` — timeout enforcement and restart mid-resume deferred

### 4.5 Workflow approval node (`app/ai/workflow/nodes/approval_node.py`)

```python
# TODO(epic-10): enforce workflow_approval_timeout_hours via background jobs.
```

`ApprovalNodeExecutor.execute()` returns `waiting_approval` metadata only; timeout enforcement is Phase 3.

### 4.6 Scheduled evaluation

No `evaluation_schedule_enabled` or scheduled eval config in `app/core/config.py` yet — Phase 6 deliverable (`scheduled_evaluation_run` handler).

---

## 5. Architecture Review

### 5.1 CHECK constraint status

| Table / column | Current CHECK values | Epic 10 additive change |
| -------------- | -------------------- | ----------------------- |
| `agent_tool_approvals.status` | `pending`, `approved`, `rejected`, `expired`, `cancelled` (`0010_hitl_tables.py:90`) | ✅ Already includes `expired` |
| `chat_messages.status` | `complete`, `stopped`, `error`, `interrupted`, `waiting_approval`, `rejected` (`0010_hitl_tables.py:29–31`) | Add `'expired'` (Phase 3 migration) |
| `workflow_node_executions.decision` | `approved`, `rejected` or NULL (`0007_workflow_tables.py:209`) | Add `'expired'` (Phase 3 migration) |

### 5.2 Background Jobs subsystem absence

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/jobs/` | ❌ Does not exist |
| `BACKGROUND_JOBS_ENABLED` | ❌ Not in config |
| `background_jobs` table | ❌ Not in migrations |
| `background_job_schedules` table | ❌ Not in migrations |
| `app/routers/jobs.py` | ❌ Does not exist |
| `tests/ai/jobs/` | ❌ Does not exist |
| Frontend `JobsPage` / `jobsClient` | ❌ Does not exist |

### 5.3 Part I architectural invariants — Phase 0 evidence

| Invariant | Phase 0 status |
| --------- | -------------- |
| Platform-first queue/worker/scheduler triad | **Planned** — no package yet |
| No new infrastructure (Postgres-only queue) | **Verified** — no broker deps in lockfile |
| Epic 06/05 in-process tasks unchanged | **Verified** — present, not to be modified |
| `BACKGROUND_JOBS_ENABLED=false` flag-off parity | **Baseline only** — flag absent; parity verified in Phase 11 |
| Handler idempotency required | **Design only** — documented in Part I |
| Claim-and-lease (no startup reconcile for jobs) | **Planned** — generalizes Epic 06 pattern |

---

## 6. Feature Flag Inventory (`app/core/config.py`)

| Flag | Default | Present | Epic 10 notes |
| ---- | ------- | ------- | ------------- |
| `tools_enabled` | `false` | ✅ | Unchanged |
| `agent_runtime_enabled` | `false` | ✅ | Unchanged |
| `workflow_engine_enabled` | `false` | ✅ | Unchanged |
| `plugins_enabled` | `false` | ✅ | Unchanged |
| `hitl_enabled` | `false` | ✅ | Epic 09 shipped |
| `observability_enabled` | `false` | ✅ | Job spans Phase 8 |
| **`background_jobs_enabled`** | **`false` (planned)** | **❌** | **Phase 1 deliverable** |
| `hitl_approval_timeout_hours` | `0` | ✅ | Lazy enforcement only; proactive sweep Phase 3 |
| `workflow_approval_timeout_hours` | `0` | ✅ | Not enforced; Phase 3 |
| `workflow_run_retention_days` | `90` | ✅ | Not enforced; Phase 4 |
| `rag_indexing_runner` | — | ❌ | Phase 5 (`"sync"` default) |
| `evaluation_schedule_enabled` | — | ❌ | Phase 6 |

**DI pattern:** `@lru_cache` singletons in `app/ai/deps.py`; settings via `Depends(get_settings)`. Phase 1 adds job queue/worker factories following existing pattern.

---

## 7. Dependency Verification

| Dependency | Phase 0 status | Notes |
| ---------- | -------------- | ----- |
| Epic 09 Human-in-the-Loop (predecessor) | **Verified** | Phases 0–10 complete; release summary published |
| Epic 06 Workflow Engine | **Verified** | Approval nodes, CAS, postgres store operational |
| Epic 02 RAG / indexing hook | **Verified** | `SyncIndexingRunner`, `IndexingJob` protocol tested |
| Epic 07 Evaluation runner | **Verified** | `make eval` 15/15 |
| Feature flag / DI patterns | **Verified** | `@lru_cache` singletons; bool flags in `Settings` |
| PostgreSQL / Alembic | **Verified** | Head: `0011_hitl_lifecycle_audit`; **next revision: 0012** |
| Redis / Celery / RQ / APScheduler | **Verified absent** | Not in `pyproject.toml` or `uv.lock` |

---

## 8. Platform Test-Backed Baseline

Automated test evidence only:

| Capability | Test path | Count (this audit) |
| ---------- | --------- | ------------------ |
| HITL package + router | `tests/ai/hitl/`, `tests/test_approvals_router.py`, `tests/test_cancel_and_stage_router.py` | **172** collected |
| Workflow package | `tests/ai/workflow/` | High |
| RAG indexing | `tests/ai/rag/test_indexing_job.py` | Present |
| Plugins | `tests/ai/plugins/`, `tests/test_plugins_router.py` | Present |

---

## 9. Alembic Migrations

| Migration | Purpose |
| --------- | ------- |
| `0001`–`0007` | Chat, documents, pgvector, quotas, FTS, memory, workflow |
| `0008_observability_usage_cost.py` | Epic 07 |
| `0009_workflow_plugin_node_type.py` | Epic 08 |
| `0010_hitl_tables.py` | Epic 09 — HITL tables, chat status CHECK extension |
| `0011_hitl_lifecycle_audit.py` | Epic 09 — lifecycle audit columns, `version` on approvals |

**Epic 10 Phase 1:** `0012_background_jobs.py` (epic doc Files index references `0011` — superseded by Epic 09 migration `0011`; correct in Phase 1).

---

## 10. TODO Marker Closure Targets

| Marker | File | Epic 10 phase |
| ------ | ---- | --------------- |
| `TODO(epic-9): QueueIndexingRunner…` | `app/ai/rag/indexing/__init__.py:5` | Phase 5 |
| `TODO(epic-9): QueueIndexingRunner…` | `app/ai/rag/indexing/sync_runner.py:7` | Phase 5 |
| `TODO(epic-10): enforce workflow_approval_timeout_hours…` | `app/ai/workflow/nodes/approval_node.py:33` | Phase 3 |

**Implicit closures (no TODO marker, documented gaps):**

- Proactive HITL approval expiry sweep (`hitl_approval_timeout_hours`) — Phase 3
- Orphaned approved snapshot resume — Phase 3
- `workflow_run_retention_days` cleanup — Phase 4
- Scheduled evaluation runs — Phase 6

---

## 11. Components to Reuse (DO NOT REIMPLEMENT)

All verified present per Part II **Reuse Existing Components**:

- `schedule_run_task`, `reconcile_orphaned_runs` — `app/ai/workflow/engine/background.py`
- `schedule_extraction_task`, `schedule_lifecycle_task` — `app/ai/memory/background_tasks.py`
- `IndexingJob`, `SyncIndexingRunner`, `PendingIndexingWork` — `app/ai/rag/indexing/`
- `AgentApprovalService`, `AgentExecutor.resume_from_approval`, `AgentToolApprovalStore` CAS — `app/ai/hitl/`
- `WorkflowManager.apply_decision`, `ApprovalNodeExecutor` — `app/ai/workflow/`
- `app/ai/evaluation/` runner — `runners.py`, `cli.py`
- `record_workflow_approval_pending_delta`, `record_agent_tool_approval_pending_delta` — observability metrics
- `approval_span`, `workflow_span` style — `app/ai/observability/tracing/spans.py`
- `get_current_caller`, `CallerContext` — `app/core/caller.py`
- Feature flag infrastructure — `app/core/config.py`
- `get_sessionmaker`, standalone-session pattern — `app/db/engine.py`, `app/ai/deps.py`

---

## 12. Acceptance (Phase 0)

| Criterion | Status |
| --------- | ------ |
| Existing platform fully operational | ✅ All gates pass |
| All extension points identified | ✅ §4 |
| No Background Jobs implementation present | ✅ §5.2 |
| Baseline metrics recorded | ✅ §2–3, §8 |
| Epic 09 complete / authorized for Epic 10 | ✅ §1 |
| Part I architecture reviewed | ✅ §5 |

---

## 13. Exit Criteria & Authorization

| Item | Status |
| ---- | ------ |
| Baseline audit published | ✅ This document |
| User confirmation to proceed to Phase 1 | ⬜ **Pending** |

**Rollback:** Not applicable — Phase 0 made no code changes.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-10-background-jobs.md](../plans/post-mvp-v2-epic-10-background-jobs.md)
- Predecessor release: [docs/releases/post-mvp-v2-epic9-release-summary.md](../releases/post-mvp-v2-epic9-release-summary.md)
- Epic 09 Phase 0 audit (template): [docs/audits/post-mvp-v2-epic9-phase-0-baseline-audit.md](./post-mvp-v2-epic9-phase-0-baseline-audit.md)
- V2 strategy § Background Jobs: [docs/references/fullstack-ai-platform-v2-architecture-implementation-strategy.md](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md)
