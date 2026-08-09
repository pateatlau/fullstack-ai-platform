# Post-MVP V2 Epic 07 Release Summary

**Release name:** Post-MVP V2 Epic 07 — Observability & Evaluation
**Release date:** 2026-08-10
**Validation:** Phase 10 final acceptance (see [post-mvp-v2-epic-07-observability-and-evaluation.md](../plans/post-mvp-v2-epic-07-observability-and-evaluation.md))
**Git commit (validation base):** `f17b186` — Epic 07 Phase 10 validation & release

---

## Summary vs Epic 06

Epic 06 shipped the Workflow Engine under `WORKFLOW_ENGINE_ENABLED`. **V2 Epic 07 adds platform-wide observability and evaluation extensions** under `app/ai/observability/` and extended `app/ai/evaluation/` (OpenTelemetry tracing, Prometheus metrics, approximate token-cost accounting, owner-scoped usage REST API, agent/workflow eval levels, regression baseline, frontend dashboard) behind `OBSERVABILITY_ENABLED` (default **off**).

| Area | Epic 06 / pre-observability platform | V2 Epic 07 |
| ---- | ------------------------------------ | ---------- |
| Tracing | None (deferred to Epic 07) | OTel spans across HTTP, LLM, prompt, tool, agent, RAG, memory, voice, workflow |
| Metrics | Pre-declared workflow metric names only | OTel counters/histograms + `GET /metrics` Prometheus exposition |
| Cost accounting | Token counts in `usage_events` only | Additive `cost_usd` + `pricing_version` via `CostCalculator` / `model_pricing.yaml` |
| Evaluation | `prompt` / `retrieval` / `e2e` levels | Additive `agent` / `workflow` levels + `RegressionChecker` + git-tracked baseline |
| Management API | Workflow REST | Authenticated `/api/observability/usage` (route-level `503` when flag off) |
| Frontend | Workflows dashboard | Additive `/observability` cost/usage dashboard + nav link (hidden when flag off or guest) |
| Chat / RAG / MCP / Memory / Voice / Agent / Tools / Workflows | Stable | Unchanged when `OBSERVABILITY_ENABLED=false` |

---

## Delivered (Phases 0–10)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | `TracerRegistry` / `MeterRegistry`, no-op fallback, trace/span log correlation, OTel config |
| 2 | `TracingLLMProvider`, `llm_span`, `prompt_span` |
| 3 | `tool_span`, `agent_span` (iteration + tool_call) |
| 4 | `rag_span`, `memory_span`, `voice_span`, `workflow_span` (background span links) |
| 5 | Migration `0008`, `CostCalculator`, `ModelPricingTable`, OTel metric instruments |
| 6 | Observability REST API, `/metrics`, `UsageAggregator`, `observability_enabled` health field |
| 7 | `AgentEvalRunner`, `WorkflowEvalRunner`, eval schema v2 + `run_environment`, CLI `--level agent`/`workflow`/`all` |
| 8 | `RegressionChecker`, `baseline-report.json`, expanded benchmark dataset (15 cases) |
| 9 | `ObservabilityPage`, `observabilityClient.ts`, health/nav integration |
| 10 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): span helpers, `TracerRegistry`, `MeterRegistry`, `CostCalculator`, `ModelPricingTable`, `UsageAggregator`; flag-guarded router and `/metrics`.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `OBSERVABILITY_ENABLED` | `false` | Off: OTel no-op providers; no spans/metrics/cost computation; Observability API returns `503 feature_disabled`; dashboard hidden; chat/RAG/MCP/memory/voice/agent/tool/workflow unchanged. On: tracing, metrics, cost accounting, usage API, and dashboard active. |

Requires PostgreSQL migration `0008_observability_usage_cost` when cost columns are needed. CI uses in-memory span exporters and fakes — no live OTLP backend required.

**Rollback:** set `OBSERVABILITY_ENABLED=false`; optionally downgrade migration `0008`; redeploy. Platform reverts to Epic 06 behaviour on hot paths.

---

## Breaking Changes

**None.** Observability is additive behind a master flag. Chat HTTP/SSE contracts unchanged. `usage_events` gains nullable `cost_usd` and `pricing_version` columns (migration `0008`).

