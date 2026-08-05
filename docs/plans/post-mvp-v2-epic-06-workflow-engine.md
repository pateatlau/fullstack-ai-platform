---
epic: v2-06
title: Workflow Engine
status: in_progress
version: 1.14
depends_on: [v2-05]
provides:
  [
    WorkflowManager,
    WorkflowStore,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    WorkflowRun,
    WorkflowNodeExecution,
    WorkflowContext,
    WorkflowExecutor,
    GraphValidator,
    ConditionEvaluator,
    PostgresWorkflowStore,
    WorkflowExecutionTool,
    WORKFLOW_ENGINE_ENABLED,
    workflow_router,
  ]
feature_flags: [WORKFLOW_ENGINE_ENABLED]
packages: [app/ai/workflow]
test_paths:
  [
    tests/ai/workflow,
    tests/test_workflow_router.py,
    tests/test_workflow_tool.py,
    frontend/src/pages/WorkflowsPage.test.tsx,
    frontend/src/api/workflowClient.test.ts,
  ]
---

# Post-MVP V2 Epic 06 — Workflow Engine

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "6. Workflow Engine"

**Predecessor:** [Epic 05 — Memory System](./post-mvp-v2-epic-05-memory-system.md)

---

# Part I — Design

## Objective

Introduce a reusable, provider-agnostic Workflow Engine that lets the platform define, execute, pause, resume, and audit multi-step graphs of tasks, LLM calls, agent sub-tasks, and human approvals — independent of any single chat turn.

The Agent Framework (Epic 01) executes one bounded ReAct loop per request. The Workflow Engine is a distinct, longer-lived orchestration layer: an explicit graph of nodes and edges whose execution can outlive the triggering HTTP or tool request, pause indefinitely for human approval, survive process restarts via checkpoint resume (single-process — not distributed), and fan work out into parallel branches. A workflow may call into the Agent Framework or the Tool platform for individual node execution, but the two subsystems remain independently addressable.

**Delivers:** A durable workflow graph model (nodes, edges, conditional routing), a checkpointing execution engine (sequential, parallel fan-out/fan-in), task/LLM/agent node types, human approval nodes with pause/resume, node-level retry and run-level crash recovery, an authenticated Workflow REST API, an agent-invocable `WorkflowExecutionTool`, and a frontend workflow dashboard — all behind `WORKFLOW_ENGINE_ENABLED=false` (default).

**Does not ship:** A visual drag-and-drop workflow builder (definitions are JSON/API-authored in v1); scheduled/cron-triggered workflows; distributed multi-worker execution; a workflow plugin SDK or dynamically loaded external node types; the full human-in-the-loop audit trail and editable-tool-argument UX; OpenTelemetry spans; multi-agent collaborative workflows; shared/organization workflows; workflow-scoped long-term memory.

Capabilities:

- Workflow graphs (nodes, edges, versioned definitions)
- Conditional routing
- Parallel execution (fork/join)
- Human approval nodes
- Resume/retry
- Persistence

The Workflow Engine is additive. When disabled, existing chat, RAG, MCP, memory, voice, agent, and streaming pipelines remain unchanged.

---

## Design Principles

- Platform-first
- Provider-agnostic (storage and node execution)
- Composition over coupling — reuses Tools, Agent, and Providers; does not reimplement them
- Deterministic orchestration
- Explainable execution (every transition persisted and inspectable)
- Security by default — no arbitrary code evaluation; conditions use a declarative DSL only
- Explicit run lifecycle
- Feature-flag rollout

---

## Scope

### In Scope

- Workflow graph definition (nodes, edges, versioning)
- Graph validation (cycles, reachability, structural integrity)
- Sequential execution engine with checkpointing
- Conditional routing (declarative condition DSL)
- Parallel execution (fork/join with configurable join policy)
- Task nodes (existing `ToolExecutor`)
- LLM nodes (existing `LLMProvider` / `PromptManager`)
- Agent nodes (existing Agent Framework `DefaultAgent`)
- Human approval nodes (pause, decision, resume)
- Node-level retry and run-level crash recovery
- Durable persistence of definitions, runs, and node executions
- Authenticated Workflow management REST API
- Agent-invocable `WorkflowExecutionTool`
- Frontend workflow dashboard (list, trigger, inspect, approve/reject)

### Out of Scope

- Visual drag-and-drop workflow builder
- Scheduled / cron-triggered workflows
- Distributed or multi-worker execution
- Workflow plugin SDK / externally loaded node types
- Full HITL audit trail, editable tool arguments, approval UX polish
- OTel spans, prompt regression, evaluation harness
- Multi-agent collaborative workflows
- Shared/organization workflows
- Workflow-scoped memory

---

## High-Level Architecture

```text
Client (REST API)          Agent (WorkflowExecutionTool)
        │                             │
        └───────────────┬────────────┘
                         ▼
                  WorkflowManager
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                  ▼
  Definition CRUD   WorkflowRun         Approval
  + GraphValidator   Lifecycle          Decisions
        │                │                  │
        └────────┬───────┴──────────────────┘
                 ▼
           WorkflowExecutor
                 │
   ┌─────────┬───┴──────┬───────────┬────────────┐
   ▼         ▼           ▼           ▼            ▼
TaskNode  LLMNode    AgentNode   RouterNode  Fork/JoinNode
(ToolExecutor) (LLMProvider) (DefaultAgent) (ConditionEvaluator) (asyncio)
   │         │           │           │            │
   └────┬────┴───────────┴───────────┴────────────┘
        ▼
  WorkflowStore (checkpoint after every node transition)
        ▼
   PostgreSQL (workflow_definitions / workflow_runs / workflow_node_executions)
```

---

## Locked Architectural Decisions

| Topic                   | Decision                                                                                                                             | Deferred to                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Graph model              | Directed graph; cycles are rejected at validation time (no implicit loop nodes in v1)                                                | Explicit loop/iteration node type → future           |
| Execution model          | Single-process, in-process `asyncio` scheduler with a DB checkpoint after every node transition — not a distributed workflow engine | Distributed execution (Temporal-like) → future epic  |
| Run launch contract      | `start_run()` persists the run, schedules `WorkflowExecutor` on an in-process `asyncio.Task`, and returns `run_id` + current status immediately — never blocks until terminal completion; callers poll via run APIs or tool `action=status`. Requires caller-supplied `idempotency_key` (owner-scoped); retries with the same `(owner_id, workflow_definition_id, idempotency_key)` return the existing run and do not start duplicate work | Synchronous wait-for-completion API → future          |
| Storage                  | PostgreSQL only (`workflow_definitions`, `workflow_runs`, `workflow_node_executions`); no vector/embedding columns                   | Alternate backends (Redis hot-state) → future        |
| Agent/Tool integration   | Workflows invoke Tools via existing `ToolExecutor` and Agent Framework via existing `DefaultAgent` — never reimplemented             | —                                                    |
| Chat/agent invocation    | Workflows are triggered via REST API or a registered `WorkflowExecutionTool`; never via direct `ChatService`/`UnifiedChatService` hooks | Agent-authored workflow graphs → V3                  |
| Approval nodes           | Pause execution; persist `waiting_approval`; decisions applied via atomic CAS (`status=waiting_approval` → terminal) in one transaction with run transition; on decision set `decision=approved\|rejected` and node `status=succeeded\|failed`; resume via REST decision endpoint | HITL audit trail, editable tool arguments → Epic 09  |
| Conditional routing      | Deterministic declarative condition DSL evaluated against `WorkflowContext`; no `eval()` / arbitrary code                            | Richer expression language → future                  |
| Parallel execution       | Explicit Fork/Join node pair; branches run concurrently via `asyncio.gather`; join policy `all` \| `any` \| `count(n)`. Parallel branch checkpoints merge via optimistic `checkpoint_version` on `WorkflowRun` (retry on conflict) — no last-writer-wins over `context` or `current_node_ids` | —                                                    |
| Retry                    | Per-node retry policy wraps `app/core/retry.py`; workflow-level resume is a distinct concern from node-level retry                    | —                                                    |
| Crash-safe `running`     | A node left `status=running` after crash is never assumed complete. Re-execute side-effecting nodes (task/llm/agent) only with a stable per-attempt `execution_receipt_id` (`{run_id}:{node_id}:{attempt}`); router/fork/join are safe to re-run; otherwise fail the run rather than duplicate external effects | Platform-wide durable tool/LLM receipt store → future |
| Background execution     | Same in-process `asyncio` scheduler as run launch; checkpoints continue progress while the process is up; after process restart, `resume()` rehydrates from DB — no dedicated worker or queue | Background Jobs queue → Epic 10                     |
| Auth                     | Workflow definitions and runs are owner-scoped; authenticated users only (no guest workflows)                                        | Shared/organization workflows → future               |
| Versioning               | Definitions are immutable once referenced by a run; edits create a new version                                                       | Full version diff/rollback UI → Epic 08 plugin SDK   |

---

## High-Level Flow

Trigger (REST API or `WorkflowExecutionTool`)
→ Load & Validate Definition
→ Create WorkflowRun (`status=running`)
→ Schedule in-process `WorkflowExecutor` (`asyncio.Task`)
→ Return `run_id` + status snapshot to caller (non-blocking)
→ [background] WorkflowExecutor step loop
→ Execute ready node(s): task | llm | agent | router | fork | join | approval
→ Persist WorkflowNodeExecution + checkpoint WorkflowRun
→ Repeat while ready nodes remain
→ Run resolves to `completed` | `failed` | `waiting_approval` | `cancelled`

On approval decision:

→ `WorkflowManager.apply_decision()` — atomic CAS on `WorkflowNodeExecution` (`WHERE status=waiting_approval`)
→ In one transaction: record decision, terminal node status, and run transition (`waiting_approval` → `running` or terminal)
→ After commit: schedule `WorkflowExecutor` continuation from the approval node
→ Continue the step loop

On crash / restart:

→ `WorkflowManager.resume(run_id)` rehydrates `WorkflowRun` + completed `WorkflowNodeExecution` rows
→ Any node still `status=running` is treated as interrupted (never promoted to `succeeded`)
→ Side-effecting nodes re-execute only with the same per-attempt `execution_receipt_id`; deterministic nodes (router/fork/join) may re-run directly
→ `WorkflowExecutor` recomputes ready nodes and continues

---

## End-to-End Sequence

```text
Client / Agent
 │
 │ Start workflow (definition_id, input)
 ▼
WorkflowManager.start_run()
 │
 ├── Load WorkflowDefinition (validated at creation time)
 │
 ├── Create WorkflowRun (status=running, context=trigger_input)
 │
 ├── Schedule WorkflowExecutor on in-process asyncio.Task
 │
 └── Return run_id + status snapshot to caller ◄── does not wait for completion
 │
 │ (background task)
 ▼
WorkflowExecutor.step()  ◄────────────────────────────┐
 │                                                     │
 ├── Resolve ready node(s) from graph + node statuses  │
 │                                                     │
 ├── Execute node (Task / LLM / Agent / Router /       │
 │    Fork / Join / Approval)                          │
 │                                                     │
 ├── On failure: apply RetryPolicy (retry or fail run)  │
 │                                                     │
 ├── Persist WorkflowNodeExecution + checkpoint run     │
 │                                                     │
 └── More ready nodes? ─────────────yes────────────────┘
     │ no
     ▼
WorkflowRun status = completed | failed | waiting_approval | cancelled
 │
 ▼
Publish Workflow Events

Caller observes progress via GET /api/workflow-runs/{run_id}
or WorkflowExecutionTool action=status (poll as needed)
```

**Approval Timing**

When execution reaches an `approval` node, the run transitions to `waiting_approval` and the step loop stops. The pending `WorkflowNodeExecution` remains `waiting_approval` until a caller submits an approve/reject decision through the REST API. Approval does not time out automatically in v1 (`workflow_approval_timeout_hours=0` means indefinite).

---

## Storage Architecture

```text
WorkflowManager
      │
 ┌────┼────────────────┬──────────────────┐
 ▼    ▼                ▼                  ▼
Definitions        Runs            Node Executions
      │                │                  │
      └────────┬───────┴──────────────────┘
               ▼
         WorkflowStore
               ▼
      PostgresWorkflowStore
               ▼
          PostgreSQL
```

---

## Workflow Store Contract

All persistence goes through one interface.

Responsibilities include:

- Store/update `WorkflowDefinition`; retrieve by id and by owner
- Create `WorkflowRun`; atomically checkpoint run status + context
- Look up an existing run by `(owner_id, workflow_definition_id, idempotency_key)` for start deduplication
- Append `WorkflowNodeExecution` rows (one row per node attempt)
- Atomically merge run checkpoints under parallel branches: append node execution + merge `context`/`current_node_ids` in one transaction with optimistic `checkpoint_version` check and retry on conflict (never blind overwrite)
- Retrieve a run with its full node execution history
- List runs by owner, definition, and status
- Record approval decisions via conditional update (`WHERE status='waiting_approval'`) or row lock; transition `WorkflowRun` in the same transaction; no-op or reject when already decided

