---
epic: v2-09
title: Human-in-the-Loop
status: in_progress
version: 1.7
depends_on: [v2-06, v2-07, v2-08]
provides:
  [
    ApprovalKind,
    ApprovalStatus,
    ProposedToolCall,
    AgentToolApproval,
    ApprovalResult,
    ApprovalRevision,
    ApprovalAuditEntry,
    ApprovalPolicy,
    AgentApprovalService,
    HITL_ENABLED,
    approvals_router,
  ]
feature_flags: [HITL_ENABLED]
packages: [app/ai/hitl]
test_paths:
  [
    tests/ai/hitl,
    tests/ai/agent/test_tool_approval.py,
    tests/ai/workflow/test_approval_node.py,
    tests/ai/workflow/test_graph_validator.py,
    tests/ai/hitl/test_coverage_mcp_plugin.py,
    tests/test_approvals_router.py,
    tests/test_workflows_router.py,
    frontend/src/pages/ApprovalsPage.test.tsx,
    frontend/src/api/approvalsClient.test.ts,
  ]
---

# Post-MVP V2 Epic 09 — Human-in-the-Loop

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "9. Human-in-the-Loop"

**Predecessor:** [Epic 08 — Plugin Architecture](./post-mvp-v2-epic-08-plugin-architecture.md)

---

# Part I — Design

## Objective

Introduce a unified **Human-in-the-Loop (HITL)** capability so any tool invocation platform-wide — chat/agent tool calls, MCP tools, and plugin tools — can require an explicit human decision before executing, with the ability to edit proposed arguments and a durable, queryable audit trail. Epic 06 shipped the workflow engine's `approval` node with pause (`waiting_approval`) and atomic decision recording, but explicitly deferred **"HITL audit trail, editable tool arguments, approval UX polish"**. Epic 08 deferred **"human-in-the-loop approval before plugin tool execution"**. Epic 03 shipped MCP tool execution with no pause/edit/approve flow at all ("MCP execution is synchronous behind `ToolExecutor`"). This epic closes all three gaps without duplicating the workflow engine's proven pause/resume design — it **extends** Epic 06's approval primitives for workflows and **adds a parallel, symmetrical primitive** for the one surface Epic 06 never covered: tool calls made directly from a chat/agent turn (outside any workflow run).

**Delivers:** A platform-wide `ApprovalPolicy` that flags tool calls as requiring human approval (per-tool default plus operator override); a new **agent tool-call approval gate** that pauses a chat/agent turn before executing a flagged tool, persists a durable `AgentToolApproval` record (including a serialized resume snapshot and a cross-system `approval_correlation_id`), and later resumes the paused ReAct loop — with optionally edited arguments — via a decision REST endpoint that streams the continuation; a canonical `ApprovalResult` decision DTO and an append-only `approval_revisions` history so every edit made before a final decision is preserved, not just the last one; **editable tool arguments and a decision `reason`** added to Epic 06's workflow approval nodes (additive columns + fields, no behavioural change when unused); a **unified read-only approval inbox/audit API** (`GET /api/approvals`, `GET /api/approvals/{id}`) that aggregates both workflow-node and agent-tool approvals into one queryable history; `GraphValidator` coverage ensuring approval-required tools cannot be reached from a workflow task/agent node without a preceding approval node; HITL observability (approval-scoped tracing attributes, decision/resume/execution latency, pending counts); reference eval scenarios; and a frontend approval inbox with inline argument editing and an audit history view — all behind `HITL_ENABLED=false` (default).

**Does not ship:** A generic mechanism for a workflow task/agent node to implicitly pause for approval without an explicit `approval` node in the graph (workflows keep Epic 06's explicit-node model; this epic only adds validation so sensitive tools cannot bypass it — see Locked Decisions); approval timeout auto-expiry enforcement (`hitl_approval_timeout_hours` is a documented config surface only, same posture as Epic 06's `workflow_approval_timeout_hours`; enforcement is `TODO(epic-10):`); multi-step chained editing across more than one paused tool call per decision; spoken/voice approval prompts (voice-initiated tool calls are gated the same as text, but the decision itself remains a text/REST interaction); RBAC-scoped approval delegation or team-shared approval queues (Epic 11); per-tool rate limits or quotas on approval requests (Epic 11); a visual policy editor (policy is config + per-tool flags, not a UI-authored rules engine).

Capabilities:

- Approval workflows
- Pause/resume
- Editable tool arguments
- Audit history

The Human-in-the-Loop capability is additive. When disabled, existing chat, RAG, MCP, memory, voice, agent, tool, workflow, plugin, and observability pipelines remain unchanged, and Epic 06's workflow approval nodes continue to behave exactly as shipped.

---

## Design Principles

- Platform-first — one `ApprovalPolicy` consulted by every tool-call path (chat/agent, workflow task/agent nodes, MCP, plugins)
- Composition over coupling — extend Epic 06's `WorkflowNodeExecution`/`apply_decision()` additively; add a **new**, symmetrical primitive for the chat/agent surface rather than forcing workflow-shaped pause semantics onto a bounded ReAct loop
- Interface-driven — `ApprovalPolicy.requires_approval(tool_name)` is the single decision point; callers never inspect tool internals
- Security by default — approval-required tools are fail-closed: a workflow graph that can reach a flagged tool without a preceding approval node fails validation; an agent turn that reaches a flagged tool without a decision never executes it
- Provider-agnostic — approval gating is tool-name based, not provider- or transport-specific (covers native, MCP, and plugin tools identically)
- Explicit lifecycle — a paused turn/run is durably persisted (Postgres), never held only in server memory across the decision round-trip
- Feature-flag rollout
- Avoid over-engineering — reuse Epic 06's Compare-And-Swap (CAS) decision pattern and background-resume pattern; no new job queue, no generic workflow-node pause primitive, no bespoke approval DSL

---

## Scope

### In Scope

