---
epic: v2-01
title: Agent Framework
status: in_progress
version: 5
depends_on: [v1.1.1]
provides:
  [
    DefaultAgent,
    AgentRequest,
    AgentResponse,
    StreamPublisher,
    AgentConfig,
    AGENT_RUNTIME_ENABLED,
  ]
feature_flags: [AGENT_RUNTIME_ENABLED]
packages: [app/ai/agent]
test_paths: [tests/ai/agent]
---

# Post-MVP V2 Epic 01 — Agent Framework

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md)

---

# Part I — Design

## Objective

Reusable, provider-agnostic agent runtime under `app/ai/agent/` — execution core for chat, assistants, automation, and future multi-agent (V3). Separates planning, execution, ephemeral memory, reflection, streaming, tools, and provider interaction. Not a user-facing feature release; chat wiring is Phase 11 behind `AGENT_RUNTIME_ENABLED=false`.

## Principles

Platform-first · provider-agnostic · interface-driven · streaming-first · async-first · composition over inheritance · strong typing · testability · no over-engineering

## Architecture

```text
Request → Agent Runtime → Planner → Execution Loop → ToolExecutor + LLMProvider → StreamPublisher → Response
```

```text
app/ai/agent/
├── interfaces/   models/   exceptions.py
├── state/   scratchpad/   retry/   streaming/
├── planner/   executor/   reflection/   runtime/   adapters/
```

Providers stay at `app/providers/` (Protocol-only dependency in agent core).

## Components

| Component  | Role                          | Key outputs                            |
| ---------- | ----------------------------- | -------------------------------------- |
| Agent      | Entry point, lifecycle        | `AgentRequest` → `AgentResponse`       |
| Planner    | Iterative next-action (ReAct) | `ExecutionPlan`, `PlannedStep`         |
| Executor   | Run plan, LLM, tools, merge   | Step results, stream events            |
| State      | Per-execution tracking        | `AgentExecutionState`, transitions     |
| Scratchpad | Ephemeral working memory      | Entries; never auto-persisted          |
| Reflection | Optional quality loop         | `REPLAN` / `RETRY_STEP` / `FINISH`     |
| Retry      | Classified LLM + tool retry   | Wraps `retry_async`                    |
| Streaming  | Typed progress events         | `StreamPublisher`; SSE in adapter only |

## Scope

**In:** Agent abstraction, planner, executor, scratchpad, reflection, multi-tool execution, retry, streaming, state, tests, feature-flagged chat adapter.

**Out:** V3 multi-agent, Epic 3 MCP, Epic 4 memory, Epic 5 workflows, Epic 6 observability, Epic 7 plugins, Epic 8 HITL, provider relocation, UI, E2E tool-round streaming.

## Dependencies

| Requires | Provides to downstream                                                                                    |
| -------- | --------------------------------------------------------------------------------------------------------- |
| V1.1.1   | `DefaultAgent`, `AgentRequest`/`AgentResponse`, `StreamPublisher`, `AgentConfig`, `AGENT_RUNTIME_ENABLED` |

**Future consumers:** Epic 2 (RAG), Epic 3 (MCP), Epic 4 (Memory), Epic 5 (Workflows), Epic 6 (Observability), Epic 8 (HITL)

## Locked decisions

| Topic         | Decision                                              | Deferred to        |
| ------------- | ----------------------------------------------------- | ------------------ |
| Package       | `app/ai/agent/`; tests `tests/ai/agent/`              | —                  |
| Providers     | `app/providers/` + `LLMProvider` Protocol             | Relocate providers |
| Planner       | ReAct iterative; `ExecutionPlan` / `PlannedStep`      | Batch planning DAG |
| Scratchpad    | In-memory only                                        | Epic 4             |
| Reflection    | Optional; default off; max 2 when on                  | —                  |
| Multi-tool    | Parallel when independent; sequential on `depends_on` | —                  |
| Iterations    | Default 5; adapter may use 3                          | —                  |
| Policy        | Guest/tool auth at adapter + `ToolAuthorizer`         | —                  |
| RAG           | `UnifiedChatService` before agent handoff             | Epic 2             |
| Chat wiring   | Phase 11; flag default off                            | Default flag flip  |
| Observability | Structured log fields                                 | Epic 6             |