Initial implementation:

- `PostgresWorkflowStore`

Future implementations:

- Redis-backed hot-state cache (performance)
- Distributed store (Temporal-like backend)

The remainder of the platform depends only on the `WorkflowStore` interface.

---

## Execution Pipeline

```text
WorkflowRun (context)
        │
GraphValidator (definition-time only — not re-run per step)
        │
WorkflowExecutor
        │
 Ready Node Resolver (topological + fork/join aware)
        │
 ├─ TaskNodeExecutor      → ToolExecutor
 ├─ LLMNodeExecutor       → LLMProvider / PromptManager
 ├─ AgentNodeExecutor     → DefaultAgent
 ├─ RouterNodeExecutor    → ConditionEvaluator
 ├─ ForkNodeExecutor      → asyncio.gather (branches)
 ├─ JoinNodeExecutor      → branch result merge (join policy)
 └─ ApprovalNodeExecutor  → pause (waiting_approval)
        │
 Node Result
        │
 RetryPolicy (on failure — retry, or fail the run)
        │
 Checkpoint (WorkflowStore)
        │
 Next ready nodes?
```

---

## Canonical Workflow Representation

```text
WorkflowDefinition
------------------
id
owner_id
name
description
version
status              (draft | active | archived)
entry_node_id
nodes[]             -> WorkflowNode
edges[]             -> WorkflowEdge
metadata
created_at
updated_at
```

```text
WorkflowNode
------------
id
type                (task | llm | agent | router | fork | join | approval | terminal)
config              (type-specific — tool name/args, prompt template, agent goal, join policy, etc.)
retry_policy        (max_retries, base_delay_seconds)
timeout_seconds
```

```text
WorkflowEdge
------------
id
from_node_id
to_node_id
condition           (optional Condition DSL — omitted edges are unconditional)
```

```text
WorkflowRun
-----------
id
workflow_definition_id
owner_id
idempotency_key     (required at start; unique per owner + definition — dedupes retries)
session_id          (optional — set when triggered from a chat session via the tool)
status              (pending | running | waiting_approval | completed | failed | cancelled)
context             (accumulated node outputs + trigger input)
current_node_ids[]  (supports parallel branches)
checkpoint_version  (monotonic int; optimistic merge for concurrent branch checkpoints)
error
created_at
updated_at
started_at
completed_at
```

```text
WorkflowNodeExecution
---------------------
id
run_id
node_id
node_type
attempt
status              (pending | running | waiting_approval | succeeded | failed | skipped | cancelled)  — NodeStatus only; no separate rejected status
input
output
error
decided_by          (user id — approval nodes only)
decided_at
decision            (approved | rejected — approval nodes only; reject sets status=failed, not status=rejected)
started_at
completed_at
```

All execution, persistence, retrieval, and REST responses operate on these canonical models. No parallel or provider-specific representations exist.

---

## WorkflowContext

Everything a running workflow needs is normalized into one context object.

```text
WorkflowContext
---------------
trigger_input       (initial input provided at run start)
variables           (node_id -> output, accumulated as nodes complete)
metadata            (owner_id, session_id, correlation id)
```

Node executors read from and write to `WorkflowContext`; `ConditionEvaluator` reads from it only. Node executors never access `WorkflowStore` directly — persistence is the executor's responsibility, not the node's.

---

## Package Structure

```text
app/
└── ai/
    └── workflow/
        ├── __init__.py
        ├── interfaces/
        │   └── workflow_store.py
        ├── providers/
        │   └── postgres.py
        ├── models/
        │   ├── definition.py        # WorkflowDefinition, WorkflowNode, WorkflowEdge, NodeType
        │   ├── run.py               # WorkflowRun, WorkflowNodeExecution, RunStatus, NodeStatus
        │   └── context.py           # WorkflowContext
        ├── graph/
        │   ├── validator.py         # GraphValidator
        │   └── traversal.py         # topological / ready-node helpers
        ├── conditions/
        │   └── evaluator.py         # ConditionEvaluator (declarative DSL)
        ├── nodes/
        │   ├── base.py              # NodeExecutor Protocol
        │   ├── task_node.py
        │   ├── llm_node.py
        │   ├── agent_node.py
        │   ├── router_node.py
        │   └── parallel_node.py     # Fork + Join
        ├── retry/
        │   └── policy.py            # wraps app/core/retry.py
        ├── engine/
        │   └── executor.py          # WorkflowExecutor
        ├── manager.py                # WorkflowManager
        ├── events.py                 # WorkflowEvent domain events
        └── exceptions.py

app/routers/workflows.py                        # NEW — authenticated Workflow REST API
app/schemas/workflow.py                         # NEW — request/response schemas
app/ai/tools/implementations/workflow_tool.py   # NEW — WorkflowExecutionTool
app/ai/prompts/workflow/                        # NEW — LLM node prompt templates
app/ai/deps.py                                  # extend — Workflow DI factories
app/db/models.py                                # modify — WorkflowDefinition/Run/NodeExecution ORM
alembic/versions/0007_workflow_tables.py        # NEW — workflow_definitions, workflow_runs, workflow_node_executions
```

---

## Core Components

- WorkflowManager
- GraphValidator
- WorkflowExecutor
- ConditionEvaluator
- TaskNodeExecutor
- LLMNodeExecutor
- AgentNodeExecutor
- RouterNodeExecutor
- ForkNodeExecutor / JoinNodeExecutor
- ApprovalNodeExecutor
- WorkflowStore
- Workflow Event Hooks
- WorkflowExecutionTool

---

## Component Responsibilities

| Component               | Responsibility                                                                                            | Inputs                          | Outputs                     | Dependencies                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ | -------------------------------- | ----------------------------- | --------------------------------------- |
| WorkflowManager          | Entry point for all workflow operations: create/validate definitions, start/resume/cancel runs, apply decisions | Workflow requests               | WorkflowRun / definitions   | WorkflowStore, WorkflowExecutor        |
| GraphValidator           | Validates a definition's graph structure before it can be activated                                        | WorkflowDefinition              | Validation result            | —                                      |
| WorkflowExecutor         | Advances a run: resolves ready nodes, dispatches to node executors, checkpoints, resolves fan-out/fan-in    | WorkflowRun, WorkflowContext     | Updated run, node results   | Node executors, WorkflowStore, RetryPolicy |
| ConditionEvaluator       | Evaluates declarative edge conditions against `WorkflowContext`                                             | Condition DSL, context           | bool                          | —                                      |
| TaskNodeExecutor         | Executes a tool call through the existing tool platform                                                    | Node config, context             | Node output                  | ToolExecutor                           |
| LLMNodeExecutor          | Executes a direct LLM call through the existing provider/prompt platform                                    | Node config, context             | Node output                  | LLMProvider, PromptManager             |
| AgentNodeExecutor        | Delegates a bounded sub-task to the Agent Framework                                                          | Node config, context             | Node output                  | DefaultAgent                           |
| RouterNodeExecutor       | Selects the next edge(s) using `ConditionEvaluator`                                                          | Node config, context             | Selected edge(s)             | ConditionEvaluator                     |
| ForkNodeExecutor         | Spawns concurrent branches                                                                                  | Node config, context             | Branch execution handles     | asyncio                                |
| JoinNodeExecutor         | Waits for and merges branch results per join policy                                                        | Branch results                   | Merged context                | asyncio                                |
| ApprovalNodeExecutor     | Pauses the run pending a human decision                                                                     | Node config                      | `waiting_approval` state     | WorkflowStore                          |
| RetryPolicy              | Classifies retryable node failures and applies backoff                                                     | Node error                       | Retry decision                | `app/core/retry.py`                    |
| WorkflowStore            | Abstract persistence interface for definitions, runs, and node executions                                  | CRUD / checkpoint requests       | Persisted records            | PostgresWorkflowStore (initial)        |
| EventPublisher           | Publishes workflow lifecycle events for future platform integrations                                       | Workflow events                  | Event notifications          | Event subscribers (future)             |
| WorkflowExecutionTool    | Agent-invocable tool to start a workflow or check a run's status                                            | Tool call args                   | ToolResult                    | WorkflowManager, ToolExecutor          |

---

## Node Types

### Task Node

Executes a single registered tool call via the existing `ToolExecutor` (validation, authorization, execution, normalization unchanged). Node config: `{tool_name, arguments_template}`. Passes a stable per-attempt `execution_receipt_id` (`{run_id}:{node_id}:{attempt}`) through `ToolExecutionContext` so crash recovery can retry without duplicating side effects when the underlying tool is receipt-aware; v1 tools that are not receipt-aware are treated as non-idempotent (see crash-safe protocol).

### LLM Node

Executes a direct, non-agentic LLM call through the existing `LLMProvider` / `PromptManager` for simple transform/classify/summarize steps that do not need multi-step reasoning or tools. Node config: `{prompt_template, model_override?}`.

### Agent Node

Delegates a bounded sub-task to the Epic 01 `DefaultAgent` (its own ReAct loop, tools, and iteration limit) and maps the resulting `AgentResponse` into node output. Requires `AGENT_RUNTIME_ENABLED=true`; if disabled, agent nodes fail with a clear configuration error at run start (not at definition time, since the flag can change independently).

### Router Node

Evaluates its outgoing edges' conditions via `ConditionEvaluator` and selects exactly one (or, for `any`-style branching, all matching) outgoing edge(s) to activate.

### Fork / Join Node

A `fork` node activates all of its outgoing edges concurrently. The matching `join` node (declared in node config) waits for its incoming branches according to a join policy — `all`, `any`, or `count(n)` — before merging their outputs into `WorkflowContext` and continuing.

### Approval Node

Pauses the run (`status=waiting_approval`) and creates a pending `WorkflowNodeExecution` (`status=waiting_approval`). On approve: set `decision=approved`, node `status=succeeded`, and follow the approved edge. On reject: set `decision=rejected`, node `status=failed` (not a separate rejected status), then follow a declared rejected edge if present; otherwise fail the run.

### Terminal Node

