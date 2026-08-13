# Post-MVP V2 Epic 10 Release Summary

**Release name:** Post-MVP V2 Epic 10 — Background Jobs (Phases 0–11)
**Release date:** 2026-08-13
**Validation:** Phase 11 final acceptance (see [post-mvp-v2-epic-10-background-jobs.md](../plans/post-mvp-v2-epic-10-background-jobs.md))
**Git commit (validation base):** `4d4ffd2` — Epic 10 Phase 11 validation & release (Phases 9–10 delivery base: `f684892`)

---

## Summary vs Epic 09

Epic 09 shipped Human-in-the-Loop under `HITL_ENABLED`. **V2 Epic 10 adds a Postgres-backed background job platform** under `app/ai/jobs/` (queue, worker, scheduler, five first-class handlers, REST API, observability, eval scenarios, frontend dashboard) behind `BACKGROUND_JOBS_ENABLED` (default **off**), closing five Phase 0–identified deferred-work gaps: Epic 02 queue-backed RAG indexing (opt-in), Epic 06 workflow approval timeout and run retention, Epic 07 scheduled evaluation runs, and Epic 09 HITL approval timeout plus orphaned-snapshot recovery — excluding Epic 05/06 in-process task migration (`schedule_extraction_task`, `schedule_run_task`, etc.), which remains out of scope.

| Area | Epic 09 / pre-jobs platform | V2 Epic 10 |
| ---- | --------------------------- | ---------- |
| HITL approval timeout | Config surface only | `hitl_approval_expiry_sweep` enforces agent + workflow surfaces |
| HITL orphan resume | Documented crash gap | `hitl_orphaned_snapshot_sweep` resumes or fail-safes |
| Workflow retention | Config surface only | `workflow_run_retention_cleanup` purges terminal runs |
| RAG indexing | `SyncIndexingRunner` only | Opt-in `QueueIndexingRunner` via `rag_indexing_runner=queue` |
| Evaluation | Manual CLI only | Opt-in `scheduled_evaluation_run` (disabled by default) |
| Job visibility | N/A | `GET /api/jobs`, schedules list, dead-letter retry; `/jobs` UI when flag on |
| Observability | HITL/workflow spans | `job.dispatch` span + six queue/handler metrics |
| Eval CLI | `--level hitl` | Additive `--level jobs` (6 cases) |

---

## Delivered (Phases 0–11)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | `app/ai/jobs/` foundations, migration `0012_background_jobs`, `BACKGROUND_JOBS_ENABLED` |
| 2 | `JobScheduler`, `PostgresJobScheduleStore`, migration `0013_background_job_schedules` (seeded schedules) |
| 3 | HITL expiry + orphan sweep handlers, migration `0014_hitl_expired_status_checks` |
| 4 | `workflow_run_retention_cleanup` + jobs self-retention |
| 5 | `QueueIndexingRunner`, migration `0015_document_upload_staging`, `rag_document_indexing` handler |
| 6 | `scheduled_evaluation_run` handler + schedule reconciliation |
| 7 | Jobs REST API + health fields |
| 8 | Job spans/metrics observability |
| 9 | Reference + adversarial scenarios, `--level jobs` eval, operator runbook |
| 10 | Frontend `/jobs` dashboard (jobs/schedules tabs, dead-letter retry) |
| 11 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `JobQueue`, `BackgroundJob`/`JobSchedule` schema, `JobHandler` signature.

**Frontend deliverables (Phase 10):**

- `frontend/src/api/jobsClient.ts` — list, detail, schedules, retry
- `frontend/src/types/jobs.ts`
- `frontend/src/pages/JobsPage.tsx` — jobs/schedules tabs, filters, dead-letter retry
- Route `/jobs`; nav gated on `background_jobs_enabled`
- `JobsPage.test.tsx` + `jobsClient.test.ts` (14 tests)

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `BACKGROUND_JOBS_ENABLED` | `false` | Off: no worker/scheduler tasks, Jobs REST returns `503 feature_disabled`, RAG stays sync regardless of `rag_indexing_runner`, HITL/workflow timeouts unenforced, frontend shows unavailable notice; Epic 09 hot paths unchanged. On: queue/worker/scheduler start, sweep/retention handlers active (HITL expiry, orphan sweep, workflow retention), REST + health fields, observability hooks, `/jobs` UI — scheduled evaluation additionally requires `evaluation_schedule_enabled=true` (default off); queue-backed RAG additionally requires `rag_indexing_runner=queue` (default `sync`). |