## Retry classification

| Condition                                    | Retry? |
| -------------------------------------------- | ------ |
| Timeout, connection, 429                     | Yes    |
| Validation, auth, not found, iteration limit | No     |

Defaults: `max_retries=3`, `retry_base_delay_seconds=1.0`. Wrap `app/core/retry.py` — no duplicate backoff.

## Reflection rules

When `reflection_enabled=true` (default **false**):

| Condition            | Decision                          |
| -------------------- | --------------------------------- |
| All tools failed     | `REPLAN`                          |
| Empty LLM content    | `RETRY_STEP`                      |
| Partial tool failure | `CONTINUE`                        |
| Success              | `FINISH`                          |
| Inconclusive         | Optional LLM (`reflection.v1.j2`) |

Bounded by `max_iterations`.

## Streaming strategy

- Core: `StreamPublisher` only — no FastAPI/`format_sse` in `app/ai/agent/`.
- Tool LLM rounds: non-streaming; final answer: token-streamed (V1.1 parity).
- Phase 11 adapter maps to SSE frames (`start`, `delta`, `tool_start`, `tool_end`, `end`, `error`).
- Events: start, planning, tool_start/end, token, reflection, complete, error (no raw provider text).

## Public APIs (stable after Phase 1)

| API                                                                                                          | Kind      |
| ------------------------------------------------------------------------------------------------------------ | --------- |
| `Agent`, `Planner`, `Executor`, `RetryPolicy`, `StreamPublisher`                                             | Protocol  |
| `AgentRequest`, `AgentContext`, `AgentResponse`, `ExecutionPlan`, `PlannedStep`, `StepAction`, `AgentConfig` | Model     |
| `AgentError`, `AgentIterationLimitError`, `AgentTimeoutError`                                                | Exception |

Internal (may evolve): `ReActPlanner`, `DefaultAgent`, state/scratchpad/retry/streaming impls, adapters.

## Configuration defaults

`max_iterations=5` · `reflection_enabled=false` · `max_retries=3` · `AGENT_RUNTIME_ENABLED=false` · parallel tools configurable · timeout configurable

## Design acceptance

Provider- and tool-independent · async · strongly typed · streaming via publisher · ≥80% coverage on `app/ai/agent/` · minimal coupling · no SDK imports in core

## Execution flow

Receive request → create state + scratchpad → plan → execute steps (tools/LLM) → optional reflect → stream progress → finalize response

## Architectural invariants

These rules must remain true throughout Epic 1 implementation. Violations require explicit user approval and Part I update.

- `app/ai/agent/` core stays **provider-agnostic** — depend on `LLMProvider` Protocol only; no provider SDK imports.
- **No transport or domain imports** in core — no FastAPI, `format_sse`, chat schemas, or session/HTTP logic (adapters under `adapters/` only).
- **Scratchpad is ephemeral** — in-memory for one execution; never auto-persisted to DB.
- **State is execution-scoped** — not session-scoped; session persistence stays in chat services.
- **Tool lifecycle stays in `ToolExecutor`** — agent wraps; does not reimplement validation or authorization.
- **Retry wraps `retry_async`** — no duplicate backoff or parallel retry implementations.
- **Streaming decoupled from SSE** — core uses `StreamPublisher` only; SSE mapping in Phase 11 adapter.
- **V1.1 unchanged when flag off** — `AGENT_RUNTIME_ENABLED=false` leaves legacy chat path untouched.
- **Public APIs stable after Phase 1** — changes to frozen Protocols/models require user approval.
- **No future-epic behaviour in core** — RAG-in-agent, MCP, memory, workflows, etc. remain out of scope; use `TODO(epic-N):` only.

---

# Part II — Execution

## Reuse existing components

**DO NOT REIMPLEMENT:**