Implicit `start` (the definition's `entry_node_id`) and any node with no outgoing edges act as terminals; reaching a terminal with no other pending branches completes the run.

---

## Existing V1/V2 Assets (reuse, do not duplicate)

| Asset                                              | Location                              | Epic 06 role                                              |
| --------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------ |
| `ToolExecutor`, registry, validator, `ToolAuthorizer` | `app/ai/tools/`                      | Task node execution; guest denial already enforced platform-wide |
| `DefaultAgent`, `AgentRequest`/`AgentResponse`      | `app/ai/agent/`                        | Agent node sub-task execution                                |
| `LLMProvider`, `ProviderFactory`                    | `app/providers/`                       | LLM node execution                                            |
| `PromptManager`, prompt templates                   | `app/ai/prompts/`                      | LLM node prompt rendering                                     |
| `retry_async`, `is_retryable_exception`             | `app/core/retry.py`                    | Node-level retry policy                                       |
| `MemoryProvider` / `MemoryManager` pattern          | `app/ai/memory/`                       | Reference pattern for the `WorkflowStore` abstraction (not reused directly — separate tables/domain) |
| Feature flag infrastructure                         | `app/core/config.py`                   | `WORKFLOW_ENGINE_ENABLED`                                     |
| DI factories                                        | `app/ai/deps.py`                       | Workflow DI wiring                                             |
| `get_current_caller`, `CallerContext`               | `app/core/caller.py` / routers/auth    | Authenticated, owner-scoped REST API                          |

Workflow Engine is additive. Existing chat, RAG, agent runtime, MCP, memory, voice, and streaming paths must remain unchanged when `WORKFLOW_ENGINE_ENABLED=false`.

---

## Chat & Agent Integration Strategy

Unlike Memory (which injects context into every chat turn), the Workflow Engine is an **on-demand** capability. It is deliberately **not** wired into `ChatService` / `UnifiedChatService` orchestration:

- **REST API** — primary interface for defining workflows and triggering/inspecting/approving runs, independent of any chat session.
- **`WorkflowExecutionTool`** — registered in the existing tool registry only when `WORKFLOW_ENGINE_ENABLED=true`. Lets an agent (Epic 01) start a workflow (`action=start`) or check a run's status (`action=status`) as an ordinary tool call, authorized by the existing `ToolAuthorizer` (guests already denied for all tools). No changes to `ChatService`, `UnifiedChatService`, or `ToolExecutor` internals — only additive tool registration. `action=start` requires `definition_id`, `idempotency_key`, and optional `input`; uses the same async launch + idempotency contract as REST (`start_run()` dedupes on owner + definition + key); returns `run_id` + current status immediately. `action=status` polls persisted run state.
- **Optional `session_id` linkage** — when triggered via the tool from within a chat session, `WorkflowRun.session_id` records the originating session for traceability only; the in-process background executor continues independently of that chat turn (e.g. through `waiting_approval` until a decision or cancel).

**Flag off:** `WorkflowExecutionTool` is not registered; Workflow REST routes return `503 feature_disabled`; no workflow UI. All other platform behaviour unchanged.

**Flag on:** Authenticated users can define/run workflows via REST; agents can start/check workflows via the tool when tools are enabled for that request.

---

## Persistence Schema

Alembic migration **`0007_workflow_tables`** (Phase 1). Independent of `memory_records`, `document_chunks`, and all other epic tables.

### `workflow_definitions`

| Column                     | Type                  | Notes                                                |
| --------------------------- | ---------------------- | ------------------------------------------------------ |
| `id`                        | uuid PK                |                                                        |
| `owner_id`                  | uuid FK → `users.id`   | Required; owner isolation                             |
| `name`                      | text                    |                                                        |
| `description`               | text NULL              |                                                        |
| `version`                   | int                     | Starts at 1; new versions on edit after first run     |
| `status`                    | text CHECK              | `draft` \| `active` \| `archived`                     |
| `entry_node_id`             | text                    |                                                        |
| `graph`                     | jsonb                   | `{nodes: [...], edges: [...]}`                        |
| `metadata`                  | jsonb                   |                                                        |
| `created_at`, `updated_at`  | timestamptz             |                                                        |

**Indexes:** `(owner_id, status)`; **Unique:** `(owner_id, name, version)`.

### `workflow_runs`

| Column                       | Type                                      | Notes                                                     |
| ----------------------------- | ------------------------------------------ | ------------------------------------------------------------ |
| `id`                          | uuid PK                                    |                                                              |
| `workflow_definition_id`      | uuid FK → `workflow_definitions.id`        |                                                              |
| `owner_id`                    | uuid FK → `users.id`                       | Required; owner isolation                                   |
| `idempotency_key`             | text                                       | Required; caller-supplied; unique with `(owner_id, workflow_definition_id)` |
| `session_id`                  | uuid FK → `chat_sessions.id` NULL          | Set when triggered via `WorkflowExecutionTool` from a chat session |
| `status`                      | text CHECK                                 | `pending` \| `running` \| `waiting_approval` \| `completed` \| `failed` \| `cancelled` |
| `context`                     | jsonb                                       | `WorkflowContext` snapshot                                   |
| `current_node_ids`            | jsonb                                       | List — supports parallel branches                           |
| `checkpoint_version`          | int                                         | Starts at 0; incremented on each merged run checkpoint       |
| `error`                       | text NULL                                   |                                                              |
| `created_at`, `updated_at`    | timestamptz                                 |                                                              |
| `started_at`, `completed_at`  | timestamptz NULL                            |                                                              |

**Indexes:** `(owner_id, status)`; `(workflow_definition_id)`; **Unique:** `(owner_id, workflow_definition_id, idempotency_key)`.

### `workflow_node_executions`

| Column                       | Type                                | Notes                                                |
| ----------------------------- | ------------------------------------ | ------------------------------------------------------- |
| `id`                          | uuid PK                              |                                                        |
| `run_id`                      | uuid FK → `workflow_runs.id`         |                                                        |
| `node_id`                     | text                                  | Matches the graph node id                             |
| `node_type`                   | text CHECK                            | `task` \| `llm` \| `agent` \| `router` \| `fork` \| `join` \| `approval` \| `terminal` |
| `attempt`                     | int                                    | 1-based; increments on retry                           |
| `status`                      | text CHECK                            | `pending` \| `running` \| `waiting_approval` \| `succeeded` \| `failed` \| `skipped` \| `cancelled` |
| `input`                       | jsonb                                  |                                                        |
| `output`                      | jsonb NULL                            |                                                        |
| `error`                       | text NULL                             |                                                        |
| `decided_by`                  | uuid FK → `users.id` NULL             | Approval nodes only                                   |
| `decided_at`                  | timestamptz NULL                      |                                                        |
| `decision`                    | text CHECK NULL                       | `approved` \| `rejected`                              |
| `started_at`, `completed_at`  | timestamptz NULL                      |                                                        |

**Indexes:** `(run_id, node_id, attempt)` unique; `(run_id, status)`.

---

## Workflow REST API

Authenticated-only (`Depends(get_current_caller)`). Router: `app/routers/workflows.py`. Always mounted in `app/main.py`; each route enforces `WORKFLOW_ENGINE_ENABLED` and returns `503 feature_disabled` when the flag is off.

| Method   | Path                                                       | Purpose                                                             |
| -------- | ----------------------------------------------------------- | ---------------------------------------------------------------------- |
| `POST`   | `/api/workflows`                                            | Create a workflow definition (validated graph)                     |
| `GET`    | `/api/workflows`                                             | List caller's workflow definitions                                  |
| `GET`    | `/api/workflows/{id}`                                        | Get one definition (owner check)                                    |
| `PUT`    | `/api/workflows/{id}`                                        | Update definition — creates a new version if the current one has runs |
| `DELETE` | `/api/workflows/{id}`                                        | Archive a definition (owner check)                                  |
| `POST`   | `/api/workflows/{id}/runs`                                   | Start a new run (`idempotency_key` + `trigger_input` body, both required); returns `run_id` + status snapshot. Retries with the same owner + definition + `idempotency_key` return the existing run (no duplicate executor). Poll `GET /api/workflow-runs/{run_id}` for progress. |
| `GET`    | `/api/workflows/{id}/runs`                                   | List runs for a definition                                          |
| `GET`    | `/api/workflow-runs`                                          | List caller's runs across all definitions                           |
| `GET`    | `/api/workflow-runs/{run_id}`                                 | Get a run's status and node execution history                       |
| `POST`   | `/api/workflow-runs/{run_id}/cancel`                          | Cancel a `running` or `waiting_approval` run                         |
| `POST`   | `/api/workflow-runs/{run_id}/resume`                          | Reattach executor and continue a crashed `running` run from its last checkpoint (`waiting_approval` runs use approve/reject, not `/resume`) |
| `POST`   | `/api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve` | Approve a pending approval node (atomic CAS; duplicate/conflicting decisions no-op or 409) |
| `POST`   | `/api/workflow-runs/{run_id}/nodes/{node_execution_id}/reject`  | Reject a pending approval node (atomic CAS; duplicate/conflicting decisions no-op or 409) |

**Health:** extend `GET /api/health` with `workflow_engine_enabled: bool` (frontend gate, same pattern as `memory_enabled`).

**Response rules:** Never expose internal retry counters, condition DSL internals, or other owners' data — human-facing definition/run/node fields plus timestamps and status only.

---

## Public APIs (stable after Phase 1)

| API                                                                                          | Kind                                |
| ---------------------------------------------------------------------------------------------- | -------------------------------------- |
| `WorkflowStore`                                                                                 | Protocol                              |
| `WorkflowManager`                                                                               | Class (public orchestration entry)    |
| `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`, `NodeType`                                | Model / enum                          |
| `WorkflowRun`, `WorkflowNodeExecution`, `RunStatus`, `NodeStatus`, `WorkflowContext`            | Model / enum                          |
| `WorkflowExecutor`, `GraphValidator`, `ConditionEvaluator`                                       | Class                                  |
| `WorkflowEvent` (domain event base)                                                              | Model                                  |
| `WorkflowError`, `WorkflowNotFoundError`, `WorkflowAccessDeniedError`, `WorkflowValidationError` | Exception                              |
| `WorkflowExecutionTool`                                                                          | Tool (registered when flag on)        |
| Workflow REST router export                                                                     | FastAPI router                        |

Internal (may evolve): `PostgresWorkflowStore`, individual `NodeExecutor` implementations, DI wiring, ready-node scheduling internals.

---

## Configuration defaults

| Setting                                    | Default    |
| -------------------------------------------- | ------------ |
| `WORKFLOW_ENGINE_ENABLED`                    | **`false`** |
| `workflow_max_nodes_per_definition`          | `50`        |
| `workflow_max_parallel_branches`             | `8`         |
| `workflow_node_timeout_seconds`              | `120`       |
| `workflow_max_node_retries`                  | `3`         |
| `workflow_node_retry_base_delay_seconds`     | `1.0`       |
| `workflow_max_run_duration_minutes`          | `60`        |
| `workflow_approval_timeout_hours`            | `0` (no timeout — indefinite) |
| `workflow_run_retention_days`                | `90`        |

---

## Dependencies

| Requires                                          | Provides to downstream                                             |
| ---------------------------------------------------- | ---------------------------------------------------------------------- |
| Epic 05 Memory (stable chat/memory/voice pipeline)   | `WorkflowManager`, `WorkflowRun`, `WORKFLOW_ENGINE_ENABLED`         |
| Epic 01 Agent Framework (`DefaultAgent`)             | Agent node execution                                                |
| Epic 01 / V1.1 Tool platform (`ToolExecutor`)        | Task node execution; `WorkflowExecutionTool` registration           |
| `LLMProvider`, `PromptManager`                       | LLM node execution                                                  |
| PostgreSQL                                            | Workflow definition/run/node-execution persistence                 |

**Future consumers:** Epic 07 Observability (workflow spans/metrics); Epic 08 Plugin Architecture (workflow plugin SDK, externally loaded node types); Epic 09 Human-in-the-Loop (full approval audit trail, editable tool arguments); Epic 10 Background Jobs (scheduled triggers, distributed execution worker).

---

## Design acceptance

- Flag off: workflow REST routes return `503 feature_disabled`; `WorkflowExecutionTool` not registered; no workflow UI; all other platform paths unchanged
- Flag on, authenticated: users can define, validate, run, inspect, cancel, resume, and approve/reject workflows via REST; agents can start/check workflows via the tool
- Graphs are validated before activation — no cycles, unreachable nodes, or dangling edges
- Sequential and parallel execution both checkpoint after every node transition
- Approval nodes pause indefinitely until a decision is recorded; decisions resume execution deterministically
- A crashed/restarted process can resume any `running`/`waiting_approval` run from its last checkpoint with no duplicate side effects on already-succeeded nodes
- Retries with the same owner + definition + `idempotency_key` return the existing run without duplicate side effects
- Task/LLM/Agent nodes reuse existing platform components with no reimplementation
- Coverage ≥80% on `app/` and `app/ai/workflow/`
- No workflow input/output content in structured logs beyond identifiers and status by default

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Orchestration boundary** — Workflow execution is invoked only through `WorkflowManager`; never directly against `WorkflowStore` or node executors from routers or the tool layer.
- **No chat pipeline coupling** — Workflows are never wired into `ChatService` or `UnifiedChatService`; the only chat-facing surface is the additive `WorkflowExecutionTool`.
- **Reuse, don't reimplement** — Task nodes use `ToolExecutor`; Agent nodes use `DefaultAgent`; LLM nodes use `LLMProvider`/`PromptManager`; retry wraps `app/core/retry.py`.
- **Deterministic conditions** — `ConditionEvaluator` supports only the declarative DSL; no `eval()`, `exec()`, or arbitrary code execution.
- **Checkpoint-per-transition** — `WorkflowStore` persists run + node-execution state after every node transition; no in-memory-only execution state. Parallel branches use optimistic merged checkpoints (`checkpoint_version`), not independent run overwrites.
- **Async run launch** — `start_run()` schedules an in-process `asyncio.Task` and returns `run_id` + current status immediately; it never blocks until terminal completion. Callers observe progress via run APIs or tool `action=status`.
- **Idempotent run start** — `start_run()` requires `idempotency_key`; `(owner_id, workflow_definition_id, idempotency_key)` lookups return an existing run instead of scheduling duplicate work.
- **Idempotent resume** — Resuming a run never re-executes an already-`succeeded` `WorkflowNodeExecution`. A node left `status=running` after crash is never assumed complete; side-effecting retries require the same per-attempt `execution_receipt_id` or the run fails closed.
- **Definition immutability post-run** — A `WorkflowDefinition` version referenced by any `WorkflowRun` is never mutated in place; edits create a new version.
- **Provider replaceability** — All persistence goes through the `WorkflowStore` Protocol; Postgres is one adapter.
- **Auth-only workflows** — Guests cannot create, run, or inspect workflows (tool calls already denied to guests platform-wide; REST routes require an authenticated caller).
- **Flag-off parity** — `WORKFLOW_ENGINE_ENABLED=false` preserves Epic 05 behaviour on all hot paths.
- **Public APIs stable after Phase 1** — Protocol/model changes require user approval.
- **No Epic 07+ behaviour early** — Scheduled triggers, distributed workers, plugin-loaded nodes, full HITL audit UI, OTel spans — `TODO(epic-N):` only.

---

## Acceptance Criteria

- Multi-step tasks can be modeled and executed as explicit graphs, independent of any single chat turn.
- Conditional branches and parallel branches both execute deterministically and are individually inspectable.
- Human approval can pause a run indefinitely and resume it exactly where it left off.
- A failed or interrupted run can be retried or resumed without duplicating completed work.
- The existing platform is unaffected when the Workflow Engine is disabled.

# Part II — Execution

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement Part II phase-by-phase. Part I is frozen and is the architectural source of truth. Do not redesign architecture during implementation.

## Phase integration rules

Early phases build **subsystems in isolation** (unit/integration tests with fakes). Agent/Tool integration and the REST API are deferred until the execution engine itself is proven.

| Phase | Builds                                                         | Agent/Chat wiring        |
| ----- | ----------------------------------------------------------------- | -------------------------- |
| 1     | Models, `WorkflowStore` scaffold, migration                       | None                       |
| 2     | Graph model + `GraphValidator`                                    | None                       |
| 3     | Sequential execution engine + Task node                           | None (manager API only)   |
| 4     | Conditional routing + Router node                                 | None                       |
| 5     | Parallel execution (Fork/Join)                                    | None                       |
| 6     | LLM node + Agent node                                             | None                       |
| 7     | Approval node, pause/resume                                       | None                       |
| 8     | Node retry + crash recovery                                       | None                       |
| 9     | Workflow REST API                                                 | REST only                 |
| 10    | `WorkflowExecutionTool`                                           | **Complete** (tool-only)   |
| 11–12 | Frontend + release                                                | —                          |

## Reuse Existing Components

**DO NOT REIMPLEMENT**

| Component                                            | Location                              |
| ------------------------------------------------------ | --------------------------------------- |
| `ToolExecutor`, registry, validator, `ToolAuthorizer` | `app/ai/tools/`                        |
| `DefaultAgent`, `AgentRequest`/`AgentResponse`        | `app/ai/agent/`                        |
| `LLMProvider`, `ProviderFactory`                      | `app/providers/`                       |
| `PromptManager`, prompt templates                     | `app/ai/prompts/`                      |
| `retry_async`, `is_retryable_exception`               | `app/core/retry.py`                    |
| `get_current_caller`, `CallerContext`                 | `app/core/caller.py` / `app/routers/auth.py` |
| Feature flag infrastructure                           | `app/core/config.py`                   |
| DI factories                                          | `app/ai/deps.py`                       |
| `ChatService`, `UnifiedChatService`                   | `app/services/`                        |

Workflow Engine is additive. Existing chat, RAG, agent runtime, MCP, memory, voice, and streaming paths must remain unchanged when `WORKFLOW_ENGINE_ENABLED=false`.

---

## Not Allowed

- Bypass `WorkflowManager` for workflow orchestration
- Wire workflow execution into `ChatService` or `UnifiedChatService`
- Reimplement tool execution, agent execution, LLM calls, or retry/backoff outside their existing platform abstractions
- Allow `ConditionEvaluator` to execute arbitrary code
- Allow node executors to access `WorkflowStore` directly
- Implement scheduled/cron triggers, distributed execution, plugin-loaded node types, or full HITL audit/editable-args UX
- Break feature-flag parity

---

## Baseline

_Reverified in Phase 0 audit (2026-08-04). See [post-mvp-v2-epic6-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic6-phase-0-baseline-audit.md)._

| Area                      | State                                                             |
| --------------------------- | -------------------------------------------------------------------- |
| Backend tests / coverage   | 1370 passed, 90.00% `app/` (Phase 2 reverified)                      |
| Frontend tests             | 251 passed (41 files); build pass                                 |
| Eval CLI                   | 5/5 passed                                                          |
| Chat pipeline              | Stable — `ChatService` + `UnifiedChatService`, Memory fully wired |
| Agent Framework            | Completed (Epic 01); `AGENT_RUNTIME_ENABLED` behind flag           |
| Memory subsystem           | Completed (Epic 05); `MEMORY_ENABLED` behind flag                 |
| Workflow Engine            | Phase 2 complete — models, validation, definition CRUD (61 workflow tests) |

---

## Phase Status

| Phase | Name                                          | Effort | Status      |
| ----- | ----------------------------------------------- | ------ | ----------- |
| 0     | Baseline Audit                                  | XS     | Completed   |
| 1     | Models, Interfaces & Migration                  | L      | Completed   |
| 2     | Graph Definition & Validation                   | M      | Completed   |
| 3     | Sequential Execution Engine                     | L      | Completed   |
| 4     | Conditional Routing                             | M      | Completed   |
| 5     | Parallel Execution (Fork/Join)                  | M      | Completed   |
| 6     | LLM & Agent Node Integration                    | M      | Completed   |
| 7     | Human Approval Nodes, Pause & Resume            | L      | Not Started |
| 8     | Node Retry & Crash Recovery                     | M      | Not Started |
| 9     | Workflow REST API                               | L      | Not Started |
| 10    | Agent Tool Integration                          | M      | Not Started |
| 11    | Frontend Controls                               | S      | Not Started |
| 12    | Validation & Release                            | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS

**Objective**

Establish a verified implementation baseline before introducing the Workflow Engine. Confirm that the existing platform is stable, all architectural dependencies are understood, and the execution environment satisfies the assumptions defined in Part I.

**Deliverables**

- `docs/audits/post-mvp-v2-epic6-phase-0-baseline-audit.md`
- Architecture inventory
- Dependency verification
- Feature flag verification
- Platform readiness assessment
- Baseline quality metrics
- Implementation readiness checklist

**Steps**

### Platform Verification

- [x] Confirm Epic 05 Phase 10 complete / authorized for Epic 06
- [x] Inventory `ToolExecutor`, `ToolAuthorizer`, registry, validator
- [x] Inventory Agent Framework (`DefaultAgent`, `AgentRequest`/`AgentResponse`, `AGENT_RUNTIME_ENABLED`)
- [x] Inventory `LLMProvider`, `ProviderFactory`, `PromptManager`
- [x] Verify Memory integration remains operational.
- [x] Verify Voice integration remains operational.
- [x] Verify RAG integration remains operational.
- [x] Verify MCP integration remains operational.
- [x] Verify Agent Framework remains operational.
- [x] Verify streaming responses remain operational.

### Architecture Review

- [x] Review the frozen Part I architecture.
- [x] Verify all architectural invariants.
- [x] Identify all Workflow Engine integration points.
- [x] Identify existing extension points (tool registry, DI factories).
- [x] Confirm no Workflow Engine implementation already exists.
- [x] Record implementation assumptions.

### Dependency Verification

- [x] Verify PostgreSQL configuration.
- [x] Verify `app/core/retry.py` availability.
- [x] Verify existing provider abstractions.
- [x] Verify dependency injection configuration.
- [x] Verify feature flag infrastructure.

### Codebase Inventory

- [x] Inventory existing chat services.
- [x] Inventory tool platform implementation.
- [x] Inventory Agent Framework implementation.
- [x] Inventory provider implementations.
- [x] Inventory existing Alembic migrations and numbering.
- [x] Record components to be reused.

### Baseline Quality Validation

- [x] Execute lint.
- [x] Execute type checking.
- [x] Execute unit tests.
- [x] Execute integration tests.
- [x] Execute evaluation suite.
- [x] Record baseline quality metrics.

### Implementation Readiness

- [x] Confirm all required dependencies are available.
- [x] Confirm implementation order matches Part II.
- [x] Confirm no architectural conflicts exist.
- [x] Publish baseline audit document.
- [x] Freeze implementation baseline.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`

Additional verification:

- [x] Chat functionality verified.
- [x] Agent functionality verified.
- [x] Memory functionality verified.
- [x] Tool execution verified.
- [x] Streaming functionality verified.
- [x] All quality gates pass.

**Acceptance**

- Existing platform is fully operational.
- All architectural assumptions have been verified.
- Required dependencies are available.
- Existing extension points have been identified.
- No implementation blockers remain.
- Baseline metrics have been recorded.
- Repository is ready for Workflow Engine implementation.

**Exit Criteria**

- Baseline audit completed.
- Platform readiness confirmed.
- Quality gates passed.
- Architecture verified.
- Implementation baseline frozen.
- User confirmation pending to proceed to Phase 1.

**Rollback**

- [x] No rollback required.
- [x] This phase introduces no functional code changes.

**Completion Record**

| Metric                     | Result                          |
| ---------------------------- | ------------------------------- |
| Lint                        | ✅ PASS                         |
| Typecheck                   | ✅ PASS                         |
| Unit Tests                  | ✅ 1305 passed                  |
| Integration Tests           | ✅ (included in backend suite)  |
| Evaluation Suite            | ✅ 5/5 passed                   |
| Platform Readiness          | ✅ Confirmed                    |
| Baseline Audit Published    | ✅ `docs/audits/post-mvp-v2-epic6-phase-0-baseline-audit.md` |

---

# Phase 1 — Models, Interfaces & Migration

**Effort:** L

**Objective**

Establish the complete Workflow domain foundation: canonical data models, the `WorkflowStore` provider contract, and the **Alembic migration** defined in Part I. This phase freezes the public Workflow API and provides the stable foundation for all subsequent phases.

**Deliverables**

- Canonical `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`
- Canonical `WorkflowRun`, `WorkflowNodeExecution`, `WorkflowContext`
- `WorkflowStore` interface
- `PostgresWorkflowStore` implementation scaffold
- `WorkflowManager` skeleton
- **`workflow_definitions` + `workflow_runs` + `workflow_node_executions` Alembic migration**
- ORM models
- Status/state enums
- Shared model validation
- Initial public API freeze
- Unit test suite

**Steps**

### Package Structure

- [x] Create the `app/ai/workflow/` package.
- [x] Create the package layout defined in Part I.
- [x] Add package exports through `__init__.py`.
- [x] Verify package imports are dependency-cycle free.

### Canonical Models

- [x] Implement `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`.
- [x] Implement `WorkflowRun`, `WorkflowNodeExecution`, `WorkflowContext`.
- [x] Implement `NodeType`, `RunStatus`, `NodeStatus` enums.
- [x] Add schema validation.
- [x] Add serialization/deserialization support.
- [x] Add comprehensive model documentation.

### Provider Contract

- [x] Create the `WorkflowStore` abstraction.
- [x] Define definition CRUD operations.
- [x] Define run + node-execution checkpoint operations.
- [x] Define listing/query operations (by owner, definition, status).
- [x] Ensure interface remains storage-agnostic.

### PostgreSQL Store Scaffold

- [x] Create `PostgresWorkflowStore`.
- [x] Implement constructor and dependency injection.
- [x] Define placeholder implementations for provider methods.
- [x] Do not implement graph validation yet (Phase 2).
- [x] Do not implement execution logic yet (Phase 3).

### Workflow Manager

- [x] Implement `WorkflowManager` skeleton.
- [x] Inject `WorkflowStore`.
- [x] Define orchestration entry points (create/get definition; start/get run).
- [x] Ensure business logic remains outside the storage provider.

### Database Migration

- [x] Add `WorkflowDefinition`, `WorkflowRun`, `WorkflowNodeExecution` ORM models to `app/db/models.py`.
- [x] Create Alembic migration `0007_workflow_tables` per Part I schema.
- [x] Verify migration is independent of `memory_records` and `document_chunks`.
- [ ] Add migration rollback test in CI (upgrade/downgrade smoke).

### Configuration

- [x] Add `WORKFLOW_ENGINE_ENABLED` feature flag.
- [x] Add Workflow configuration section (Part I § Configuration defaults).
- [x] Register provider configuration.
- [x] Preserve backward compatibility when disabled.

### Testing

- [x] Add model validation tests.
- [x] Add serialization tests.
- [x] Add provider contract tests.
- [x] Add dependency injection tests.
- [x] Add package import tests.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`

Additional verification:

- [x] All Workflow models serialize correctly.
- [x] `WorkflowStore` interface compiles successfully.
- [x] Dependency injection resolves successfully.
- [x] No circular imports detected.
- [x] Feature flag defaults to disabled.

**Acceptance**

- Canonical models exactly match the frozen Part I architecture.
- Public APIs are stable and storage-independent.
- Storage implementation details remain hidden behind `WorkflowStore`.
- `WorkflowManager` becomes the single orchestration entry point.
- No graph validation or execution logic is implemented before their respective phases.
- Existing application behaviour remains unchanged with `WORKFLOW_ENGINE_ENABLED=false`.

**Exit Criteria**

- All model tests pass.
- All interface tests pass.
- All quality gates pass.
- Public APIs frozen.
- Ready to begin Phase 2 without further structural changes.

**Rollback**

- [ ] Remove `app/ai/workflow/` package.
- [ ] Remove feature flag additions.
- [ ] Remove dependency registration.
- [ ] Verify application builds successfully without Workflow components.

**Completion Record**

| Metric                | Result                                                                 |
| ------------------------ | ---------------------------------------------------------------------- |
| Models implemented      | `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`, `WorkflowRun`, `WorkflowNodeExecution`, `WorkflowContext`, enums |
| Provider interfaces     | `WorkflowStore` (Protocol) + `PostgresWorkflowStore` scaffold        |
| Unit tests               | ✅ 39 new tests (`tests/ai/workflow/`); 1344 total backend passed     |
| Coverage                 | ✅ 89.80% `app/`                                                       |
| API freeze completed     | ✅ `app/ai/workflow/__init__.py` exports frozen Phase 1 surface       |
| Migration rollback CI    | ⏳ Pending (`0007_workflow_tables` smoke test not yet added)          |

---

# Phase 2 — Graph Definition & Validation

**Effort:** M

**Objective**

Implement `GraphValidator`, the definition-time structural checks that every `WorkflowDefinition` must pass before it can be activated, and wire definition CRUD through `WorkflowManager`/`WorkflowStore`.

**Deliverables**

- `GraphValidator`
- Definition CRUD (`create`, `get`, `list`, `update` → new version, `archive`)
- Cycle detection
- Reachability / dangling-edge checks
- Node/edge integrity checks (fork/join pairing, single entry node)
- Integration test suite

**Steps**

### Graph Validation

- [x] Implement `GraphValidator.validate(definition)`.
- [x] Detect cycles (reject unless explicitly modeled as retry edges — none allowed in v1).
- [x] Detect unreachable nodes from `entry_node_id`.
- [x] Detect dangling edges (edges referencing missing nodes).
- [x] Validate every `fork` node has a matching `join` node reference.
- [x] Validate node `type` against the supported `NodeType` set.
- [x] Validate `condition` DSL shape on edges (without evaluating it).

### Definition CRUD

- [x] Implement `WorkflowManager.create_definition()` (runs `GraphValidator` before persisting).
- [x] Implement `WorkflowManager.get_definition()` / `list_definitions()` (owner-scoped).
- [x] Implement `WorkflowManager.update_definition()` — creates a new version once any run references the current version.
- [x] Implement `WorkflowManager.archive_definition()`.
- [x] Persist via `PostgresWorkflowStore`.

### Error Handling

- [x] Raise `WorkflowValidationError` with actionable messages for each failure class.
- [x] Owner-scoped CRUD lookups return `None` or raise `WorkflowNotFoundError` when a definition is missing or owned by another user (intentional — avoids existence leakage); `WorkflowAccessDeniedError` is reserved for explicit cross-owner actions in later phases (REST Phase 9).
- [x] Ensure invalid definitions are never persisted as `active`.

### Testing

- [x] Add cycle detection tests.
- [x] Add reachability tests.
- [x] Add fork/join pairing tests.
- [x] Add definition CRUD tests.
- [x] Add versioning tests (edit after run vs. before run).
- [x] Add owner-isolation tests.

**Verify**

- `pytest tests/ai/workflow/test_graph_validator.py tests/ai/workflow/test_manager_definitions.py`

Additional verification:

- [x] Valid graphs pass validation.
- [x] Invalid graphs are rejected with clear errors.
- [x] Definitions version correctly after being referenced by a run.
- [x] Owner isolation holds for all CRUD operations.

**Acceptance**

- No invalid graph can be activated.
- Definitions are versioned, never mutated in place once run.
- CRUD operates entirely through `WorkflowManager`/`WorkflowStore`.

**Exit Criteria**

- Validation and CRUD tests pass.
- Ready for the execution engine (Phase 3).

**Completion Record**

| Metric                  | Result                                                                 |
| ----------------------- | ---------------------------------------------------------------------- |
| Graph validation        | ✅ `GraphValidator` + `conditions/schema.py` (DSL shape only)         |
| Definition CRUD         | ✅ `WorkflowManager` + `PostgresWorkflowStore` definition persistence  |
| Fork/join pairing       | ✅ `config.join_node_id` / `config.fork_node_id` bidirectional checks  |
| Unit tests              | ✅ 22 new tests; 61 total in `tests/ai/workflow/`                      |
| Backend regression      | ✅ 1370 passed; 90.00% `app/` coverage                                 |
| Run persistence         | ⏳ Deferred to Phase 3 (`PostgresWorkflowStore` run methods stubbed)   |

---

# Phase 3 — Sequential Execution Engine

**Effort:** L

**Objective**

Implement `WorkflowExecutor` for the simple case: a linear or branching (but non-parallel, non-approval) graph of `task` nodes, executed to completion with a checkpoint persisted after every node transition.

**Deliverables**

- `WorkflowExecutor` core step loop
- Ready-node resolver (topological, sequential-only)
- `TaskNodeExecutor`
- `NodeExecutor` Protocol
- `WorkflowManager.start_run()` / `get_run()`
- Checkpointing after every node transition
- Integration test suite

**Steps**

### Execution Engine

- [x] Implement `NodeExecutor` Protocol.
- [x] Implement `WorkflowExecutor.execute_run()` — resolve ready nodes, execute, advance until terminal/paused.
- [x] Implement the ready-node resolver for sequential/branching (non-parallel) graphs.
- [x] Implement run completion detection (no more ready nodes, no pending branches).

### Task Node

- [x] Implement `TaskNodeExecutor` calling `ToolExecutor.execute()`.
- [x] Map node `config` (`tool_name`, `arguments_template`) against `WorkflowContext`.
- [x] Map `ToolResult` into `WorkflowNodeExecution.output`.
- [x] Pass `execution_receipt_id` via `ToolExecutionContext` (crash-safe protocol; Phase 8).
- [x] Handle tool authorization failures as node failures (not run crashes).

### Run Lifecycle

- [x] Implement `WorkflowManager.start_run(definition_id, trigger_input, idempotency_key, owner_id)`.
- [x] Create `WorkflowRun` (`status=running`) when no run exists for `(owner_id, definition_id, idempotency_key)`; otherwise return the existing run without scheduling a duplicate executor.
- [x] Schedule `WorkflowExecutor` on an in-process `asyncio.Task`, and return `run_id` + status snapshot immediately (do not block until terminal completion).
- [x] Implement `WorkflowManager.get_run(run_id)` — returns owned `WorkflowRun` snapshot only (node execution history via `WorkflowStore.get_run_with_executions()`; `WorkflowManager` wrapper lands in Phase 9).
- [x] Implement `WorkflowManager.list_runs()` (owner-scoped).

### Checkpointing

- [x] Persist a `WorkflowNodeExecution` row before executing a node (`status=running`), including a stable per-attempt `execution_receipt_id` (`{run_id}:{node_id}:{attempt}`) in the checkpointed `input`.
- [x] Persist the node result and updated `WorkflowRun.context` after execution in the same atomic write as the terminal node status (`succeeded`/`failed`).
- [x] Ensure checkpoint writes are atomic (run + node execution together) so a crash after an external side effect but before the result checkpoint leaves the node in `running`, not `succeeded`.

### Error Handling

- [x] Handle node execution failures — mark node `failed`, fail the run (retry arrives in Phase 8).
- [x] Handle tool timeouts.
- [x] Log operational failures without exposing node input/output content.

### Testing

- [x] Add single-node run tests.
- [x] Add multi-node sequential run tests.
- [x] Add branching (non-parallel) run tests.
- [x] Add checkpoint persistence tests.
- [x] Add failure propagation tests.
- [x] Add idempotent start tests (same owner + definition + key returns existing run).
- [x] Add owner-isolation tests for runs.

**Verify**

- `pytest tests/ai/workflow/test_executor_sequential.py tests/ai/workflow/test_task_node.py`

Additional verification:

- [x] Sequential workflows execute to completion.
- [x] Task nodes correctly invoke `ToolExecutor`.
- [x] Every node transition is checkpointed.
- [x] Failures mark the run `failed` without crashing the process.

**Acceptance**

- `WorkflowExecutor` drives a run from `pending` to `completed`/`failed` for sequential/branching (non-parallel) graphs.
- Task nodes reuse `ToolExecutor` with no duplicated validation/authorization logic.
- Every transition is durably checkpointed.

**Exit Criteria**

- Sequential execution tests pass.
- Ready for conditional routing (Phase 4).

**Completion Record**

| Metric               | Result                                                                     |
| -------------------- | --------------------------------------------------------------------------- |
| Execution engine     | ✅ `WorkflowExecutor` step loop + sequential/branching ready-node resolver  |
| Task node            | ✅ `TaskNodeExecutor` — dot-path `arguments_template` resolution, `ToolExecutor` reuse |
| Run lifecycle        | ✅ `WorkflowManager.start_run()` (idempotent, requires `ACTIVE` definition) / `get_run()` / `list_runs()` |
| Checkpointing        | ✅ `PostgresWorkflowStore` run + node execution persistence, optimistic concurrency via `checkpoint_version` |
| Background execution | ✅ `asyncio.Task` scheduling with dedicated session (`engine/background.py`) |
| Unit tests           | ✅ 27 new tests; 88 total in `tests/ai/workflow/`                            |
| Backend regression   | ✅ 1397 passed; 89.20% `app/` coverage                                       |

---

# Phase 4 — Conditional Routing

**Effort:** M

**Objective**

Implement the declarative condition DSL and `RouterNodeExecutor` so a workflow can branch deterministically based on accumulated `WorkflowContext` state.

**Deliverables**

- `ConditionEvaluator`
- Condition DSL (`field`, `operator`, `value`; `all`/`any` composition)
- `RouterNodeExecutor`
- Edge condition evaluation in the ready-node resolver
- Integration test suite

**Steps**

### Condition DSL

- [x] Define the `Condition` model (`field`, `operator`, `value`).
- [x] Support operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`, `exists`.
- [x] Support `all`/`any` composition of conditions.
- [x] Implement dot-path field resolution against `WorkflowContext.variables`.
- [x] Reject any non-declarative condition input at validation time (Phase 2 hook).

### Condition Evaluator

- [x] Implement `ConditionEvaluator.evaluate(condition, context) -> bool`.
- [x] Ensure no code execution path exists (no `eval`/`exec`/`getattr` on arbitrary objects).
- [x] Handle missing fields deterministically (`exists` semantics; other operators treat missing as non-matching).

### Router Node

- [x] Implement `RouterNodeExecutor` — evaluates each outgoing edge's condition in declaration order.
- [x] Select the first matching edge for exclusive routing; support multi-match "activate all matching" mode via node config.
- [x] Fail the node deterministically if no edge matches and no default edge is declared.

### Ready-Node Resolver

- [x] Extend the Phase 3 resolver to only activate nodes reached by a selected edge.
- [x] Skip (not fail) nodes on unselected branches; mark their `WorkflowNodeExecution` as `skipped`.

### Testing

- [x] Add condition DSL unit tests (each operator).
- [x] Add `all`/`any` composition tests.
- [x] Add router node branching tests.
- [x] Add default-edge and no-match-failure tests.
- [x] Add skipped-node tests.

**Verify**

- `pytest tests/ai/workflow/test_condition_evaluator.py tests/ai/workflow/test_router_node.py`

Additional verification:

- [x] Conditions evaluate deterministically against `WorkflowContext`.
- [x] Router nodes select the correct branch(es).
- [x] Unselected branches are marked `skipped`, not executed.

**Acceptance**

- Conditional routing is fully declarative — no arbitrary code execution.
- Branching is deterministic and explainable from persisted node execution history.

**Exit Criteria**

- Conditional routing tests pass.
- Ready for parallel execution (Phase 5).

**Completion Record**

| Metric               | Result                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------- |
| Condition DSL        | ✅ `ConditionLeaf` / `ConditionComposite` models; `conditions/field_resolution.py` dot-path |
| Condition evaluator  | ✅ `ConditionEvaluator` — declarative operators + `all`/`any`; dict lookup only           |
| Router node          | ✅ `RouterNodeExecutor` — exclusive / `all_matching` modes; default (unconditional) edges   |
| Ready-node resolver  | ✅ Router edge selection + `skipped_node_ids` in run context metadata                       |
| Skip semantics       | ✅ Unselected branch nodes persisted as `skipped`; merge nodes proceed when selected path completes |
| Unit tests           | ✅ 40 new tests; 128 total in `tests/ai/workflow/`                                          |
| Backend regression   | ✅ 1437 passed; 90.00% `app/` coverage                                                      |

---

# Phase 5 — Parallel Execution (Fork/Join)

**Effort:** M

**Objective**

Implement `ForkNodeExecutor` and `JoinNodeExecutor` so independent branches of a workflow can execute concurrently and be merged back into a single `WorkflowContext`.

**Deliverables**

- `ForkNodeExecutor`
- `JoinNodeExecutor`
- Join policies: `all`, `any`, `count(n)`
- Branch isolation and result merge strategy
- `workflow_max_parallel_branches` enforcement
- Integration test suite

**Steps**

### Fork Node

- [x] Implement `ForkNodeExecutor` — activates all outgoing edges concurrently via `asyncio.gather`.
- [x] Enforce `workflow_max_parallel_branches` at validation time (Phase 2 hook) and at execution time.
- [x] Track `current_node_ids` for all active branches on the `WorkflowRun` (updated via merged checkpoints, not per-branch overwrites).

### Join Node

- [x] Implement `JoinNodeExecutor` — waits for its declared incoming branches.
- [x] Support join policy `all` (wait for every branch).
- [x] Support join policy `any` (continue on first completion; cancel or ignore the rest per config).
- [x] Support join policy `count(n)` (continue once `n` branches complete).
- [x] Merge branch outputs into `WorkflowContext.variables` under their originating node ids.

### Branch Isolation

- [x] Ensure branch execution failures do not corrupt sibling branches' state.
- [x] Ensure branch-local retries (Phase 8) do not block sibling branches.
- [x] Merge parallel branch checkpoints in a single DB transaction: append the branch's `WorkflowNodeExecution`, deep-merge its outputs into `context.variables`, and update `current_node_ids` atomically.
- [x] Use optimistic concurrency on `WorkflowRun.checkpoint_version` (`UPDATE … WHERE checkpoint_version = :expected`); on conflict, re-read run state and retry the merge (prevent last-writer-wins data loss).
- [x] Add concurrent checkpoint consistency tests (parallel branches completing near-simultaneously preserve all branch outputs and active node ids).

### Ready-Node Resolver

- [x] Extend the resolver to track multiple concurrently `running` node ids.
- [x] Extend completion detection to require all branches (or per join policy) resolved before advancing past a join.

### Testing

- [x] Add fork/join `all` policy tests.
- [x] Add fork/join `any` policy tests.
- [x] Add fork/join `count(n)` policy tests.
- [x] Add branch failure isolation tests.
- [x] Add `workflow_max_parallel_branches` enforcement tests.
- [x] Add concurrent checkpoint merge tests (optimistic retry; no lost `context`/`current_node_ids` updates).

**Verify**

- `pytest tests/ai/workflow/test_fork_join.py`

Additional verification:

- [x] Parallel branches execute concurrently, not sequentially.
- [x] Join policies behave per specification.
- [x] Branch failures are isolated appropriately per policy.
- [x] Checkpoints remain consistent under concurrent branch execution.

**Acceptance**

- Independent workflow branches execute in parallel with correct isolation and merge semantics.
- Join policies are deterministic and configuration-driven.

**Exit Criteria**

- Parallel execution tests pass.
- Ready for LLM/Agent node integration (Phase 6).

**Completion Record**

| Metric               | Result                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------- |
| Fork node            | ✅ `ForkNodeExecutor` — fans out branch targets; enforces `workflow_max_parallel_branches` |
| Join node            | ✅ `JoinNodeExecutor` — join policies `all` / `any` / `count(n)`; merges branch outputs    |
| Parallel executor    | ✅ `WorkflowExecutor` runs fork-region branches via `asyncio.gather`                        |
| Checkpoint merge     | ✅ Optimistic `checkpoint_version` retry; deep-merge `context`/`current_node_ids`           |
| Ready-node resolver  | ✅ Fork/join region detection; join-policy-aware readiness; parallel ready grouping         |
| Branch isolation     | ✅ Sibling failures isolated per policy; incomplete branches skipped on `any`/`count` join  |
| Unit tests           | ✅ 13 new tests; 141 total in `tests/ai/workflow/`                                          |
| Backend regression   | ✅ 1450 passed; 89% `app/` coverage                                                         |

---

# Phase 6 — LLM & Agent Node Integration

**Effort:** M

**Objective**

Implement `LLMNodeExecutor` and `AgentNodeExecutor`, letting workflow nodes perform direct LLM calls or delegate bounded sub-tasks to the Agent Framework, reusing existing platform abstractions without modification.

**Deliverables**

- `LLMNodeExecutor`
- `AgentNodeExecutor`
- `app/ai/prompts/workflow/` LLM node prompt template(s)
- Node config schemas for `llm` and `agent` node types
- Integration test suite

**Steps**

### LLM Node

- [x] Implement `LLMNodeExecutor` using `PromptManager` + `LLMProvider`/`ProviderFactory`.
- [x] Render node `prompt_template` against `WorkflowContext.variables`.
- [x] Support optional `model_override` in node config.
- [x] Map the LLM response into `WorkflowNodeExecution.output`.
- [x] Include `execution_receipt_id` in LLM node output for checkpoint tracking.
- [ ] Pass `execution_receipt_id` to provider calls (crash-safe protocol; **deferred to Phase 8** — `LLMProvider.complete_chat()` has no receipt parameter in v1).
- [x] Handle provider errors as node failures (not run crashes).

### Agent Node

- [x] Implement `AgentNodeExecutor` using `DefaultAgent`.
- [x] Map node `config` (goal/instructions, tool allowlist, iteration limit) into an `AgentRequest`.
- [x] Map `AgentResponse` into `WorkflowNodeExecution.output`.
- [x] Pass `execution_receipt_id` via `AgentContext.metadata` (`AgentRequest` has no metadata field; checkpoint tracking only).
- [ ] Pass `execution_receipt_id` through to agent tool calls (crash-safe protocol; **deferred to Phase 8** — `DefaultAgent` does not yet propagate metadata to `ToolExecutionContext`).
- [x] Fail agent nodes with a clear configuration error at run time if `AGENT_RUNTIME_ENABLED=false`.
- [x] Respect the Agent Framework's own retry/iteration limits (no double-wrapping).

### Node Config Schemas

- [x] Define and validate `llm` node config shape.
- [x] Define and validate `agent` node config shape.
- [x] Extend `GraphValidator` (Phase 2) to validate these shapes at definition time.

### Testing

- [x] Add LLM node execution tests (fake provider).
- [x] Add prompt rendering tests.
- [x] Add agent node execution tests (fake agent).
- [x] Add `AGENT_RUNTIME_ENABLED=false` failure tests.
- [x] Add node config validation tests.

**Verify**

- `pytest tests/ai/workflow/test_llm_node.py tests/ai/workflow/test_agent_node.py`

Additional verification:

- [x] LLM nodes execute through the existing provider abstraction.
- [x] Agent nodes execute through the existing Agent Framework.
- [x] No duplicated retry/iteration logic between Workflow and Agent Framework.

**Acceptance**

- Workflows can perform direct LLM calls and delegate bounded reasoning to the Agent Framework without reimplementing either.
- Both node types degrade to a clear node failure (not a process crash) on misconfiguration or provider error.

**Exit Criteria**

- LLM/Agent node tests pass.
- Ready for approval nodes (Phase 7).

**Completion Record**

| Metric               | Result                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------- |
| LLM node             | ✅ `LLMNodeExecutor` — inline/`@category/name/version` prompt rendering via `PromptManager` |
| Agent node           | ✅ `AgentNodeExecutor` — `DefaultAgent` sub-tasks; fails when `AGENT_RUNTIME_ENABLED=false` |
| Node config schemas  | ✅ `graph/node_config.py`; `GraphValidator` validates `llm`/`agent` shapes at definition time |
| Prompt templates     | ✅ `app/ai/prompts/workflow/transform.v1.j2`                                                 |
| DI wiring            | ✅ `get_workflow_manager` registers `NodeType.LLM` and `NodeType.AGENT` executors           |
| Crash-safe receipts    | ⏳ Receipt ID in node output / `AgentContext.metadata`; provider & tool pass-through deferred to Phase 8 |
| Unit tests           | ✅ 21 new tests; 162 total in `tests/ai/workflow/`                                          |
| Backend regression   | ✅ 1471 passed                                                                              |

---

# Phase 7 — Human Approval Nodes, Pause & Resume

**Effort:** L

**Objective**

Implement `ApprovalNodeExecutor` and the pause/decision/resume lifecycle: a run can stop indefinitely at an approval node and later resume — following the approved or rejected edge — from an explicit human decision.

**Deliverables**

- `ApprovalNodeExecutor`
- `WorkflowRun.status = waiting_approval`
- `WorkflowManager.apply_decision(run_id, node_execution_id, decision)`
- Approved/rejected edge routing
- Integration test suite

**Steps**

### Approval Node

- [ ] Implement `ApprovalNodeExecutor` — creates a `waiting_approval` `WorkflowNodeExecution` and stops the step loop.
- [ ] Set `WorkflowRun.status = waiting_approval` and persist `current_node_ids` pointing at the approval node.
- [ ] Support optional `workflow_approval_timeout_hours` (default `0` = no timeout) as a documented future extension point (`TODO(epic-10):` for actual timeout enforcement via background jobs).

### Decision Handling

- [ ] Implement `WorkflowManager.apply_decision()` — owner-scoped; validates run + node belong to caller.
- [ ] **Atomic CAS:** apply decision only when `WorkflowNodeExecution.status` is still `waiting_approval` via conditional update (`UPDATE … WHERE id=:id AND status='waiting_approval'`) or equivalent row lock.
- [ ] In the **same DB transaction:** record `decided_by`, `decided_at`, `decision`, terminal node status (`succeeded` on approve / `failed` on reject), and transition `WorkflowRun` (`waiting_approval` → `running`, or `failed` when reject ends the run).
- [ ] If CAS affects 0 rows, another decision already landed: **no-op** when the stored decision matches the request; **reject** (409/conflict) when it differs.
- [ ] After successful commit, schedule `WorkflowExecutor` continuation (approve → follow approved edge; reject → rejected edge or fail run) — never double-schedule on losing CAS.
- [ ] On `approve`: set `decision=approved`, node `status=succeeded`; continue along the "approved" edge (or the single outgoing edge if unconditional).
- [ ] On `reject`: set `decision=rejected`, node `status=failed`; follow a declared "rejected" edge if present, otherwise fail the run.

### Resume Semantics

- [ ] Implement `WorkflowManager.resume(run_id)` to rehydrate a crashed `running` run and re-enter `WorkflowExecutor.step()` (approval continuation uses `apply_decision`, not `/resume`).
- [ ] Ensure resume never re-executes an already-`succeeded` node.
- [ ] Ensure resume correctly restores `current_node_ids` and parallel-branch state (Phase 5) if the run was interrupted mid-fan-out.

### Error Handling

- [ ] Reject decisions from non-owners.
- [ ] On CAS miss: return existing decision (no-op) if request matches; return conflict if a different decision is already recorded.
- [ ] Handle resume-of-already-completed-run as a no-op with a clear response.

### Testing

- [ ] Add approval-pause tests.
- [ ] Add approve-and-resume tests.
- [ ] Add reject-with-rejected-edge tests.
- [ ] Add reject-without-rejected-edge (run fails) tests.
- [ ] Add double-decision idempotency tests (concurrent approve/reject; only one wins; loser no-ops or conflicts).
- [ ] Add non-owner decision rejection tests.
- [ ] Add pause/resume-with-parallel-branches tests.

**Verify**

- `pytest tests/ai/workflow/test_approval_node.py tests/ai/workflow/test_resume.py`

Additional verification:

- [ ] Runs pause deterministically at approval nodes.
- [ ] Approve/reject decisions resume execution correctly.
- [ ] Resume never duplicates already-succeeded node side effects.
- [ ] Only the run owner can decide a pending approval.

**Acceptance**

- Human approval nodes pause a run indefinitely and resume it exactly where it left off based on an explicit decision; rejected approvals are recorded as `decision=rejected` with node `status=failed`.
- Decision handling is atomic, idempotent, and owner-scoped (CAS on `waiting_approval`).
- Full approval audit trail / editable tool arguments remain explicitly deferred to Epic 09.

**Exit Criteria**

- Approval/resume tests pass.
- Ready for node retry and crash recovery (Phase 8).

---

# Phase 8 — Node Retry & Crash Recovery

**Effort:** M

**Objective**

Implement per-node retry policy (wrapping `app/core/retry.py`) and run-level crash recovery so a process restart or transient node failure does not lose workflow progress.

**Deliverables**

- Workflow `RetryPolicy` wrapping `app/core/retry.py`
- Retry classification per node type
- `attempt` tracking on `WorkflowNodeExecution`
- `WorkflowManager.resume(run_id)` crash-recovery path (distinct from Phase 7's approval-resume, same API)
- Integration test suite

**Steps**

### Node Retry

- [ ] Implement workflow `RetryPolicy` wrapping `retry_async`/`is_retryable_exception`.
- [ ] Classify retryable failures (timeout, connection, provider 429) vs. non-retryable (validation, auth, not found).
- [ ] Apply `workflow_max_node_retries` / `workflow_node_retry_base_delay_seconds` per node.
- [ ] Increment `WorkflowNodeExecution.attempt` on each retry; persist one row per attempt.
- [ ] Fail the run once retries are exhausted.

### Crash Recovery

- [ ] Implement startup/administrative reconciliation: identify `running`/`waiting_approval` runs with no active in-process executor.
- [ ] Implement `WorkflowManager.resume(run_id)` to rehydrate `WorkflowContext` from the latest checkpoint and continue.
- [ ] Ensure rehydration correctly restores `current_node_ids`, including mid-fork/join state.
- [ ] **Crash-safe `running` protocol:** never treat `status=running` as complete; mark the interrupted attempt `failed` (`execution_interrupted`) and increment `attempt` before retrying side-effecting nodes.
- [ ] Re-execute **task/llm/agent** nodes only with the same `execution_receipt_id` for that attempt (pass through to `ToolExecutor` / provider calls); if idempotency cannot be guaranteed, fail the run with a clear error instead of duplicating side effects.
- [ ] Re-execute **router/fork/join** nodes directly (no external side effects).

### Run Duration Guard

- [ ] Enforce `workflow_max_run_duration_minutes` — fail runs that exceed the configured wall-clock budget.

### Error Handling

- [ ] Log retry attempts and crash-recovery actions without exposing node input/output content.
- [ ] Ensure retry/resume failures never corrupt previously checkpointed state.

### Testing

- [ ] Add retryable-vs-non-retryable classification tests.
- [ ] Add retry exhaustion tests.
- [ ] Add attempt-tracking tests.
- [ ] Add crash-recovery rehydration tests (simulated process restart).
- [ ] Add crash-mid-task-node tests proving no duplicate side effects (receipt/idempotency or fail-closed).
- [ ] Add mid-fork/join crash-recovery tests.
- [ ] Add `workflow_max_run_duration_minutes` enforcement tests.

**Verify**

- `pytest tests/ai/workflow/test_retry.py tests/ai/workflow/test_crash_recovery.py`

Additional verification:

- [ ] Retryable node failures recover automatically within policy limits.
- [ ] Non-retryable failures fail fast.
- [ ] A simulated crash mid-run resumes without duplicating side effects.
- [ ] Runs exceeding the duration guard fail cleanly.

**Acceptance**

- Node-level retry and run-level crash recovery are both provided without a background job queue (in-process, checkpoint-driven).
- No completed node is ever re-executed on resume; interrupted `running` nodes follow the crash-safe receipt protocol.

**Exit Criteria**

- Retry and crash-recovery tests pass.
- Ready for the Workflow REST API (Phase 9).

---

# Phase 9 — Workflow REST API

**Effort:** L

**Objective**

Expose the Workflow Engine through an authenticated, owner-scoped REST API per Part I, always mounted and gated per-route by `WORKFLOW_ENGINE_ENABLED`.

**Deliverables**

- `app/schemas/workflow.py`
- `app/routers/workflows.py`
- `GET /api/health` extended with `workflow_engine_enabled`
- Full endpoint set per Part I § Workflow REST API
- Integration test suite

**Steps**

### Schemas

- [ ] Define request/response schemas for definitions (create/update/list/get).
- [ ] Define request/response schemas for runs (start requires `idempotency_key` + `trigger_input`; list/get/cancel/resume).
- [ ] Define request/response schemas for approval decisions.
- [ ] Ensure schemas never expose internal-only fields (Part I § Response rules).

### Router

- [ ] Implement `POST /api/workflows`, `GET /api/workflows`, `GET /api/workflows/{id}`, `PUT /api/workflows/{id}`, `DELETE /api/workflows/{id}`.
- [ ] Implement `POST /api/workflows/{id}/runs`, `GET /api/workflows/{id}/runs` (start route enforces required `idempotency_key` and idempotent dedupe).
- [ ] Implement `GET /api/workflow-runs`, `GET /api/workflow-runs/{run_id}`.
- [ ] Implement `POST /api/workflow-runs/{run_id}/cancel`, `/resume`.
- [ ] Implement `POST /api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve`, `/reject`.
- [ ] Enforce `Depends(get_current_caller)` and owner checks on every route.
- [ ] Return `503 feature_disabled` per-route when `WORKFLOW_ENGINE_ENABLED=false`.
- [ ] Mount the router in `app/main.py`.

### Health

- [ ] Extend `app/routers/health.py` with `workflow_engine_enabled`.

### Error Handling

- [ ] Map `WorkflowNotFoundError` → `404`.
- [ ] Map `WorkflowAccessDeniedError` → `403`.
- [ ] Map `WorkflowValidationError` → `422`.
- [ ] Map generic `WorkflowError` → `500` with a safe message.

### Testing

- [ ] Add router tests for every endpoint (happy path).
- [ ] Add owner-isolation tests (cross-owner 403/404).
- [ ] Add feature-flag-off tests (`503` on every route).
- [ ] Add router idempotency tests (retry same key returns existing run, no duplicate side effects).
- [ ] Add validation-error response tests.
- [ ] Add health endpoint tests.

**Verify**

- `pytest tests/test_workflow_router.py`

Additional verification:

- [ ] All endpoints function per Part I contract.
- [ ] Feature flag gates every route independently.
- [ ] Owner isolation holds across all endpoints.
- [ ] Health endpoint reports `workflow_engine_enabled` correctly.

**Acceptance**

- The Workflow Engine is fully controllable via REST without any other client integration.
- API responses never leak cross-owner data or internal-only fields.
- Flag-off behaviour matches Memory's `503 feature_disabled` convention.

**Exit Criteria**

- REST API tests pass.
- Ready for agent tool integration (Phase 10).

---

# Phase 10 — Agent Tool Integration

**Effort:** M

**Objective**

Register `WorkflowExecutionTool` in the existing tool platform so an agent can start a workflow or check a run's status as an ordinary tool call, with zero changes to `ChatService`, `UnifiedChatService`, or `ToolExecutor` internals.

**Deliverables**

- `app/ai/tools/implementations/workflow_tool.py`
- Conditional registration in `app/ai/tools/registration.py` (only when `WORKFLOW_ENGINE_ENABLED=true`)
- Integration test suite

**Steps**

### Tool Implementation

- [ ] Implement `WorkflowExecutionTool` with actions `start` (definition_id, idempotency_key, input) and `status` (run_id).
- [ ] Map `WorkflowManager.start_run()` → `run_id` + status snapshot (idempotent on owner + definition + key); map `get_run()` → full run state for `action=status`.
- [ ] Set `WorkflowRun.session_id` from `ToolExecutionContext` when available.
- [ ] Ensure tool failures return a normalized `ToolResult(success=False, ...)`, never raise past `ToolExecutor`.

### Registration

- [ ] Register `WorkflowExecutionTool` in `app/ai/tools/registration.py` guarded by `settings.workflow_engine_enabled`.
- [ ] Verify `ToolAuthorizer` already denies guests (no new authorization logic needed).
- [ ] Verify the tool's JSON schema (`ToolDefinition.parameters`) is well-formed for LLM function-calling.

### Testing

- [ ] Add tool start idempotency tests (same key returns existing run).
- [ ] Add tool `start` action tests.
- [ ] Add tool `status` action tests.
- [ ] Add tool registration-gating tests (flag on/off).
- [ ] Add guest-denial tests (via existing `ToolAuthorizer`).
- [ ] Add end-to-end test: agent invokes the tool via `ToolExecutor` (fake provider/agent).

**Verify**

- `pytest tests/test_workflow_tool.py`

Additional verification:

- [ ] The tool is registered only when the flag is on.
- [ ] Agents can start and check workflows through the existing tool-calling path.
- [ ] No `ChatService`/`UnifiedChatService` code paths were modified.

**Acceptance**

- Workflows are reachable from chat/agent contexts exclusively through the additive tool registration.
- Existing chat, tool, and agent behaviour is unchanged when the flag is off.

**Exit Criteria**

- Tool integration tests pass.
- Ready for frontend controls (Phase 11).

**Rollback**

- [ ] Disable `WORKFLOW_ENGINE_ENABLED`.
- [ ] Remove `WorkflowExecutionTool` registration.
- [ ] Verify existing tool-calling behaviour is unchanged.

---

# Phase 11 — Frontend Controls

**Effort:** S

**Objective**

Implement a frontend dashboard for defining, triggering, inspecting, and deciding on workflows. No visual graph builder — definitions are authored as JSON in v1; the UI focuses on run visibility and approval decisions.

**Deliverables**

- Workflow dashboard UI
- Run inspection (status, node execution history)
- Approval decision UI
- Frontend API integration
- Integration test suite

**Steps**

### Dashboard UI

- [ ] Add a Workflows section to the authenticated app.
- [ ] Display Workflow feature availability (via `workflow_engine_enabled`).
- [ ] List the caller's workflow definitions.
- [ ] Support creating a definition from a JSON graph payload.

### Run Inspection

- [ ] List runs for a definition and across all definitions.
- [ ] Display a run's status and full node execution history.
- [ ] Display node input/output for completed nodes.
- [ ] Support cancelling a `running` or `waiting_approval` run.
- [ ] Support resuming a crashed `running` run via `/resume`.

### Approval Decisions

- [ ] Highlight runs in `waiting_approval` status.
- [ ] Display the pending approval node's context.
- [ ] Provide approve/reject actions.
- [ ] Confirm rejection before submission.
- [ ] Refresh run status after a decision.

### API Integration

- [ ] Create `frontend/src/api/workflowClient.ts`.
- [ ] Create `frontend/src/types/workflow.ts`.
- [ ] Create `frontend/src/pages/WorkflowsPage.tsx` (authenticated route).
- [ ] Extend `frontend/src/api/healthClient.ts` with `workflow_engine_enabled`.
- [ ] Wire navigation link in the authenticated app shell.

### Feature Flag Integration

- [ ] Hide Workflow controls when `WORKFLOW_ENGINE_ENABLED=false`.
- [ ] Preserve existing authenticated user experience.
- [ ] Preserve guest user experience.

### Error Handling

- [ ] Handle API failures gracefully.
- [ ] Handle validation failures (invalid graph JSON) with clear messages.
- [ ] Preserve existing application behaviour during frontend failures.

### Testing

- [ ] Add component tests.
- [ ] Add API integration tests.
- [ ] Add approval-decision tests.
- [ ] Add feature flag tests.
- [ ] Add accessibility tests.

**Verify**

- Frontend lint
- Frontend tests
- Production build

Additional verification:

- [ ] Workflows page renders successfully.
- [ ] Definitions and runs load correctly.
- [ ] Approve/reject actions succeed and refresh state.
- [ ] Feature flag regression passes.

**Acceptance**

- Authenticated users can define, trigger, inspect, and decide on workflows entirely through the public Workflow API.
- Frontend remains fully functional when the Workflow Engine is disabled.
- Guest users continue to experience the existing application unchanged.

**Exit Criteria**

- Workflow dashboard operational.
- API integration validated.
- Ready for production validation.

**Rollback**

- [ ] Hide Workflows navigation and page.
- [ ] Disable frontend Workflow API integration.
- [ ] Verify existing application behaviour is unchanged.

---

# Phase 12 — Validation & Release

**Effort:** M

**Objective**

Perform comprehensive validation of the completed Workflow Engine, ensuring all Part I architectural invariants have been preserved, all phases are correctly integrated, and the platform remains fully functional with the Workflow Engine both enabled and disabled. This phase certifies the Workflow Engine as production-ready.

**Deliverables**

- End-to-end validation report
- Regression test report
- Performance validation report
- Production readiness assessment
- Release summary
- Completion metrics
- Epic completion sign-off

**Steps**

### Functional Validation

- [ ] Verify all implementation phases have been completed.
- [ ] Verify all Part I architectural invariants.
- [ ] Verify sequential execution.
- [ ] Verify conditional routing.
- [ ] Verify parallel execution (fork/join).
- [ ] Verify LLM and Agent node execution.
- [ ] Verify approval pause/resume.
- [ ] Verify node retry and crash recovery.

### Integration Validation

- [ ] Verify `WorkflowManager` orchestration.
- [ ] Verify `WorkflowStore` abstraction.
- [ ] Verify `WorkflowExecutionTool` registration and execution.
- [ ] Verify Workflow REST API functionality.
- [ ] Verify Agent Framework integration.
- [ ] Verify Tool platform integration.

### Regression Testing

- [ ] Execute full backend regression suite.
- [ ] Execute full frontend regression suite.
- [ ] Verify chat functionality.
- [ ] Verify Memory functionality.
- [ ] Verify Voice functionality.
- [ ] Verify RAG functionality.
- [ ] Verify MCP functionality.
- [ ] Verify Agent Framework functionality.
- [ ] Verify Tool execution.
- [ ] Verify streaming responses.

### Feature Flag Validation

- [ ] Validate `WORKFLOW_ENGINE_ENABLED=true`.
- [ ] Validate `WORKFLOW_ENGINE_ENABLED=false`.
- [ ] Verify identical platform behaviour when disabled.
- [ ] Verify graceful feature enablement.

### Performance Validation

- [ ] Measure node execution overhead (checkpoint write latency).
- [ ] Measure parallel branch execution speedup vs. sequential.
- [ ] Measure crash-recovery rehydration latency.
- [ ] Verify acceptable production performance.

### Quality Validation

- [ ] Validate graph validation coverage.
- [ ] Validate retry classification.
- [ ] Validate idempotent resume (no duplicate side effects).
- [ ] Validate owner isolation across definitions, runs, and decisions.

### Production Readiness

- [ ] Review observability metrics.
- [ ] Review structured logging.
- [ ] Verify error handling.
- [ ] Verify failure recovery.
- [ ] Verify deployment configuration (migration applied).
- [ ] Publish production readiness report.

### Documentation

- [ ] Update implementation documentation.
- [ ] Update architecture documentation where required.
- [ ] Publish release summary.
- [ ] Record implementation metrics.
- [ ] Update Epic status.

### Testing

- [ ] Execute complete backend test suite.
- [ ] Execute complete frontend test suite.
- [ ] Execute integration tests.
- [ ] Execute end-to-end tests.
- [ ] Execute evaluation suite.
- [ ] Execute performance validation.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`
- Frontend lint
- Frontend tests
- Frontend production build

Additional verification:

- [ ] All architectural invariants preserved.
- [ ] All implementation phases validated.
- [ ] Workflow execution operational (sequential, parallel, approval).
- [ ] Frontend Workflow dashboard operational.
- [ ] Existing platform functionality unchanged.
- [ ] Production deployment ready.

**Acceptance**

- All Part I architectural constraints have been preserved.
- All implementation phases have been successfully completed.
- The Workflow Engine integrates seamlessly into the existing platform architecture.
- Existing chat, Memory, Voice, RAG, MCP, Agent, Tool execution, and streaming behaviour remain unchanged when `WORKFLOW_ENGINE_ENABLED=false`.
- Performance remains within acceptable production limits.
- All quality gates pass.
- The Workflow Engine is approved for production deployment.

**Exit Criteria**

- All validation activities completed.
- Regression suite passed.
- Performance validation approved.
- Production readiness confirmed.
- Epic formally completed.

**Rollback**

- [ ] Disable `WORKFLOW_ENGINE_ENABLED`.
- [ ] Redeploy the previous stable release if required.
- [ ] Verify platform functionality without the Workflow Engine.
- [ ] Confirm rollback validation passes.
- [ ] Record rollback outcome if executed.

**Completion Record**

| Metric                    | Result  |
| --------------------------- | ------- |
| Backend Tests               | Pending |
| Frontend Tests               | Pending |
| Integration Tests            | Pending |
| End-to-End Tests             | Pending |
| Performance Validation        | Pending |
| Feature Flag Regression       | Pending |
| Production Readiness          | Pending |
| Release Summary Published     | Pending |
| Epic Status                  | Pending |

---

# PR Map

One PR per phase.

- v2/epic-06/phase-00-baseline
- v2/epic-06/phase-01-models-migration
- v2/epic-06/phase-02-graph-validation
- v2/epic-06/phase-03-sequential-engine
- v2/epic-06/phase-04-conditional-routing
- v2/epic-06/phase-05-parallel-execution
- v2/epic-06/phase-06-llm-agent-nodes
- v2/epic-06/phase-07-approval-resume
- v2/epic-06/phase-08-retry-crash-recovery
- v2/epic-06/phase-09-rest-api
- v2/epic-06/phase-10-agent-tool
- v2/epic-06/phase-11-frontend
- v2/epic-06/phase-12-release

---

# Risks

| Risk                          | Mitigation                                                                 |
| -------------------------------- | ------------------------------------------------------------------------- |
| Graph misconfiguration           | `GraphValidator` rejects invalid definitions before activation            |
| Runaway/looping graphs           | Cycle detection at validation time; `workflow_max_run_duration_minutes`  |
| Condition DSL abuse              | Declarative-only DSL; no `eval`/`exec`                                    |
| Duplicate side effects on start  | Required `idempotency_key`; unique `(owner_id, workflow_definition_id, idempotency_key)` |
| Duplicate side effects on resume | Checkpoint-per-transition; never assume `running`=complete; per-attempt `execution_receipt_id` for task/llm/agent; fail-closed when not idempotent |
| Cross-owner data leakage         | Owner isolation on every `WorkflowStore`/REST operation                   |
| Provider/backend coupling        | `WorkflowStore` abstraction                                                |
| Parallel execution races         | Per-run optimistic `checkpoint_version` merge + retry; single-transaction append/merge of node execution + run state |
| Indefinite approval pauses       | `workflow_approval_timeout_hours` config surface (enforcement deferred — `TODO(epic-10)`) |
| Feature regression               | `WORKFLOW_ENGINE_ENABLED` flag-off parity                                 |
| Excessive run/history growth     | `workflow_run_retention_days` (cleanup mechanism deferred — `TODO(epic-10)`) |
| Agent/Tool coupling              | Reuse existing `DefaultAgent`/`ToolExecutor`; no reimplementation          |

---

# Observability

Structured metrics only.

| Field                          | Purpose                    |
| --------------------------------- | ----------------------------- |
| workflow_engine_enabled           | Feature flag state           |
| workflow_runs_started              | Trigger volume                |
| workflow_runs_completed            | Completion volume             |
| workflow_runs_failed               | Failure volume                |
| workflow_node_execution_latency_ms | Per-node execution latency    |
| workflow_checkpoint_latency_ms     | Persistence latency           |
| workflow_retry_count               | Retry volume                  |
| workflow_approval_pending_count    | Pending approvals             |
| workflow_parallel_branch_count     | Fan-out width distribution    |

No workflow input/output content or personally identifiable information should be logged by default.

---

# Definition of Done

- [ ] All Part I architectural invariants preserved.
- [ ] Public APIs frozen after Phase 1.
- [ ] Workflow execution invoked only through `WorkflowManager`.
- [ ] No `ChatService`/`UnifiedChatService` coupling — tool-only chat/agent surface.
- [ ] Task/LLM/Agent nodes reuse existing platform components with no reimplementation.
- [ ] `WORKFLOW_ENGINE_ENABLED=false` preserves Epic 05 behaviour (full flag-off parity validated in Phase 12).
- [ ] Conditional routing, parallel execution, approval pause/resume, retry, and crash recovery operational.
- [ ] Workflow REST API and frontend dashboard complete.
- [ ] Backend and frontend tests pass; coverage ≥80% on `app/ai/workflow/`.
- [ ] Release summary published.
- [ ] User authorizes Epic 07.

---

## Files index

| Path                                                       | Action  | Owner    | Phase |
| ------------------------------------------------------------- | ------- | -------- | ----- |
| `docs/audits/post-mvp-v2-epic6-phase-0-baseline-audit.md`   | create  | Docs     | 0     |
| `app/ai/workflow/**`                                        | create  | Core     | 1–8   |
| `app/ai/prompts/workflow/**`                                 | create  | Core     | 6     |
| `app/db/models.py`                                          | modify  | Core     | 1     |
| `alembic/versions/0007_workflow_tables.py`                  | create  | Core     | 1     |
| `app/core/config.py`                                        | modify  | Core     | 1     |
| `backend-python/.env.example`                                | modify  | Docs     | 1     |
| `app/schemas/workflow.py`                                    | create  | Core     | 9     |
| `app/routers/workflows.py`                                   | create  | Adapter  | 9     |
| `app/routers/health.py`                                       | modify  | Adapter  | 9     |
| `app/main.py`                                                 | modify  | Adapter  | 9     |
| `app/ai/deps.py`                                              | modify  | Adapter  | 1, 9, 10 |
| `app/ai/tools/implementations/workflow_tool.py`               | create  | Adapter  | 10    |
| `app/ai/tools/registration.py`                                | modify  | Adapter  | 10    |
| `tests/ai/workflow/**`                                        | create  | Tests    | 1–8   |
| `tests/test_workflow_router.py`                               | create  | Tests    | 9     |
| `tests/test_workflow_tool.py`                                 | create  | Tests    | 10    |
| `tests/fakes.py`                                              | modify  | Tests    | 1–8   |
| `frontend/src/api/workflowClient.ts`                          | create  | Frontend | 11    |
| `frontend/src/types/workflow.ts`                              | create  | Frontend | 11    |
| `frontend/src/pages/WorkflowsPage.tsx`                        | create  | Frontend | 11    |
| `frontend/src/api/healthClient.ts`                             | modify  | Frontend | 11    |
| `docs/releases/post-mvp-v2-epic6-release-summary.md`          | create  | Docs     | 12    |

---

## Changelog

| Version | Date       | Changes            |
| ------- | ---------- | -------------------- |
| 1       | 2026-08-04 | Initial epic draft |
| 1.1     | 2026-08-04 | Phase 0 complete: baseline audit published; quality gates verified (1305 tests, 89.66% cov, eval 5/5, frontend 251 tests). Part II only. |
| 1.2     | 2026-08-04 | Clarify run launch contract: async in-process `start_run()` returns before completion; remove multi-process wording; align scheduler, REST, tool, and Phase 3/10 steps. Part I only. |
| 1.3     | 2026-08-04 | Remove orphan `paused` run status; human approval uses `waiting_approval`; `/resume` is crash-recovery for `running` runs only. Part I + Part II doc sync. |
| 1.4     | 2026-08-04 | Align approval reject semantics: node `status=failed` + `decision=rejected` (NodeStatus only; no rejected status value). Part I + Phase 7 sync. |
| 1.5     | 2026-08-04 | Require owner-scoped `idempotency_key` on run start (REST + tool); dedupe `(owner_id, workflow_definition_id, idempotency_key)`. Part I + Phases 1/3/9/10 sync. |
| 1.6     | 2026-08-04 | Define crash-safe protocol for interrupted `status=running` nodes: execution receipts + fail-closed for non-idempotent side effects. Part I + Phases 3/8 sync. |
| 1.7     | 2026-08-04 | Parallel branch checkpoints: optimistic `checkpoint_version` merge + retry; prevent last-writer-wins on `context`/`current_node_ids`. Part I + Phase 5 sync. |
| 1.8     | 2026-08-04 | `apply_decision()` atomic CAS on `waiting_approval` + same-transaction run transition; no-op/conflict on duplicate decisions. Phase 7 sync. |
| 1.9     | 2026-08-05 | Phase 1 complete: canonical models/enums, `WorkflowStore` protocol, `PostgresWorkflowStore` scaffold, `WorkflowManager` skeleton, `0007_workflow_tables` migration, `WORKFLOW_ENGINE_ENABLED` + workflow config, DI wiring. 39 workflow tests; 1344 total backend passed; 89.80% coverage. Public API frozen. Phase 2 complete: `GraphValidator`, condition DSL shape validation, definition CRUD via `WorkflowManager`/`PostgresWorkflowStore`, versioning on run reference. 61 workflow tests; 1370 total backend passed; 90.00% coverage. Migration rollback CI smoke test pending. |
| 1.10    | 2026-08-05 | Phase 3 complete: `WorkflowExecutor` sequential/branching step loop, `NodeExecutor` protocol, `TaskNodeExecutor` (dot-path `arguments_template` resolution), `WorkflowManager.start_run()`/`get_run()`/`list_runs()`, per-transition checkpointing via `PostgresWorkflowStore`, background run scheduling. 88 workflow tests; 1397 total backend passed; 89.20% coverage. |
| 1.11    | 2026-08-05 | Phase 4 complete: `ConditionEvaluator`, declarative condition DSL, `RouterNodeExecutor` (exclusive / `all_matching`), routing-aware ready-node resolver, unselected-branch skip semantics. 128 workflow tests; 1437 total backend passed; 90.00% coverage. |
| 1.12    | 2026-08-05 | Phase 5 complete: `ForkNodeExecutor`, `JoinNodeExecutor`, fork/join parallel execution (`asyncio.gather`), join policies (`all` / `any` / `count(n)`), optimistic checkpoint merge/retry, join-policy-aware ready-node resolver. 141 workflow tests; 1450 total backend passed; 89% coverage. |
| 1.13    | 2026-08-05 | Phase 6 complete: `LLMNodeExecutor`, `AgentNodeExecutor`, `graph/node_config.py`, workflow prompt templates, DI wiring for LLM/Agent node types. 162 workflow tests; 1471 total backend passed. |
| 1.14    | 2026-08-05 | Phase 6 doc sync: clarify `execution_receipt_id` is carried in node output / `AgentContext.metadata` (not `AgentRequest`); provider & tool pass-through deferred to Phase 8. |

---
