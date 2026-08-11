# Post-MVP V2 Epic 09 Phase 0 — Baseline Audit

**Epic:** v2-09 Human-in-the-Loop
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-11
**Auditor:** AI Agent
**Status:** Complete
**Git commit (validation base):** `4f56cab` — Epic 08 complete; Epic 09 not started

---

## Executive Summary

Baseline audit before implementing Epic 09 (Human-in-the-Loop). All quality gates pass; test count matches Epic 08 Phase 10 (**1778 passed**). Coverage is **89.19%** (+0.02 pp vs Epic 08 **89.17%**); still above the **80%** gate. Epic 08 is complete with release summary published. **No HITL implementation exists** — clean baseline. All extension points (`ToolRunner`, `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, `GraphValidator`, `ChatMessage`, `ToolDefinition`) are inventoried and verified present. **Phase 0 complete.** Phase 1 requires separate user confirmation.

**Key findings:**

- ✅ Backend gates pass: lint, format-check, typecheck, **1778 tests**, **89.19%** `app/` coverage (≥80% gate met), eval **15/15** (`--level all`) + **3/3** (`--level plugin`)
- ✅ Frontend gates pass: lint, **291 tests** (48 files), build successful
- ✅ Epic 08 complete: `app/ai/plugins/` operational; `PLUGINS_ENABLED` present (default `false`); release summary published
- ✅ Extension points verified: `ToolRunner._execute_with_retry` dispatch site, `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, `build_approval_decision_output`, `GraphValidator` (10 validation passes), `ChatMessage.status` CHECK, `ToolDefinition` schema
- ✅ `Scratchpad`/`ScratchpadEntry` are Pydantic `BaseModel` types suitable for JSON snapshotting
- ❌ `HITL_ENABLED` absent; `app/ai/hitl/` does not exist (expected Phase 1+)
- ❌ `ToolDefinition.requires_approval` absent (expected Phase 1)
- ❌ `app/routers/approvals.py`, `approval_span`, frontend approval inbox absent (expected Phases 6–9)
- ✅ **Phase 0 authorized** (user requested Phase 0 baseline audit)
- ⬜ **Phase 1 not authorized** — explicit user confirmation pending

---

## 1. Epic 08 Phase 10 Status

**Finding:** Epic 08 Plugin Architecture Phase 10 validation complete; release summary published.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-08-plugin-architecture.md` — Phases 0–10 marked **Completed**
- `docs/releases/post-mvp-v2-epic8-release-summary.md` — published 2026-08-11
- `app/ai/plugins/` — manifest, loader, registry, registrar, four contribution kinds
- `app/routers/plugins.py` — authenticated inventory REST API (route-level `503` when flag off)
- Epic 08 Phase 10 baseline: 1778 passed, 89.17%; Phase 0 run: **1778 passed, 89.19%** (+0.02 pp; same `make test-cov` command; ≥80% gate met)

**Recommendation:** Epic 09 **Phase 0** is complete. Do not start Phase 1 until the user explicitly confirms.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~0.7 s
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 530 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~8.2 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 1778 passed, 0 failed
Coverage: 89.19% on app/ (≥80% required)
Duration: ~202 s
```

**Notable coverage (unchanged from Epic 08):**

| Module | Coverage |
| ------ | -------- |
| `app/services/unified_chat_service.py` | 60% (conditional RAG/tools/agent/memory branches) |
| `app/ai/workflow/` (aggregate) | High — 207 tests in `tests/ai/workflow/` |
| `app/ai/agent/` | High — 147 tests in `tests/ai/agent/` |
| `app/ai/plugins/` | High — plugin package tested |
| `app/ai/tools/` | ToolExecutor, registry, authorizer tested |
| `app/ai/mcp/` | 93%+ |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 15/15 (--level all)
  prompt: 5, retrieval: 3, e2e: 2, agent: 1, workflow: 4
Duration: ~3.5 s

Command: uv run python -m app.ai.evaluation.cli --level plugin
Result: ✅ PASS — 3/3
  plugin_echo_tool_ping, plugin_echo_prompt_greeting, plugin_echo_workflow_node
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm test -- --run     ✅ PASS — 48 files, 291 tests
npm run build         ✅ PASS — 533 kB JS bundle
```

No approval UI modules exist (`approvalsClient`, `ApprovalsPage` absent — expected Phase 9).

---

## 4. Extension Point Inventory

### 4.1 ToolRunner (`app/ai/agent/executor/tool_runner.py`)

| Aspect | Current state | Epic 09 role |
| ------ | ------------- | ------------ |
| Entry point | `run_tool_steps()` → `_run_step_batch()` → `_run_single_step()` → `_run_single_tool()` | Phase 2: pre-dispatch `ApprovalPolicy` gate in `_run_single_tool` / `_run_single_step` |
| Dispatch site | `_execute_with_retry()` line 241: `await self._executor.execute(call, tool_context)` | Gate consults policy **before** this call when `HITL_ENABLED=true` |
| Streaming | Publishes `AgentStreamEvent.tool_start` / `tool_end` around dispatch | Phase 2: emit `waiting_approval` event before pause |
| Parallel tools | `_parallel_tools_enabled` batches via `asyncio.gather` | Phase 2: entire step pauses when any call requires approval |
| Retry | `_execute_with_retry` wraps `ToolExecutor.execute` with retry policy | Unchanged for approved/resumed calls |

**HITL gate insertion point:** `_run_single_tool()` between event publish and `_execute_with_retry()` (or at `_run_single_step` for step-level pause per Locked Decisions).

### 4.2 ApprovalNodeExecutor & apply_decision (`app/ai/workflow/`)

| Component | Location | Current state | Epic 09 role |
| --------- | -------- | ------------- | ------------ |
| `ApprovalNodeExecutor` | `nodes/approval_node.py` | Returns `{"status": "waiting_approval", ...}`; persistence by `WorkflowExecutor` | Phase 4: unchanged pause behaviour; additive `edited_arguments`/`reason` on decision |
| `build_approval_decision_output()` | `nodes/approval_node.py:68` | Builds `{node_id, decision, selected_edge_ids}` | Phase 4: merge `edited_arguments` into output for downstream templating |
| `WorkflowManager.apply_decision()` | `manager.py:369` | CAS decision recording; resumes run via background task | Phase 4: accept optional `edited_arguments`, `reason`; return `ApprovalResult` |
| CAS pattern | `manager.py` | `UPDATE … WHERE status='pending'` with retry loop | Reuse for agent tool approvals (Phase 3) |

**Workflow node execution columns (current):** `decided_by`, `decided_at`, `decision` — no `edited_arguments` or `reason` yet (Phase 1 migration).

### 4.3 GraphValidator (`app/ai/workflow/graph/validator.py`)

Current `validate()` passes (in order):

1. `_validate_node_count`
2. `_validate_entry_node`
3. `_validate_node_types`
4. `_reject_plugin_nodes_when_disabled` (when `PLUGINS_ENABLED=false`)
5. `_validate_node_configs`
6. `_validate_plugin_nodes` (when `PLUGINS_ENABLED=true`)
7. `_validate_dangling_edges`
8. `_validate_edge_conditions`
9. `_validate_cycles`
10. `_validate_reachability`
11. `_validate_fork_join_pairing`
12. `_validate_approval_nodes`

**Missing (Epic 09 Phase 5):** Approval-required tool reachability check — reject graphs where a `task`/`agent` node can reach a flagged tool without a preceding `approval` node. Gated on `HITL_ENABLED`.

**Constructor params today:** `max_nodes_per_definition`, `max_parallel_branches`, `plugins_enabled`, `plugin_registry`, `workflow_plugin_registry`. Phase 5 adds `hitl_enabled`, `approval_policy` (or equivalent).

### 4.4 ChatMessage status & persistence

**Model** (`app/db/models.py:144`):

| Field | Current state |
| ----- | ------------- |
| `status` CHECK | `'complete'`, `'stopped'`, `'error'`, `'interrupted'` only |
| `pending_approval_id` | **Absent** — Phase 1 migration |

**Persistence flow:** `ChatService._persist_stream_result()` (`chat_service.py:1488`) writes assistant messages with caller-supplied `status`. Used by `UnifiedChatService` for all streaming paths.

**Epic 09 additions:** `waiting_approval`, `rejected` status values; `pending_approval_id` FK to `agent_tool_approvals` (Phase 1 migration).

### 4.5 ToolDefinition schema (`app/ai/tools/schemas.py`)

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
```

**Missing (Phase 1):** `requires_approval: bool = False`

**Policy union (Phase 1):** `ApprovalPolicy.requires_approval(tool)` = `tool.requires_approval OR tool.name in settings.hitl_required_tool_names`

---

## 5. Agent Runtime Extension Points

### 5.1 AgentExecutionStatus (`app/ai/agent/models/state.py`)

Current values: `created`, `planning`, `executing`, `reflecting`, `completed`, `failed`

**Epic 09 approach:** No new status enum value in v1 — pause is modeled via `AgentToolApproval` record + placeholder `ChatMessage.status='waiting_approval'`, not via `AgentExecutionStatus`.

### 5.2 AgentStreamEventType (`app/ai/agent/models/events.py`)

Current values: `start`, `planning`, `tool_start`, `tool_end`, `token`, `reflection`, `complete`, `error`

**Epic 09 Phase 2:** Add `waiting_approval` event type with payload (proposed calls, approval_id).

### 5.3 Scratchpad / ScratchpadEntry (`app/ai/agent/scratchpad/`)

| Aspect | Status |
| ------ | ------ |
| `ScratchpadEntry` | Pydantic `BaseModel` with `kind`, `content`, `tool_call_id`, `metadata`, `provider_message` |
| JSON serializable | ✅ Yes — all fields are JSON-compatible types |
| Persistence today | ❌ Never persisted (docstring: "never persisted") |
| HITL exception | Phase 2–3: snapshot to `agent_tool_approvals.scratchpad_snapshot` on pause only |

### 5.4 AgentExecutor resume hook

**Current:** No `resume_from_approval()` method — Phase 3 deliverable. Re-enters ReAct loop from `PLANNING` after decision.

---

## 6. Feature Flag Inventory (`app/core/config.py`)

| Flag | Default | Present | Epic 09 notes |
| ---- | ------- | ------- | ------------- |
| `tools_enabled` | `false` | ✅ | Unchanged |
| `mcp_enabled` | `false` | ✅ | MCP tools covered transparently via `ToolDefinition` |
| `agent_runtime_enabled` | `false` | ✅ | Agent approval gate requires flag on |
| `workflow_engine_enabled` | `false` | ✅ | Workflow approval nodes require flag on |
| `plugins_enabled` | `false` | ✅ | Plugin tools covered via `ToolDefinition.requires_approval` |
| `observability_enabled` | `false` | ✅ | `approval_span` Phase 7 |
| **`hitl_enabled`** | **`false` (planned)** | **❌** | **Phase 1 deliverable** |
| `hitl_required_tool_names` | — | ❌ | Phase 1 |
| `hitl_approval_timeout_hours` | — | ❌ | Phase 1 (documented only; enforcement Epic 10) |
| `hitl_max_reason_length` | — | ❌ | Phase 1 |

**DI pattern:** `@lru_cache` singletons in `app/ai/deps.py`; settings injected via `Depends(get_settings)`. Phase 1 adds `get_approval_policy()` factory following existing pattern.

---

## 7. HITL Subsystem Absence

Confirmed no prior HITL implementation:

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/hitl/` | ❌ Does not exist |
| `app/routers/approvals.py` | ❌ Does not exist |
| `app/schemas/approvals.py` | ❌ Does not exist |
| `HITL_ENABLED` in config | ❌ Not present |
| `ApprovalPolicy`, `AgentToolApproval`, `ApprovalResult` | ❌ Not present |
| `agent_tool_approvals` table | ❌ Not in migrations |
| `approval_revisions` table | ❌ Not in migrations |
| `AgentApprovalService` | ❌ Not present |
| `AgentExecutor.resume_from_approval()` | ❌ Not present |
| `approval_span` in observability | ❌ Not present |
| `record_workflow_approval_pending_delta` | ✅ Present (Epic 06/07 — reuse pattern for agent pending count) |
| `tests/ai/hitl/` | ❌ Does not exist |
| `tests/test_approvals_router.py` | ❌ Does not exist |
| Frontend `ApprovalsPage` / `approvalsClient` | ❌ Not present |

---

## 8. Architecture Review

### 8.1 Part I Architectural Invariants — Phase 0 Evidence

| Invariant | Phase 0 status | Notes |
| --------- | -------------- | ----- |
| Platform-first `ApprovalPolicy` | **Planned** | No policy exists yet; `ToolDefinition` has no `requires_approval` |
| Extend Epic 06 workflow approval additively | **Verified (prerequisite)** | `apply_decision`, CAS, `ApprovalNodeExecutor` operational and tested |
| New symmetrical primitive for chat/agent | **Planned** | `ToolRunner` dispatch site identified; no pause mechanism yet |
| Fail-closed graph validation | **Planned** | `GraphValidator` has 12 passes; HITL reachability check absent |
| Durable pause (Postgres) | **Planned (workflow)** / **Absent (agent)** | Workflow: `waiting_approval` on runs/node_executions exists; agent: no table yet |
| `HITL_ENABLED=false` flag-off parity | **Verified (baseline)** | No HITL code exists; full parity unverified until Phase 1+ / Phase 10 |
| Scratchpad snapshot exception | **Verified (design)** | `ScratchpadEntry` is JSON-serializable Pydantic model |
| CAS concurrency on decisions | **Verified (workflow)** | Epic 06 pattern in `apply_decision`; agent CAS planned Phase 3 |

### 8.2 Epic 09 Integration Points (by Phase)

| Phase | Integration point |
| ----- | ----------------- |
| 1 | `app/ai/hitl/` package, models, `ApprovalPolicy`, migration `0010`, `HITL_ENABLED`, unit tests |
| 2 | `ToolRunner` pre-dispatch gate, pause snapshot, `waiting_approval` stream event |
| 3 | `AgentApprovalService.decide()`, `AgentExecutor.resume_from_approval()` |
| 4 | `WorkflowManager.apply_decision()` + `edited_arguments`/`reason`; `build_approval_decision_output` |
| 5 | `GraphValidator` approval-required tool reachability |
| 6 | `app/routers/approvals.py` unified audit API |
| 7 | `approval_span`, HITL metrics |
| 8 | Reference eval scenarios (`--level hitl`) |
| 9 | Frontend approval inbox |
| 10 | Full validation & release |

### 8.3 Components to Reuse (DO NOT REIMPLEMENT)

Per Part II **Reuse Existing Components** — all verified present:

- `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, CAS helpers — `app/ai/workflow/`
- `WorkflowExecutor.continue_from_approval`, `schedule_run_task` — `app/ai/workflow/engine/`
- `ToolExecutor`, `ToolValidator`, `ToolRegistry`, `ToolAuthorizer` — `app/ai/tools/`
- `AgentExecutor`, `ToolRunner`, `Scratchpad`, `AgentStateManager` — `app/ai/agent/`
- `AgentStreamEvent`, `StreamPublisher`, SSE frame formatting — `app/ai/agent/models/events.py`, `app/schemas/chat.py`
- `GraphValidator` — `app/ai/workflow/graph/validator.py`
- `record_workflow_approval_pending_delta` — `app/ai/observability/metrics/instruments.py`
- `PluginRegistrar.register_tool`, MCP adapter — `app/ai/plugins/`, `app/ai/mcp/`
- `get_current_caller`, `CallerContext` — `app/core/caller.py`
- Feature flag infrastructure — `app/core/config.py`
- DI factories — `app/ai/deps.py`
- Evaluation harness — `app/ai/evaluation/` (`make eval` 15/15)

---

## 9. Dependency Verification

| Dependency | Phase 0 status | Notes |
| ---------- | -------------- | ----- |
| Epic 08 Plugin Architecture (predecessor) | **Verified** | Phases 0–10 complete; release summary published |
| Epic 06 Workflow Engine | **Verified** | Approval nodes, `apply_decision`, CAS, postgres store operational |
| Epic 01 Agent runtime | **Verified** | `ToolRunner`, `AgentExecutor`, `Scratchpad` tested |
| Epic 03 MCP Integration | **Verified** | Tools register via `ToolDefinition`; no HITL-specific code needed |
| Feature flag / DI patterns | **Verified** | `@lru_cache` singletons; bool flags in `Settings` |
| PostgreSQL / Alembic | **Verified** | Head: `0009_workflow_plugin_node_type.py`; next revision **0010** (Phase 1) |

---

## 10. Platform Test-Backed Baseline

Automated test evidence only — no live manual smoke tests in this audit:

| Capability | Automated test evidence | Count (this audit) |
| ---------- | ----------------------- | ------------------ |
| Agent runtime | `tests/ai/agent/` | **147** |
| Workflow package | `tests/ai/workflow/` | **207** |
| Workflow router | `tests/test_workflow_router.py` | **23** |
| Workflow execution tool | `tests/test_workflow_tool.py` | **11** |
| **Workflow integration total** | workflow package + router + tool | **241** |
| Workflow + MCP + plugins spot check | `tests/ai/workflow/` + router + tool + `tests/ai/mcp/` + `tests/test_plugins_router.py` + `tests/ai/plugins/` | **512** |
| Plugins router | `tests/test_plugins_router.py` | (included above) |
| Observability router | `tests/test_observability_router.py` | **15** |

Epic 09 plan baseline **1778** backend tests matches this audit run exactly.

---

## 11. Alembic Migrations

| Migration | Purpose |
| --------- | ------- |
| `0001`–`0007` | Chat, documents, pgvector, quotas, FTS, memory, workflow |
| `0008_observability_usage_cost.py` | Epic 07 — usage cost columns |
| `0009_workflow_plugin_node_type.py` | Epic 08 — `plugin` node type on workflow node executions |

**Epic 09 Phase 1:** `0010_hitl_tables.py` — `agent_tool_approvals`, `approval_revisions`; extend `chat_messages` status CHECK + `pending_approval_id`; add `workflow_node_executions.edited_arguments` / `.reason`.

---

## 12. Implementation Assumptions (from Part I)

| Assumption | Value |
| ---------- | ----- |
| Master flag | `HITL_ENABLED=false` (default) |
| Policy union | `ToolDefinition.requires_approval OR hitl_required_tool_names` |
| Agent pause granularity | Entire planned step pauses when any call requires approval |
| Workflow pause | Explicit `approval` node only (Epic 06 model unchanged) |
| Timeout enforcement | Documented only; `TODO(epic-10):` |
| Audit aggregation | Read-only merge of agent + workflow approvals; no denormalized audit table |

---

## 13. Acceptance (Phase 0)

| Criterion | Status |
| --------- | ------ |
| Existing platform fully operational | ✅ All gates pass |
| All extension points identified | ✅ §4 |
| No HITL implementation present | ✅ §7 |
| Baseline metrics recorded | ✅ §2–3, §10 |
| Epic 08 complete / authorized for Epic 09 | ✅ §1 |
| Part I architecture reviewed | ✅ §8 |

---

## 14. Exit Criteria & Authorization

| Item | Status |
| ---- | ------ |
| Baseline audit published | ✅ This document |
| User confirmation to proceed to Phase 1 | ⬜ **Pending** |

**Rollback:** Not applicable — Phase 0 made no code changes.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-09-human-in-the-loop.md](../plans/post-mvp-v2-epic-09-human-in-the-loop.md)
- Predecessor release: [docs/releases/post-mvp-v2-epic8-release-summary.md](../releases/post-mvp-v2-epic8-release-summary.md)
- Epic 08 Phase 0 audit (template): [docs/audits/post-mvp-v2-epic8-phase-0-baseline-audit.md](./post-mvp-v2-epic8-phase-0-baseline-audit.md)
- V2 strategy § Human-in-the-Loop: [docs/references/fullstack-ai-platform-v2-architecture-implementation-strategy.md](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md)