| Component                                            | Location                       |
| ---------------------------------------------------- | ------------------------------ |
| `retry_async`, `is_retryable_exception`              | `app/core/retry.py`            |
| `ToolExecutor`, registry, validator, authorizer      | `app/ai/tools/`                |
| `LLMProvider`, `ProviderFactory`, `get_capabilities` | `app/providers/`               |
| `PromptManager`                                      | `app/ai/prompts/`              |
| SSE frame models                                     | `app/schemas/chat.py`          |
| `format_sse()`, `normalize_chat_error()`             | `app/services/chat_service.py` |
| `FakeProvider`, echo stub                            | `tests/fakes.py`               |

## Not allowed

- Refactor unrelated code beyond adapter/integration steps
- Move `app/providers/` or rename packages
- Add dependencies without user approval
- Change existing chat API contracts (adapter is additive behind flag)
- Implement Epic 2+ functionality
- Duplicate tool validation/auth outside `ToolExecutor`
- Import provider SDKs or FastAPI in `app/ai/agent/` core

## Baseline (post-V1.1.1, 2026-07-22)

| Area                     | State                                                    |
| ------------------------ | -------------------------------------------------------- |
| Backend tests / coverage | 453 passed, **87.14%** `app/`                            |
| Orchestration            | `UnifiedChatService` → `ToolChatService` / `ChatService` |
| Agent code               | None                                                     |

## Phase status

| Phase | Name                         | Effort | Status    |
| ----- | ---------------------------- | ------ | --------- |
| 0     | Baseline Audit               | XS     | Completed |
| 1     | Scaffold, Models, Interfaces | M      | Completed |
| 2     | Agent State                  | S      | Completed |
| 3     | Scratchpad                   | S      | Completed |
| 4     | Retry Framework              | S      | Completed |
| 5     | Streaming Engine             | M      | Completed |
| 6     | Planner                      | M      | Completed |
| 7     | Multi-Tool Execution         | M      | Completed |
| 8     | Execution Loop               | L      | Completed |
| 9     | Reflection                   | S      | Completed |
| 10    | Agent Runtime                | M      | Completed |
| 11    | Chat Adapter                 | M      | Completed |
| 12    | Validation & Release         | S      | Completed |

---

## Phase 0 — Baseline Audit

**Effort:** XS

**Deliverables:** `docs/audits/post-mvp-v2-epic1-phase-0-baseline-audit.md`

**Steps:**

- [x] Confirm V1.1.1 complete
- [x] Run backend gates: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
- [x] Run frontend gates: `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build`
- [x] Inventory `tool_chat_service.py`, `unified_chat_service.py`, `chat_service.py`, `ai/tools/executor.py`, `providers/base.py`, `schemas/chat.py`, `core/retry.py`
- [x] Confirm no `app/ai/agent/` conflicts
- [x] Write audit doc; record metrics below
- [x] Phase 0 complete — user confirmed

**Verify:** `make lint && make typecheck && make test-cov && make eval`

**Acceptance:**

- All quality gates pass
- Orchestration inventory documented with file paths
- No repository code changes

**Exit criteria:**

- Audit doc published; baseline recorded; user confirmed Phase 0

**Completion record:**

| Metric                   | Result                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------- |
| Backend tests / coverage | **458 passed**, **87.23%** `app/`                                                                    |
| Frontend tests           | **165 passed / 2 failed** (167 total)                                                                |
| Eval CLI                 | **5 passed**, 0 failed                                                                               |
| Git commit               | `05c8e59`                                                                                            |
| Audit doc                | [post-mvp-v2-epic1-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic1-phase-0-baseline-audit.md) |

---

## Phase 1 — Scaffold, Models, Interfaces

**Effort:** M

**Deliverables:** `app/ai/agent/` tree; `AGENT_RUNTIME_ENABLED=false`; public API exports

**Steps:**

- [x] Create package tree (`interfaces/`, `models/`, `exceptions.py`)
- [x] Implement models and Protocol interfaces per Part I § Public APIs
- [x] Add `AgentConfig` defaults per Part I § Configuration defaults
- [x] Export public API from `__init__.py`
- [x] Add flag to `config.py` and `.env.example`
- [x] Add `tests/ai/agent/test_models.py`, `test_interfaces.py`
- [x] Phase 1 complete — user confirmed