---

## Migration / Upgrade Notes

1. Pull release; run `alembic upgrade head` (migration `0008_observability_usage_cost`).
2. Ensure `backend-python/.env.example` includes `OBSERVABILITY_*` and OTel settings (`OBSERVABILITY_ENABLED=false` by default).
3. To exercise locally: set `OBSERVABILITY_ENABLED=true`, ensure DB migrated, sign in, open `/observability`.
4. Optional: configure `OTEL_EXPORTER_OTLP_ENDPOINT` for OTLP export; default is console exporter in dev.
5. Override `otel_traces_sample_ratio` in staging/production (dev default is `1.0`).

---

## Manual E2E Smoke (documented procedure)

Run with `OBSERVABILITY_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `observability_enabled: true` |
| 2. Nav | "Observability" link visible when signed in; hidden for guests and when flag off |
| 3. Dashboard | `/observability` loads usage/cost summary with date range and `group_by` |
| 4. Usage API | `GET /api/observability/usage` returns owner-scoped aggregates |
| 5. Metrics | `GET /metrics` returns Prometheus text (aggregate counters/histograms only) |
| 6. Traces | Console or OTLP backend shows content-free spans on chat/agent/workflow activity |
| 7. Eval | `make eval --level all --check-regression` passes with agent/workflow prerequisites |
| 8. Flag off | `observability_enabled: false`; API `503`; chat unchanged from pre-epic |

Automated CI covers observability modules, router, eval/regression, and frontend with mocks/fakes.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Embedded trace/span visualization UI | Out of scope — use external OTLP backend |
| Vendor-specific APM SDKs | Out of scope |
| Distributed cross-service trace propagation | Out of scope (single-process platform) |
| Billing-grade cost reconciliation | Out of scope — approximate static pricing only |
| Scheduled/cron evaluation runs | Future epic |
| Historical evaluation trend storage | Out of scope |
| Alerting/paging integrations | Out of scope |
| Prompt/tool/message content in spans | Explicitly forbidden |

---

## Verification Metrics (Phase 10 — 2026-08-10)

| Gate | Result |
| ---- | ------ |
| Backend `make lint` + `format-check` + `typecheck` | **Clean** |
| Flag-off `make test-cov` | **1691 passed**, **89.21%** coverage on `app/` |
| Observability package `app/ai/observability/` | **89%** (gate ≥80%) |
| Epic 07 test paths | **126 passed** (79 observability + 15 router + 32 eval/regression) |
| `make eval --level all` | **15/15** passed (agent + workflow against Postgres/pgvector) |
| `--check-regression` | **No regressions detected** |
| Flag-off full suite (`OBSERVABILITY_ENABLED=false`) | **1691 passed** |
| Frontend lint + format + build | **Clean** |
| Frontend Vitest | **281** tests (46 files) — all pass |
| Observability frontend tests (2 files) | **11 passed** (`ObservabilityPage`, `observabilityClient`) |

**Functional validation (automated):** eight tracing domains (HTTP, LLM, prompt, tool, agent, RAG, memory, voice, workflow); cost accounting known/unknown models; owner-scoped usage API; aggregate `/metrics`; agent/workflow eval + regression checker; content-leakage guards; fail-open telemetry boundaries; owner isolation — covered by `tests/ai/observability/`, `tests/test_observability_router.py`, `tests/ai/evaluation/`.

**Performance validation:** no dedicated latency benchmarks in CI. Behavioural evidence from unit/integration tests: span helpers record `latency_ms`; cost computation at usage-write boundary; `/metrics` router tests under instrumented state. Fail-open design ensures telemetry never blocks business operations.

**Architectural invariants (Part I):** provider-agnostic OTel API only; no content in spans/metrics/logs; flag-off parity confirmed; metrics aggregate-only (no owner labels on `/metrics`); evaluation extends V1 harness in place; workflow background tasks use span links.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-07-observability-and-evaluation.md](../plans/post-mvp-v2-epic-07-observability-and-evaluation.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic7-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic7-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic6-release-summary.md](./post-mvp-v2-epic6-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
