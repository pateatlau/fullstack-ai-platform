# Post-MVP V2 Epic 06 Release Summary

**Release name:** Post-MVP V2 Epic 06 — Workflow Engine
**Release date:** 2026-08-05
**Validation:** Phase 12 final acceptance (see [post-mvp-v2-epic-06-workflow-engine.md](../plans/post-mvp-v2-epic-06-workflow-engine.md))
**Git commit (validation base):** `7ed213f` — Epic 06 Phase 12 validation & release

---

## Summary vs Epic 05

Epic 05 shipped the Memory System under `MEMORY_ENABLED`. **V2 Epic 06 adds a provider-agnostic Workflow Engine** under `app/ai/workflow/` (graph definitions, sequential/parallel execution, conditional routing, task/LLM/agent/approval nodes, checkpointing, retry, crash recovery, REST API, agent tool, dashboard) behind `WORKFLOW_ENGINE_ENABLED` (default **off**).

| Area | Epic 05 / pre-workflow platform | V2 Epic 06 |
| ---- | ------------------------------- | ---------- |
| Orchestration | Single chat turn / agent ReAct loop | Additive multi-step workflow graphs with durable runs |
| Graph model | None | Versioned `WorkflowDefinition` (nodes, edges, validation) |
| Execution | N/A | In-process `asyncio` scheduler + DB checkpoint per transition |
| Human approval | N/A | `waiting_approval` pause/resume via REST + dashboard |
| Chat coupling | Memory via `ChatService` / `UnifiedChatService` | **None** — workflows via REST API or `WorkflowExecutionTool` only |
| Management API | Memory REST | Authenticated `/api/workflows/*` (route-level `503` when flag off) |
| Frontend | Memory settings page | Additive `/workflows` dashboard + nav link (hidden when flag off or guest) |
| Memory / Voice / RAG / MCP / Tools | Stable | Unchanged when `WORKFLOW_ENGINE_ENABLED=false` |

---

## Delivered (Phases 0–12)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Models/enums, `WorkflowStore` protocol, `PostgresWorkflowStore` scaffold, migration `0007`, `WORKFLOW_ENGINE_ENABLED`, DI wiring |
| 2 | `GraphValidator`, definition CRUD, versioning |
| 3 | Sequential `WorkflowExecutor`, Task node, checkpointing |
| 4 | `ConditionEvaluator`, Router node, declarative edge DSL |
| 5 | Fork/Join parallel execution, optimistic `checkpoint_version` merge |
| 6 | LLM node (`LLMProvider`/`PromptManager`), Agent node (`DefaultAgent`) |
| 7 | Approval node, pause/resume, atomic CAS decisions |
| 8 | `RetryPolicy`, crash recovery, startup reconciliation, execution receipts |
| 9 | Workflow REST API, `workflow_engine_enabled` health field, cancel/resume |
| 10 | `WorkflowExecutionTool`, conditional tool registration |
| 11 | `WorkflowsPage`, `workflowClient.ts`, health/nav integration |
| 12 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `WorkflowStore`, `WorkflowManager`, canonical models/enums, `GraphValidator`, `WorkflowExecutor`, `ConditionEvaluator`, node executors, `RetryPolicy`; flag-guarded router and tool.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `WORKFLOW_ENGINE_ENABLED` | `false` | Off: no workflow tool registration; Workflow API returns `503 feature_disabled`; dashboard hidden; chat/memory/voice/RAG/MCP unchanged. On: authenticated users get workflow CRUD, run launch, approval decisions, and dashboard. |

Requires PostgreSQL migration `0007_workflow_tables`. CI uses fakes — no live LLM/agent calls in unit/integration tests.

**Rollback:** set `WORKFLOW_ENGINE_ENABLED=false`; redeploy; in-flight runs may remain in DB but are not executed until flag re-enabled and process restarts reconciliation.

---

## Breaking Changes

**None.** Workflow Engine is additive behind a master flag. Chat HTTP/SSE contracts unchanged.

---

## Migration / Upgrade Notes