**Verify:** `make typecheck && pytest tests/ai/agent/test_models.py tests/ai/agent/test_interfaces.py`

**Acceptance:**

- Package imports cleanly; mypy passes
- Public APIs match Part I freeze list
- Chat hot path untouched

**Exit criteria:**

- Tests pass; public API finalized; user confirmed Phase 1

---

## Phase 2 — Agent State

**Effort:** S

**Deliverables:** `state/manager.py`; expanded `models/state.py`

**Steps:**

- [x] Implement `AgentExecutionState`, `AgentStateManager`, transitions (`CREATED` → `COMPLETED`)
- [x] `to_dict()` without secrets; iteration limit helper
- [x] Add `tests/ai/agent/test_state_manager.py`
- [x] Phase 2 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_state_manager.py`

**Acceptance:**

- Lifecycle and invalid transitions handled; no DB/chat imports

**Exit criteria:**

- Tests pass; user confirmed Phase 2

---

## Phase 3 — Scratchpad

**Effort:** S

**Deliverables:** `scratchpad/scratchpad.py`, `scratchpad/store.py`

**Steps:**

- [x] Implement `Scratchpad` + `ScratchpadStore`; wire in `AgentStateManager.create_initial_state`
- [x] `to_message_context()` compatible with `LLMProvider`
- [x] Add `tests/ai/agent/test_scratchpad.py`
- [x] Phase 3 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_scratchpad.py`

**Acceptance:**

- Per-execution isolation; never persisted

**Exit criteria:**

- Tests pass; user confirmed Phase 3

---

## Phase 4 — Retry Framework

**Effort:** S

**Deliverables:** `retry/policies.py`, `classifier.py`, `executor.py`

**Steps:**

- [x] Implement policies per Part I § Retry classification
- [x] `retry_operation()` wrapping `retry_async`
- [x] Add `tests/ai/agent/test_retry.py`
- [x] Phase 4 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_retry.py`

**Acceptance:**

- Classification matches Part I table; core retry tests still pass

**Exit criteria:**

- Tests pass; user confirmed Phase 4

---

## Phase 5 — Streaming Engine

**Effort:** M

**Deliverables:** `streaming/publisher.py`, `streaming/adapter.py`; expanded `models/events.py`

**Steps:**

- [x] Event models per Part I § Streaming strategy
- [x] `InMemoryStreamPublisher`, `QueueStreamPublisher`, no-op publisher
- [x] `sse_frame_from_agent_event()` for Phase 11
- [x] Add `tests/ai/agent/test_streaming.py`
- [x] Phase 5 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_streaming.py`

**Acceptance:**

- No FastAPI/`format_sse` in core; SSE frame names match `app/schemas/chat.py`

**Exit criteria:**

- Tests pass; user confirmed Phase 5

---

## Phase 6 — Planner

**Effort:** M

**Deliverables:** `planner/react_planner.py`, `parser.py`, `prompts/agent/planner.v1.j2`

**Steps:**

- [x] Implement `ReActPlanner.plan_next()` + parser
- [x] Inject `LLMProvider`, `ToolRegistry`, `PromptManager`, retry only
- [x] Add `tests/ai/agent/test_planner.py` + prompt snapshot
- [x] Phase 6 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_planner.py`

**Acceptance:**

- Single tool, parallel tools, finalize, iteration-limit scenarios pass

**Exit criteria:**

- Tests pass; user confirmed Phase 6

---

## Phase 7 — Multi-Tool Execution

**Effort:** M

**Deliverables:** `executor/tool_runner.py`, `dependency_resolver.py`, `result_aggregator.py`

**Steps:**

- [x] Wrap `ToolExecutor` only; sequential vs parallel per Part I locked decisions
- [x] Emit tool stream events; apply `ToolRetryPolicy`
- [x] Add `tests/ai/agent/test_tool_runner.py`
- [x] Phase 7 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_tool_runner.py tests/test_tool_platform.py`