Additional settings (see `backend-python/.env.example`): worker/scheduler poll intervals, claim lease, handler timeout, retry policy, `rag_indexing_runner`, `evaluation_schedule_enabled`, `hitl_orphan_sweep_grace_seconds`, `background_jobs_retention_days`.

**Rollback:** set `BACKGROUND_JOBS_ENABLED=false`; redeploy. Platform reverts to Epic 09 behaviour on hot paths.

---

## Breaking Changes

**None.** Background Jobs is additive behind a master flag. Migrations `0012`–`0015` add tables/columns; unused when the flag is off.

---

## Migration / Upgrade Notes

1. Pull release; run `make db-migrate` (revisions through `0015_document_upload_staging`).
2. Ensure `backend-python/.env.example` includes Background Jobs settings (present since Phase 1).
3. To exercise locally: set `BACKGROUND_JOBS_ENABLED=true`, restart API, open `/jobs` or inspect `GET /api/health` for queue depth fields.
4. Optional RAG queue path: set `rag_indexing_runner=queue` (requires flag on).
5. Optional scheduled eval: set `evaluation_schedule_enabled=true`.
6. Reference eval: `BACKGROUND_JOBS_ENABLED=true uv run python -m app.ai.evaluation.cli --level jobs`.

---

## Closed deferred-work markers

All `TODO(epic-9)` / `TODO(epic-10)` markers identified in Phase 0 have been removed from application code:

| Gap | Handler / deliverable | Former marker location |
| --- | --------------------- | ---------------------- |
| RAG queue indexing | `QueueIndexingRunner` + `rag_document_indexing` | `app/ai/rag/indexing/__init__.py`, `sync_runner.py` |
| Workflow approval timeout | `hitl_approval_expiry_sweep` | `app/ai/workflow/nodes/approval_node.py` |
| HITL approval timeout | `hitl_approval_expiry_sweep` | Epic 09 config-only |
| Orphaned approval resume | `hitl_orphaned_snapshot_sweep` | Epic 09 snapshot cleanup gap |
| Workflow run retention | `workflow_run_retention_cleanup` | Epic 06 config-only |
| Scheduled evaluation | `scheduled_evaluation_run` | Epic 07 deferred cron runs |

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Cron-expression scheduling | Interval-seconds only in V2 |
| Multi-replica worker orchestration | Claim-safe; single in-process worker per instance |
| Epic 06/05 in-process task migration | Out of scope — `schedule_run_task`, `schedule_extraction_task` unchanged |
| `ApprovalStatus.CANCELLED` sweep | Still deferred — no V2 orphan path |
| RBAC-scoped job visibility | Epic 11 |
| Job payload editing on retry | Not supported — re-enqueue only |

---

## Verification Metrics (Phase 11 — 2026-08-13)

| Gate | Result |
| ---- | ------ |
| Backend `make lint` + `format-check` + `typecheck` | **Clean** |
| Flag-on `make test-cov` | **2141 passed**, **89.19%** coverage on `app/` |
| Jobs package `app/ai/jobs/` | **91%** (gate ≥80%) |
| Epic 10 test paths | **112+ passed** (`tests/ai/jobs/`, `test_jobs_router.py`, `test_queue_indexing_runner.py`) |
| `make eval --level all` | **15/15** passed |
| `--level jobs` (flag on) | **6/6** passed |
| `--level hitl` | **5/5** passed |
| `--level plugin` | **3/3** passed |
| `--check-regression` | **3 soft latency regressions** (retrieval, e2e, workflow — within documented soft targets) |
| Flag-off full suite (`BACKGROUND_JOBS_ENABLED=false make test-cov`) | **2141 passed**, **89.22%** |
| Frontend lint + format + build | **Clean** |
| Frontend Vitest | **318** tests (52 files) — all pass |

**Architectural invariants (Part I):** claim-and-lease queue; handler idempotency; transaction boundaries (claim commits before dispatch); idempotency keys for scheduler ticks; payload/result redaction; no `job_id` metric labels; flag-off parity confirmed.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-10-background-jobs.md](../plans/post-mvp-v2-epic-10-background-jobs.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic10-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic10-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic9-release-summary.md](./post-mvp-v2-epic9-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
