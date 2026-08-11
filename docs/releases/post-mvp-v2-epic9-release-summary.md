# Post-MVP V2 Epic 09 Release Summary

**Release name:** Post-MVP V2 Epic 09 — Human-in-the-Loop (Phases 0–10)
**Release date:** 2026-08-12
**Validation:** Phase 10 final acceptance (see [post-mvp-v2-epic-09-human-in-the-loop.md](../plans/post-mvp-v2-epic-09-human-in-the-loop.md))
**Git commit (validation base):** `077cfc0` — Epic 09 Phases 9–10 validation & release

---

## Summary vs Epic 08

Epic 08 shipped the Plugin Architecture under `PLUGINS_ENABLED`. **V2 Epic 09 adds platform-wide Human-in-the-Loop (HITL)** under `app/ai/hitl/` (approval policy, agent tool-call pause/resume, workflow approval enhancements, graph guard, unified approvals REST API, observability, reference eval scenarios) behind `HITL_ENABLED` (default **off**), plus a **frontend approval inbox and inline chat decision UI** when the flag is on.

| Area | Epic 08 / pre-HITL platform | V2 Epic 09 |
| ---- | ----------------------------- | ---------- |
| Agent tool calls | Execute immediately when planned | Optional pause when `ApprovalPolicy.requires_approval()` |
| Workflow approvals | Epic 06 pause/decide only | Additive `edited_arguments`, `reason`, revision history |
| Graph validation | Structural + Epic 06 rules | Fail-closed reachability guard for approval-required tools |
| Management API | Plugin inventory | Authenticated `/api/approvals` inbox (route-level `503` when flag off) |
| Agent decide/resume | N/A | `POST /api/approvals/{id}/revise`, `POST …/decide` (SSE on approve) |
| Observability | Plugin spans/metrics | `approval_span`, HITL counters/histograms, `approval_correlation_id` |
| Eval CLI | `--level plugin` | Additive `--level hitl` (5 cases) |
| Frontend | Plugin inventory page | `/approvals` inbox + audit tabs; chat inline `ApprovalDecisionCard`; WorkflowsPage edit/reason |
| Chat / RAG / MCP / Memory / Voice / Agent / Tools / Workflows / Plugins | Stable | Unchanged when `HITL_ENABLED=false` |

---

## Delivered (Phases 0–10)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | `app/ai/hitl/` foundations, migration `0010_hitl_tables`, `HITL_ENABLED`, `ToolDefinition.requires_approval` |
| 2 | Agent tool-call approval gate (`ToolRunner` pause, `approval_required` SSE) |
| 3 | Agent decide/revise/resume (`AgentApprovalService`, REST endpoints, scratchpad snapshot) |
| 4 | Workflow approval enhancements (`edited_arguments`, `reason`, `ApprovalResult`) |
| 5 | Graph guard + MCP/plugin HITL coverage |
| 6 | Unified approvals REST API + audit aggregation |
| 7 | HITL observability (spans, metrics, correlation id) |
| 8 | Reference scenarios, `--level hitl` eval, adversarial integration tests |
| 9 | Frontend approval inbox (`ApprovalsPage`), chat `approval_required` UI, WorkflowsPage edit/reason |
| 10 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `ApprovalKind`, `ApprovalStatus`, `ProposedToolCall`, `AgentToolApproval`, `ApprovalResult`, `ApprovalRevision`, `ApprovalAuditEntry`, `ApprovalPolicy`, `AgentApprovalService`, `approvals_router`.

**Frontend deliverables (Phase 9):**

- `frontend/src/api/approvalsClient.ts` — list, detail, revisions, revise, reject, SSE approve
- `frontend/src/types/approvals.ts` — audit/revision/decide types
- `frontend/src/pages/ApprovalsPage.tsx` — pending inbox + history tabs with revision expander
- `frontend/src/components/ApprovalDecisionCard.tsx` — inline chat approval card
- Chat reducer/stream — `approval_required`, `waiting_approval`/`rejected` message statuses
- `WorkflowsPage` — `WorkflowPendingApprovalPanel` with editable JSON args and reason
- Protected route `/approvals`; nav link when `hitl_enabled`

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `HITL_ENABLED` | `false` | Off: no policy checks, no new tables read, Approval REST returns `503 feature_disabled`; `GraphValidator` reachability check skipped; frontend shows unavailable notice; Epic 08 hot paths unchanged. On: agent pause/resume, workflow edit/reason persistence, unified inbox API, graph guard, observability hooks, `/approvals` UI. |