**Acceptance:**

- Parallel, sequential, dependency-chain tests pass

**Exit criteria:**

- Tests pass; tool platform regression green; user confirmed Phase 7

---

## Phase 8 — Execution Loop

**Effort:** L

**Deliverables:** `executor/agent_executor.py`, `llm_step.py`, `finalizer.py`

**Steps:**

- [x] Main loop per Part I § Execution flow; parity ref: `ToolChatService._run_tool_loop`
- [x] V1.1-style iteration limit message
- [x] Add `tests/ai/agent/test_agent_executor.py`
- [x] Phase 8 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_agent_executor.py --cov=app/ai/agent --cov-fail-under=80`

**Acceptance:**

- E2E tool round, multi-iter, LLM-only, limit, streaming events; no chat service imports

**Exit criteria:**

- Integration tests pass; ≥80% on `app/ai/agent/`; user confirmed Phase 8

---

## Phase 9 — Reflection

**Effort:** S

**Deliverables:** `reflection/engine.py`, `quality_checker.py`, `prompts/agent/reflection.v1.j2`

**Steps:**

- [x] Implement engine per Part I § Reflection rules
- [x] Integrate when `reflection_enabled`; emit events
- [x] Add `tests/ai/agent/test_reflection.py`
- [x] Phase 9 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_reflection.py tests/ai/agent/test_agent_executor.py`

**Acceptance:**

- All decision paths tested; disabled = no-op

**Exit criteria:**

- Tests pass; user confirmed Phase 9

---

## Phase 10 — Agent Runtime

**Effort:** M

**Deliverables:** `runtime/default_agent.py`, `factory.py`; `get_agent_runtime()` in `deps.py`

**Steps:**