- HITL core (`app/ai/hitl/`): `ApprovalPolicy`, `ApprovalKind`, `ApprovalStatus`, `ProposedToolCall`, `AgentToolApproval`, `ApprovalAuditEntry`, exceptions
- `HITL_ENABLED` feature flag (default `false`)
- Per-tool `ToolDefinition.requires_approval: bool = False` plus operator override via `hitl_required_tool_names` config (union of both — either source can flag a tool)
- **Agent tool-call approval gate** — `ToolRunner` consults `ApprovalPolicy` before dispatching a planned tool-call step; when required, the step pauses instead of executing
- **Pause snapshot** — serialize the in-flight `Scratchpad` entries and `AgentExecutionState` into a new `agent_tool_approvals` table row; persist a placeholder assistant `ChatMessage` (`status='waiting_approval'`) linked via `pending_approval_id`
- **Resume** — `AgentApprovalService.decide()` rehydrates the scratchpad/state snapshot, applies the decision (optionally with `edited_calls`, validated against each tool's parameter schema), executes approved calls through the existing `ToolExecutor`, and re-enters `AgentExecutor` to continue the ReAct loop to completion (further tool calls may pause again using the same gate)
- **Editable tool arguments** — both surfaces support edits at decision time: agent tool approvals via `edited_calls`; workflow approval nodes via new `edited_arguments`, propagated into `WorkflowContext.variables` for downstream node templating (reuses existing `{{variables.…}}` resolution — no new templating engine)
- **Audit trail** — `reason` (decision comment) added to both surfaces; append-only decision history; an append-only `approval_revisions` ledger capturing every edit (not only the final one) with its author and timestamp; unified read-only aggregation across workflow-node and agent-tool approvals
- **Workflow graph guard** — `GraphValidator` rejects workflow definitions where a `task`/`agent` node's configured tool is approval-required and no `approval` node with a matching outgoing path precedes it (fail-closed; Epic 06 explicit-approval-node model unchanged otherwise); a documented `O(V + E)` reachability check
- **Cross-system correlation** — `approval_correlation_id` links an approval record to its tool execution, trace spans, audit entries, and eval run output, independent of `execution_id`/`run_id` lifetimes
- **Canonical decision result** — `ApprovalResult` (status, edited arguments, reason, approver, decided_at) is the single shape both `AgentApprovalService.decide()` and `WorkflowManager.apply_decision()` produce, reducing ad-hoc status-field threading across agent/workflow/REST/frontend layers
- Authenticated Approval REST API — unified list/detail (read-only aggregation) plus the new agent-tool decision endpoint and a pre-decision revise endpoint; existing workflow approve/reject endpoints gain optional `edited_arguments`/`reason` request body fields (additive)
- Observability hooks — `approval_span` (with `approval_id`, `approval_kind`, `approval_status`, `approval_decision` attributes), `agent_tool_approval_pending_count`, `approval_decisions_total`, `hitl_approval_decision_latency_ms`, `hitl_resume_latency_ms`, `hitl_tool_execution_latency_ms` (when `OBSERVABILITY_ENABLED`)
- Evaluation cases exercising a sensitive reference tool through approve, approve-with-edits, and reject paths (chat/agent and workflow), plus adversarial/concurrency cases (duplicate decisions, concurrent decisions, invalid edits, stale ids)
- Frontend approval inbox (pending, cross-surface) with inline argument editing, reason capture, and an audit history tab showing the latest revision (full revision history available on demand); `WorkflowsPage` approval UI gains edit-args + reason inputs

### Out of Scope

- Implicit approval pausing for workflow task/agent nodes without an explicit `approval` node (validation-guarded instead — see Locked Decisions)
- Approval timeout auto-expiry / background enforcement of `hitl_approval_timeout_hours` (Epic 10)
- Automatic cancellation of pending approvals when their session/run/plugin disappears (`ApprovalStatus.CANCELLED` is reserved in the enum for this; enforcement is `TODO(epic-10):`)
- RBAC-scoped approval delegation, team/shared approval queues, per-role visibility (Epic 11)
- Rate limits or quotas on approval requests (Epic 11)
- Spoken/voice approval prompts or barge-in during a paused turn (voice tool calls are gated identically to text; the decision UX remains text/REST)
- Chained multi-turn approval editing **across separate pauses** (only the currently paused tool call(s) are editable per decision; a subsequent pause is a new, independent `AgentToolApproval`) — multiple revisions **within the same pause**, before its final decision, are in scope (see § Approval Revision History)
- Visual policy authoring UI (policy is `ToolDefinition.requires_approval` + config, not a rules engine); RBAC/org/environment-conditional policy engines (see § Approval Policy & Configuration extensibility note)
- Distributed/multi-worker approval resume (single-process background resume, same posture as Epic 06 workflow execution)
- Derived/computed operational metrics (success rate, rejection rate, edit rate, average edits per approval) as new instruments — these are dashboard-layer computations over the raw counters this epic ships (see § Observability Design)

---

## High-Level Architecture

```text
                     ┌───────────────────────────────┐
                     │        ApprovalPolicy          │
                     │  requires_approval(tool_name)  │
                     └───────────────┬─────────────────┘
                                     │ consulted by
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
 ToolRunner (agent)          GraphValidator (workflow)      (MCP / plugin tools —
 pauses the ReAct turn       rejects graphs reaching a       just ToolDefinitions;
 before dispatch             flagged tool without a          covered transparently
        │                    preceding approval node         by both paths above)
        ▼                            │
 agent_tool_approvals table   ApprovalNodeExecutor (Epic 06, unchanged pause)
        │                            │
        ▼                            ▼
 AgentApprovalService.decide()   WorkflowManager.apply_decision()
   (edited_calls, reason)          (edited_arguments, reason — additive)
        │                            │
        ▼                            ▼
 AgentExecutor.resume_from_    WorkflowExecutor.continue_from_approval()
 approval() → ToolExecutor           (unchanged Epic 06 continuation)
        │                            │
        └──────────────┬─────────────┘
                        ▼
            ApprovalAuditEntry aggregation
          GET /api/approvals (unified, read-only)
                        │
                approval_span / metrics (when Observability on)
```

**Two symmetrical pause primitives, one policy:** `ApprovalPolicy` is the single source of truth for "does this tool require a human decision", but the _pause mechanism_ differs by invocation surface because the surfaces themselves differ in shape:

- **Workflow** runs are already a durable, checkpointed state machine (Epic 06) — an approval-required tool call reachable from a `task`/`agent` node is handled by requiring an explicit `approval` node upstream in the graph; no new pause mechanism is introduced for workflows.
- **Chat/agent** turns are a bounded, in-process ReAct loop with no prior pause primitive (Part I of Epic 01) — this epic adds the durable `agent_tool_approvals` record plus scratchpad/state snapshotting as the minimal necessary primitive to pause and resume a turn.

Both primitives write into the **same audit surface** (`ApprovalAuditEntry`) so operators have one place to review every human decision made platform-wide, regardless of which surface triggered it.

---

## Locked Architectural Decisions

| Topic                         | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Deferred to                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Approval scope determination  | `ApprovalPolicy.requires_approval(tool_name)` = `ToolDefinition.requires_approval OR tool_name in settings.hitl_required_tool_names`; union of tool-authored default and operator override                                                                                                                                                                                                                                                                                                        | Per-caller/per-argument conditional policy → future                                                                          |
| Workflow pause mechanism      | Unchanged from Epic 06 — explicit `approval` node only; **no** implicit pause for task/agent node tool calls                                                                                                                                                                                                                                                                                                                                                                                      | Implicit node-level pause → future                                                                                           |
| Workflow safety net           | `GraphValidator` (extended) fails workflow definition create/update when a `task`/`agent` node's tool is approval-required and no `approval` node with a satisfying path precedes it in the graph                                                                                                                                                                                                                                                                                                 | Runtime auto-insertion of approval nodes → forbidden                                                                         |
| Agent/chat pause mechanism    | New `agent_tool_approvals` table; pause snapshots the in-flight `Scratchpad` entries (`ScratchpadEntry[]`, already JSON-serializable Pydantic models) and `AgentExecutionState` — the only case where scratchpad content is persisted (documented exception to Epic 01's "never persisted" scratchpad invariant, scoped to HITL pause only)                                                                                                                                                       | General scratchpad persistence / cross-session replay → future                                                               |
| Step-level pause granularity  | A planned step containing **any** approval-required tool call pauses the **entire step** (all calls in that step, including non-flagged ones, wait together); no partial-step execution                                                                                                                                                                                                                                                                                                           | Independent per-call approval within one step → future                                                                       |
| Editable arguments (agent)    | `AgentToolApproval.edited_calls` — optional list mirroring `proposed_calls` shape; when present for a call, validated via `ToolValidator` against that tool's parameter schema before execution; validation failure rejects the decision (`422`), pause remains `pending`                                                                                                                                                                                                                         | Free-form argument diffing/merge UI → future                                                                                 |
| Editable arguments (workflow) | Approval node decisions accept optional `edited_arguments: dict[str, object]`; stored on `WorkflowNodeExecution.edited_arguments` and merged into `build_approval_decision_output()` output under `run.context.variables[node_id].edited_arguments`, resolvable by downstream `task`/`agent` node `arguments_template` via existing `{{variables.<node_id>.edited_arguments.<field>}}` placeholders (`app/ai/workflow/nodes/task_node.py::_resolve_template` — no new templating code)            | Schema-validated edits at decision time → future (v1 validates only at the consuming node, same as any other template value) |
| Decision reason / comment     | `reason: str \| None` added to both `AgentToolApproval` and `WorkflowNodeExecution`; free-text, size-bounded (2 KB), never interpreted, always included in audit responses                                                                                                                                                                                                                                                                                                                        | Structured rejection-reason taxonomy → future                                                                                |
| Resume after agent approval   | On approve, `AgentApprovalService` rehydrates scratchpad/state, executes approved (possibly edited) calls directly via `ToolExecutor` (bypassing the gate for those specific calls — the gate already ran once at pause time), appends results to the rehydrated scratchpad, and calls `AgentExecutor.resume_from_approval()` to re-enter the ReAct loop from the `PLANNING` transition (reflection on the just-approved step is skipped in v1 — the loop proceeds to the next planner iteration) | Resuming into the exact pre-pause reflection step → future                                                                   |
| Reject semantics (agent)      | Rejected agent tool approval: `AgentToolApproval.status=rejected`; linked `ChatMessage.status` set to `rejected` (new allowed value) with no further tool execution; the agent turn ends — caller must start a new turn to retry                                                                                                                                                                                                                                                                  | Automatic re-plan around a rejected tool → future                                                                            |
| Reject semantics (workflow)   | Unchanged from Epic 06 — `decision=rejected`, node `status=failed`, run follows rejected edge or ends                                                                                                                                                                                                                                                                                                                                                                                             | —                                                                                                                            |
| Timeout                       | `hitl_approval_timeout_hours` (default `0` = no timeout) documented on both surfaces; enforcement is a background-job concern                                                                                                                                                                                                                                                                                                                                                                     | Epic 10                                                                                                                      |
| MCP / plugin tool coverage    | No MCP- or plugin-specific code — both are ordinary `ToolDefinition`/handler pairs in `ToolRegistry`; `requires_approval` works identically regardless of origin. Plugin manifests may set `requires_approval: true` on a contributed tool (parsed, not enforced by the Plugin SDK itself — enforcement is `ApprovalPolicy`'s job)                                                                                                                                                                | Per-plugin approval RBAC → Epic 11                                                                                           |
| Audit aggregation             | `GET /api/approvals` reads from **both** `agent_tool_approvals` and workflow `node_executions` (filtered to `node_type=approval`) and merges into `ApprovalAuditEntry` — no new denormalized audit table; source-of-truth stays in each surface's existing storage                                                                                                                                                                                                                                | Durable unified audit table → future (only if query performance requires it)                                                 |
| Concurrency                   | Agent tool approval decisions use the same Compare-And-Swap (CAS) pattern as Epic 06 (`UPDATE … WHERE status='pending'`); duplicate/conflicting decisions return `409`                                                                                                                                                                                                                                                                                                                                               | —                                                                                                                            |
| Caller scope                  | Only the session/run owner may decide; `GET /api/approvals` (list) is caller-scoped (`owner_id = current user`) — no cross-user visibility in v1                                                                                                                                                                                                                                                                                                                                                  | Team/shared queues → Epic 11                                                                                                 |
| Revision history              | Every edit submitted for a pending approval — whether via the pre-decision revise endpoint or supplied inline on the final decide/apply call — appends one immutable `ApprovalRevision` row; nothing is ever overwritten or deleted from `approval_revisions`                                                                                                                                                                                                                                    | Diff/merge visualization across revisions → future                                                                          |
| Decision result shape         | `AgentApprovalService.decide()` and `WorkflowManager.apply_decision()` both build and return an `ApprovalResult` (status, edited arguments, reason, approver, decided_at); callers (REST, frontend) consume this one shape regardless of surface                                                                                                                                                                                                                                                 | —                                                                                                                            |
| Cross-system correlation      | `approval_correlation_id` (UUID, generated at pause/approval-node creation) is attached to the approval record, propagated onto `ToolExecutionContext` for the eventual execution, and set as a span attribute on both the approval span and the resulting `tool_span` — the durable link when `execution_id`/`run_id` values are ephemeral or reused                                                                                                                                          | Cross-system correlation into external SIEM/audit export → future                                                          |
| Approval status vs. execution outcome | `AgentToolApproval.status`/`WorkflowNodeExecution.decision` reflect the **human decision only** and never change once terminal; if the approved tool subsequently fails (provider/plugin/MCP/infra error), that failure is recorded on the *execution* result (`ToolResult.success=False`, `ChatMessage.status=error`, or the workflow run's own failure path) — an approval is never retroactively "un-approved" | Automatic re-approval workflow on execution failure → future                                                               |
| Cancelled state               | `ApprovalStatus` reserves `cancelled` (pending approval whose owning session/run/plugin was removed before a decision was made); the value exists in the enum and schema in V2 but nothing transitions an approval to it yet — no orphaned-approval sweep exists until Epic 10                                                                                                                                                                                                                   | Automatic cancellation on session/run/plugin deletion → Epic 10                                                             |
| Policy extensibility          | `ApprovalPolicy.requires_approval()` is a pure function today; its constructor accepts pre-resolved settings so a future policy engine (RBAC, org rules, environment-conditional logic) can be substituted behind the same interface without touching call sites; an in-memory decision cache is an allowed internal implementation detail as long as `HITL_ENABLED`/config changes take effect on process restart (no runtime cache invalidation API in V2)                                | RBAC/org/environment-conditional policy engine → Epic 11                                                                    |

---

## Approval Policy & Configuration

`ApprovalPolicy` (`app/ai/hitl/policy.py`) is a small, stateless decision function consulted by both gating points:

```python
class ApprovalPolicy:
    def __init__(self, *, required_tool_names: frozenset[str]) -> None:
        self._required_tool_names = required_tool_names

    def requires_approval(self, tool: ToolDefinition) -> bool:
        return tool.requires_approval or tool.name in self._required_tool_names
```

- `ToolDefinition.requires_approval: bool = False` — new optional field (additive; existing tool registrations default unchanged). Tool authors (native, MCP adapter construction, or plugin `register_tool()`) may set it to mark a tool permanently sensitive (e.g. `delete_file`, `send_email`).
- `settings.hitl_required_tool_names: frozenset[str] = frozenset()` — operator override so an existing tool can be flagged sensitive **without a code change or redeploy**, mirroring the operational flexibility of `plugin_allowlist` (Epic 08).
- The union means either source is sufficient; neither can _unflag_ a tool the other has flagged.
- When `HITL_ENABLED=false`, `ApprovalPolicy.requires_approval()` is never consulted — `ToolRunner` and `GraphValidator` behave exactly as before this epic.

**Forward-looking extensibility (not implemented in V2):** `ApprovalPolicy` is intentionally the *only* call site both surfaces consult, so a future policy engine — RBAC-scoped rules, organization policies, environment-conditional logic (e.g. "require approval only in production") — can replace `requires_approval()`'s implementation without touching `ToolRunner` or `GraphValidator`. An implementation is free to memoize `requires_approval()` results in-process (tool set is static after startup registration), but V2 does not require a cache and ships none by default; if one is added, it must be invalidated on process restart only (no runtime cache-bust API).

---

## Agent Tool-Call Approval — Domain Model

New table **`agent_tool_approvals`** (Postgres) and mirrored Pydantic model `AgentToolApproval`:

| Field                       | Type                                   | Notes                                                                                                               |
| --------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `id`                        | `uuid`                                 | Primary key                                                                                                         |
| `session_id`                | `uuid` FK `chat_sessions.id`           | Owning chat session                                                                                                 |
| `owner_id`                  | `uuid` FK `users.id`                   | Session owner; only this user may decide                                                                            |
| `execution_id`              | `text`                                 | Agent execution id correlating scratchpad/trace/spans for *this* turn                                               |
| `approval_correlation_id`   | `uuid`                                 | Stable id linking this approval to its eventual tool execution, trace spans, audit entries, and eval output — survives even where `execution_id` is not reused across a pause/resume boundary |
| `status`                    | `text` CHECK                           | `pending` \| `approved` \| `rejected` \| `expired` \| `cancelled` (`expired`/`cancelled` reserved — no transition path yet; see Locked Decisions) |
| `proposed_calls`            | `jsonb`                                | `ProposedToolCall[]` — `{name, arguments, call_id}` as planned by the ReAct loop                                    |
| `edited_calls`              | `jsonb` \| `null`                      | The **latest** revision's overrides, same shape as `proposed_calls`; `null` when approved as-is or rejected. Full edit history lives in `approval_revisions`, not here |
| `reason`                    | `text` \| `null`                       | Optional **final decision** comment (≤ 2 KB) — distinct from a revision's optional `note`                          |
| `paused_scratchpad`         | `jsonb`                                | Serialized `ScratchpadEntry[]` at pause time (HITL-only persistence exception)                                      |
| `paused_state`              | `jsonb`                                | Serialized `AgentExecutionState` snapshot at pause time                                                             |
| `pending_message_id`        | `uuid` FK `chat_messages.id` \| `null` | The placeholder assistant message created at pause                                                                  |
| `requested_at`              | `timestamptz`                          | Pause time                                                                                                          |
| `decided_at`                | `timestamptz` \| `null`                | Decision time                                                                                                       |
| `decided_by`                | `uuid` FK `users.id` \| `null`         | Always `owner_id` in v1 (no delegation)                                                                             |
| `created_at` / `updated_at` | `timestamptz`                          | Standard bookkeeping                                                                                                |

**`ChatMessage` extension (additive):**

- `status` CHECK constraint extended: `'complete' | 'stopped' | 'error' | 'interrupted' | 'waiting_approval' | 'rejected'`
- New nullable column `pending_approval_id: uuid FK agent_tool_approvals.id` — set only on messages created by the HITL pause path

**`ProposedToolCall`** (`app/ai/hitl/models.py`):

```python
class ProposedToolCall(BaseModel):
    name: str
    arguments: dict[str, object]
    call_id: str
```

---

## Approval Revision History

The first draft of this document persisted only the final `edited_calls`, losing every intermediate edit a caller made while reviewing a pending approval. New table **`approval_revisions`** (Postgres) — append-only, never updated or deleted:

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `uuid` | Primary key |
| `approval_id` | `uuid` | References `agent_tool_approvals.id` **or** a workflow `node_execution.id`, disambiguated by `approval_kind` (no FK constraint across the two source tables — application-enforced) |
| `approval_kind` | `text` CHECK | `agent_tool` \| `workflow_node` |
| `revision_number` | `int` | Monotonically increasing per `(approval_id, approval_kind)`, starting at `1` |
| `edited_by` | `uuid` FK `users.id` | Always the approval's owner in v1 |
| `edited_at` | `timestamptz` | When this revision was submitted |
| `edited_payload` | `jsonb` | `ProposedToolCall[]` (agent) or `dict[str, object]` (workflow) — matches the surface's own edit field shape |
| `note` | `text` \| `null` | Optional short per-revision comment, distinct from the final decision `reason` |

**How revisions are created:**

- **Agent tool approvals** — a new pre-decision `POST /api/approvals/{id}/revise` endpoint (Phase 3) lets the caller submit `edited_calls` while the approval is still `status=pending`; each call appends one `ApprovalRevision` row and updates `agent_tool_approvals.edited_calls` to the latest value. The final `decide()` call may omit `edited_calls` (using the latest revision) or supply its own (appending one more revision first).
- **Workflow approval nodes** — supplying `edited_arguments` on the `/approve` call appends a single `ApprovalRevision` row (`revision_number=1`) at decision time; Epic 06's one-shot decision model is unchanged, so a workflow approval has at most one revision in V2.
- **Frontend contract** — the inbox and audit UI render only the latest revision by default (per the review's recommendation), with a "view revision history" affordance listing every `ApprovalRevision` for that approval.

`approval_revisions` is read-only from the execution path's perspective — it never gates execution; only `agent_tool_approvals.edited_calls` / `WorkflowNodeExecution.edited_arguments` (already kept in sync with the latest revision) are read when a decision executes.

---

## `ApprovalResult` — Canonical Decision Result

Both `AgentApprovalService.decide()` and `WorkflowManager.apply_decision()` build and return the same shape, so REST handlers, the frontend, and eval assertions consume one contract instead of reading ad-hoc fields off two differently-shaped objects:

```python
class ApprovalResult(BaseModel):
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    status: ApprovalStatus            # terminal status after this decision
    edited: bool                       # True when any revision (incl. this decision) supplied an edit
    final_payload: dict[str, object] | list[ProposedToolCall] | None
    reason: str | None
    approver: uuid.UUID
    decided_at: datetime.datetime
    approval_correlation_id: uuid.UUID
```

`ApprovalResult` is an internal/transport DTO, not a new persisted table — it is assembled from `AgentToolApproval`/`WorkflowNodeExecution` plus the latest `ApprovalRevision` at the moment a decision is recorded, then handed to the REST layer to shape the HTTP response and to `ApprovalAuditEntry` construction.

---

## Approval Lifecycle State Machine

```text
                    ┌─────────┐
        ┌──────────▶│ PENDING │◀────────────────┐
        │           └────┬────┘                  │ revise (agent only —
        │                │                       │ appends ApprovalRevision;
   created by             │                       │ status stays PENDING)
   pause / approval        │
   node entry              │
        │        ┌─────────┼─────────┬───────────────┐
        │        ▼         ▼         ▼               ▼
        │   ┌─────────┐┌──────────┐┌─────────┐ ┌───────────┐
        │   │APPROVED ││REJECTED  ││ EXPIRED │ │ CANCELLED │
        │   └────┬────┘└──────────┘└─────────┘ └───────────┘
        │        │      (terminal)  (terminal,   (terminal,
        │        │                  reserved —   reserved —
        │        │                  TODO(epic-10) TODO(epic-10)
        │        │                  enforcement)  enforcement)
        │        ▼
        │  ┌────────────────────────────────────┐
        │  │ Tool execution (ToolExecutor)        │
        │  │  success → turn/run continues        │
        │  │  failure → execution error recorded  │
        │  │  on ChatMessage/WorkflowRun — the     │
        │  │  approval itself stays APPROVED       │
        │  │  either way (see Locked Decisions)    │
        │  └────────────────────────────────────┘
```

`PENDING`, `APPROVED`, `REJECTED` are implemented in V2. `EXPIRED` and `CANCELLED` are reserved enum values with no transition path yet — documented now so the schema, REST contract, and frontend filter list do not need a breaking change when Epic 10 adds enforcement.

---

## Agent Tool-Call Approval — Pause & Resume Lifecycle

```text
AgentExecutor.run()
  │
  ├─ planner produces a tool-call step
  │
  ▼
ToolRunner (before dispatch)
  │
  ├─ HITL_ENABLED=false → dispatch unchanged (Epic 01 behaviour)
  │
  └─ HITL_ENABLED=true
        │
        ├─ ApprovalPolicy.requires_approval() false for every call in the step → dispatch unchanged
        │
        └─ true for any call in the step → PAUSE
              │
              ├─ Serialize Scratchpad.entries + AgentExecutionState
              ├─ Insert agent_tool_approvals row (status=pending, proposed_calls=step.tool_calls)
              ├─ Insert placeholder ChatMessage (status=waiting_approval, pending_approval_id=…)
              ├─ Publish AgentStreamEvent.approval_required(execution_id, approval_id, proposed_calls)
              └─ Stream ends (SSE `approval_required` frame, then connection closes — no `end` frame yet)

Caller decides — POST /api/approvals/{approval_id}/decide
  │
  ├─ decision=rejected
  │     → Compare-And-Swap (CAS): status pending→rejected; ChatMessage.status=rejected; return 200 JSON (no stream)
  │
  └─ decision=approved (optional edited_calls, reason)
        │
        ├─ Compare-And-Swap (CAS): status pending→approved
        ├─ Validate edited_calls (if present) against each tool's ToolValidator schema
        ├─ Rehydrate Scratchpad + AgentExecutionState from paused_state
        ├─ Execute approved (possibly edited) calls via ToolExecutor directly (gate already satisfied)
        ├─ Append tool results to rehydrated scratchpad
        └─ AgentExecutor.resume_from_approval() → re-enter ReAct loop at PLANNING
              │
              ├─ May finalize → update placeholder ChatMessage (status=complete, final content)
              ├─ May pause again on a later approval-required call → new agent_tool_approvals row
              └─ Streams SSE (`delta` / `tool_start` / `tool_end` / `approval_required` / `end` / `error`)
                 as the response body of the decide call itself
```

**Why the decide call streams:** the paused turn has no other open connection to resume onto — the original SSE request already closed when the pause frame was sent. Streaming the continuation from the decide endpoint keeps the response model consistent with `POST /api/chat/stream` (same SSE frame vocabulary) instead of inventing a second polling/websocket channel.

---

## Decision Execution Stages

Both surfaces' `decide()`/`apply_decision()` implementations are internally organized into four explicit stages, even though V2 executes all four synchronously within one request:

| Stage | What happens | V2 (synchronous) | Future (Epic 10 Background Jobs) |
| ----- | ------------ | ----------------- | ---------------------------------- |
| 1. Decision recorded | Compare-And-Swap (CAS) status transition (`pending→approved`/`rejected`), latest revision resolved into `ApprovalResult` | In-process, same DB transaction as the Compare-And-Swap (CAS) write | Unchanged — recording a decision is always synchronous and immediate |
| 2. Resume scheduled | The rehydrated scratchpad/state (agent) or `continue_from_approval()` call (workflow) is queued for execution | Immediately invoked in the same request/response cycle | Could be handed to a job queue instead of executing inline |
| 3. Tool execution | Approved (possibly edited) call(s) run through `ToolExecutor` | Runs inline, before the response is returned/streamed | Could run on a worker process; approval stays `approved` regardless of where execution happens |
| 4. Agent/workflow continuation | ReAct loop resumes (`resume_from_approval()`) or `WorkflowExecutor.continue_from_approval()` proceeds | Runs inline; result streamed (agent) or applied to the run (workflow) | Could be a separate resumed job; the approval record itself never needs to change |

Separating these stages **now** (as distinct internal method calls/log points rather than one monolithic function) costs nothing in V2 and means Epic 10 can move stages 2–4 onto a job queue without touching stage 1's decision-recording contract or the `ApprovalResult` shape.

---

## Symmetrical Approval Lifecycles

The agent pause/resume diagram above and Epic 06's existing workflow approval flow are structurally symmetrical — both consult the same `ApprovalPolicy`, pause before dispatch, and resume through the same four decision-execution stages. Shown side by side for clarity:

```text
Agent tool-call approval                    Workflow approval node (Epic 06, extended)
──────────────────────                      ────────────────────────────────────────────
ToolRunner (before dispatch)                ApprovalNodeExecutor (node reached in run)
  │                                            │
  ▼                                            ▼
ApprovalPolicy.requires_approval()          Node type is `approval` — always pauses
  │ true                                       │
  ▼                                            ▼
PAUSE: snapshot Scratchpad + State          PAUSE: run.status=WAITING_APPROVAL
  → agent_tool_approvals (pending)            → node_execution.status=pending
  → placeholder ChatMessage                    → run persisted (already durable)
  → SSE approval_required, stream closes       → run execution suspends
  │                                            │
  ▼                                            ▼
POST /api/approvals/{id}/decide             POST …/nodes/{id}/approve|reject
  │  {decision, edited_calls?, reason?}        │  {edited_arguments?, reason?}
  ▼                                            ▼
Stage 1: Compare-And-Swap (CAS) status → approved/rejected     Stage 1: Compare-And-Swap (CAS) status → approved/rejected
Stage 2: resume scheduled (inline)          Stage 2: resume scheduled (inline)
Stage 3: ToolExecutor.execute()             Stage 3: ToolExecutor.execute() (downstream
                                                       task/agent node, when reached)
Stage 4: AgentExecutor.resume_from_         Stage 4: WorkflowExecutor.continue_from_
         approval() → SSE continuation               approval() → run continues
```

The workflow side of this diagram documents **existing, unchanged** Epic 06 behaviour (plus the additive `edited_arguments`/`reason` fields); it is included here only so the two pause primitives can be reviewed for architectural consistency in one place, per the review recommendation.

---

## Execution Failure Semantics

An approval decision and the resulting tool/node execution are two distinct facts, and this epic keeps them that way:

- **Approval remains approved even if execution later fails.** If `ToolExecutor.execute()` raises or returns a failed `ToolResult` after an agent tool approval was approved, `AgentToolApproval.status` stays `approved` — the failure surfaces on the resumed turn (`ChatMessage.status=error`, matching Epic 01's existing tool-failure handling) or as normal `ToolResult.success=False` scratchpad content the ReAct loop reasons about, exactly as it would for a never-gated tool failure.
- **Same rule for workflows.** If the `task`/`agent` node reached after an `approval` node fails (provider, plugin, or MCP error, or infrastructure outage), the **node** and **run** fail per Epic 06's existing failure semantics (`NodeStatus.failed`, run follows its failure edge or ends) — the **preceding approval node's decision** is untouched and still reads `approved` in the audit trail.
- **Rationale.** A human approved a *request to attempt* the action; whether the attempt subsequently succeeded is an execution-layer concern the audit trail must be able to show separately (e.g. "approved at 10:02, execution failed at 10:02:03") rather than conflating the two into one ambiguous status.
- **No automatic retry or re-approval.** A failed execution after approval does not trigger a new approval request; the caller must retry the underlying action (new chat turn, or workflow run retry) which will pause for approval again if the retry reaches the same flagged tool call.

---

## Editable Tool Arguments Contract

Both surfaces validate edits **before** execution, never after:

| Surface                | Edit field                                    | Validated by                                                                                                                                                                                         | On invalid edit                                                                                                                                     |
| ---------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Agent tool approval    | `edited_calls: ProposedToolCall[] \| null`    | `ToolValidator.validate(tool, arguments)` per call (same validator `ToolExecutor` already uses)                                                                                                      | `422`; `agent_tool_approvals.status` remains `pending`; no scratchpad/state mutation                                                                |
| Workflow approval node | `edited_arguments: dict[str, object] \| null` | Not schema-validated at decision time (arguments are opaque until a downstream `task`/`agent` node consumes them via templating); that node's existing `ToolValidator` call is the enforcement point | Downstream node fails with `invalid_config`/`validation_error` — same failure surface as a bad `arguments_template`, run does not silently continue |

This asymmetry is intentional: an agent tool-call approval's edited arguments are about to execute _immediately_ (validate synchronously, fail the decision cleanly); a workflow approval node's edited arguments are _data_ consumed by a separate, later node — validating them against an unrelated tool schema at decision time would require the approval node to know which downstream tool will consume them, coupling two independently-authored graph nodes. The existing task/agent node validation path already catches bad values when they're actually used.

An agent tool-call approval's edits are also **schema-validated on the pre-decision revise endpoint**, not only at final decide time — an invalid `POST /api/approvals/{id}/revise` returns `422` without appending a revision row, so `approval_revisions` never contains a payload that failed `ToolValidator`.

---

## Workflow Graph Guard (Epic 06 Extension)

`GraphValidator` (extended, `app/ai/workflow/graph/validator.py`) adds one check, evaluated only when `HITL_ENABLED=true`:

- For every `task` node with `config.tool_name` and every `agent` node capable of invoking tools, resolve the referenced `ToolDefinition` via `ToolRegistry`.
- If `ApprovalPolicy.requires_approval(tool)` is true, walk backward from the node through the definition's edges; the node must be reachable **only** via at least one `approval` node on every path from the trigger — i.e. there is no path from the workflow's entry node(s) to this node that bypasses an `approval` node.
- Violation → workflow definition create/update fails with a `WorkflowValidationError` naming the offending node and tool (no silent auto-insertion of an approval node).
- `HITL_ENABLED=false` → this check is skipped entirely (Epic 06 validation behaviour unchanged).

This is a **structural** graph check (reachability), not a runtime interception — it costs nothing at execution time and fails fast at authoring time, consistent with the platform's "security by default" principle without touching `WorkflowExecutor`.

**Complexity:** the check is a single reverse-reachability traversal (BFS/DFS from each approval-required node back to the trigger, tracking whether every discovered path crosses an `approval` node) over the workflow definition's node/edge graph — `O(V + E)` where `V`/`E` are the definition's node and edge counts, run once per approval-required node at create/update time. Workflow definitions are small (dozens of nodes, not thousands), so this is not a performance concern even run for every flagged node in the graph.

---

## Unified Audit Trail

`ApprovalAuditEntry` (`app/ai/hitl/models.py`) — the read-model shared by `GET /api/approvals`:

```python
class ApprovalAuditEntry(BaseModel):
    id: uuid.UUID                     # agent_tool_approvals.id OR workflow node_execution.id
    kind: ApprovalKind                 # "agent_tool" | "workflow_node"
    approval_correlation_id: uuid.UUID
    status: str                        # AgentToolApproval.status | NodeStatus (approval nodes only)
    tool_calls: list[ProposedToolCall] | None   # agent_tool only; null for workflow_node
    workflow_run_id: uuid.UUID | None           # workflow_node only
    workflow_node_id: str | None                # workflow_node only
    session_id: uuid.UUID | None                # agent_tool only
    requested_at: datetime.datetime
    decided_at: datetime.datetime | None
    decided_by: uuid.UUID | None
    decision: str | None               # "approved" | "rejected"
    reason: str | None
    edited: bool                       # True when the latest revision's payload is non-null
    revision_count: int                 # len(approval_revisions) for this approval — 0 when never edited
    decide_url: str                    # kind-specific action endpoint for the frontend to call
```

`ApprovalsStore` (read façade, `app/ai/hitl/store.py`) queries `agent_tool_approvals` directly and delegates workflow-side rows to the existing `WorkflowStore` (`get_run_with_executions` filtered to `node_type=approval`), merging both into `ApprovalAuditEntry` sorted by `requested_at desc`. `revision_count` is a cheap `COUNT(*)` against `approval_revisions` per entry (or a batched query across the page of results); the full revision list is fetched separately via `GET /api/approvals/{id}/revisions` only when the caller opens the history view. No new denormalized table — see Locked Decisions.

---

## Security Model

HITL adds a decision gate; it does not change the trust model of the underlying tool/workflow/plugin platforms.

| Control                | v1 behaviour                                                                                                                                                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Who may decide         | Session/run owner only (`decided_by` forced to `owner_id`); no delegation                                                                                                                              |
| Who may view           | `GET /api/approvals` list is caller-scoped (`owner_id = current caller`); detail endpoint 404s for non-owned ids                                                                                       |
| Edited arguments       | Schema-validated (agent) or downstream-validated (workflow) before execution — never `eval`/`exec`, never bypass `ToolValidator`                                                                       |
| Rejected calls         | Never execute; no partial side effects                                                                                                                                                                 |
| Scratchpad persistence | Only the paused snapshot is persisted (HITL-scoped exception); cleared per § Snapshot Cleanup Strategy once the approval reaches a terminal status and the resumed turn completes              |
| Secrets in audit       | `reason`, `proposed_calls`, `edited_calls` may contain user-authored text/arguments — **never** provider credentials or MCP server secrets, which never flow through tool _arguments_ in this platform |
| Flag off               | No policy consulted, no gate, no new tables read; byte-for-byte Epic 06/08 behaviour                                                                                                                   |

Operators who flag a tool `requires_approval` are asserting it is sensitive; this epic enforces that no code path can silently skip the resulting pause.

---

## Snapshot Cleanup Strategy

The `paused_scratchpad`/`paused_state` columns on `agent_tool_approvals` hold a full copy of the turn's in-flight scratchpad — the scoped exception to Epic 01's "never persisted" invariant. Their lifecycle is intentionally simple and synchronous in V2:

- **When cleared:** immediately after the resumed turn reaches a terminal outcome — `AgentExecutor.resume_from_approval()` finalizes (`ChatMessage.status=complete`) or itself pauses again on a subsequent approval-required call. On reject, the snapshot is cleared immediately (no resume ever happens).
- **How cleared:** the two `jsonb` columns are set to `null` in the same transaction that finalizes the resumed turn (or records the rejection) — the `agent_tool_approvals` row itself is never deleted, preserving the audit trail; only the (potentially large, and no-longer-needed) snapshot payload is nulled out.
- **Retention duration:** none by design — a snapshot is working state for exactly one pause/resume round trip, not a durable artifact. There is no retention window to configure.
- **Retry behaviour:** if a decide call fails mid-execution (e.g. the process crashes after Stage 1 but before Stage 4 completes — see § Decision Execution Stages), the snapshot is **not** cleared, so the approval remains `approved` with its snapshot intact; a `TODO(epic-10):` background sweep is required to detect and safely resume (or fail) such orphaned approved-but-not-resumed rows. V2 does not implement this sweep — a crash in that narrow window leaves a resumable-but-stuck row, which is an accepted V2 limitation (single-process, no HA, consistent with Epic 06's existing single-worker workflow posture).
- **Cleanup responsibility:** owned entirely by `AgentApprovalService` — no separate cleanup job, cron, or TTL index in V2. This keeps cleanup transactionally coupled to the same operation that made the snapshot unnecessary, at the cost of the orphaned-row edge case above (deferred, not silently ignored).

---

## High-Level Flow

**Agent tool-call approval (chat path)**

`POST /api/chat/stream` → `AgentExecutor.run()` → `ToolRunner` (gate) → pause → `agent_tool_approvals` row + placeholder `ChatMessage` → SSE `approval_required` → stream closes

**Agent tool-call decision (resume)**

`POST /api/approvals/{id}/decide` → `AgentApprovalService.decide()` → (validate edits) → `ToolExecutor.execute()` per approved call → `AgentExecutor.resume_from_approval()` → SSE continuation (`delta`/`tool_start`/`tool_end`/`end`) → placeholder `ChatMessage` updated to `complete`

**Workflow approval decision (unchanged trigger, extended payload)**

`POST /api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve` (body: `{edited_arguments?, reason?}`) → `WorkflowManager.apply_decision()` → `build_approval_decision_output()` (now includes `edited_arguments`) → `WorkflowExecutor.continue_from_approval()` (unchanged)

**Audit / inbox (read-only)**

`GET /api/approvals?status=pending` → `ApprovalsStore` aggregates `agent_tool_approvals` + workflow approval node executions (owner-scoped) → `ApprovalAuditEntry[]`

---

## End-to-End Sequence

```text
Operator sets HITL_ENABLED=true and flags "send_email" as requires_approval
  │
  ▼
User chat turn triggers the agent to plan a send_email call
  │
  ▼
ToolRunner detects ApprovalPolicy.requires_approval("send_email") == true
  │
  ▼
Scratchpad + AgentExecutionState snapshotted → agent_tool_approvals row (pending)
Placeholder ChatMessage (status=waiting_approval) persisted
SSE stream emits `approval_required` { approval_id, proposed_calls } and closes
  │
  ▼
Frontend approval inbox shows the pending request with editable JSON arguments
  │
  ▼
User edits the "to" argument and approves with a reason
  │
  ▼
POST /api/approvals/{id}/decide { decision: approved, edited_calls: […], reason: "…" }
  │
  ├─ ToolValidator validates edited arguments against send_email's schema
  ├─ ToolExecutor executes send_email with the edited arguments
  ├─ Scratchpad rehydrated + tool result appended
  └─ AgentExecutor.resume_from_approval() continues planning → finalizes
  │
  ▼
SSE continuation streams the assistant's final reply; placeholder ChatMessage updated to complete
  │
  ▼
GET /api/approvals/{id} now shows status=approved, edited=true, reason, decided_at, decided_by
```

---

## Storage Architecture

```text
New table: agent_tool_approvals (Postgres)
        │
New table: approval_revisions (Postgres)
        │
Extended: chat_messages (status values + pending_approval_id FK)
        │
Extended: workflow_node_executions (edited_arguments, reason columns)
        │
ApprovalsStore (read aggregation — no new denormalized audit table)
        │
GET /api/approvals → ApprovalAuditEntry[]
```

No new vector/queue infrastructure. All new persistence is relational, following the existing `alembic/versions/NNNN_*.py` migration convention.

### Migration Impact Summary

| Aspect | Detail |
| ------ | ------ |
| New tables | `agent_tool_approvals`, `approval_revisions` — both created in `0010_hitl_tables.py` |
| Modified tables | `chat_messages` — `status` CHECK constraint gains `waiting_approval`, `rejected`; new nullable `pending_approval_id` FK. `workflow_node_executions` — new nullable `edited_arguments` (jsonb), `reason` (text) columns |
| Backward compatibility | All modifications are additive (new nullable columns, new CHECK values, new tables); existing rows are valid under the new constraints with no backfill required; no column is renamed, retyped, or dropped |
| Rollout considerations | Migration is applied before code deploy (standard practice for this codebase); `HITL_ENABLED=false` means the new columns/tables are simply unused post-migration — no behavioural change until the flag flips; downgrade drops both new tables and reverts the two extended columns/constraints, safe as long as no HITL data has been written (documented operator caveat, same posture as Epic 06/08 migrations) |
| Data volume | `approval_revisions` grows with edit activity, not decision volume — bounded by caller behavior (typically 0–1 rows per approval); no unbounded growth pattern expected at V2 scale |

---

## Package Structure

```text
app/
└── ai/
    └── hitl/
        ├── __init__.py
        ├── models.py            # ApprovalKind, ApprovalStatus, ProposedToolCall,
        │                        #   AgentToolApproval, ApprovalResult, ApprovalRevision,
        │                        #   ApprovalAuditEntry
        ├── policy.py            # ApprovalPolicy
        ├── store.py             # AgentToolApprovalStore (CRUD + Compare-And-Swap (CAS) decide + revisions) + ApprovalsStore (read aggregation)
        ├── service.py           # AgentApprovalService — pause() / revise() / decide() / resume orchestration
        └── exceptions.py        # HitlError, ApprovalNotFoundError, ApprovalDecisionConflictError, ApprovalValidationError

app/routers/approvals.py         # NEW — GET /api/approvals, GET /api/approvals/{id}, GET /api/approvals/{id}/revisions,
                                  #        POST /api/approvals/{id}/revise, POST /api/approvals/{id}/decide
app/schemas/approvals.py         # NEW — request/response schemas
app/schemas/workflow.py          # extend — ApprovalDecisionRequest { edited_arguments?, reason? }
app/routers/workflows.py         # modify — approve/reject accept optional JSON body
app/core/config.py               # extend — HITL_ENABLED, hitl_required_tool_names, hitl_approval_timeout_hours
app/main.py                      # modify — mount approvals_router
app/ai/tools/schemas.py          # extend — ToolDefinition.requires_approval: bool = False;
                                  #          ToolExecutionContext.approval_correlation_id: str | None
app/ai/agent/executor/tool_runner.py     # modify — consult ApprovalPolicy before dispatch; delegate pause to AgentApprovalService
app/ai/agent/executor/agent_executor.py  # modify — resume_from_approval()
app/ai/agent/models/state.py             # extend — AgentExecutionStatus.WAITING_APPROVAL (non-terminal)
app/ai/agent/models/events.py            # extend — APPROVAL_REQUIRED event type + payload
app/ai/agent/scratchpad/scratchpad.py    # extend — to_snapshot()/from_snapshot() (HITL-scoped serialization)
app/schemas/chat.py               # extend — `approval_required` SSE frame
app/services/chat_service.py      # modify — persist waiting_approval placeholder; decide-and-resume streaming path
app/ai/workflow/manager.py        # modify — apply_decision(..., edited_arguments=None, reason=None)
app/ai/workflow/nodes/approval_node.py    # modify — build_approval_decision_output(edited_arguments, reason)
app/ai/workflow/models/run.py             # modify — WorkflowNodeExecution.edited_arguments, .reason
app/ai/workflow/graph/validator.py        # extend — approval-reachability guard (flag-gated)
app/ai/observability/metrics/instruments.py  # extend — agent_tool_approval_pending_count, approval_decisions_total,
                                              #          hitl_approval_decision_latency_ms, hitl_resume_latency_ms,
                                              #          hitl_tool_execution_latency_ms
app/ai/observability/tracing/spans.py        # extend — approval_span (approval_id, approval_kind, approval_status, approval_decision)

backend-python/alembic/versions/0010_hitl_tables.py   # NEW migration — agent_tool_approvals, approval_revisions,
                                                       #   chat_messages/workflow_node_executions extensions

tests/ai/hitl/                    # unit tests for policy/store/service (incl. revisions)
tests/ai/agent/test_tool_approval.py   # pause/resume integration tests
tests/ai/workflow/test_approval_node.py  # extended with edited_arguments/reason cases
tests/test_approvals_router.py    # NEW
```

---

## Core Components

- `ApprovalPolicy`
- `AgentToolApproval` / `AgentToolApprovalStore`
- `ApprovalRevision` (append-only revision history, shared by both surfaces)
- `ApprovalResult` (canonical decision-result DTO)
- `AgentApprovalService`
- `ApprovalsStore` (unified read aggregation)
- `ApprovalAuditEntry`
- `GraphValidator` approval-reachability extension
- `HITL_ENABLED`

---

## Component Responsibilities

| Component                                   | Responsibility                                                                                   | Inputs                                                 | Outputs                                                 | Dependencies                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------ | ------------------------------------------------------- | --------------------------------------------------------- |
| `ApprovalPolicy`                            | Decide whether a tool call requires human approval                                               | `ToolDefinition`, settings                             | `bool`                                                  | —                                                         |
| `ToolRunner` (extended)                     | Gate dispatch of a planned tool-call step; delegate pause                                        | `PlannedStep`, `ApprovalPolicy`                        | Dispatch, or a pause via `AgentApprovalService`         | `ApprovalPolicy`, `AgentApprovalService`                  |
| `AgentApprovalService`                      | Orchestrate pause (snapshot + persist), revise (append revision), and decide (validate, execute, resume) — builds an `ApprovalResult` on decide | Scratchpad, `AgentExecutionState`, decision payload    | `AgentToolApproval`, `ApprovalResult`, resumed `AgentResponse`/SSE stream | `AgentToolApprovalStore`, `ToolExecutor`, `AgentExecutor` |
| `AgentToolApprovalStore`                    | Postgres CRUD + Compare-And-Swap (CAS) decision recording for `agent_tool_approvals`, plus append-only writes to `approval_revisions` | SQL session                                            | `AgentToolApproval` rows, `ApprovalRevision` rows        | PostgreSQL                                                |
| `ApprovalsStore`                            | Read-only aggregation across agent-tool and workflow-node approvals, incl. revision counts/lists | Both stores                                            | `ApprovalAuditEntry[]`, `ApprovalRevision[]`             | `AgentToolApprovalStore`, `WorkflowStore`                 |
| `GraphValidator` (extended)                 | Reject workflow definitions reaching an approval-required tool without a preceding approval node | `WorkflowDefinition`, `ToolRegistry`, `ApprovalPolicy` | Validation errors                                       | `ToolRegistry`                                            |
| `WorkflowManager.apply_decision` (extended) | Accept optional `edited_arguments`/`reason`; append one `ApprovalRevision` when edited; build and return an `ApprovalResult`; unchanged Compare-And-Swap (CAS)/continuation semantics | Decision payload                                       | `WorkflowRun`, `ApprovalResult`                          | Epic 06 `WorkflowExecutor`                                |

---

## Existing V1/V2 Assets (reuse, do not duplicate)

| Asset                                                                            | Location                                                               | Epic 09 role                                                                     |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, Compare-And-Swap (CAS) decision pattern   | `app/ai/workflow/nodes/approval_node.py`, `app/ai/workflow/manager.py` | Workflow approval pause/resume (extended, not replaced)                          |
| `WorkflowExecutor.continue_from_approval`, `schedule_run_task` background resume | `app/ai/workflow/engine/`                                              | Pattern reused conceptually for agent resume; workflow path itself unchanged     |
| `ToolExecutor`, `ToolValidator`, `ToolRegistry`, `ToolDefinition`                | `app/ai/tools/`                                                        | Execution + schema validation for both approval surfaces                         |
| `AgentExecutor`, `ToolRunner`, `Scratchpad`, `AgentExecutionState`               | `app/ai/agent/`                                                        | Agent tool-call gate + pause/resume host                                         |
| `AgentStreamEvent`, `StreamPublisher`, SSE frame mapping                         | `app/ai/agent/models/events.py`, `app/schemas/chat.py`                 | New `approval_required` event/frame                                              |
| `GraphValidator`                                                                 | `app/ai/workflow/graph/validator.py`                                   | Approval-reachability extension                                                  |
| `record_workflow_approval_pending_delta`, `workflow_approval_pending_count`      | `app/ai/observability/metrics/`                                        | Pattern reused for sibling agent-tool metric                                     |
| `PluginRegistrar.register_tool`, plugin `ToolDefinition` contributions           | `app/ai/plugins/` (Epic 08)                                            | Plugin tools inherit `requires_approval` transparently — no plugin-specific code |
| `McpToolExecutionAdapter`                                                        | `app/ai/mcp/` (Epic 03)                                                | MCP tools inherit `requires_approval` transparently — no MCP-specific code       |
| Feature flag infrastructure                                                      | `app/core/config.py`                                                   | `HITL_ENABLED`                                                                   |
| DI factories                                                                     | `app/ai/deps.py`                                                       | Wire `ApprovalPolicy`, `AgentApprovalService`, `ApprovalsStore`                  |
| `get_current_caller`                                                             | `app/core/caller.py`                                                   | Authenticated Approval REST API                                                  |

When `HITL_ENABLED=false`, none of the above behaviours change.

---

## Platform Integration Strategy

Unlike Plugins (new registration boundary) or Observability (cross-cutting spans), HITL **inserts one decision point** into two already-existing execution paths:

- **Agent/chat tool calls** — `ToolRunner` gains a pre-dispatch check; when negative (the common case, and always the case when `HITL_ENABLED=false`), behaviour is byte-for-byte identical to Epic 01.
- **Workflow tool calls** — no runtime change; `GraphValidator` gains a structural check evaluated once, at definition create/update time, not per-execution.
- **MCP and plugin tools** — zero code changes; both are `ToolDefinition`/handler pairs already flowing through `ToolRegistry` → `ToolExecutor`/`ToolRunner`, so `requires_approval` applies uniformly the moment either sets the flag.

**Flag off:** No policy consulted; `ToolRunner` dispatches immediately (Epic 01 behaviour); `GraphValidator` skips the reachability check (Epic 06 behaviour); Approval REST routes return `503 feature_disabled`; no new tables read; workflow approve/reject endpoints ignore `edited_arguments`/`reason` if supplied (accepted as optional fields regardless of flag state, but only persisted/applied when `HITL_ENABLED=true`).

**Flag on:** Flagged tools pause instead of executing on both surfaces; inbox/audit API reflects live pending state and history.

---

## HITL REST API

Authenticated-only (`Depends(get_current_caller)`). Router: `app/routers/approvals.py`. Mounted in `app/main.py`; returns `503 feature_disabled` when `HITL_ENABLED=false`.

| Method | Path                         | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------ | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/approvals`             | List approvals owned by the caller across both kinds. Query params: `status` (`pending`\|`approved`\|`rejected`\|`expired`\|`cancelled`), `kind` (`agent_tool`\|`workflow_node`), pagination (`limit`/`offset`). Returns `ApprovalAuditEntry[]`                                                                                                                                                                                                                    |
| `GET`  | `/api/approvals/{id}`        | Detail for one approval (either kind); `404` if not owned/found                                                                                                                                                                                                                                                                                                                                                                                       |
| `GET`  | `/api/approvals/{id}/revisions` | Full `ApprovalRevision[]` history for one approval (either kind), ordered by `revision_number`; `404` if not owned/found                                                                                                                                                                                                                                                                                                                          |
| `POST` | `/api/approvals/{id}/revise` | **Agent-tool approvals only.** Body: `{edited_calls: ProposedToolCall[], note?: string}`. Appends an `ApprovalRevision`, updates `edited_calls` on the approval, and returns the updated entry as `200 application/json`. `422` on schema-invalid edits (no revision appended); `409` if the approval is no longer `pending`                                                                                                                       |
| `POST` | `/api/approvals/{id}/decide` | **Agent-tool approvals only.** Body: `{decision: "approved"\|"rejected", edited_calls?: ProposedToolCall[], reason?: string}`. `approved` → `200 text/event-stream` continuation; `rejected` → `200 application/json` `ApprovalResult`. `409` on stale/duplicate decision. Workflow-node approvals continue to use the existing `/api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve\|reject` endpoints (now accepting the same optional body) |

**Workflow endpoints (extended, additive body):**

| Method | Path                                                            | Change                                                                                                  |
| ------ | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `POST` | `/api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve` | Body now optional `{edited_arguments?: dict, reason?: string}`; omitted body behaves exactly as Epic 06 |
| `POST` | `/api/workflow-runs/{run_id}/nodes/{node_execution_id}/reject`  | Body now optional `{reason?: string}`; omitted body behaves exactly as Epic 06                          |

**Health:** extend `GET /api/health` with `hitl_enabled: bool` and `hitl_pending_approvals_count: int` (sum of pending across both kinds; `0` when flag off).

**Response rules:** never include provider credentials, MCP server secrets, plugin `metadata` bags, filesystem paths, or scratchpad `provider_message` raw payloads beyond what is needed to display the proposed/edited tool call and reason. `reason` and tool arguments are user-authored text/data and are returned verbatim (they are not secrets by construction, but callers should be advised not to place secrets in tool arguments — unchanged from existing tool-execution guidance).

---

## Public APIs (stable after Phase 1)

| API                                                                                              | Kind                               |
| ------------------------------------------------------------------------------------------------ | ---------------------------------- |
| `HITL_ENABLED`                                                                                   | Constant/setting                   |
| `ApprovalKind`, `ApprovalStatus`                                                                 | Enum                               |
| `ProposedToolCall`, `AgentToolApproval`, `ApprovalResult`, `ApprovalRevision`, `ApprovalAuditEntry` | Model                            |
| `ApprovalPolicy`                                                                                 | Class                              |
| `AgentToolApprovalStore`, `ApprovalsStore`                                                       | Class                              |
| `AgentApprovalService`                                                                           | Class                              |
| `HitlError`, `ApprovalNotFoundError`, `ApprovalDecisionConflictError`, `ApprovalValidationError` | Exception                          |
| `ToolDefinition.requires_approval`                                                               | Model field                        |
| `WorkflowManager.apply_decision(..., edited_arguments=None, reason=None)`                        | Method signature (additive kwargs) |
| Approvals REST router export                                                                     | FastAPI router                     |

Internal (may evolve): scratchpad snapshot serialization shape, `ApprovalsStore` aggregation query internals, test fixture helpers.

---

## Configuration defaults

| Setting                       | Default                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| `HITL_ENABLED`                | **`false`**                                                                                |
| `hitl_required_tool_names`    | `frozenset()` (empty — only tools with `ToolDefinition.requires_approval=True` are gated)  |
| `hitl_approval_timeout_hours` | `0` (no timeout — enforcement deferred, same posture as `workflow_approval_timeout_hours`) |
| `hitl_max_reason_length`      | `2000` (characters; server-side truncation guard, not user-facing validation UX)           |

Existing flags (`WORKFLOW_ENGINE_ENABLED`, `PLUGINS_ENABLED`, `MCP_ENABLED`, `OBSERVABILITY_ENABLED`, `agent_runtime_enabled`, …) unchanged.

---

## Dependencies

| Requires                                                                                  | Provides to downstream                                      |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Epic 08 Plugin Architecture (stable platform; plugin tools registered via `ToolRegistry`) | `requires_approval` coverage for plugin tools               |
| Epic 07 Observability (span/metric helpers)                                               | `approval_span`, HITL metrics                               |
| Epic 06 Workflow Engine (`ApprovalNodeExecutor`, `apply_decision`, `GraphValidator`)      | Editable arguments + reason + reachability guard extensions |
| Epic 03 MCP Integration (tools registered via `ToolRegistry`)                             | `requires_approval` coverage for MCP tools                  |
| Epic 01 Agent Framework (`AgentExecutor`, `ToolRunner`, `Scratchpad`)                     | Agent tool-call approval gate and resume                    |
| Epic 01 Tool platform (`ToolExecutor`, `ToolValidator`)                                   | Edited-argument validation and execution                    |

**Future consumers:** Epic 10 Background Jobs (approval timeout/cancellation enforcement, orphaned-snapshot sweep, moving Decision Execution Stages 2–4 off the request/response cycle); Epic 11 Security & Governance (RBAC-scoped approval delegation, team queues, rate limits on approval requests, exporting `approval_correlation_id`-linked audit data to external SIEM).

---

## Future Enhancements (Out of V2 Scope)

Documented extension points reserved by this epic's model and API design — **not implemented in V2**:

| Enhancement                      | Motivation                                                                                      | V2 foundation                                                                                                      |
| -------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Timeout auto-expiry**          | Prevent indefinitely pending approvals from blocking sessions/runs forever                      | `hitl_approval_timeout_hours` field present; `status=expired` reserved in `ApprovalStatus`                         |
| **Cancellation on resource deletion** | Avoid leaving a `pending` approval for a session/run/plugin that no longer exists            | `status=cancelled` reserved in `ApprovalStatus`; no sweep implemented (see Locked Decisions)                        |
| **Implicit workflow node pause** | Avoid requiring workflow authors to manually place `approval` nodes before every sensitive tool | `GraphValidator` reachability check documents the requirement explicitly; no runtime coupling to retrofit          |
| **Team/shared approval queues**  | Multiple operators triaging the same pending queue                                              | Caller-scoped `GET /api/approvals` query shape is additive-extensible with a future `visible_to` filter            |
| **Structured rejection reasons** | Machine-readable rejection categories for analytics                                             | `reason` is free text in V2; a future `reason_code` enum can be added additively                                   |
| **Durable unified audit table**  | Faster cross-surface audit queries at scale                                                     | `ApprovalAuditEntry` DTO shape is stable; a materialized table can be introduced without changing the API contract |
| **Richer inbox filtering/search** | Filter by edited-state or origin (workflow/agent/plugin/MCP); search by tool name, workflow, session, user, or reason | `GET /api/approvals` query params (`status`, `kind`, pagination) are additive-extensible; `ApprovalAuditEntry` already carries the fields a search index would key on |
| **Derived operational rate metrics** | Approval success/rejection/edit rate, average edits per approval, queue size trends           | Computable today from `approval_decisions_total{kind,decision}` and `approval_revisions` row counts without new instruments — a dashboard/analytics concern, not a new metric |
| **RBAC / org / environment-conditional policy engine** | Approval requirements that vary by role, org, or deploy environment                     | `ApprovalPolicy.requires_approval()` is the single, swappable decision point (see § Approval Policy & Configuration) |
| **Revision diff/merge visualization** | Show what changed between consecutive `ApprovalRevision` rows                              | `approval_revisions.edited_payload` is a full snapshot per revision, sufficient to diff client-side without new storage |

These items require explicit Part I updates and should remain `TODO(future):` during V2 implementation.

---

## Glossary

| Term | Definition |
| ---- | ---------- |
| `ApprovalKind` | Enum distinguishing which surface produced an approval: `agent_tool` (chat/agent pause) or `workflow_node` (Epic 06 approval node) |
| `ApprovalStatus` | Enum of an approval's lifecycle state: `pending`, `approved`, `rejected`, `expired` (reserved), `cancelled` (reserved) — see § Approval Lifecycle State Machine |
| `ApprovalPolicy` | The single stateless decision function (`requires_approval(tool)`) consulted by both `ToolRunner` and `GraphValidator` to determine whether a tool call needs a human decision |
| `AgentToolApproval` | The persisted record of one paused chat/agent tool-call step — proposed calls, the pause-time scratchpad/state snapshot, and the eventual decision |
| `ApprovalRevision` | One immutable, append-only edit submitted against a pending approval (agent revise call or a workflow decision's `edited_arguments`) — preserves full edit history, not just the final value |
| `ApprovalResult` | The canonical DTO both `AgentApprovalService.decide()` and `WorkflowManager.apply_decision()` return — status, edited payload, reason, approver, decided_at, correlation id |
| `ApprovalAuditEntry` | The unified, read-only row shape `GET /api/approvals` returns, aggregating both `AgentToolApproval` and workflow approval `node_execution` rows |
| `approval_correlation_id` | A UUID generated at pause/approval-node creation that stays stable across the approval record, its eventual tool execution, trace spans, audit entries, and eval run output — independent of `execution_id`/`run_id` |
| Compare-And-Swap (CAS) | An atomic database update that succeeds only when a row is still in an expected state (here: `status='pending'`), preventing duplicate or conflicting decisions — implemented as `UPDATE … WHERE id=… AND status='pending'`; conflicting updates return `409` |

---

## Implementation Risks

Risks specific to *how* this epic must be built (see § Risks in Part II for delivery/mitigation tracking):

- **Pause/resume serialization fidelity** — `Scratchpad`/`AgentExecutionState` must round-trip through `model_dump(mode="json")`/reconstruction without losing information the ReAct loop needs to resume coherently; any field added to either model in a future epic must be verified JSON-serializable or the HITL snapshot silently degrades.
- **Streaming continuation semantics** — the decide endpoint must behave like `POST /api/chat/stream` (same SSE frame vocabulary, same error handling) despite being a different route; drift between the two SSE implementations would produce inconsistent client behavior depending on which endpoint produced the stream.
- **Concurrency on shared state** — the Compare-And-Swap (CAS) pattern (`WHERE status='pending'`) prevents double-decision races, but the pre-decision revise endpoint introduces a second concurrent writer path (revise vs. decide racing on the same row); Phase 3 must ensure a decide-in-flight cannot be silently overwritten by a concurrent revise (and vice versa) — resolved via the same `WHERE status='pending'` guard on both operations.
- **Snapshot cleanup edge cases** — a process crash between Stage 1 (decision recorded) and Stage 4 (continuation complete) leaves a resumable-but-stuck `approved` row with its snapshot intact (see § Snapshot Cleanup Strategy); this is an accepted, documented V2 limitation, not a silent data-loss risk, but implementers must not "helpfully" clear the snapshot earlier to work around it.
- **Approval recovery after partial failure** — if Stage 3 (tool execution) succeeds but Stage 4 (continuation/streaming) fails before the client receives the result, the tool has already run — the implementation must not re-execute on client retry; the `agent_tool_approvals` row's terminal `approved` status (not a request-level idempotency key) is the signal that execution already happened.

---

---

## Design acceptance

- Flag off: zero policy checks, zero new tables read, Approval REST returns `503`; `GraphValidator` reachability check skipped; workflow approve/reject endpoints behave byte-for-byte as Epic 06; all other platform paths unchanged
- Flag on: a tool flagged `requires_approval` pauses a chat/agent turn instead of executing; the paused turn resumes correctly (including with edited arguments) via the decide endpoint and produces a coherent final assistant reply
- A workflow definition referencing an approval-required tool from a `task`/`agent` node with no preceding `approval` node fails validation at create/update time
- Workflow approval nodes accept `edited_arguments`/`reason`, persist them, and expose the edited value to a downstream node via existing `{{variables.…}}` templating — Epic 06 behaviour unchanged when these fields are omitted
- `GET /api/approvals` returns a merged, owner-scoped view of pending and historical approvals across both kinds, each with a caller-actionable `decide_url`
- Rejected agent tool calls never execute; rejected workflow approval nodes behave exactly as Epic 06
- MCP-backed and plugin-backed tools are gated identically to native tools with no MCP- or plugin-specific code paths
- Every edit submitted against a pending approval (agent revise, or either surface's final decision) is preserved as an immutable `ApprovalRevision`; the audit UI can show only the latest while the full history remains queryable
- An approval's terminal decision status never changes once recorded, even if the subsequently executed tool call or workflow node fails
- Coverage ≥80% on `app/` and `app/ai/hitl/`

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Single policy, dual mechanism** — `ApprovalPolicy` is the only place "does this tool need approval" is decided; the _pause_ mechanism differs by surface (agent snapshot vs. workflow graph guard) but both consult the same policy.
- **No implicit workflow pause** — a `task`/`agent` node never pauses a workflow run on its own; sensitive tools inside workflows are only reachable through an explicit `approval` node, enforced by `GraphValidator`.
- **Execution path reuse** — approved tool calls (edited or not) always execute via `ToolExecutor`; never a parallel execution path.
- **No silent bypass** — a tool flagged `requires_approval` cannot execute without a recorded decision, regardless of invocation surface (chat, workflow, MCP, plugin).
- **Fail-closed on invalid edits** — an edited agent tool-call argument that fails schema validation never executes; the pause remains `pending` for a corrected retry.
- **Scratchpad persistence exception is scoped** — only HITL pause snapshots persist scratchpad content; this does not become a general session-replay or cross-session memory feature (that boundary belongs to Epic 05 Memory).
- **Flag-off parity** — `HITL_ENABLED=false` preserves Epic 01/03/06/08 behaviour on every hot path.
- **No content leakage in telemetry** — span/metric attributes carry ids, kind, status, latency, counts — never tool arguments, reasons, or scratchpad content.
- **Decision status is immutable once terminal** — `approved`/`rejected` never changes after being recorded; execution outcome (success or failure) is tracked separately and never retroactively alters the decision (see § Execution Failure Semantics).
- **Revisions are append-only** — `approval_revisions` rows are never updated or deleted; a correction is a new revision, never an edit to an existing one.
- **Public APIs stable after Phase 1** — `ApprovalPolicy`, `AgentToolApproval` schema, `ApprovalResult` shape, and `apply_decision()` signature require user approval to change.
- **No Epic 10+ behaviour early** — timeout enforcement, cancellation sweeps, RBAC delegation, shared queues, rate limits — `TODO(epic-N):` only.

---

## Acceptance Criteria

- Operators can flag any tool (native, MCP, or plugin) as requiring human approval via code or config, without touching the tool's own implementation.
- A flagged tool invoked from a chat/agent turn pauses that turn, surfaces the proposed call for review, and resumes correctly on an approve/reject/edit decision.
- A flagged tool referenced from a workflow graph without a preceding approval node is rejected at definition time; existing workflow approval nodes gain editable arguments and a decision reason.
- Authenticated users can inspect a unified pending/approved/rejected history across both surfaces via REST, including per-approval revision history.
- Reference eval scenarios demonstrate approve, approve-with-edits, and reject end-to-end on both surfaces.
- When HITL is disabled, the platform behaves identically to Epic 08.
- Approval-scoped tracing attributes (`approval_id`, `approval_kind`, `approval_status`, `approval_decision`) and correlation ids are present on every decision span and propagate to the resulting tool execution span.

---

# Part II — Execution

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement Part II phase-by-phase. Part I is frozen and is the architectural source of truth. Do not redesign architecture during implementation.

## Phase integration rules

Early phases build **HITL primitives in isolation** (unit tests with fixture tools/approvals). Each surface (agent, workflow) integrates in its own phase. REST API, observability, eval, and frontend follow once both surfaces work.

| Phase | Builds                                               | Wiring                                    |
| ----- | ---------------------------------------------------- | ----------------------------------------- |
| 1     | HITL foundations (policy, models, migration, flag)   | None                                      |
| 2     | Agent tool-call approval gate (pause)                | `ToolRunner`                              |
| 3     | Agent approval decision & resume                     | `AgentApprovalService`, `AgentExecutor`   |
| 4     | Workflow approval enhancements (edited args, reason) | `WorkflowManager`, `ApprovalNodeExecutor` |
| 5     | Workflow graph guard + MCP/plugin coverage           | `GraphValidator`                          |
| 6     | Unified Approval REST API & audit aggregation        | REST only                                 |
| 7     | HITL observability                                   | Internal                                  |
| 8     | Reference scenarios + eval cases                     | CLI                                       |
| 9     | Frontend approval inbox & audit UI                   | Frontend                                  |
| 10    | Validation & release                                 | —                                         |

## Reuse Existing Components

**DO NOT REIMPLEMENT**

| Component                                                                      | Location                                               |
| ------------------------------------------------------------------------------ | ------------------------------------------------------ |
| `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, Compare-And-Swap (CAS) decision helpers | `app/ai/workflow/`                                     |
| `WorkflowExecutor.continue_from_approval`, `schedule_run_task`                 | `app/ai/workflow/engine/`                              |
| `ToolExecutor`, `ToolValidator`, `ToolRegistry`, `ToolAuthorizer`              | `app/ai/tools/`                                        |
| `AgentExecutor`, `ToolRunner`, `Scratchpad`, `AgentStateManager`               | `app/ai/agent/`                                        |
| `AgentStreamEvent`, `StreamPublisher`, SSE frame formatting                    | `app/ai/agent/models/events.py`, `app/schemas/chat.py` |
| `GraphValidator`                                                               | `app/ai/workflow/graph/validator.py`                   |
| `record_workflow_approval_pending_delta` metric pattern                        | `app/ai/observability/metrics/`                        |
| `PluginRegistrar.register_tool`, `McpToolExecutionAdapter`                     | `app/ai/plugins/`, `app/ai/mcp/`                       |
| `get_current_caller`, `CallerContext`                                          | `app/core/caller.py`                                   |
| Feature flag infrastructure                                                    | `app/core/config.py`                                   |
| DI factories                                                                   | `app/ai/deps.py`                                       |
| `app/ai/evaluation/` harness                                                   | `app/ai/evaluation/`                                   |

When `HITL_ENABLED=false`, existing platform behaviour must remain unchanged.

---

## Not Allowed

- Reimplement workflow pause/resume; only extend `apply_decision()` and `ApprovalNodeExecutor` additively
- Allow a workflow task/agent node to pause a run implicitly without an `approval` node
- Execute an edited tool-call argument without schema validation
- Persist scratchpad content outside the HITL pause snapshot path
- Add MCP- or plugin-specific approval code (policy must apply transparently via `ToolDefinition`)
- Attach tool arguments, reasons, or scratchpad content to metrics labels
- Implement Epic 10+ timeout enforcement, RBAC delegation, or shared queues
- Break feature-flag parity

---

## Baseline

_Re-verified in Epic 09 Phase 0 (2026-08-11); source of truth: [post-mvp-v2-epic9-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic9-phase-0-baseline-audit.md). Epic 08 Phase 10 completion record used as the starting template only._

| Area                     | State                                                             |
| ------------------------ | ----------------------------------------------------------------- |
| Backend tests / coverage | **1778 passed**, **89.19%** `app/` (Phase 0)                      |
| Plugin package coverage  | **91%** on `app/ai/plugins/` (Epic 08 Phase 10; not re-measured)  |
| Frontend tests           | **291 passed** (48 files); lint + build pass (Phase 0)            |
| Integration tests        | Workflow + MCP + plugins **512 passed** (Phase 0 spot check)      |
| Eval CLI                 | 15/15 `--level all`; 3/3 `--level plugin`; regression check clean (Phase 0) |
| Feature Flag Regression  | 1778 passed with `PLUGINS_ENABLED=false` (Phase 0 full suite)     |
| Plugin Architecture      | Epic 08 Phases 0–10 **Completed** — release summary published     |

---

## Phase Status

| Phase | Name                                       | Effort | Status      |
| ----- | ------------------------------------------ | ------ | ----------- |
| 0     | Baseline Audit                             | XS     | Completed   |
| 1     | HITL Foundations                           | M      | Completed   |
| 2     | Agent Tool-Call Approval Gate              | L      | Completed   |
| 3     | Agent Approval Decision & Resume           | L      | Completed   |
| 4     | Workflow Approval Enhancements             | M      | Completed   |
| 5     | Workflow Graph Guard & MCP/Plugin Coverage | M      | Completed   |
| 6     | Unified Approval REST API & Audit          | S      | Not Started |
| 7     | HITL Observability                         | S      | Not Started |
| 8     | Reference Scenarios & Eval Cases           | M      | Not Started |
| 9     | Frontend Approval Inbox & Audit UI         | S      | Not Started |
| 10    | Validation & Release                       | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS
**Status:** Completed (2026-08-11 — see [post-mvp-v2-epic9-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic9-phase-0-baseline-audit.md))

**Objective**

Establish a verified implementation baseline before introducing Human-in-the-Loop. Confirm Epic 08 is complete, inventory the exact extension points this epic will touch (`ToolRunner`, `ApprovalNodeExecutor`, `apply_decision`, `GraphValidator`, `ToolDefinition`, `ChatMessage`), and verify no HITL implementation exists yet.

**Deliverables**

- `docs/audits/post-mvp-v2-epic9-phase-0-baseline-audit.md`
- Architecture inventory
- Extension point verification (`ToolRunner` dispatch site, `ApprovalNodeExecutor`/`apply_decision`, `GraphValidator`, `ChatMessage.status` CHECK constraint, `ToolDefinition` schema)
- Feature flag verification
- Baseline quality metrics

**Steps**

## Platform Verification

- [x] Confirm Epic 08 Phase 10 complete / authorized for Epic 09.
- [x] Inventory `ToolRunner._execute_with_retry` / `_run_single_tool` dispatch call site (`app/ai/agent/executor/tool_runner.py`).
- [x] Inventory `ApprovalNodeExecutor`, `WorkflowManager.apply_decision`, `build_approval_decision_output` (`app/ai/workflow/`).
- [x] Inventory `GraphValidator` current validation passes (`app/ai/workflow/graph/validator.py`).
- [x] Inventory `ChatMessage` status CHECK constraint and message persistence flow (`app/db/models.py`, `app/services/chat_service.py::_persist_stream_result`).
- [x] Verify chat, RAG, MCP, memory, voice, agent, tool, workflow, plugin, and observability pipelines operational.

## Architecture Review

- [x] Review frozen Part I architecture.
- [x] Confirm `AgentExecutionStatus` and `AgentStreamEventType` current values and extension approach.
- [x] Confirm `Scratchpad`/`ScratchpadEntry` are Pydantic models suitable for JSON snapshotting.
- [x] Confirm no `app/ai/hitl/` package exists.

## Dependency Verification

- [x] Verify DI and feature flag patterns in `app/ai/deps.py` / `app/core/config.py`.
- [x] Verify Alembic migration numbering (next available revision: **0010**).

## Baseline Quality Validation

- [x] Execute lint, typecheck, unit tests, integration tests, eval suite.
- [x] Record baseline metrics in audit doc.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`

**Acceptance**

- Existing platform fully operational.
- All extension points identified.
- No HITL implementation present.
- Baseline metrics recorded.

**Exit criteria**

- [x] Baseline audit published.
- [x] User confirmation to proceed to Phase 1.

**Rollback**

- [x] No rollback required (no code changes).

**Completion Record**

| Metric             | Result                                      |
| ------------------ | ------------------------------------------- |
| Lint               | ✅ PASS                                     |
| Format check       | ✅ PASS — 530 files                         |
| Typecheck          | ✅ PASS — 0 errors                          |
| Backend tests      | ✅ 1778 passed, 89.19% `app/` coverage      |
| Eval CLI           | ✅ 15/15 (`--level all`); 3/3 (`--level plugin`) |
| Frontend tests     | ✅ 291 passed (48 files); lint + build pass |
| Baseline audit     | ✅ [post-mvp-v2-epic9-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic9-phase-0-baseline-audit.md) |
| Phase 0 status     | ✅ Completed                                |
| Phase 1 authorized | ✅ User confirmed                           |

---

# Phase 1 — HITL Foundations

**Effort:** M
**Status:** Completed (2026-08-11)

**Objective**

Introduce the core HITL package, domain models, database migration, and feature flag — without wiring into `ToolRunner` or `GraphValidator` yet.

**Deliverables**

- `app/ai/hitl/` package scaffold
- `ApprovalKind`, `ApprovalStatus` (incl. reserved `expired`/`cancelled`), `ProposedToolCall`, `AgentToolApproval`, `ApprovalRevision`, `ApprovalResult`, `ApprovalAuditEntry`
- `ApprovalPolicy`
- `HitlError`, `ApprovalNotFoundError`, `ApprovalDecisionConflictError`, `ApprovalValidationError`
- `alembic/versions/0010_hitl_tables.py` — `agent_tool_approvals` table; `approval_revisions` table; `chat_messages` status CHECK + `pending_approval_id`; `workflow_node_executions.edited_arguments`/`.reason`
- `ToolDefinition.requires_approval: bool = False`
- `HITL_ENABLED`, `hitl_required_tool_names`, `hitl_approval_timeout_hours`, `hitl_max_reason_length`
- Unit tests for `ApprovalPolicy` and model round-trips

**Steps**

## Package Structure

- [x] Create `app/ai/hitl/` per Part I package layout.
- [x] Export public API from `__init__.py`.
- [x] Verify import cycle freedom.

## Models

- [x] Implement `ApprovalKind`, `ApprovalStatus` enums (`pending`, `approved`, `rejected`, `expired`, `cancelled` — the last two reserved, no transition path yet).
- [x] Implement `ProposedToolCall`, `AgentToolApproval`, `ApprovalRevision`, `ApprovalResult`, `ApprovalAuditEntry` Pydantic models matching Part I schema.
- [x] Implement `ApprovalPolicy.requires_approval(tool)` per Locked Decisions (union of `ToolDefinition.requires_approval` and `hitl_required_tool_names`).

## Migration

- [x] Create `agent_tool_approvals` table (all columns per Part I § Agent Tool-Call Approval — Domain Model, incl. `approval_correlation_id`).
- [x] Create `approval_revisions` table (all columns per Part I § Approval Revision History).
- [x] Extend `chat_messages.status` CHECK constraint with `waiting_approval`, `rejected`; add nullable `pending_approval_id` FK.
- [x] Add `workflow_node_executions.edited_arguments` (jsonb, nullable) and `.reason` (text, nullable) columns.
- [x] Verify migration upgrade/downgrade round-trip.

## Tool Schema

- [x] Add `ToolDefinition.requires_approval: bool = False` (additive; confirm `get_schemas_for_llm` does not leak the flag to provider function-calling schemas).

## Configuration

- [x] Add `HITL_ENABLED` (default `false`) and related settings to `app/core/config.py`.
- [x] Document settings in `backend-python/.env.example`.

## Testing

- [x] `ApprovalPolicy` tests: tool-flagged, config-flagged, both, neither.
- [x] Model serialization round-trip tests (`ProposedToolCall`, `AgentToolApproval`, `ApprovalRevision`, `ApprovalResult`, `ApprovalAuditEntry`).
- [x] Migration upgrade/downgrade test (both new tables).

**Verify**

- `make lint`
- `make typecheck`
- `pytest tests/ai/hitl/`

**Acceptance**

- Public APIs match Part I § Public APIs (foundation subset).
- No changes to runtime agent/workflow behaviour yet.

**Exit criteria**

- [x] Foundation tests pass.
- [x] Public model/policy APIs frozen.
- [x] User confirmation to proceed to Phase 2.

**Rollback**

- Downgrade migration; remove `app/ai/hitl/` package and config flags.
- Verify application builds without HITL modules.

**Completion Record**

| Metric                | Result                                      |
| --------------------- | ------------------------------------------- |
| Lint                  | ✅ PASS                                     |
| Typecheck             | ✅ PASS — 0 errors                          |
| HITL foundation tests | ✅ 25 passed (`tests/ai/hitl/`)             |
| Migration round-trip  | ✅ PASS — `0010_hitl_tables` upgrade/downgrade |
| Phase 1 status        | ✅ Completed                                |
| Phase 2 authorized    | ✅ User confirmed                           |

---

# Phase 2 — Agent Tool-Call Approval Gate

**Effort:** L
**Status:** Completed (2026-08-11)

**Objective**

Wire `ApprovalPolicy` into `ToolRunner` so an approval-required tool call pauses the agent turn instead of executing: snapshot scratchpad/state, persist `agent_tool_approvals`, persist a placeholder `ChatMessage`, and emit a new `approval_required` SSE event.

**Deliverables**

- `Scratchpad.to_snapshot()` / snapshot deserialization helper
- `AgentToolApprovalStore` (create/get/list)
- `AgentApprovalService.pause()`
- `ToolRunner` gate before dispatch
- `AgentStreamEventType.APPROVAL_REQUIRED` + payload
- `approval_required` SSE frame in `app/schemas/chat.py`
- `ChatService` / `UnifiedChatService` wiring to persist the placeholder message and stop the stream cleanly

**Steps**

## Scratchpad Snapshotting

- [x] Implement `Scratchpad.to_snapshot() -> list[dict]` (serializes `ScratchpadEntry[]`); document as the sole exception to "never persisted".
- [x] Implement `AgentExecutionState` snapshot serialization (already a Pydantic model — use `model_dump(mode="json")`).

## Gate Wiring

- [x] In `ToolRunner`, before dispatching a planned tool-call step, resolve each call's `ToolDefinition` and check `ApprovalPolicy.requires_approval()`.
- [x] If any call in the step requires approval, pause the **entire step** (Locked Decision — step-level granularity).
- [x] Delegate to `AgentApprovalService.pause(step, scratchpad, state, session_id, owner_id)`.

## Pause Persistence

- [x] `AgentApprovalService.pause()` generates a new `approval_correlation_id` (UUID) and inserts `agent_tool_approvals` (status=pending, proposed_calls, paused_scratchpad, paused_state, approval_correlation_id).
- [x] Insert placeholder `ChatMessage` (status=waiting_approval, content="", pending_approval_id=…) via `ChatStore`.
- [x] Publish `AgentStreamEvent.approval_required(execution_id, approval_id, approval_correlation_id, proposed_calls)`.
- [x] Ensure the agent run terminates cleanly after the pause (no `complete`/`error` event emitted for this turn) and scratchpad store cleanup does not lose the snapshot already persisted.

## Testing

- [x] Fixture tool flagged `requires_approval=True`; assert `ToolRunner` does not dispatch it.
- [x] Assert `agent_tool_approvals` row created with correct `proposed_calls`.
- [x] Assert placeholder `ChatMessage` persisted with `status=waiting_approval`.
- [x] Assert SSE stream emits `approval_required` and then closes (no `end` frame).
- [x] Test: flag off — tool dispatches normally, no new rows.
- [x] Test: step with a mix of flagged and unflagged calls pauses the whole step.

**Verify**

- `pytest tests/ai/hitl/test_pause.py tests/ai/agent/test_tool_approval.py`

**Acceptance**

- Approval-required tool calls never execute without a pause.
- Flag-off parity preserved (Epic 01 behaviour byte-for-byte).

**Exit criteria**

- [x] Pause path tests pass.
- [x] User confirmation to proceed to Phase 3.

**Rollback**

- Remove `ToolRunner` gate; disable `HITL_ENABLED`.
- Re-run agent executor test suite.

**Completion Record**

| Metric             | Result                                      |
| ------------------ | ------------------------------------------- |
| Lint               | ✅ PASS                                     |
| Typecheck          | ✅ PASS — 0 errors                          |
| Pause path tests   | ✅ 9 passed (`test_pause.py`, `test_tool_approval.py`) |
| Phase 2 status     | ✅ Completed                                |
| Phase 3 authorized | ✅ User confirmed                           |

---

# Phase 3 — Agent Approval Decision & Resume

**Effort:** L
**Status:** Completed (2026-08-11)

**Objective**

Implement `AgentApprovalService.decide()`: record the decision via Compare-And-Swap (CAS), validate any edited arguments, execute approved calls, rehydrate the scratchpad/state, and resume the ReAct loop via `AgentExecutor.resume_from_approval()`, streaming the continuation as the response of the decide call.

**Deliverables**

- `AgentApprovalService.revise()` — pre-decision edit, appends `ApprovalRevision`
- `AgentApprovalService.decide()` — builds and returns `ApprovalResult`
- `AgentExecutor.resume_from_approval()`
- `POST /api/approvals/{id}/revise` and `POST /api/approvals/{id}/decide` (agent-tool kind) — SSE on approve, JSON on reject/revise
- `GET /api/approvals/{id}/revisions`
- `ChatStore` message-update method (placeholder → final content)
- Integration tests covering approve, approve-with-edits, revise-then-approve, and reject

**Steps**

## Decision Recording (Stage 1)

- [x] Implement Compare-And-Swap (CAS) update (`UPDATE agent_tool_approvals SET status=… WHERE id=… AND status='pending'`); `409` on conflict/duplicate.
- [x] On reject: set `decided_by`, `decided_at`, `reason`; update linked `ChatMessage.status=rejected`; build `ApprovalResult(status=rejected, ...)`; return JSON, no resume.
- [x] Resolve the `ApprovalResult.final_payload` from the latest `ApprovalRevision` (if any) or the original `proposed_calls` otherwise.

## Pre-Decision Revise

- [x] Implement `AgentApprovalService.revise(approval_id, edited_calls, note, owner_id)`: `422` if any call fails `ToolValidator`; otherwise Compare-And-Swap (CAS)-guarded (`WHERE status='pending'`) — `409` if the approval already has a terminal status.
- [x] On success, append an `ApprovalRevision` row (`revision_number` = previous max + 1) and update `agent_tool_approvals.edited_calls` to the new value in the same transaction.
- [x] `GET /api/approvals/{id}/revisions` returns the full ordered list for either approval kind.

## Approve Path (Stages 2–4)

- [x] Validate `edited_calls` (if present on the decide call itself) per call via `ToolValidator` against the resolved `ToolDefinition`; `422` on failure, pause remains `pending`. If omitted, use the latest `ApprovalRevision`'s payload (if any).
- [x] If the decide call itself supplies `edited_calls`, append one more `ApprovalRevision` before proceeding (Stage 1 completes the full edit history).
- [x] Rehydrate `Scratchpad` from `paused_scratchpad` and `AgentExecutionState` from `paused_state` (Stage 2 — resume scheduled).
- [x] Execute approved (possibly edited) calls directly via `ToolExecutor.execute()` (bypassing the gate — already satisfied), passing `approval_correlation_id` through `ToolExecutionContext` (Stage 3 — tool execution). On execution failure, leave `agent_tool_approvals.status=approved` unchanged — record the failure on the resumed turn only (see Part I § Execution Failure Semantics).
- [x] Append tool results to the rehydrated scratchpad (reuse `_record_tool_results` pattern).
- [x] Implement `AgentExecutor.resume_from_approval(scratchpad, state, request, context, tool_context)` — re-enters the loop at the `PLANNING` transition and continues to finalize or a subsequent pause (Stage 4 — continuation).

## Streaming & Persistence

- [x] Stream the resumed loop's events (`delta`/`tool_start`/`tool_end`/`approval_required`/`end`/`error`) as the decide endpoint's SSE body.
- [x] On finalize, update the placeholder `ChatMessage` in place (status=complete, final content, finish_reason) rather than inserting a new row.
- [x] On a subsequent pause within the same resumed turn, insert a new `agent_tool_approvals` row (independent from the first, with its own `approval_correlation_id`).

## Testing

- [x] Test: approve as-is → tool executes with original arguments → turn finalizes.
- [x] Test: approve with `edited_calls` → tool executes with edited arguments; one `ApprovalRevision` recorded.
- [x] Test: approve with invalid `edited_calls` → `422`, pause remains `pending`, no revision appended.
- [x] Test: revise once, then revise again → two ordered `ApprovalRevision` rows; `edited_calls` reflects the latest.
- [x] Test: revise with invalid payload → `422`, no revision appended.
- [x] Test: revise after a terminal decision → `409`.
- [x] Test: reject → tool never executes; `ChatMessage.status=rejected`; `ApprovalResult.status=rejected`.
- [x] Test: duplicate decision → `409`.
- [x] Test: resumed turn hits a second approval-required call → second pause recorded with a new `approval_correlation_id`.
- [x] Test: approved call's execution fails (mocked `ToolExecutor` failure) → `agent_tool_approvals.status` remains `approved`; resumed `ChatMessage.status=error`.
- [x] Test: flag off — decide/revise endpoints return `503`.

**Verify**

- `pytest tests/ai/hitl/test_decide.py tests/ai/hitl/test_revise.py tests/ai/agent/test_tool_approval.py tests/test_approvals_router.py`

**Acceptance**

- Approved calls execute exactly once with the correct (possibly edited) arguments.
- Resumed turns produce a coherent final assistant message.
- Rejected calls never execute.
- Every edit — pre-decision revise or inline at decide time — is preserved in `approval_revisions`.
- An execution failure after approval never changes the approval's own terminal status.

**Exit criteria**

- [x] Decision/resume tests pass.
- [ ] User confirmation to proceed to Phase 4.

**Rollback**

- Remove decide endpoint and `resume_from_approval`; disable `HITL_ENABLED`.
- Re-run agent executor and chat streaming test suites.

**Completion Record**

| Metric                | Result                                      |
| --------------------- | ------------------------------------------- |
| Lint                  | ✅ PASS                                     |
| Typecheck             | ✅ PASS — 0 errors (`app/ai/hitl/`)         |
| Decision/resume tests | ✅ 29 passed (Phase 3 verify suite)         |
| Revision history tests | ✅ covered in `test_revise.py` + decide tests |
| PR review hardening   | ✅ SSE publisher cleanup on early failure; revision row lock (`FOR UPDATE`); `AgentApprovalStore` protocol |
| Phase 3 status        | ✅ Completed                                |
| Phase 4 authorized    | ✅ User requested Phase 4 implementation      |

---

# Phase 4 — Workflow Approval Enhancements

**Effort:** M
**Status:** Completed

**Objective**

Extend Epic 06's workflow approval nodes with editable arguments and a decision reason, additively, with zero behavioural change when the new fields are omitted.

**Deliverables**

- `ApprovalDecisionRequest` schema (`edited_arguments?`, `reason?`)
- `WorkflowManager.apply_decision(..., edited_arguments=None, reason=None) -> tuple[WorkflowRun, ApprovalResult]`
- `build_approval_decision_output()` extended to include `edited_arguments`
- `WorkflowNodeExecution.edited_arguments`, `.reason` populated
- One `ApprovalRevision` (`kind=workflow_node`) appended whenever `edited_arguments` is supplied
- Updated `/approve` / `/reject` routers accepting optional JSON body

**Steps**

## Manager & Store

- [x] Extend `WorkflowManager.apply_decision()` signature with optional `edited_arguments: dict[str, object] | None` and `reason: str | None`; build and return an `ApprovalResult`.
- [x] Extend `WorkflowStore.record_approval_decision()` to persist `edited_arguments`/`reason` on the `WorkflowNodeExecution` row (same transaction as the existing Compare-And-Swap (CAS) write) and append one `ApprovalRevision` row when `edited_arguments` is non-null.
- [x] Extend `build_approval_decision_output()` to include `edited_arguments` in the `run.context.variables[node_id]` output when present.
- [x] Ensure a subsequent execution failure on the downstream `task`/`agent` node (per Part I § Execution Failure Semantics) never touches this node's own `decision`/`status` fields — confirm via the existing Epic 06 failure-propagation path, no new code required.

## Router & Schema

- [x] Add `ApprovalDecisionRequest` (optional body) to `app/schemas/workflow.py`.
- [x] Update `POST …/approve` and `POST …/reject` to accept the optional body; omitted body preserves Epic 06 behaviour exactly.
- [x] Extend `WorkflowNodeExecutionResponse` with `edited_arguments`, `reason`.

## Testing

- [x] Test: approve with `edited_arguments` → downstream task node's `{{variables.<node_id>.edited_arguments.<field>}}` template resolves correctly; one `ApprovalRevision` recorded.
- [x] Test: approve without body → Epic 06 byte-for-byte behaviour (regression); zero `ApprovalRevision` rows.
- [x] Test: reject with `reason` → persisted and returned in detail response.
- [x] Test: `hitl_max_reason_length` truncation/validation.
- [x] Test: downstream node execution fails after approval with `edited_arguments` → approval node's `decision` remains `approved`; run fails per existing Epic 06 semantics.

**Verify**

- `pytest tests/ai/workflow/test_approval_node.py tests/test_workflows_router.py`

**Acceptance**

- Editable arguments flow into downstream node templating without new templating code.
- Epic 06 approval behaviour is unchanged when new fields are omitted.
- Downstream execution failure never retroactively changes the approval node's decision.

**Exit criteria**

- [x] Workflow approval enhancement tests pass.
- [x] User confirmation to proceed to Phase 5.

**Rollback**

- Revert manager/store/schema changes to Epic 06 signatures.
- Re-run workflow test suite.

**Completion Record**

| Metric                  | Result                                      |
| ----------------------- | ------------------------------------------- |
| Lint                    | ✅ PASS                                     |
| Typecheck               | ✅ PASS — 0 errors (changed modules)        |
| Workflow approval tests | ✅ 39 passed (`test_approval_node` + router) |
| Phase 4 status          | ✅ Completed                                |
| Phase 5 authorized      | ✅ Authorized                               |

---

# Phase 5 — Workflow Graph Guard & MCP/Plugin Coverage

**Effort:** M
**Status:** Completed

**Objective**

Add the `GraphValidator` reachability guard so approval-required tools cannot be reached from a workflow `task`/`agent` node without a preceding `approval` node; verify `requires_approval` applies transparently to MCP and plugin tools on both surfaces.

**Deliverables**

- `GraphValidator` reachability extension (flag-gated)
- Integration tests with MCP fixture tool and Epic 08 reference plugin tool flagged `requires_approval`

**Steps**

## Graph Validation

- [x] Implement backward-reachability check: for every `task`/`agent` node referencing an approval-required tool, verify every path from the trigger passes through an `approval` node — a single `O(V + E)` reverse traversal per flagged node (see Part I § Workflow Graph Guard for the complexity note).
- [x] Raise `WorkflowValidationError` naming the offending node and tool on violation.
- [x] Gate the check on `HITL_ENABLED`; skip entirely when disabled (Epic 06 parity).
- [x] Test against Epic 06's existing parallel/fork-join fixtures to confirm no false positives on complex branching (nested/parallel approval-required paths).

## MCP Coverage

- [x] Fixture MCP tool with `requires_approval=True` on its adapter's `ToolDefinition`.
- [x] Test: chat/agent path pauses on the MCP tool identically to a native tool.
- [x] Test: workflow graph guard rejects a definition referencing the MCP tool without a preceding approval node.

## Plugin Coverage

- [x] Extend or add a plugin fixture (reusing Epic 08's `echo-tool` pattern) registering a tool with `requires_approval=True` via `PluginRegistrar.register_tool()`.
- [x] Test: chat/agent path pauses on the plugin tool identically to a native tool.
- [x] Test: workflow graph guard rejects a definition referencing the plugin tool without a preceding approval node.

## Testing

- [x] Test: unflagged tools unaffected by the reachability check.
- [x] Test: a `task` node correctly _preceded_ by an `approval` node passes validation.
- [x] Test: flag off — reachability check skipped; Epic 06 workflows using previously-invalid graphs (if any) are unaffected.

**Verify**

- `pytest tests/ai/workflow/test_graph_validator.py tests/ai/hitl/test_coverage_mcp_plugin.py`

**Acceptance**

- Sensitive tools cannot be reached from a workflow graph without an explicit approval node, regardless of tool origin.
- No MCP- or plugin-specific approval code exists.

**Exit criteria**

- [x] Graph guard and coverage tests pass.
- [ ] User confirmation to proceed to Phase 6.

**Rollback**

- Remove the reachability check from `GraphValidator`; disable `HITL_ENABLED`.

**Completion Record**

| Metric                    | Result                                                         |
| ------------------------- | -------------------------------------------------------------- |
| Lint                      | ✅ PASS                                                        |
| Typecheck                 | ✅ PASS — 0 errors (changed modules)                           |
| Graph guard tests         | ✅ 37 passed (`test_graph_validator` + `test_coverage_mcp_plugin`) |
| MCP/plugin coverage tests | ✅ included in verify suite above                              |
| Phase 5 status            | ✅ Completed                                                   |
| Phase 6 authorized        | ⬜ Pending user confirmation                                   |

---

# Phase 6 — Unified Approval REST API & Audit

**Effort:** S
**Status:** Not Started

**Objective**

Expose the read-only, cross-surface approval inbox/audit API and extend health with HITL fields.

**Deliverables**

- `app/schemas/approvals.py`
- `app/routers/approvals.py` (`GET /api/approvals`, `GET /api/approvals/{id}`, `GET /api/approvals/{id}/revisions`)
- `ApprovalsStore` aggregation (incl. `revision_count`/revision list)
- Router tests

**Steps**

## API Implementation

- [ ] `GET /api/approvals` — merge `agent_tool_approvals` (owner-scoped) and workflow approval node executions (owner-scoped via `WorkflowStore`) into `ApprovalAuditEntry[]`; support `status`/`kind` filters and pagination. `status`/`kind` filter values are additive-extensible for future richer filtering (edited-state, plugin/MCP origin) — `TODO(future):` per Part I § Future Enhancements, not implemented in this phase.
- [ ] `GET /api/approvals/{id}` — detail; `404` when not found or not owned; include `approval_correlation_id` and `revision_count`.
- [ ] `GET /api/approvals/{id}/revisions` — full `ApprovalRevision[]` for either kind, ordered by `revision_number`; `404` when not found or not owned.
- [ ] Return `503 feature_disabled` when `HITL_ENABLED=false`.
- [ ] Include `decide_url` per entry (kind-specific action endpoint).

## Health Extension

- [ ] Add `hitl_enabled`, `hitl_pending_approvals_count` to health payload.

## Mount Router

- [ ] Include router in `app/main.py`.

## Testing

- [ ] Router tests with flag on/off.
- [ ] Assert list is owner-scoped (no cross-user visibility).
- [ ] Assert responses exclude secrets/paths beyond documented bounds.
- [ ] Assert pagination and filter params behave correctly across mixed-kind result sets.
- [ ] Assert `GET /api/approvals/{id}/revisions` returns revisions in order and 404s for non-owned ids.

**Verify**

- `pytest tests/test_approvals_router.py`

**Acceptance**

- Authenticated callers can inspect a unified pending/history view.
- No secret/credential leakage.

**Exit criteria**

- [ ] Router tests pass.
- [ ] User confirmation to proceed to Phase 7.

**Rollback**

- Remove router mount; disable flag.

**Completion Record**

| Metric                 | Result          |
| ---------------------- | --------------- |
| Lint                   | Pending Phase 6 |
| Typecheck              | Pending Phase 6 |
| Approvals router tests | Pending Phase 6 |
| Revisions endpoint tests | Pending Phase 6 |
| Health tests           | Pending Phase 6 |
| Phase 6 status         | Not Started     |
| Phase 7 authorized     | Pending         |

---

# Phase 7 — HITL Observability

**Effort:** S
**Status:** Not Started

**Objective**

Add approval span/metric instrumentation closing Epic 07's deferred "approval latency metrics" item.

**Deliverables**

- `approval_span(approval_id, approval_kind, approval_status, approval_decision)` in `app/ai/observability/tracing/spans.py`
- `agent_tool_approval_pending_count` (sibling to Epic 06's `workflow_approval_pending_count`)
- `approval_decisions_total` counter (labels: `kind`, `decision`) — the raw counter future success/rejection/edit-rate dashboards derive from
- `hitl_approval_decision_latency_ms` histogram (label: `kind`)
- `hitl_resume_latency_ms` histogram (label: `kind`) — Stage 2→4 duration (resume scheduled → continuation complete)
- `hitl_tool_execution_latency_ms` histogram (label: `kind`) — Stage 3 duration (`ToolExecutor.execute()` for approved calls only)
- `approval_correlation_id` propagated onto the resulting `tool_span` (span attribute, not a metric label)
- Tests

**Steps**

## Span Helper

- [ ] Implement `approval_span` with fixed name `approval.decide` and attributes `approval_id`, `approval_kind`, `approval_status`, `approval_decision`, `decision_latency_ms`, `edited: bool` (ids are span attributes only — never metric labels, per the unbounded-cardinality invariant).
- [ ] Wrap pause, revise, and decide operations when `OBSERVABILITY_ENABLED=true`.
- [ ] Set `approval_correlation_id` on the approval span and propagate it into `ToolExecutionContext` so the resulting `tool_span` (Epic 07, existing) carries the same value as an attribute — the durable cross-span link.

## Metrics

- [ ] Add `agent_tool_approval_pending_count` (`UpDownCounter`, no unbounded labels) incremented/decremented on pause/decide, mirroring `record_workflow_approval_pending_delta`.
- [ ] Add `approval_decisions_total` (`Counter`, labels `kind` ∈ `{agent_tool, workflow_node}`, `decision` ∈ `{approved, rejected}`) incremented once per terminal decision — the basis for future success-rate/rejection-rate/edit-rate dashboards without adding new instruments per the review's "derived metrics" recommendation.
- [ ] Add `hitl_approval_decision_latency_ms` (`Histogram`, label `kind`) recorded from `requested_at` to `decided_at` on both surfaces.
- [ ] Add `hitl_resume_latency_ms` (`Histogram`, label `kind`) recorded from Stage 2 start to Stage 4 completion (see Part I § Decision Execution Stages).
- [ ] Add `hitl_tool_execution_latency_ms` (`Histogram`, label `kind`) recorded around the Stage 3 `ToolExecutor.execute()` call for approved calls only.
- [ ] Extend `ALLOWED_LABEL_KEYS` in `app/ai/observability/metrics/labels.py` as needed; no `tool_name`/`session_id`/`run_id`/`approval_id` labels (unbounded cardinality) — `kind` and `decision` are the only labels, both small closed sets.

## Testing

- [ ] In-memory span exporter tests for pause/revise/decide, asserting `approval_id`/`approval_kind`/`approval_status`/`approval_decision` attributes are present and `approval_correlation_id` matches on the paired `tool_span`.
- [ ] Metric tests for pending count increment/decrement, `approval_decisions_total` increments, and all three latency histograms recording (both kinds).
- [ ] Verify flag off → no HITL spans/metrics.

**Verify**

- `pytest tests/ai/hitl/test_observability.py tests/ai/observability/`

**Acceptance**

- HITL telemetry follows Epic 07's content-free invariant (ids/kind/status/latency/counts only — never tool arguments, reasons, or scratchpad content).
- Workflow's existing `workflow_approval_pending_count` unaffected.
- `approval_correlation_id` reliably links an approval span to its execution's `tool_span`.

**Exit criteria**

- [ ] Observability tests pass.
- [ ] User confirmation to proceed to Phase 8.

**Rollback**

- Remove HITL span/metric hooks only.

**Completion Record**

| Metric              | Result          |
| ------------------- | --------------- |
| Lint                | Pending Phase 7 |
| Observability tests | Pending Phase 7 |
| Phase 7 status      | Not Started     |
| Phase 8 authorized  | Pending         |

---

# Phase 8 — Reference Scenarios & Eval Cases

**Effort:** M
**Status:** Not Started

**Objective**

Ship reference sensitive-tool scenarios and extend the evaluation harness with HITL coverage across both surfaces.

**Deliverables**

- A reference "sensitive" tool (e.g. extend `web_search` fixture or add a small `send_notification` example tool) flagged `requires_approval=True` for eval use
- Eval cases: agent approve, agent approve-with-edits, agent reject; workflow approve-with-edits, workflow reject
- Adversarial/edge-case eval and integration coverage (duplicate/concurrent decisions, invalid edits, stale ids, plugin/MCP tool approvals, multiple approvals per conversation, nested workflow approvals, streaming interruption)
- README section documenting operator steps (flag on, flag a tool, decide via REST)

**Steps**

## Reference Scenario

- [ ] Add a reference tool (or config-flag an existing safe example tool via `hitl_required_tool_names` in eval fixtures) suitable for approve/reject/edit demonstration without side effects.
- [ ] Add a matching reference workflow definition with an `approval` node preceding a `task` node using edited arguments.

## Eval Extension

- [ ] Add `--level hitl` eval cases (or extend `--level agent`/`--level workflow`) gated on `HITL_ENABLED`, following existing harness patterns (Epic 08's `--level plugin` precedent).
- [ ] Document skip policy when HITL disabled.

## Adversarial & Concurrency Scenarios

Implemented as integration tests (and, where the harness supports it, `--level hitl` eval cases) in this phase:

- [ ] **Duplicate approval submissions** — decide the same approval twice; assert the second call returns `409` and the first decision stands unchanged.
- [ ] **Concurrent approval decisions** — two decide calls racing on the same `pending` row (simulated via two sessions issuing the Compare-And-Swap (CAS) update); assert exactly one succeeds and the other observes `409` (exercises the same Compare-And-Swap (CAS) guard as Phase 3, at an integration level).
- [ ] **Invalid edited arguments** — revise and decide calls with schema-invalid `edited_calls`/`edited_arguments`; assert `422` and no state mutation (no revision appended, no execution).
- [ ] **Stale approval ids** — decide/revise against a nonexistent id or an id already in a terminal state; assert `404`/`409` respectively, never a silent no-op.
- [ ] **Plugin tool approvals** — reuse the Phase 5 plugin fixture; assert the full pause → decide → resume loop behaves identically to a native tool.
- [ ] **MCP tool approvals** — reuse the Phase 5 MCP fixture; assert the full pause → decide → resume loop behaves identically to a native tool.
- [ ] **Multiple approvals within a single conversation** — a chat turn whose plan contains two sequential approval-required tool calls in separate steps; assert each produces its own `agent_tool_approvals` row with its own `approval_correlation_id`, decided independently, and the turn finalizes only after both are resolved.
- [ ] **Nested workflow approvals** — a workflow graph with an `approval` node inside a parallel/fork-join branch (reusing Epic 06's branching fixtures); assert `GraphValidator` accepts it when every branch is properly guarded and the run executes correctly end to end.
- [ ] **Streaming interruption during approval** — simulate the client disconnecting after the `approval_required` SSE frame is sent (before calling decide); assert the `agent_tool_approvals` row remains `pending` and is independently resumable via a later decide call from a fresh connection.
- [ ] **Expired approvals** — documented, not eval-tested in V2: `hitl_approval_timeout_hours` has no enforcement (`TODO(epic-10):`), so there is no behavior to assert beyond "an old pending approval remains decidable indefinitely" (implicitly covered by the streaming-interruption case above).
- [ ] **Server restart/resume** — documented as a known V2 gap, not eval-tested: a process restart between Stage 1 and Stage 4 (see § Decision Execution Stages) leaves a resumable-but-stuck row (see Part I § Snapshot Cleanup Strategy); no test asserts recovery since none is implemented until Epic 10.

## Documentation

- [ ] Document operator steps: enable flag, flag a tool, decide via REST, inspect audit trail.
- [ ] Document which adversarial scenarios above are eval-tested vs. documented-only gaps, cross-referencing Part I § Implementation Risks.

## Testing

- [ ] Integration test exercising the full pause → decide → resume loop end-to-end (agent surface).
- [ ] Integration test exercising the full pause → decide (edited) → continue loop end-to-end (workflow surface).
- [ ] Eval cases pass in CI when flags enabled.

**Verify**

- `pytest tests/ai/hitl/test_reference_scenarios.py tests/ai/hitl/test_adversarial_scenarios.py`
- `make eval` (with HITL flags on in test env)

**Acceptance**

- Reference scenarios demonstrate approve, approve-with-edits, and reject on both surfaces.
- Eval covers HITL happy paths without side effects in CI.
- Adversarial/edge-case scenarios listed above are either passing tests or explicitly documented as deferred gaps — none are silently unhandled.

**Exit criteria**

- [ ] Reference scenario tests pass.
- [ ] Adversarial scenario tests pass.
- [ ] User confirmation to proceed to Phase 9.

**Rollback**

- Remove reference scenario from default eval dataset; keep `HITL_ENABLED=false` default.

**Completion Record**

| Metric                    | Result          |
| ------------------------- | --------------- |
| Reference scenario tests  | Pending Phase 8 |
| Adversarial scenario tests | Pending Phase 8 |
| Eval `--level hitl`       | Pending Phase 8 |
| README                    | Pending Phase 8 |
| Phase 8 status            | Not Started     |
| Phase 9 authorized        | Pending         |

---

# Phase 9 — Frontend Approval Inbox & Audit UI

**Effort:** S
**Status:** Not Started

**Objective**

Add a frontend approval inbox (pending, cross-surface) with inline argument editing and reason capture, plus an audit history view; extend `WorkflowsPage`'s existing approval UI with the new edit/reason inputs.

**Deliverables**

- `frontend/src/api/approvalsClient.ts`
- `frontend/src/types/approvals.ts`
- `frontend/src/pages/ApprovalsPage.tsx` (pending inbox + audit history tabs)
- Chat UI handling for the `approval_required` SSE event (inline decision card or link to inbox)
- `WorkflowsPage` approval UI extended with edit-args + reason inputs
- Component tests

**Steps**

## API Client

- [ ] `GET /api/approvals` (list, filters) and `GET /api/approvals/{id}` (detail).
- [ ] `GET /api/approvals/{id}/revisions` and `POST /api/approvals/{id}/revise`.
- [ ] `POST /api/approvals/{id}/decide` — handle SSE response on approve, JSON on reject.
- [ ] Handle `503 feature_disabled` with a friendly empty state.

## Chat UI

- [ ] Handle the `approval_required` SSE frame: render an inline decision card (proposed arguments, editable JSON, approve/reject, reason) or a link into the inbox.
- [ ] Render `waiting_approval`/`rejected` message statuses distinctly from `complete`/`error`.

## Inbox & Audit UI

- [ ] Pending tab: cross-surface list with editable arguments per entry; edits on an agent-tool approval call `revise` (not `decide`) so the user can keep editing before submitting a final decision.
- [ ] History tab: decided entries with `decision`, `reason`, `edited`, `decided_by`, `decided_at`; show only the latest revision by default (per Part I § Approval Revision History) with a "view revision history" expander that fetches and lists the full `ApprovalRevision[]`.
- [ ] `WorkflowsPage`: add edit-args (JSON) and reason inputs to the existing approve/reject actions.

## Testing

- [ ] MSW/mock tests for list, detail, decide (approve/reject), and disabled states.
- [ ] Chat streaming test for `approval_required` handling.

**Verify**

- Frontend lint, tests, build

**Acceptance**

- Inbox and audit views render correctly when the backend flag is on.
- No secrets or paths displayed.

**Exit criteria**

- [ ] Frontend tests pass.
- [ ] User confirmation to proceed to Phase 10.

**Rollback**

- Remove route/page and chat UI branch for `approval_required`.

**Completion Record**

| Metric                  | Result          |
| ----------------------- | --------------- |
| Lint                    | Pending Phase 9 |
| Frontend tests          | Pending Phase 9 |
| `approvalsClient` tests | Pending Phase 9 |
| `ApprovalsPage` tests   | Pending Phase 9 |
| Route + nav             | Pending Phase 9 |
| Phase 9 status          | Not Started     |
| Phase 10 authorized     | Pending         |

---

# Phase 10 — Validation & Release

**Effort:** M
**Status:** Not Started

**Objective**

Full-platform validation, flag-off regression, release summary, and epic completion.

**Deliverables**

- `docs/releases/post-mvp-v2-epic9-release-summary.md`
- Updated epic Phase status and completion records
- Changelog entry

**Steps**

## Validation

- [ ] Full backend test suite + coverage ≥80% on `app/ai/hitl/`.
- [ ] Frontend tests + build.
- [ ] Integration tests (agent tool approval, workflow approval, MCP, plugins, approvals router).
- [ ] Eval suite + regression check.
- [ ] Flag-off regression: entire suite with `HITL_ENABLED=false`.

## Documentation

- [ ] Publish release summary.
- [ ] Update `backend-python/.env.example` with HITL settings.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`
- Frontend lint, tests, production build

**Acceptance**

- All Part I architectural invariants preserved.
- Flag-off parity confirmed.
- Reference scenarios documented and reproducible.

**Exit criteria**

- Release summary published.
- User authorizes Epic 10.

**Rollback**

- Disable `HITL_ENABLED`.
- Redeploy previous release if needed.

**Completion Record**

| Metric                    | Result                    |
| ------------------------- | ------------------------- |
| Backend Tests             | Pending Phase 10          |
| HITL package coverage     | Pending Phase 10          |
| Frontend Tests            | Pending Phase 10          |
| Integration Tests         | Pending Phase 10          |
| Eval Suite                | Pending Phase 10          |
| Feature Flag Regression   | Pending Phase 10          |
| Release Summary Published | Pending Phase 10          |
| Epic Status               | Not Started               |
| Epic 10 authorization     | Pending user confirmation |

---

# PR Map

One PR per phase.

- v2/epic-09/phase-00-baseline
- v2/epic-09/phase-01-hitl-foundations
- v2/epic-09/phase-02-agent-approval-gate
- v2/epic-09/phase-03-agent-approval-resume
- v2/epic-09/phase-04-workflow-approval-enhancements
- v2/epic-09/phase-05-graph-guard-mcp-plugin
- v2/epic-09/phase-06-rest-api
- v2/epic-09/phase-07-observability
- v2/epic-09/phase-08-reference-eval
- v2/epic-09/phase-09-frontend
- v2/epic-09/phase-10-release

---

# Risks

| Risk                                                                             | Mitigation                                                                                                                                               |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Resumed ReAct loop diverges from a natural continuation (missed reflection step) | Documented Locked Decision — reflection is skipped on resume in v1; covered by explicit reference eval scenario                                          |
| Scratchpad snapshot grows unbounded on long turns                                | Snapshot captures only the current turn's scratchpad (never cross-session); size bound follows existing scratchpad/message size limits                   |
| Duplicate/racing decisions on the same approval                                  | Compare-And-Swap (CAS) pattern reused from Epic 06; `409` on conflict; covered by concurrent-decision eval scenario (Phase 8)                                                |
| Revise/decide race on the same pending approval                                  | Both operations share the same `WHERE status='pending'` Compare-And-Swap (CAS) guard — a concurrent revise cannot overwrite an in-flight decide, or vice versa (Phase 3)   |
| Workflow graph guard false-positives on complex branching                        | Reachability check is conservative (any unguarded path fails); tested against Epic 06's existing parallel/fork-join fixtures, incl. nested-approval scenario (Phase 8) |
| Metric cardinality from tool names, session ids, or approval ids                  | Never label metrics with `tool_name`, `session_id`, `run_id`, or `approval_id` — those are span attributes only, never metric labels                     |
| Edited arguments smuggling unexpected types                                      | Agent-side edits schema-validated via `ToolValidator` before execution (including on the pre-decision revise endpoint); workflow-side edits validated at the consuming node (existing enforcement point) |
| `approval_revisions` row growth if a UI bug submits redundant revises            | Rows are small and edit activity is caller-paced (human-speed), not a hot path; no cleanup job needed at V2 scale (see § Migration Impact Summary)        |
| Process crash between decision recording and resumed continuation               | Documented, accepted V2 gap — snapshot is not cleared, leaving a resumable-but-stuck `approved` row; `TODO(epic-10):` background sweep (see § Snapshot Cleanup Strategy) |
| Feature regression                                                               | `HITL_ENABLED` flag-off parity tests in Phase 10                                                                                                         |

---

# Observability

Metrics/spans this epic adds (when respective flags enabled):

| Field                               | Purpose                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------ |
| `approval.decide` span              | Per-decision; attributes: `approval_id`, `approval_kind`, `approval_status`, `approval_decision`, `decision_latency_ms`, `edited` |
| `agent_tool_approval_pending_count` | Pending agent tool-call approvals (sibling to Epic 06's `workflow_approval_pending_count`) |
| `approval_decisions_total`         | Counter of terminal decisions — labels `kind`, `decision`; basis for derived success/rejection/edit-rate dashboards (no new instruments needed for those) |
| `hitl_approval_decision_latency_ms` | Time from request to decision — label `kind` ∈ `{agent_tool, workflow_node}`               |
| `hitl_resume_latency_ms`           | Time from resume-scheduled to continuation-complete (Decision Execution Stages 2→4) — label `kind` |
| `hitl_tool_execution_latency_ms`   | Time spent in `ToolExecutor.execute()` for approved calls (Decision Execution Stage 3) — label `kind` |
| `approval_correlation_id` (span attribute, not a metric) | Propagated from the approval span onto the resulting `tool_span`, linking a decision to its execution in traces |
| `hitl_enabled`                      | Health field                                                                               |
| `hitl_pending_approvals_count`      | Health field (sum across both kinds)                                                       |

Tool execution itself continues to emit existing `tool_span` events — no duplicate execution spans required; the correlation id above links the two without introducing a second span type.

---

# Definition of Done

- [ ] All Part I architectural invariants preserved.
- [ ] Public APIs frozen after Phase 1.
- [ ] Agent tool-call approval gate and resume operational (approve, approve-with-edits, reject).
- [ ] Workflow approval nodes support editable arguments and decision reason; graph guard enforced.
- [ ] MCP and plugin tools gated identically to native tools with no origin-specific code.
- [ ] Unified Approval REST API and frontend inbox/audit UI operational, including per-approval revision history.
- [ ] Reference scenarios and adversarial/edge-case eval coverage shipped (or explicitly documented as deferred gaps).
- [ ] Approval-scoped tracing attributes and correlation ids present on every decision and correctly linked to the resulting tool execution span.
- [ ] `HITL_ENABLED=false` preserves Epic 08 behaviour.
- [ ] Backend coverage ≥80% on `app/ai/hitl/`.
- [ ] Release summary published.
- [ ] User authorizes Epic 10.

---

## Files index

| Path                                                      | Action | Owner    | Phase |
| --------------------------------------------------------- | ------ | -------- | ----- |
| `docs/audits/post-mvp-v2-epic9-phase-0-baseline-audit.md` | create | Docs     | 0     |
| `app/ai/hitl/**`                                          | create | Core     | 1–3   |
| `alembic/versions/0010_hitl_tables.py`                    | create | Core     | 1     |
| `app/core/config.py`                                      | modify | Core     | 1     |
| `backend-python/.env.example`                             | modify | Docs     | 1, 10 |
| `app/ai/tools/schemas.py`                                 | modify | Core     | 1     |
| `app/ai/agent/scratchpad/scratchpad.py`                   | modify | Core     | 2     |
| `app/ai/agent/executor/tool_runner.py`                    | modify | Core     | 2     |
| `app/ai/agent/models/events.py`                           | modify | Core     | 2     |
| `app/ai/agent/models/state.py`                            | modify | Core     | 2     |
| `app/schemas/chat.py`                                     | modify | Core     | 2     |
| `app/services/chat_service.py`                            | modify | Adapter  | 2, 3  |
| `app/ai/agent/executor/agent_executor.py`                 | modify | Core     | 3     |
| `app/routers/approvals.py`                                | create | Adapter  | 3, 6  |
| `app/schemas/approvals.py`                                | create | Core     | 3, 6  |
| `app/ai/workflow/manager.py`                              | modify | Core     | 4     |
| `app/ai/workflow/nodes/approval_node.py`                  | modify | Core     | 4     |
| `app/ai/workflow/models/run.py`                           | modify | Core     | 4     |
| `app/schemas/workflow.py`                                 | modify | Core     | 4     |
| `app/routers/workflows.py`                                | modify | Adapter  | 4     |
| `app/ai/workflow/graph/validator.py`                      | modify | Core     | 5     |
| `app/routers/health.py`                                   | modify | Adapter  | 6     |
| `app/main.py`                                             | modify | Adapter  | 3, 6  |
| `app/ai/observability/tracing/spans.py`                   | modify | Core     | 7     |
| `app/ai/observability/metrics/instruments.py`             | modify | Core     | 7     |
| `tests/ai/hitl/**`                                        | create | Tests    | 1–8   |
| `tests/ai/agent/test_tool_approval.py`                    | create | Tests    | 2, 3  |
| `tests/ai/workflow/test_approval_node.py`                 | modify | Tests    | 4     |
| `tests/ai/workflow/test_graph_validator.py`               | modify | Tests    | 5     |
| `tests/test_approvals_router.py`                          | create | Tests    | 6     |
| `tests/ai/hitl/test_adversarial_scenarios.py`             | create | Tests    | 8     |
| `tests/ai/evaluation/**`                                  | modify | Tests    | 8     |
| `frontend/src/api/approvalsClient.ts`                     | create | Frontend | 9     |
| `frontend/src/types/approvals.ts`                         | create | Frontend | 9     |
| `frontend/src/pages/ApprovalsPage.tsx`                    | create | Frontend | 9     |
| `frontend/src/pages/WorkflowsPage.tsx`                    | modify | Frontend | 9     |
| `docs/releases/post-mvp-v2-epic9-release-summary.md`      | create | Docs     | 10    |

---

## Changelog

| Version | Date       | Changes                                                                                          |
| ------- | ---------- | ------------------------------------------------------------------------------------------------ |
| 1       | 2026-08-11 | Initial epic draft — Part I design + Part II 11-phase execution plan (Phases 0–10). Not started. |
| 1.1     | 2026-08-11 | Incorporated architecture review: added `approval_revisions` history + `POST …/revise` endpoint, canonical `ApprovalResult` DTO, `approval_correlation_id` cross-system correlation, explicit Decision Execution Stages (aligned with future Epic 10 background jobs), execution-failure-vs-decision-status semantics, reserved `cancelled` status and approval lifecycle FSM diagram, `O(V+E)` graph-guard complexity note, `approval_decisions_total`/`hitl_resume_latency_ms`/`hitl_tool_execution_latency_ms` metrics and first-class approval tracing attributes, expanded adversarial/edge-case eval scenarios (Phase 8), Migration Impact Summary, Glossary, and Implementation Risks section. No architectural redesign — additive only. Not started. |
| 1.2     | 2026-08-11 | Phase 0 baseline audit complete — [post-mvp-v2-epic9-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic9-phase-0-baseline-audit.md). Part II only. |
| 1.3     | 2026-08-11 | Phase 1 HITL foundations complete — `app/ai/hitl/` package, migration `0010_hitl_tables`, `ToolDefinition.requires_approval`, `HITL_ENABLED` config, foundation tests. Part II only. |
| 1.4     | 2026-08-11 | Phase 2 agent tool-call approval gate complete — `ToolRunner` pause gate, `AgentApprovalService.pause()`, `AgentToolApprovalStore`, `approval_required` SSE, pause path tests. Part II only. |
| 1.5     | 2026-08-11 | Phase 3 agent approval decision & resume complete — `AgentApprovalService.revise()`/`decide()`/`approve_and_resume()`, `AgentExecutor.resume_from_approval()`, REST endpoints (`POST …/revise`, `POST …/decide`, `GET …/revisions`), `ChatStore.update_message`, 29-test verify suite; PR hardening (SSE stream cleanup, revision row lock, `AgentApprovalStore` protocol). Part II only. |
| 1.6     | 2026-08-11 | Phase 4 workflow approval enhancements complete — `edited_arguments`/`reason` on workflow decisions, `ApprovalDecisionRequest`, `ApprovalRevision` append on edit, `ApprovalResult` from `apply_decision`, optional approve/reject body; 39-test verify suite. Part II only. |
| 1.7     | 2026-08-11 | Phase 5 workflow graph guard & MCP/plugin coverage complete — `GraphValidator` HITL reachability guard (flag-gated), `WorkflowManager` wiring, MCP/plugin `requires_approval` integration tests; 37-test verify suite. Part II only. |