Additional settings (see `backend-python/.env.example`): `HITL_REQUIRED_TOOL_NAMES`, `HITL_APPROVAL_TIMEOUT_HOURS` (documented only; enforcement deferred), `HITL_MAX_REASON_LENGTH`.

**Rollback:** set `HITL_ENABLED=false`; redeploy. Platform reverts to Epic 08 behaviour on hot paths.

---

## Breaking Changes

**None.** HITL is additive behind a master flag. Migration `0010_hitl_tables` adds tables/columns; unused when the flag is off.

---

## Migration / Upgrade Notes

1. Pull release; run `make db-migrate` (revision `0010_hitl_tables`).
2. Ensure `backend-python/.env.example` includes HITL settings (present since Phase 1).
3. To exercise locally: set `HITL_ENABLED=true`, flag tools via `ToolDefinition.requires_approval` or `HITL_REQUIRED_TOOL_NAMES`, restart API, open `/approvals` or trigger chat approval via flagged tool.
4. Reference eval tool: `send_notification` stub; run `uv run python -m app.ai.evaluation.cli --level hitl`.

---

## Manual E2E Smoke (documented procedure)

**Status:** Pending — **not a Phase 10 release gate**; scheduled as a **post-release operator smoke** once `HITL_ENABLED=true` is exercised in a local/staging environment. Merge acceptance relies on the automated gates in **Verification Metrics** below.

Run with `HITL_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `hitl_enabled: true`, `hitl_pending_approvals_count` |
| 2. Approvals nav | "Approvals" link visible; `/approvals` loads pending + history tabs |
| 3. Approvals API | `GET /api/approvals` lists pending/history; `503` when flag off |
| 4. Agent pause | Chat stream emits `approval_required`; inline decision card renders; row in `agent_tool_approvals` |
| 5. Decide | Approve streams continuation via SSE; reject ends turn; inbox reflects decision |
| 6. Workflow | Approval node panel accepts optional edited JSON args/reason; graph guard rejects unguarded sensitive tools |
| 7. Eval | `--level hitl` passes 5/5 when prerequisites enabled |
| 8. Flag off | `hitl_enabled: false`; approvals API `503`; `/approvals` unavailable notice; agent/workflow behave as pre-epic |

Automated CI covers HITL package, agent gate, workflow enhancements, router, observability, reference/adversarial scenarios, eval harness, and frontend approval client/page/chat tests.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Approval timeout enforcement (`hitl_approval_timeout_hours`) | `TODO(epic-10):` background job |
| Orphaned approval sweep / `cancelled` status transitions | `TODO(epic-10):` |
| Process crash between decision and resume | Documented V2 gap — resumable-but-stuck row |
| RBAC / team approval queues | Epic 11 |

---

## Verification Metrics (Phase 10 — 2026-08-12, updated with Phase 9)

| Gate | Result |
| ---- | ------ |
| Backend `make lint` + `format-check` + `typecheck` | **Clean** |
| Flag-on `make test-cov` | **1912 passed**, **88.95%** coverage on `app/` |
| HITL package `app/ai/hitl/` | **84%** (gate ≥80%) |
| Epic 09 test paths | **157 passed** (`tests/ai/hitl/`, agent approval, workflow approval/graph, hitl eval runner, approvals router) |
| `make eval --level all` | **15/15** passed |
| `--level hitl` | **5/5** passed |
| `--level plugin` | **3/3** passed |
| `--check-regression` | **No regressions detected** |
| Flag-off full suite (`HITL_ENABLED=false make test-cov`) | **1912 passed**, **88.99%** |
| Frontend lint + format + build | **Clean** |
| Frontend Vitest | **300** tests (50 files) — all pass (+9 vs pre-Phase-9 baseline) |

**Architectural invariants (Part I):** single `ApprovalPolicy` decision point; agent/workflow symmetrical pause primitives; CAS decisions; append-only revision history; graph fail-closed guard; MCP/plugin transparent coverage; flag-off parity confirmed; no high-cardinality metric labels.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-09-human-in-the-loop.md](../plans/post-mvp-v2-epic-09-human-in-the-loop.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic9-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic9-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic8-release-summary.md](./post-mvp-v2-epic8-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