- [x] `DefaultAgent.run()` / `.stream()`; lifecycle teardown
- [x] Structured logs: `agent_execution_id`, `agent_iterations`, `agent_tools_used`
- [x] Add `tests/ai/agent/test_default_agent.py`
- [x] Phase 10 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_default_agent.py`

**Acceptance:**

- Runnable without HTTP; stream ends with complete; scratchpad cleaned up

**Exit criteria:**

- Tests pass; user confirmed Phase 10

---

## Phase 11 — Chat Adapter

**Effort:** M

**Deliverables:** `adapters/chat_adapter.py`, `chat_stream_adapter.py`; branches in `UnifiedChatService`

**Steps:**

- [x] Request/response + stream mapping; branch on `AGENT_RUNTIME_ENABLED`
- [x] Preserve guest denial, persistence, usage, `tools_used`, `retrieved_chunks`
- [x] RAG in `UnifiedChatService` before agent handoff
- [x] Add `tests/ai/agent/test_chat_adapter.py`; update README + `.env.example`
- [x] Phase 11 complete — user confirmed

**Verify:** `pytest tests/ai/agent/test_chat_adapter.py` · flag off: `pytest tests/test_unified_chat.py tests/test_tool_chat_service.py`

**Acceptance:**

- Flag off: legacy unchanged; flag on: web search + SSE parity

**Exit criteria:**

- Parity tests pass; user confirmed Phase 11

**Rollback:**

- Set `AGENT_RUNTIME_ENABLED=false`; remove adapter branches from hot path
- Re-run: `pytest tests/test_unified_chat.py tests/test_tool_chat_service.py`
- Revert PR if needed

---

## Phase 12 — Validation & Release

**Effort:** S

**Steps:**

- [x] Full suite: flag off, then flag on
- [x] Docker smoke; `make eval`
- [x] Docs + `docs/releases/post-mvp-v2-epic1-release-summary.md`
- [x] Set Phase status to **Completed**; tick DoD below
- [ ] Phase 12 complete — user confirmed; Epic 2 authorized

**Verify:** `make test-cov && make eval`

**Acceptance:**

- Part I design acceptance met; coverage gates met

**Exit criteria:**

- Release summary published; user confirmed Phase 12

**Completion record:**

| Metric                   | Result                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------- |
| Backend tests / coverage | Flag-off: **604 passed**, **88.19%** `app/`; flag-on: **604 passed**, **87.61%** `app/`        |
| `app/ai/agent/` coverage | **91.12%** (144 tests; gate ≥80%)                                                              |
| Eval CLI                 | **5 passed**, 0 failed (`2026-07-23T23:55:38Z`)                                                |
| Flag-off regression      | **Pass** — `AGENT_RUNTIME_ENABLED=false make test-cov`                                         |
| Flag-on parity           | **Pass** — `AGENT_RUNTIME_ENABLED=true make test-cov` (settings default-off test env-isolated) |

---

## Files index

| Path                                                      | Action | Owner   | Phase  |
| --------------------------------------------------------- | ------ | ------- | ------ |
| `docs/audits/post-mvp-v2-epic1-phase-0-baseline-audit.md` | create | Docs    | 0      |
| `app/ai/agent/**`                                         | create | Core    | 1–11   |
| `app/core/config.py`                                      | modify | Core    | 1      |
| `app/ai/deps.py`                                          | modify | Adapter | 10     |
| `app/services/unified_chat_service.py`                    | modify | Adapter | 11     |
| `app/ai/prompts/agent/*.j2`                               | create | Core    | 6, 9   |
| `tests/ai/agent/**`                                       | create | Tests   | 1–11   |
| `backend-python/.env.example`                             | modify | Docs    | 1, 11  |
| `backend-python/README.md`                                | modify | Docs    | 11, 12 |
| `README.md`                                               | modify | Docs    | 12     |
| `docs/releases/post-mvp-v2-epic1-release-summary.md`      | create | Docs    | 12     |

## PR map

One PR per phase; branch `v2/epic-01/phase-{pp}-{slug}`.

## Risks

| Risk                         | Mitigation                                                      |
| ---------------------------- | --------------------------------------------------------------- |
| Breaks V1.1 chat             | Flag default off; Phase 11 rollback; parity before default flip |
| Scope creep                  | Not Allowed + Part I scope; design changes in Part I only       |
| Accidental provider coupling | Protocol-only core; test/lint no SDK in `app/ai/agent/`         |
| Parallel tool races          | Dependency resolver; parallel off by default                    |
| Reflection loops             | Bounded iterations; rule-based checks first                     |
| AI duplication               | Reuse section                                                   |

## Observability

Structured log fields (no message content): `agent_execution_id`, `agent_iterations`, `agent_tools_used`, `agent_reflection_total`, `agent_retry_total`, `agent_parallel_tools_total`, `agent_iteration_limit_reached`.

## Definition of done

- [x] Part I components delivered; Part I design acceptance met
- [x] Public APIs stable per Phase 1
- [x] Chat adapter behind flag; V1.1 unchanged when off; parity when on
- [x] `tests/ai/agent/` complete; coverage ≥80% on `app/ai/agent/` and `app/`
- [x] `make eval` passes; release summary published
- [ ] All phases **Completed**; user confirmed Phase 12
- [x] Program DoD: [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md)

## Changelog

| Date       | Change                                                                                                                                                                                                                         |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-07-23 | Phase 0 baseline audit published; completion record filled; Phase 0 status → In Progress (pending user confirmation). Part II only.                                                                                            |
| 2026-07-23 | Phase 0 and Phase 1 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                     |
| 2026-07-23 | Phase 2 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 4 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 5 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 6 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 7 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 8 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 9 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                 |
| 2026-07-23 | Phase 10 marked Completed; all steps checkmarked. Part II only.                                                                                                                                                                |
| 2026-07-24 | Phase 11 marked Completed (code already delivered; plan status reconciled in Phase 12). Phase 12 status → In Progress. Part II only.                                                                                           |
| 2026-07-24 | Phase 12 validation complete: flag-off/on `make test-cov`, `make eval`, Docker smoke, frontend gates, release summary published. Phase 12 status → Completed (pending user confirmation / Epic 2 authorization). Part II only. |