1. Pull release; run `alembic upgrade head` (migration `0007_workflow_tables`).
2. Ensure `backend-python/.env.example` includes `WORKFLOW_*` settings (`WORKFLOW_ENGINE_ENABLED=false` by default).
3. To exercise locally: set `WORKFLOW_ENGINE_ENABLED=true`, ensure DB migrated, sign in (guests have no workflows), open `/workflows` or invoke via `WorkflowExecutionTool`.
4. Run start requires owner-scoped `idempotency_key`; dedupe on `(owner_id, workflow_definition_id, idempotency_key)`.

---

## Manual E2E Smoke (documented procedure)

Run with `WORKFLOW_ENGINE_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `workflow_engine_enabled: true` |
| 2. Nav | "Workflows" link visible when signed in; hidden for guests and when flag off |
| 3. Dashboard | `/workflows` loads definitions and runs |
| 4. Create definition | POST definition with valid graph; appears in list |
| 5. Start run | POST run with `idempotency_key`; run progresses asynchronously |
| 6. Approval | Run reaches `waiting_approval`; approve/reject via UI or REST |
| 7. Cancel/resume | Cancel active run; resume interrupted `running` run after restart |
| 8. Flag off | `workflow_engine_enabled: false`; API `503`; chat unchanged from pre-epic |

Automated CI covers workflow modules, router, tool, and frontend with mocks; live LLM/agent smoke is manual.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Guest workflows | Out of scope (authenticated-only) |
| Visual drag-and-drop builder | Out of scope v1 (JSON/API-authored) |
| Scheduled/cron triggers | Future epic |
| Distributed multi-worker execution | Future epic |
| Plugin-loaded external node types | Future epic |
| Full HITL audit trail / editable tool args | Epic 09 |
| Approval timeout enforcement | Config surface only (`workflow_approval_timeout_hours`; `TODO(epic-10)`) |
| Run retention cleanup | Config surface only (`workflow_run_retention_days`; `TODO(epic-10)`) |
| Workflow OTel spans, eval harness | Epic 07 |
| Shared/organization workflows | Future epic |

---

## Verification Metrics (Phase 12 — 2026-08-05)

| Gate | Result |
| ---- | ------ |
| Backend `make lint` + `format-check` + `typecheck` | **Clean** |
| Flag-off `make test-cov` | **1551 passed**, **89.05%** coverage on `app/` |
| Workflow package `app/ai/workflow/` | **85%** (gate ≥80%) |
| Workflow test paths | **241 passed** (207 `tests/ai/workflow` + 23 router + 11 tool) |
| Chat/Memory/Voice/RAG/MCP/Agent flag-off parity spot checks | **21 passed** (8 scenarios) |
| `make eval` | **5/5** passed (`backend-python/.eval/eval-report.json`, timestamp `2026-08-05T09:39:27Z`) |
| Frontend lint + format + build | **Clean** |
| Frontend Vitest | **268** tests (43 files) — all pass |
| Workflow frontend tests (2 files) | **17 passed** (`WorkflowsPage`, `workflowClient`) |

**Functional validation (automated):** sequential execution, conditional routing, fork/join parallelism, LLM/agent nodes, approval pause/resume, node retry, crash recovery, owner isolation, idempotent run start, graph validation — covered by `tests/ai/workflow/` suite.

**Performance validation:** no dedicated latency benchmarks in CI. Behavioural evidence from integration tests: checkpoint-after-transition model (fake/postgres stores), parallel branch tests (`test_fork_join.py`), crash recovery rehydration (`test_crash_recovery.py`, `test_resume.py`). In-process asyncio + per-transition DB checkpoint deemed acceptable for v1 single-process deployment.

**Architectural invariants (Part I):** orchestration only via `WorkflowManager`; no `ChatService`/`UnifiedChatService` coupling; Task/LLM/Agent nodes reuse existing platform components; declarative condition DSL only (no `eval`/`exec`); flag-off parity confirmed.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-06-workflow-engine.md](../plans/post-mvp-v2-epic-06-workflow-engine.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic6-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic6-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic5-release-summary.md](./post-mvp-v2-epic5-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
