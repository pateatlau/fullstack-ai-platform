---
epic: v2-07
title: Observability & Evaluation
status: not_started
version: 1.1
depends_on: [v2-06]
provides:
  [
    TracerRegistry,
    NoOpTracer,
    llm_span,
    prompt_span,
    tool_span,
    agent_span,
    rag_span,
    memory_span,
    voice_span,
    workflow_span,
    CostCalculator,
    ModelPricingTable,
    UsageAggregator,
    ObservabilityStore,
    EvalCase,
    AgentEvalRunner,
    WorkflowEvalRunner,
    RegressionChecker,
    RegressionResult,
    OBSERVABILITY_ENABLED,
    observability_router,
  ]
feature_flags: [OBSERVABILITY_ENABLED]
packages: [app/ai/observability, app/ai/evaluation]
test_paths:
  [
    tests/ai/observability,
    tests/ai/evaluation,
    tests/test_observability_router.py,
    frontend/src/pages/ObservabilityPage.test.tsx,
    frontend/src/api/observabilityClient.test.ts,
  ]
---

# Post-MVP V2 Epic 07 — Observability & Evaluation

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "7. Observability & Evaluation"

**Predecessor:** [Epic 06 — Workflow Engine](./post-mvp-v2-epic-06-workflow-engine.md)

---

# Part I — Design

## Objective

Give every existing pipeline — chat, RAG, MCP, voice, memory, agent, tools, and workflows — a single, provider-agnostic observability layer: distributed tracing (OpenTelemetry), prompt/tool/agent/workflow span coverage, approximate token-cost accounting, and an evaluation harness capable of detecting quality and latency regressions before they reach production.

Epic 06 explicitly deferred "OTel spans, prompt regression, evaluation harness" and pre-declared the metric names this epic must emit (`workflow_runs_started`, `workflow_node_execution_latency_ms`, etc.). This epic delivers that promise for the whole platform, not just workflows, and extends the V1 evaluation framework (`app/ai/evaluation/`) that already runs `prompt`/`retrieval`/`e2e` levels via `make eval`, rather than building a parallel one.

**Delivers:** An OpenTelemetry tracing foundation with a feature-flagged no-op fallback; spans across LLM calls, prompt rendering, tool execution, the agent loop, RAG retrieval, memory operations, voice sessions, and workflow run/node transitions; OTel counters/histograms plus a Prometheus exposition endpoint; approximate per-model cost accounting persisted onto the existing `usage_events` table; an authenticated, owner-scoped Observability REST API and dashboard; and an evaluation framework extension adding `agent`/`workflow` eval levels, a git-tracked regression baseline, and expanded benchmark datasets.

**Does not ship:** Vendor-specific tracing/APM SDKs (Datadog, New Relic, etc.) or auto-instrumentation bundles; distributed cross-service trace propagation (this platform is a single backend process); an embedded trace/span visualization UI (use any OTLP-compatible backend); real-time or billing-grade cost reconciliation with provider invoices; scheduled/cron-triggered evaluation runs; historical evaluation trend storage; alerting/paging integrations; prompt/tool/message content capture for tracing or debugging, even opt-in.

Capabilities:

- OpenTelemetry
- Structured logging (trace/span correlation)
- Prompt tracing
- Tool tracing
- Token/cost metrics
- Prompt regression
- RAG evaluation
- Benchmark datasets

Observability is additive instrumentation. When disabled, every existing chat, RAG, MCP, voice, memory, agent, tool, and workflow code path executes identically — same return values, same latency characteristics, same log output — with zero spans and zero metrics emitted.

---

## Design Principles

- Platform-first — one tracer/meter/cost accessor used everywhere, not one per pipeline
- Composition over coupling — instruments existing call sites; never re-implements the thing it observes
- Provider-agnostic (OTel API only; exporter is configuration, not code)
- Non-blocking — a tracing, metrics, or cost-calculation failure never fails the underlying operation
- Zero content leakage — spans/metrics/cost records carry identifiers, counts, durations, and status only
- Explainable, not exhaustive — favor a small number of well-chosen spans/metrics over exhaustive instrumentation
- Feature-flag rollout
- Avoid over-engineering — reuse the existing V1 evaluation harness and `usage_events` table instead of building parallel systems

---

## Scope

### In Scope

- OpenTelemetry `TracerProvider`/`MeterProvider` setup with a no-op fallback when disabled
- Spans for: HTTP requests, LLM provider calls, prompt rendering, tool execution, the agent ReAct loop, RAG retrieval, memory retrieval/extraction, voice sessions, and workflow run/node transitions
- Trace ID / span ID correlation into the existing structured logging context (`app/core/logging.py`)
- OTel counters/histograms for the metric names Epic 06 pre-declared, plus LLM/tool/agent equivalents
- Prometheus exposition endpoint (`GET /metrics`) for the above counters/histograms
- Approximate per-model token cost accounting (`CostCalculator`, versioned pricing table) persisted onto `usage_events`
- Owner-scoped Observability REST API for usage/cost summaries
- Evaluation framework extension: `agent` and `workflow` eval levels reusing `DefaultAgent`/`WorkflowManager`
- Prompt/RAG/agent/workflow regression detection against a git-tracked JSON baseline
- Expanded benchmark dataset coverage (tool-calling, agent, and workflow cases)
- Frontend Observability dashboard (cost/usage summary only)

### Out of Scope

- Vendor-specific APM SDKs or auto-instrumentation bundles
- Distributed, cross-process/service trace propagation
- Embedded trace/span explorer UI
- Real-time or billing-grade cost reconciliation
- Scheduled/cron-triggered evaluation runs (Epic 10 Background Jobs)
- Historical evaluation trend storage/dashboard
- Alerting/paging integrations
- Prompt/tool/message content capture (even opt-in, even for debugging)
- Multi-agent or multi-workflow aggregate trace views

---

## High-Level Architecture

```text
Every existing pipeline
(Chat | RAG | MCP | Voice | Memory | Agent | Tools | Workflow)
                 │
        Span helpers (llm_span, tool_span, prompt_span,
        agent_span, rag_span, memory_span, voice_span, workflow_span)
                 │
         TracerRegistry (OTel Tracer / NoOpTracer)
                 │
   ┌─────────────┼──────────────────┐
   ▼             ▼                  ▼
Console       OTLP HTTP        MeterProvider
Exporter      Exporter        (counters/histograms)
  (dev)     (OTEL_EXPORTER_       │
             OTLP_ENDPOINT)  Prometheus exposition (`/metrics`)

Provider call site (ProviderFactory)
        │
CostCalculator (ProviderUsage + ModelPricingTable → cost_usd)
        │
usage_events (existing table, + cost_usd, + pricing_version)
        │
UsageAggregator ── ObservabilityStore ── GET /api/observability/usage

Evaluation CLI (make eval)
        │
EvalDataset ── PromptEvalRunner / RetrievalEvalRunner / EndToEndEvalRunner
             ── AgentEvalRunner (NEW) / WorkflowEvalRunner (NEW)
        │
EvalRunReport ── RegressionChecker (NEW) ── baseline-report.json (git-tracked)
```

---

## Locked Architectural Decisions

| Topic                        | Decision                                                                                                                                                                                                                                                                                                                                                                                                       | Deferred to                                                           |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Tracing SDK                  | OpenTelemetry API/SDK only; console exporter by default (dev), OTLP/HTTP exporter when `otel_exporter_otlp_endpoint` is configured; no vendor SDK imported into core packages                                                                                                                                                                                                                                  | Vendor-specific auto-instrumentation bundle → future                  |
| Flag semantics               | `OBSERVABILITY_ENABLED=false` installs a process-wide no-op `TracerProvider`/`MeterProvider` (OTel API's own `NoOpTracer`, not a bespoke reimplementation) — zero spans, zero metrics, zero overhead beyond a cheap flag check; every instrumented call site's return value and behavior is unchanged                                                                                                          | —                                                                     |
| Span/metric content policy   | Attributes carry identifiers, counts, durations, provider/model names, and status only; reuses `app.core.logging.sanitize_value` for any dynamic attribute; prompt text, tool arguments/results, and chat message content are never attached to a span or metric                                                                                                                                               | Opt-in payload capture for debugging → future                         |
| Trace context propagation    | Per-HTTP-request trace context flows through OTel's built-in context + the existing `correlation_id` middleware (`request_id` and `trace_id`/`span_id` both bound into log context). Workflow runs that continue on an in-process `asyncio.Task` after the triggering request completes start a new root span carrying the original `trace_id` as a span link (best-effort correlation, not a hard dependency) | Fully distributed cross-service trace propagation → future            |
| Cost accounting              | Static, versioned per-(provider, model) pricing table computed at usage-record time from the existing `ProviderUsage` counts; approximate, not billing-grade (consistent with the existing `usage_events` "not billing" comment); unknown provider/model → `cost_usd=NULL`, never blocks the usage write                                                                                                       | Real-time reconciliation with provider invoices → future              |
| Metrics exposition           | OTel `MeterProvider` backed by a Prometheus reader, exposed at `GET /metrics` in Prometheus text format; contains only aggregate counters/histograms (no owner-identifying labels); owner-scoped cost/usage detail is served only via the authenticated REST API, never `/metrics`                                                                                                                             | Push-based metrics backend (CloudWatch, Datadog) integration → future |
| Evaluation framework         | Extends `app/ai/evaluation/` (`datasets.py`, `runners.py`, `metrics.py`, `report.py`, `cli.py`) in place; no parallel evaluation framework. New `agent`/`workflow` levels reuse `DefaultAgent`/`WorkflowManager` exactly as the existing `e2e` level reuses `RAGService`                                                                                                                                       | Continuous/scheduled evaluation runs → Epic 10                        |
| Regression baseline          | A git-tracked JSON snapshot (`tests/data/evaluation/baseline-report.json`) produced by the existing `EvalRunReport` serializer; `RegressionChecker` is a pure function comparing a new report to the baseline within configurable pass-rate/latency tolerances — no baseline persisted in Postgres                                                                                                             | Historical trend storage/dashboard → future                           |
| Dashboard scope              | The frontend Observability dashboard shows only DB-native, owner-scoped cost/usage summaries sourced from `usage_events`; trace/span visualization is explicitly out of scope — operators point any OTLP-compatible backend (Jaeger, Tempo, Grafana, Datadog) at the configured exporter                                                                                                                       | Embedded trace explorer UI → future                                   |
| Instrumentation failure mode | Any exception raised while creating a span, recording a metric, or computing cost is caught, logged at `warning`, and the underlying operation proceeds unaffected (fail-open)                                                                                                                                                                                                                                 | —                                                                     |
| Trace sampling               | `ParentBased(TraceIdRatioBased(otel_traces_sample_ratio))` sampler; ratio defaults are environment-dependent (see § Trace Sampling Strategy) — 100% sampling is a dev-only default, never assumed safe in production                                                                                                                                                                                             | Tail-based / adaptive sampling → future                              |
| Metric cardinality            | Metric labels are restricted to a fixed, low-cardinality allowlist (see § Metric Cardinality Policy); no per-user, per-session, per-request, or per-trace label is ever attached to a counter or histogram                                                                                                                                                                                                      | —                                                                     |

---

## Trace Sampling Strategy

Sampling controls trace volume/cost without disabling observability. The platform uses OTel's standard `ParentBased(TraceIdRatioBased(ratio))` sampler — a child span always follows its parent's sampling decision, and root spans are sampled at `otel_traces_sample_ratio`.

| Environment | Recommended `otel_traces_sample_ratio` | Rationale |
| ----------- | --------------------------------------- | --------- |
| Development / local | `1.0` (100%) | Full visibility while iterating; low volume |
| Staging / CI | `0.25` (25%) | Enough signal to catch regressions without excessive exporter/storage load |
| Production | `0.05` (5%) | Representative sampling at sustained volume; raise temporarily when investigating an incident |

`otel_traces_sample_ratio` defaults to `1.0` (dev-safe) in `app/core/config.py`; deployment configuration (`.env` per environment) **must** override it for staging/production. This is called out explicitly so a 100% default is never silently carried into production. Sampling never affects metrics or cost accounting — `CostCalculator` and the OTel `Meter` instruments record every request regardless of trace sampling decisions, since cost/metric accuracy must not depend on trace volume.

---

## High-Level Flow

**Request tracing**

Incoming HTTP request
→ `correlation_id_middleware` mints/propagates `request_id`
→ OTel auto-creates a root HTTP span (`http.server`) with `trace_id`/`span_id` bound into log context alongside `request_id`
→ Downstream pipeline code opens child spans via the span helpers as it calls providers, prompts, tools, RAG, memory, voice, agent, or workflow subsystems
→ Spans close with duration + status; exporter ships them to console (dev) or the configured OTLP endpoint
→ Response returned; log context cleared (unchanged from Epic 06 baseline)

**Cost accounting**

LLM call via `ProviderFactory.get_provider()`
→ (if enabled) provider wrapped by a tracing/cost decorator
→ `ProviderUsage` returned by the concrete provider
→ `CostCalculator.price(provider, model, usage) -> cost_usd | None`
→ `SqlUsageStore.record(...)` persists `usage_events` row including `cost_usd`, `pricing_version`
→ `UsageAggregator` later aggregates rows for `GET /api/observability/usage`

**Evaluation & regression**

`make eval` (unchanged entry point)
→ `EvalDataset` loaded (extended schema: `agent`, `workflow` cases alongside existing `prompt`/`retrieval`/`e2e`)
→ Runners execute each case (existing runners unchanged; new `AgentEvalRunner`/`WorkflowEvalRunner` added)
→ `EvalRunReport` produced (existing serializer unchanged, extended with new levels)
→ `RegressionChecker.compare(report, baseline)` (new, opt-in via `--check-regression`)
→ CLI exits non-zero on any failing case **or** a regression finding beyond tolerance

---

## End-to-End Sequence

```text
Client
 │
 │ POST /api/chat  (or any instrumented pipeline entrypoint)
 ▼
correlation_id_middleware
 │  binds request_id + (if enabled) trace_id/span_id into log context
 ▼
Router → Service (ChatService / RAGService / DefaultAgent / WorkflowExecutor / ...)
 │
 ├── prompt_span → span "prompt.render" ──► PromptManager.render()
 │
 ├── llm_span → span "llm.complete" ──► LLMProvider.complete_chat() / stream_chat()
 │        │     (streaming: span covers whole call; ends after final chunk)
 │        └── CostCalculator.price(usage) → SqlUsageStore.record(cost_usd=...)
 │              (usage/cost recorded from the terminal chunk only)
 │
 ├── tool_span → span "tool.execute" ──► ToolExecutor.execute()
 │
 ├── agent_span → span "agent.iteration" ──► DefaultAgent reasoning loop
 │
 ├── rag_span "rag.retrieve" / memory_span "memory.retrieve"|"memory.extract" / voice_span "voice.session"
 │
 └── workflow_span → span "workflow.run"|"workflow.node" ──► WorkflowExecutor.step()
          (background asyncio.Task — root span links back to originating trace_id)
 │
 ▼
Spans close → exporter (console | OTLP) ── Meter records counters/histograms
 │
 ▼
Response returned to client

Independently:
Operator / CI
 │
 │ GET /api/observability/usage?since=...&group_by=day
 ▼
ObservabilityStore → UsageAggregator → owner-scoped cost/usage summary

Prometheus scraper
 │
 │ GET /metrics
 ▼
Aggregate counters/histograms (no owner data)
```

---

## Storage Architecture

```text
CostCalculator
      │
ModelPricingTable (static, versioned config)
      │
SqlUsageStore.record(..., cost_usd, pricing_version)
      │
usage_events (existing table — extended, not replaced)
      │
UsageAggregator
      │
ObservabilityStore
      │
GET /api/observability/usage
```

No new domain store/Protocol is introduced for tracing or metrics — OpenTelemetry's `TracerProvider`/`MeterProvider` **is** the abstraction, matching the "use the platform standard, don't reinvent it" principle. The only new persistence surface is the additive `usage_events` extension.

**`ObservabilityStore` is provisional.** It is included in the design as the router-facing façade (mirroring the `WorkflowStore`-style separation elsewhere in the platform), but it is not assumed to carry business logic up front. If, during Phase 6 implementation, it turns out to be a pure pass-through over `UsageAggregator` with no orchestration or aggregation responsibility of its own, it should be collapsed — the router depends on `UsageAggregator` directly instead of preserving an empty indirection layer. This is a Phase 6 implementation decision, not a Part I lock; either outcome satisfies the acceptance criteria in this document.

---

## Cost Accounting Contract

`CostCalculator` is the single place token counts become a dollar estimate.

Responsibilities:

- Look up a `(provider, model)` entry in the active `ModelPricingTable` (per-1K-token input/output rates)
- Compute `cost_usd = (prompt_tokens / 1000) * input_rate + (completion_tokens / 1000) * output_rate`
- Return `None` (not zero, not an exception) when the provider/model has no pricing entry, or when `ProviderUsage` fields are `None` — cost is unknown, not free
- Stamp every priced record with the pricing table's `pricing_version` so historical rows remain interpretable after a price update
- Never raise past the caller — pricing errors are fail-open (`cost_usd=NULL`), consistent with the existing "estimated" `token_source` fallback pattern

`ModelPricingTable` is a static, versioned mapping loaded from application configuration (not a database table) — it changes on release, not at runtime, mirroring how `workflow_*` tuning defaults are configuration rather than DB-editable state.

**Pricing table lifecycle:**

- A `usage_events.cost_usd` value is computed **once**, at write time, and is never recalculated retroactively when `ModelPricingTable` is later updated (a price change is not backfilled onto historical rows).
- `pricing_version` is the permanent, immutable record of which pricing table produced a given row's `cost_usd`; it is what makes a historical estimate interpretable after the table has since changed (e.g., "this row used the `2026-08` table, not today's").
- Comparing cost trends across a pricing-version boundary is a reporting-layer concern (`UsageAggregator` surfaces `pricing_version` alongside aggregates); it is not solved by mutating stored data.

---

## Metric Cardinality Policy

Prometheus-backed metrics degrade badly under high label cardinality (unbounded label values create unbounded time series). Every OTel metric instrument this epic defines (Phase 5) uses **only** a fixed, low-cardinality label set.

| Allowed labels | Forbidden labels |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `provider` (e.g. `openai`, `anthropic`) | `user_id`, `guest_id` |
| `model` (e.g. `gpt-4o`, `claude-opus`) | `session_id`, `request_id`, `trace_id`, `span_id` |
| `tool_name` | `workflow_run_id`, `workflow_node_id` (node **type**, not node id, is allowed) |
| `workflow_type` / `node_type` | `message_id`, conversation identifiers |
| `status` (e.g. `succeeded`, `failed`, `skipped`) | Any other free-form or unbounded-cardinality value (prompt text, error messages, arbitrary IDs) |

This distinguishes **metrics** (Prometheus labels — must stay low-cardinality) from **spans** (OTel trace attributes, which tolerate higher cardinality like `run_id` because each trace is stored/queried individually, not aggregated into a time series). A `run_id` may appear as a **span attribute** (Part I § Tracing Domains) but must never appear as a **metric label**. Phase 5 tests explicitly assert this boundary.

---

## Evaluation Framework Extension

Builds on the existing `EvalLevel = Literal["prompt", "retrieval", "e2e"]` (`app/ai/evaluation/datasets.py`) rather than replacing it.

| New level  | Runner               | Reuses                                                                                             | Skip condition                                                                           |
| ---------- | -------------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `agent`    | `AgentEvalRunner`    | `DefaultAgent`, fake provider/tools (same pattern as `_EvalLLMProvider`)                           | `AGENT_RUNTIME_ENABLED=false` → case marked `skipped` with a clear reason, not a failure |
| `workflow` | `WorkflowEvalRunner` | `WorkflowManager`, `PostgresWorkflowStore` (same Postgres-availability check as `retrieval`/`e2e`) | `WORKFLOW_ENGINE_ENABLED=false` or Postgres unavailable → `skipped`                      |

Each new case type extends `EvalCase`/`EvalDataset` parsing (`_parse_agent_case`, `_parse_workflow_case`) with the same "fail fast on malformed dataset" validation style as existing case parsers. `EvalCaseResult` gains only the fields needed for these levels (e.g., `tool_calls_correct: bool | None`, `terminal_status: str | None`) — no unrelated schema churn.

`RegressionChecker` is a pure function: `compare(current: EvalRunReport, baseline: EvalRunReport, *, pass_rate_tolerance_pct: float, latency_tolerance_pct: float) -> RegressionResult`. It flags:

- Any case that passed in the baseline and fails now (always a hard regression, regardless of tolerance)
- A per-level pass-rate drop beyond `observability_regression_pass_rate_tolerance_pct`
- A per-level mean-latency increase beyond `observability_regression_latency_tolerance_pct`

`RegressionResult` is JSON-serializable and printed alongside the existing console summary; it never mutates the baseline file itself — updating the baseline is an explicit `--update-baseline` CLI action, never automatic.

**Reproducibility metadata:** A pass/fail or a regression finding is only interpretable if the conditions that produced it are known. `EvalRunReport` (`app/ai/evaluation/report.py`) is extended to capture, per case where applicable, the execution metadata that could explain a difference between two runs:

| Field | Purpose |
| ---------------- | ------------------------------------------------------------------------------- |
| `model` | The concrete model used for the case (already implied by `settings_snapshot`, now recorded per-case for `agent`/`e2e`/`workflow` levels where a model override is possible) |
| `model_version` | Provider-reported model version/snapshot id, when the provider exposes one |
| `temperature` | Sampling temperature used for the case |
| `seed` | Deterministic seed, when the provider supports one (`None` otherwise — never fabricated) |
| `prompt_version` | The `PromptManager` category/name/version rendered for the case |

This metadata is additive to the existing `EvalCaseResult`/`EvalRunReport` schema (`schema_version` bump, not a breaking change) and is included in `baseline-report.json` so a future regression finding can be explained by "the model/prompt/temperature changed" rather than left ambiguous.

---

## Package Structure

```text
app/
└── ai/
    ├── observability/
    │   ├── __init__.py
    │   ├── tracing/
    │   │   ├── provider.py         # TracerRegistry: real OTel TracerProvider or NoOpTracer
    │   │   ├── spans.py            # llm_span, prompt_span, tool_span, agent_span,
    │   │   │                       #   rag_span, memory_span, voice_span, workflow_span
    │   │   └── provider_wrapper.py # TracingLLMProvider decorator around LLMProvider
    │   ├── metrics/
    │   │   ├── meter.py            # MeterRegistry: real OTel MeterProvider or no-op
    │   │   └── instruments.py      # counter/histogram definitions (names match Epic 06 § Observability)
    │   ├── cost/
    │   │   ├── pricing.py          # ModelPricingTable (static, versioned)
    │   │   └── calculator.py       # CostCalculator
    │   ├── aggregation/
    │   │   └── usage_aggregator.py # UsageAggregator (owner-scoped usage/cost queries)
    │   └── exceptions.py
    └── evaluation/                 # EXTENDED, not new
        ├── datasets.py             # + agent/workflow EvalCase parsing
        ├── runners.py              # + AgentEvalRunner, WorkflowEvalRunner
        ├── report.py               # + agent/workflow summary fields
        ├── regression.py           # NEW — RegressionChecker, RegressionResult
        └── cli.py                  # + --check-regression, --update-baseline flags

app/routers/observability.py            # NEW — authenticated usage/cost API + GET /metrics
app/schemas/observability.py            # NEW — request/response schemas
app/db/usage.py                         # extend — cost_usd/pricing_version on record()
app/db/models.py                        # modify — UsageEvent + cost_usd/pricing_version columns
alembic/versions/0008_observability_usage_cost.py   # NEW
app/core/config.py                      # extend — OBSERVABILITY_ENABLED + OTel/cost/regression settings
app/ai/deps.py                          # extend — Observability DI factories
app/main.py                             # modify — tracer/meter bootstrap, mount router, /metrics route
app/middleware/correlation_id.py        # modify — bind trace_id/span_id alongside request_id
tests/data/evaluation/baseline-report.json  # NEW — git-tracked regression baseline
```

---

## Core Components

- TracerRegistry
- MeterRegistry
- Span helpers (`llm_span`, `prompt_span`, `tool_span`, `agent_span`, `rag_span`, `memory_span`, `voice_span`, `workflow_span`)
- TracingLLMProvider (decorator)
- CostCalculator
- ModelPricingTable
- UsageAggregator
- ObservabilityStore (thin read façade used by the router)
- AgentEvalRunner
- WorkflowEvalRunner
- RegressionChecker

---

## Component Responsibilities

| Component          | Responsibility                                                                                                         | Inputs                                                   | Outputs                            | Dependencies                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------- | ------------------------------------------------- |
| TracerRegistry     | Provides the process-wide OTel `Tracer` (real or no-op) based on `OBSERVABILITY_ENABLED`                               | Settings                                                 | `Tracer`                           | OpenTelemetry SDK                                 |
| MeterRegistry      | Provides the process-wide OTel `Meter` (real or no-op) and the Prometheus reader                                       | Settings                                                 | `Meter`, Prometheus registry       | OpenTelemetry SDK                                 |
| Span helpers       | Thin context managers that open a named span, attach sanitized attributes, and record status/duration                  | Domain-specific args (provider, tool name, node id, ...) | Closed span                        | TracerRegistry, `app.core.logging.sanitize_value` |
| TracingLLMProvider | Wraps a concrete `LLMProvider` to emit `llm_span`s and record token/latency metrics without touching provider adapters | Wrapped `LLMProvider`                                    | Same `LLMProvider` interface       | Span helpers, MeterRegistry                       |
| CostCalculator     | Converts `ProviderUsage` into an approximate `cost_usd`                                                                | provider, model, `ProviderUsage`                         | `float \| None`, `pricing_version` | ModelPricingTable                                 |
| ModelPricingTable  | Static per-(provider, model) pricing lookup                                                                            | Settings/config                                          | Pricing entry or `None`            | —                                                 |
| UsageAggregator    | Owner-scoped aggregation queries over `usage_events` (by day / provider / model)                                       | owner id, date range, group-by                           | Usage/cost summary rows            | SQLAlchemy `AsyncSession`                         |
| ObservabilityStore | Read façade the router depends on (keeps router thin, mirrors `WorkflowStore`-style separation) — **provisional; collapse into `UsageAggregator` in Phase 6 if it adds no logic of its own (see Storage Architecture)** | Aggregation requests                                     | Summary DTOs                       | UsageAggregator                                   |
| AgentEvalRunner    | Runs an `agent`-level eval case through `DefaultAgent` with fake provider/tools                                        | `EvalCase`                                               | `EvalCaseResult`                   | `DefaultAgent`, `AGENT_RUNTIME_ENABLED`           |
| WorkflowEvalRunner | Runs a `workflow`-level eval case through `WorkflowManager` to a terminal run status                                   | `EvalCase`                                               | `EvalCaseResult`                   | `WorkflowManager`, `WORKFLOW_ENGINE_ENABLED`      |
| RegressionChecker  | Compares a new `EvalRunReport` against the git-tracked baseline                                                        | Current + baseline reports, tolerances                   | `RegressionResult`                 | —                                                 |

---

## Span Naming Convention

Every span helper opens a span under a **fixed, dot-namespaced name** — `{domain}.{action}` — regardless of the dynamic provider/tool/node involved. The dynamic detail (which provider, which tool, which node) is an **attribute**, never part of the name. This keeps span names low-cardinality and consistent across every OTLP-compatible backend, and is a distinct concern from the § Metric Cardinality Policy (which governs Prometheus *labels*, not trace *span names*).

| Helper | Span name | Fixed regardless of |
| -------------- | ------------------------------------------- | ------------------------------------------------------ |
| `prompt_span` | `prompt.render` | category / name / version (attributes) |
| `llm_span` | `llm.complete` | provider / model / streaming (attributes) |
| `tool_span` | `tool.execute` | tool name (attribute) |
| `agent_span` | `agent.iteration`, `agent.tool_call`, `agent.reflection` | iteration index / tool name (attributes) |
| `rag_span` | `rag.retrieve` | top_k / retrieved_count (attributes) |
| `memory_span` | `memory.retrieve`, `memory.extract` | — |
| `workflow_span` | `workflow.run`, `workflow.node` | run id / node type / attempt (attributes) |
| `voice_span` | `voice.session` | — |

Every span helper function signature in Part I (e.g. `llm_span(provider, model, streaming)`) takes these dynamic values as **arguments used to populate attributes**, not to construct the span name.

---

## Tracing Domains

### LLM Spans — `llm.complete`

`llm_span(provider, model, streaming: bool)` wraps every `LLMProvider.complete_chat` / `complete_chat_with_tools` / `stream_chat` call via `TracingLLMProvider`, installed once in `ProviderFactory.get_provider()` rather than in each provider adapter. Attributes: `provider`, `model`, `streaming`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`, `latency_ms`. No message content.

**Streaming span lifecycle** (`stream_chat`): a single `llm.complete` span covers the entire stream, not one span per chunk.

1. The span **starts** immediately before the provider request is issued (before the first chunk is awaited).
2. Each streamed chunk is consumed inside the span's context but does **not** create a child span or record usage — chunks are not individually observable events, only the overall call is.
3. The span **ends** only after the final streamed chunk has been emitted (i.e., after the async generator is exhausted / `finish_reason` is set).
4. `prompt_tokens`/`completion_tokens`/`total_tokens` and the resulting `cost_usd` are recorded **only from the terminal chunk's usage payload** (mirrors the existing streaming usage-handling pattern); intermediate chunks never trigger a cost calculation or a metric emission.

### Prompt Spans — `prompt.render`

`prompt_span(category, name, version)` wraps `PromptManager.render()`. Attributes: `category`, `name`, `version`, `variable_count`, `rendered_length_chars`. Never the rendered text itself.

### Tool Spans — `tool.execute`

`tool_span(tool_name)` wraps `ToolExecutor.execute()`. Attributes: `tool_name`, `success`, `retry_count`, `latency_ms`, `authorization_result`. Never tool arguments or results.

### Agent Spans — `agent.iteration` / `agent.tool_call` / `agent.reflection`

`agent_span("iteration" | "tool_call" | "reflection")` wraps the `DefaultAgent` reasoning loop per iteration. Attributes: `iteration_index`, `tool_calls_count`, `finish_reason`, `latency_ms`. Never scratchpad/reasoning content.

### RAG / Memory / Voice Spans — `rag.retrieve` / `memory.retrieve` / `memory.extract` / `voice.session`

`rag_span("retrieve")`, `memory_span("retrieve" | "extract")`, `voice_span("session")` wrap `Retriever.retrieve()`, the Memory retrieval/extraction entry points, and voice session lifecycle events respectively. Attributes: counts (`retrieved_count`, `top_k`), latency, and status only.

### Workflow Spans — `workflow.run` / `workflow.node`

`workflow_span("run" | "node")` wraps `WorkflowExecutor.step()` at both the run level (one span per `start_run`/`resume` invocation) and the node level (one span per `WorkflowNodeExecution` attempt). Attributes: `node_type`, `attempt`, `status`, `latency_ms`; run-level spans additionally carry `run_id` as a **span attribute** (never a metric label — see § Metric Cardinality Policy). Because the executor continues on an in-process `asyncio.Task` that can outlive the triggering HTTP request, the run-level root span carries the originating `trace_id` as a span **link** (best-effort correlation) rather than as a parent — the workflow run's own trace remains self-contained and inspectable even if the original request's trace has already been exported.

---

## Existing V1/V2 Assets (reuse, do not duplicate)

| Asset                                                                                   | Location                                         | Epic 07 role                                                                          |
| --------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `StructuredLogger`, `sanitize_value`, `bind_context`                                    | `app/core/logging.py`                            | Reused directly for span/metric attribute redaction and trace/span-id log correlation |
| `correlation_id_middleware`, `get_request_id`                                           | `app/middleware/correlation_id.py`               | Extended to additionally bind `trace_id`/`span_id`                                    |
| `SqlUsageStore`, `UsageEvent`                                                           | `app/db/usage.py`, `app/db/models.py`            | Extended (not replaced) with `cost_usd`/`pricing_version`                             |
| `ProviderUsage`, `LLMProvider` Protocol                                                 | `app/providers/base.py`                          | Source data for cost calculation and LLM span attributes; Protocol unchanged          |
| `PromptManager`                                                                         | `app/ai/prompts/manager.py`                      | Instrumented with `prompt_span`; rendering logic unchanged                            |
| `ToolExecutor`                                                                          | `app/ai/tools/executor.py`                       | Instrumented with `tool_span`; execution/authorization logic unchanged                |
| `DefaultAgent`                                                                          | `app/ai/agent/runtime/default_agent.py`          | Instrumented with `agent_span`; reused directly by `AgentEvalRunner`                  |
| `WorkflowManager`, `WorkflowExecutor`                                                   | `app/ai/workflow/`                               | Instrumented with `workflow_span`; reused directly by `WorkflowEvalRunner`            |
| `Retriever`, memory retrieval/extraction entry points, voice `session.py`               | `app/ai/rag/`, `app/ai/memory/`, `app/ai/voice/` | Instrumented with `rag_span`/`memory_span`/`voice_span`; logic unchanged              |
| `app/ai/evaluation/` (`datasets.py`, `runners.py`, `metrics.py`, `report.py`, `cli.py`) | `app/ai/evaluation/`                             | Extended in place with `agent`/`workflow` levels + regression checking                |
| Feature flag infrastructure                                                             | `app/core/config.py`                             | `OBSERVABILITY_ENABLED`                                                               |
| DI factories                                                                            | `app/ai/deps.py`                                 | Observability DI wiring                                                               |
| `get_current_caller`, `CallerContext`                                                   | `app/core/caller.py` / routers/auth              | Authenticated, owner-scoped Observability REST API                                    |

Observability is additive. Existing chat, RAG, MCP, memory, voice, agent, tool, and workflow behaviour is unchanged in return value, latency, and log output when `OBSERVABILITY_ENABLED=false`.

---

## Platform Integration Strategy

Unlike Memory (which changes prompt content) or Workflows (which adds a new orchestration surface), Observability is **cross-cutting instrumentation with no new user-facing pipeline**. It wraps existing call sites rather than adding new ones:

- **Span/metric helpers** — imported by existing modules (`PromptManager`, `ToolExecutor`, `DefaultAgent`, `Retriever`, Memory, Voice, `WorkflowExecutor`) at their existing call boundaries. No new request path is introduced.
- **`TracingLLMProvider`** — installed once, centrally, in `ProviderFactory.get_provider()`; individual provider adapters (`openai_provider.py`, `anthropic_provider.py`, etc.) are never modified.
- **Cost accounting** — inserted at the existing `SqlUsageStore.record()` call sites (`ChatService`/`ToolChatService`); no new usage-recording pipeline.
- **REST API / dashboard** — the only genuinely new surface, and it is read-only (usage/cost summaries), independent of any chat/agent/workflow turn, following the same authenticated-owner-scoped pattern as the Workflow REST API.

**Flag off:** No spans, no metrics, `usage_events.cost_usd` stays `NULL`, Observability REST routes return `503 feature_disabled`, `GET /metrics` returns `404`, no dashboard. All other platform behaviour — including the existing `prompt`/`retrieval`/`e2e` eval levels — is byte-for-byte unchanged.

**Flag on:** Every instrumented pipeline emits spans/metrics; usage rows are priced; authenticated users can query their own usage/cost summaries; an OTLP-compatible backend can visualize traces; `make eval --level agent` / `--level workflow` become meaningful (previously non-existent levels).

---

## Persistence Schema

Alembic migration **`0008_observability_usage_cost`** (Phase 5). Purely additive to `usage_events`; no new tables.

### `usage_events` (extended)

| Column            | Type               | Notes                                                                                  |
| ----------------- | ------------------ | -------------------------------------------------------------------------------------- |
| `cost_usd`        | numeric(12,6) NULL | Approximate cost; `NULL` when pricing is unavailable for the model                     |
| `pricing_version` | text NULL          | Pricing table version active when the row was priced; `NULL` when `cost_usd` is `NULL` |

**Indexes (new):** `(user_id, created_at)`, `(provider, model, created_at)` — support the aggregation queries `UsageAggregator` runs (owner + time-range, and provider/model breakdowns).

No other epic tables (`workflow_*`, `memory_records`, `document_chunks`, ...) are touched.

---

## Observability REST API

Authenticated-only (`Depends(get_current_caller)`) except `GET /metrics`. Router: `app/routers/observability.py`. Always mounted in `app/main.py`; each authenticated route enforces `OBSERVABILITY_ENABLED` and returns `503 feature_disabled` when the flag is off; `GET /metrics` returns `404` when the flag is off (nothing to scrape).

| Method | Path                       | Purpose                                                                                                                                                   |
| ------ | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/observability/usage` | Caller-scoped usage/cost summary. Query params: `since`, `until` (defaults: trailing 30 days), `group_by` (`day` \| `provider` \| `model`, default `day`) |
| `GET`  | `/metrics`                 | Prometheus text-format exposition of aggregate counters/histograms (no owner-identifying labels)                                                          |

**Health:** extend `GET /api/health` with `observability_enabled: bool` (same pattern as `memory_enabled`, `workflow_engine_enabled`).

**Response rules:** `GET /api/observability/usage` never exposes another owner's rows, internal trace/span IDs, or raw prompt/tool content — only aggregated counts, token totals, and cost figures for the calling user/guest. `GET /metrics` never includes a `user_id`, `guest_id`, `session_id`, or any other owner-identifying label.

---

## Public APIs (stable after Phase 1)

| API                                                                                                            | Kind                                 |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `get_tracer(name: str) -> Tracer`                                                                              | Function                             |
| `llm_span`, `prompt_span`, `tool_span`, `agent_span`, `rag_span`, `memory_span`, `voice_span`, `workflow_span` | Context manager factories            |
| `TracingLLMProvider`                                                                                           | Class (decorator over `LLMProvider`) |
| `CostCalculator`                                                                                               | Class                                |
| `ModelPricingTable`                                                                                            | Class / config loader                |
| `UsageAggregator`                                                                                              | Class                                |
| `EvalCase`, `EvalDataset`, `EvalLevel` (extended with `agent`, `workflow`)                                     | Model / enum                         |
| `AgentEvalRunner`, `WorkflowEvalRunner`                                                                        | Class                                |
| `RegressionChecker`, `RegressionResult`                                                                        | Class / model                        |
| `ObservabilityError` (and subclasses)                                                                          | Exception                            |
| Observability REST router export                                                                               | FastAPI router                       |

Internal (may evolve): OTel SDK bootstrap internals, Prometheus registry wiring, pricing table file format, baseline snapshot file format, `ObservabilityStore` query internals.

---

## Configuration defaults

| Setting                                            | Default                         |
| -------------------------------------------------- | ------------------------------- |
| `OBSERVABILITY_ENABLED`                            | **`false`**                     |
| `otel_service_name`                                | `"fullstack-ai-platform"`       |
| `otel_exporter_otlp_endpoint`                      | `""` (empty = console exporter) |
| `otel_traces_sample_ratio`                         | `1.0` (dev-safe default — **override per environment**; see § Trace Sampling Strategy) |
| `observability_cost_pricing_version`               | `"2026-08"`                     |
| `observability_usage_retention_days`               | `90`                            |
| `observability_regression_pass_rate_tolerance_pct` | `5.0`                           |
| `observability_regression_latency_tolerance_pct`   | `20.0`                          |

---

## Dependencies

| Requires                                                                     | Provides to downstream                                                         |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Epic 06 Workflow Engine (stable chat/memory/workflow pipeline)               | `TracerRegistry`, `CostCalculator`, `UsageAggregator`, `OBSERVABILITY_ENABLED` |
| All prior pipelines (chat, RAG, MCP, voice, memory, agent, tools, workflows) | Span/metric coverage of every existing call site                               |
| `app/ai/evaluation/` (V1 evaluation harness)                                 | Extended `agent`/`workflow` eval levels, regression baseline                   |
| PostgreSQL                                                                   | `usage_events.cost_usd`/`pricing_version` persistence                          |

**Future consumers:** Epic 08 Plugin Architecture (plugin execution spans/metrics); Epic 09 Human-in-the-Loop (approval latency metrics); Epic 10 Background Jobs (scheduled/continuous eval runs, metrics-driven worker scaling); Epic 11 Security & Governance (audit-log ↔ trace_id correlation, rate-limit metrics).

---

## Design acceptance

- Flag off: no spans, no metrics, `usage_events.cost_usd` stays `NULL`, Observability REST routes return `503 feature_disabled`, `GET /metrics` returns `404`, no dashboard; all other platform paths byte-for-byte unchanged
- Flag on, authenticated: every LLM call, tool call, prompt render, agent iteration, RAG retrieval, memory operation, voice session, and workflow node transition emits a correlated span with zero content leakage
- Every priced `usage_events` row carries an approximate `cost_usd` and the `pricing_version` used, or `NULL` when pricing is unavailable — never a blocking error
- `GET /api/observability/usage` returns only the caller's own aggregated usage/cost; `GET /metrics` never includes owner-identifying labels
- `make eval --level agent` and `make eval --level workflow` execute real cases through `DefaultAgent`/`WorkflowManager` and are skipped (not failed) when the corresponding feature flag is off
- `make eval --check-regression` detects a hard pass→fail regression, a pass-rate drop, or a latency increase beyond configured tolerance against the git-tracked baseline
- A tracing, metrics, or cost-calculation failure never fails the underlying request, tool call, or node execution
- Every metric instrument uses only the § Metric Cardinality Policy allowlist; no owner/session/request/trace identifier ever appears as a metric label
- Deployment configuration overrides `otel_traces_sample_ratio` per § Trace Sampling Strategy — 100% sampling is never the production default
- Coverage ≥80% on `app/` and `app/ai/observability/`
- No prompt, tool argument/result, or message content in spans, metrics, or the Observability REST API responses

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **No content in telemetry** — spans, metrics, and Observability REST responses carry identifiers, counts, durations, and status only; `app.core.logging.sanitize_value` is reused for any dynamic attribute, never bypassed.
- **No vendor lock-in** — tracing/metrics code depends only on the OpenTelemetry API; exporter selection is configuration (`otel_exporter_otlp_endpoint`), never a per-vendor code branch.
- **Reuse, don't reimplement** — cost figures derive only from the existing `ProviderUsage`; `agent`/`workflow` eval levels reuse `DefaultAgent`/`WorkflowManager` exactly; the evaluation framework is extended in place, not forked.
- **Non-blocking instrumentation** — a tracing/metrics/cost-calculation exception is caught, logged, and never propagates to fail the instrumented operation.
- **Flag-off parity** — `OBSERVABILITY_ENABLED=false` preserves Epic 06 behaviour on every hot path, including exact `usage_events` write shape (minus the new nullable columns) and existing eval level results.
- **Owner isolation** — `GET /api/observability/usage` is strictly caller-scoped; `GET /metrics` never contains owner-identifying labels.
- **Bounded metric cardinality** — every counter/histogram uses only the § Metric Cardinality Policy allowlist (`provider`, `model`, `tool_name`, `workflow_type`/`node_type`, `status`); high-cardinality identifiers are span attributes only, never metric labels.
- **No new orchestration surface** — Observability introduces span/metric helpers and a read-only REST API only; it never becomes a dependency of `ChatService`, `WorkflowExecutor`, or any other business-logic control flow (i.e., disabling it cannot change what those components decide to do).
- **Public APIs stable after Phase 1** — span helper signatures and the tracer/meter accessors require user approval to change.
- **No Epic 08+ behaviour early** — plugin tracing hooks, scheduled/continuous eval runs, audit-log correlation, alerting — `TODO(epic-N):` only.

---

## Acceptance Criteria

- Every existing pipeline emits correlated, content-free OpenTelemetry spans when Observability is enabled, exportable to any OTLP-compatible backend without platform code changes.
- Token usage is priced into an approximate, versioned `cost_usd` on every `usage_events` row, queryable per owner via REST.
- The evaluation harness can exercise agent and workflow behaviour end-to-end, in addition to the existing prompt/retrieval/RAG levels, and can detect regressions against a versioned baseline.
- The existing platform is unaffected when Observability is disabled.
- No prompt, tool, or message content is ever captured by tracing, metrics, or the Observability API.

# Part II — Execution

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement Part II phase-by-phase. Part I is frozen and is the architectural source of truth. Do not redesign architecture during implementation.

## Phase integration rules

Early phases build **tracing/metrics infrastructure in isolation** (unit tests with a captured in-memory span exporter). Instrumentation of each existing pipeline follows once the infrastructure is proven. The REST API, evaluation extensions, and frontend are deferred until instrumentation itself is complete.

| Phase | Builds                                                    | Wiring        |
| ----- | --------------------------------------------------------- | ------------- |
| 1     | Tracing/metrics foundation, config, no-op fallback        | None          |
| 2     | LLM provider tracing + prompt tracing                     | Internal only |
| 3     | Tool tracing + agent loop tracing                         | Internal only |
| 4     | RAG / Memory / Voice / Workflow tracing                   | Internal only |
| 5     | Token & cost metrics (migration + `CostCalculator`)       | Internal + DB |
| 6     | Observability REST API + `/metrics`                       | REST only     |
| 7     | Evaluation framework: `agent`/`workflow` levels           | CLI only      |
| 8     | Prompt/RAG/agent/workflow regression + benchmark datasets | CLI only      |
| 9     | Frontend dashboard                                        | Frontend      |
| 10    | Validation & release                                      | —             |

## Reuse Existing Components

**DO NOT REIMPLEMENT**

| Component                                                      | Location                                     |
| -------------------------------------------------------------- | -------------------------------------------- |
| `StructuredLogger`, `sanitize_value`, `bind_context`           | `app/core/logging.py`                        |
| `correlation_id_middleware`, `get_request_id`                  | `app/middleware/correlation_id.py`           |
| `SqlUsageStore`, `UsageEvent`                                  | `app/db/usage.py`, `app/db/models.py`        |
| `ProviderUsage`, `LLMProvider`, `ProviderFactory`              | `app/providers/`                             |
| `PromptManager`                                                | `app/ai/prompts/manager.py`                  |
| `ToolExecutor`, registry, validator, `ToolAuthorizer`          | `app/ai/tools/`                              |
| `DefaultAgent`, `AgentRequest`/`AgentResponse`                 | `app/ai/agent/`                              |
| `WorkflowManager`, `WorkflowExecutor`                          | `app/ai/workflow/`                           |
| `Retriever`, RAG pipeline                                      | `app/ai/rag/`                                |
| Memory retrieval/extraction                                    | `app/ai/memory/`                             |
| Voice session lifecycle                                        | `app/ai/voice/`                              |
| `app/ai/evaluation/` (datasets, runners, metrics, report, cli) | `app/ai/evaluation/`                         |
| `get_current_caller`, `CallerContext`                          | `app/core/caller.py` / `app/routers/auth.py` |
| Feature flag infrastructure                                    | `app/core/config.py`                         |
| DI factories                                                   | `app/ai/deps.py`                             |

Observability is additive. Existing chat, RAG, MCP, memory, voice, agent, tool, and workflow behaviour must remain unchanged when `OBSERVABILITY_ENABLED=false`.

---

## Not Allowed

- Bypass the span/tracer/meter accessors to call the OTel SDK directly from feature code
- Add a vendor-specific tracing/APM SDK or auto-instrumentation bundle
- Attach prompt, tool argument/result, or message content to any span, metric, or log field
- Reimplement token counting, retrieval, the agent loop, or workflow execution in order to add tracing
- Block or slow down request/tool/node execution on a tracing, metrics, or cost-calculation failure
- Include owner-identifying data in `GET /metrics`
- Build a custom trace/span visualization UI
- Implement scheduled/cron evaluation runs or historical evaluation trend storage
- Break feature-flag parity

---

## Baseline

_Copied from [Epic 06 Phase 12 completion record](./post-mvp-v2-epic-06-workflow-engine.md#phase-12--validation--release). Reverify in Phase 0._

| Area                     | State                                                             |
| ------------------------ | ----------------------------------------------------------------- |
| Backend tests / coverage | 1551 passed, 89.05% `app/`                                        |
| Frontend tests           | 268 passed (43 files); lint + build pass                          |
| Integration tests        | Workflow suite 241 passed; router 23; tool 11                     |
| Eval CLI                 | 5/5 passed                                                        |
| Chat pipeline            | Stable — `ChatService` + `UnifiedChatService`, Memory fully wired |
| Agent Framework          | Completed (Epic 01); `AGENT_RUNTIME_ENABLED` behind flag          |
| Memory subsystem         | Completed (Epic 05); `MEMORY_ENABLED` behind flag                 |
| Workflow Engine          | Completed (Epic 06); `WORKFLOW_ENGINE_ENABLED` behind flag        |
| Observability            | Not started                                                       |

---

## Phase Status

| Phase | Name                                          | Effort | Status      |
| ----- | --------------------------------------------- | ------ | ----------- |
| 0     | Baseline Audit                                | XS     | Not Started |
| 1     | Tracing & Metrics Foundation                  | M      | Not Started |
| 2     | LLM Provider & Prompt Tracing                 | M      | Not Started |
| 3     | Tool & Agent Tracing                          | M      | Not Started |
| 4     | RAG, Memory, Voice & Workflow Tracing         | L      | Not Started |
| 5     | Token & Cost Metrics                          | L      | Not Started |
| 6     | Observability REST API & `/metrics`           | M      | Not Started |
| 7     | Evaluation Framework: Agent & Workflow Levels | L      | Not Started |
| 8     | Prompt Regression & Benchmark Datasets        | M      | Not Started |
| 9     | Frontend Observability Dashboard              | S      | Not Started |
| 10    | Validation & Release                          | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS

**Objective**

Establish a verified implementation baseline before introducing Observability instrumentation. Confirm the existing platform is stable, all architectural dependencies are understood, and no observability implementation already exists.

**Deliverables**

- `docs/audits/post-mvp-v2-epic7-phase-0-baseline-audit.md`
- Architecture inventory
- Dependency verification
- Feature flag verification
- Platform readiness assessment
- Baseline quality metrics
- Implementation readiness checklist

**Steps**

### Platform Verification

- [ ] Confirm Epic 06 Phase 12 complete / authorized for Epic 07.
- [ ] Inventory `app/core/logging.py`, `app/middleware/correlation_id.py`.
- [ ] Inventory `app/db/usage.py`, `UsageEvent`, `ProviderUsage`.
- [ ] Inventory `app/ai/evaluation/` (datasets, runners, metrics, report, cli) and `make eval`.
- [ ] Verify chat, RAG, MCP, memory, voice, agent, tool, and workflow pipelines all remain operational.
- [ ] Verify streaming responses remain operational.

### Architecture Review

- [ ] Review the frozen Part I architecture.
- [ ] Verify all architectural invariants are understood.
- [ ] Identify every existing call site each span helper will instrument.
- [ ] Identify existing extension points (`ProviderFactory`, DI factories, feature flag infra).
- [ ] Confirm no OpenTelemetry / cost-accounting implementation already exists.
- [ ] Record implementation assumptions.

### Dependency Verification

- [ ] Verify PostgreSQL configuration and `usage_events` current schema.
- [ ] Confirm target OpenTelemetry SDK/exporter package versions.
- [ ] Verify existing provider abstractions (`LLMProvider`, `ProviderFactory`).
- [ ] Verify dependency injection configuration (`app/ai/deps.py`).
- [ ] Verify feature flag infrastructure.

### Codebase Inventory

- [ ] Inventory `PromptManager`, `ToolExecutor`, `DefaultAgent`, `WorkflowExecutor`, RAG/Memory/Voice entry points.
- [ ] Inventory existing Alembic migrations and numbering (`0007_workflow_tables` is latest).
- [ ] Record components to be reused vs. newly introduced.

### Baseline Quality Validation

- [ ] Execute lint.
- [ ] Execute type checking.
- [ ] Execute unit tests.
- [ ] Execute integration tests.
- [ ] Execute evaluation suite.
- [ ] Record baseline quality metrics.

### Implementation Readiness

- [ ] Confirm all required dependencies are available.
- [ ] Confirm implementation order matches Part II.
- [ ] Confirm no architectural conflicts exist.
- [ ] Publish baseline audit document.
- [ ] Freeze implementation baseline.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`

Additional verification:

- [ ] Chat, RAG, memory, voice, agent, tool, and workflow functionality verified.
- [ ] Streaming functionality verified.
- [ ] All quality gates pass.

**Acceptance**

- Existing platform is fully operational.
- All architectural assumptions have been verified.
- Required dependencies are available.
- Existing extension points have been identified.
- No implementation blockers remain.
- Baseline metrics have been recorded.
- Repository is ready for Observability implementation.

**Exit Criteria**

- Baseline audit completed.
- Platform readiness confirmed.
- Quality gates passed.
- Architecture verified.
- Implementation baseline frozen.
- User confirmation pending to proceed to Phase 1.

**Rollback**

- No rollback required. This phase introduces no functional code changes.

**Completion Record**

| Metric                   | Result |
| ------------------------ | ------ |
| Lint                     |        |
| Typecheck                |        |
| Unit Tests               |        |
| Integration Tests        |        |
| Evaluation Suite         |        |
| Platform Readiness       |        |
| Baseline Audit Published |        |

---

# Phase 1 — Tracing & Metrics Foundation

**Effort:** M

**Objective**

Establish the OpenTelemetry foundation: `TracerRegistry`/`MeterRegistry` with a no-op fallback, configuration surface, dependency additions, and trace/span-id correlation into the existing structured logging context. No pipeline is instrumented yet.

**Deliverables**

- `app/ai/observability/` package scaffold
- `TracerRegistry` (real `TracerProvider` when enabled; OTel `NoOpTracer` when disabled)
- `MeterRegistry` (real `MeterProvider` + Prometheus reader when enabled; no-op when disabled)
- `OBSERVABILITY_ENABLED` feature flag + OTel configuration settings
- `correlation_id_middleware` extended to bind `trace_id`/`span_id`
- OpenTelemetry SDK dependency additions
- Unit test suite (in-memory span exporter)

**Steps**

### Package Structure

- [ ] Create the `app/ai/observability/` package with `tracing/`, `metrics/`, `cost/`, `aggregation/` subpackages.
- [ ] Add package exports through `__init__.py`.
- [ ] Verify package imports are dependency-cycle free.

### Dependencies

- [ ] Add `opentelemetry-api`, `opentelemetry-sdk` to `pyproject.toml`.
- [ ] Add an OTLP HTTP exporter package and a Prometheus exporter/reader package.
- [ ] Pin versions consistent with `requires-python = ">=3.12"`.

### Tracer / Meter Registry

- [ ] Implement `TracerRegistry.get_tracer(name) -> Tracer` — returns a real tracer when `OBSERVABILITY_ENABLED=true`, else OTel's `NoOpTracer`.
- [ ] Configure a console `SpanExporter` by default; switch to an OTLP/HTTP exporter when `otel_exporter_otlp_endpoint` is set.
- [ ] Configure a `ParentBased(TraceIdRatioBased(otel_traces_sample_ratio))` sampler per Part I § Trace Sampling Strategy; verify `.env.example` documents environment-specific recommended ratios (dev `1.0` / staging `0.25` / production `0.05`).
- [ ] Implement `MeterRegistry.get_meter(name) -> Meter` with the same real/no-op split, backed by a Prometheus reader.
- [ ] Ensure both registries initialize once per process (idempotent bootstrap in `app/main.py` startup).
- [ ] Verify metric/cost recording paths (Phase 5) are wired independently of the trace sampler — a sampled-out span must never suppress a metric or cost record.

### Span Helper Scaffold

- [ ] Implement the span helper module (`spans.py`) with the context-manager signatures frozen in Part I (bodies are no-ops / generic until Phases 2–4 wire real call sites).
- [ ] Ensure span helpers sanitize any dynamic attribute via `app.core.logging.sanitize_value`.
- [ ] Ensure span helpers catch and log (not raise) any OTel SDK exception.

### Logging Correlation

- [ ] Extend `correlation_id_middleware` to bind `trace_id`/`span_id` into log context alongside `request_id` when Observability is enabled.
- [ ] Verify log context is unaffected (no `trace_id`/`span_id` keys) when the flag is off.

### Configuration

- [ ] Add `OBSERVABILITY_ENABLED` feature flag (default `false`).
- [ ] Add `otel_service_name`, `otel_exporter_otlp_endpoint`, `otel_traces_sample_ratio` settings.
- [ ] Preserve backward compatibility when disabled.

### Testing

- [ ] Add `TracerRegistry`/`MeterRegistry` real-vs-no-op tests.
- [ ] Add in-memory span exporter tests verifying span helper attribute sanitization.
- [ ] Add logging-correlation tests (flag on/off).
- [ ] Add package import tests.

**Verify**

- `make lint`
- `make typecheck`
- `pytest tests/ai/observability/test_tracer_registry.py tests/ai/observability/test_meter_registry.py`

Additional verification:

- [ ] Flag off yields a genuine no-op tracer/meter (zero exporter calls).
- [ ] Flag on yields a real tracer/meter with a console exporter by default.
- [ ] No circular imports detected.
- [ ] Feature flag defaults to disabled.

**Acceptance**

- `TracerRegistry`/`MeterRegistry` are the only way any code obtains a tracer/meter.
- Span helper signatures match the frozen Part I API.
- No pipeline code has been modified yet — this phase is infrastructure-only.
- Existing application behaviour is unchanged with `OBSERVABILITY_ENABLED=false`.

**Exit Criteria**

- All foundation tests pass.
- All quality gates pass.
- Public tracer/meter/span-helper APIs frozen.
- Ready to begin Phase 2 without further structural changes.

**Rollback**

- Remove `app/ai/observability/` package.
- Remove feature flag and OTel settings.
- Remove OTel dependencies from `pyproject.toml`.
- Revert `correlation_id_middleware` changes.
- Verify application builds and runs successfully without Observability components.

**Completion Record**

_Filled upon phase completion._

---

# Phase 2 — LLM Provider & Prompt Tracing

**Effort:** M

**Objective**

Instrument every LLM call and every prompt render with spans and latency/token attributes, without modifying any concrete provider adapter or the prompt rendering logic itself.

**Deliverables**

- `TracingLLMProvider` decorator
- `ProviderFactory.get_provider()` wraps the returned provider when enabled
- `prompt_span` wired into `PromptManager.render()`
- Integration test suite

**Steps**

### LLM Tracing

- [ ] Implement `TracingLLMProvider` wrapping `complete_chat`, `complete_chat_with_tools`, and `stream_chat`.
- [ ] Open a span named `llm.complete` (`llm_span(provider, model, streaming)`) around each call; record `provider`, `model`, `streaming`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`, `latency_ms` as span attributes (per Part I § Tracing Domains, `llm.complete` is the fixed span name; provider/model are attributes, not name components).
- [ ] Implement the streaming span lifecycle exactly per Part I § LLM Spans: start the `llm.complete` span immediately before issuing the `stream_chat` request; keep it open across every yielded chunk (no per-chunk child span); close it only after the async generator is exhausted.
- [ ] Record `prompt_tokens`/`completion_tokens`/`total_tokens` and trigger `CostCalculator.price(...)` **only** from the terminal chunk's usage payload — never from intermediate chunks (mirrors existing streaming usage handling in `ChatService`).
- [ ] Wrap the provider returned by `ProviderFactory.get_provider()` only when `OBSERVABILITY_ENABLED=true`; return the concrete provider unmodified otherwise.
- [ ] Verify no individual provider adapter file (`openai_provider.py`, `anthropic_provider.py`, `gemini_provider.py`, `groq_provider.py`) is modified.

### Prompt Tracing

- [ ] Wrap `PromptManager.render()` with `prompt_span(category, name, version)`.
- [ ] Record `variable_count`, `rendered_length_chars` as attributes; never the rendered text.
- [ ] Verify rendering behaviour and return value are unchanged.

### Testing

- [ ] Add `TracingLLMProvider` tests (fake provider + in-memory span exporter) for `complete_chat`, `complete_chat_with_tools`, `stream_chat`.
- [ ] Add a streaming lifecycle test: exactly one `llm.complete` span per `stream_chat` call (not one per chunk), span duration covers the full stream, and usage/cost are recorded exactly once, from the terminal chunk.
- [ ] Add `ProviderFactory` wrapping tests (flag on/off).
- [ ] Add `PromptManager` span tests (attributes present, content absent).
- [ ] Add failure-mode tests: a span/attribute error never fails the LLM call or prompt render.

**Verify**

- `pytest tests/ai/observability/test_llm_tracing.py tests/ai/observability/test_prompt_tracing.py`

Additional verification:

- [ ] LLM spans carry token/latency attributes and no message content.
- [ ] Prompt spans carry category/name/version and no rendered content.
- [ ] Chat, RAG, and tool-calling flows behave identically with the flag on or off.

**Acceptance**

- Every LLM call and prompt render produces a correlated span when enabled, with zero reimplementation of provider or prompt logic.
- No content leakage in any span attribute.

**Exit Criteria**

- LLM/prompt tracing tests pass.
- Ready for tool and agent tracing (Phase 3).

**Completion Record**

_Filled upon phase completion._

---

# Phase 3 — Tool & Agent Tracing

**Effort:** M

**Objective**

Instrument tool execution and the agent reasoning loop with spans, reusing `ToolExecutor` and `DefaultAgent` exactly as they exist today.

**Deliverables**

- `tool_span` wired into `ToolExecutor.execute()`
- `agent_span` wired into the `DefaultAgent` iteration loop
- Integration test suite

**Steps**

### Tool Tracing

- [ ] Wrap `ToolExecutor.execute()` with `tool_span(tool_name)`.
- [ ] Record `success`, `retry_count`, `latency_ms`, `authorization_result` as attributes.
- [ ] Verify tool arguments/results are never attached to the span.
- [ ] Verify authorization failures and tool errors are recorded as span status, not exceptions raised from the span helper.

### Agent Tracing

- [ ] Wrap each `DefaultAgent` reasoning iteration with `agent_span("iteration")`.
- [ ] Record `iteration_index`, `tool_calls_count`, `finish_reason`, `latency_ms`.
- [ ] Add a nested `agent_span("tool_call")` around each tool dispatch within an iteration (parents to the existing `tool_span`, not a duplicate).
- [ ] Verify no change to the agent's reasoning/tool-selection behaviour.

### Testing

- [ ] Add `ToolExecutor` span tests (success, failure, retry, authorization-denied cases).
- [ ] Add `DefaultAgent` span tests (multi-iteration run, fake provider/tools).
- [ ] Add failure-mode tests: a span error never fails a tool call or agent iteration.

**Verify**

- `pytest tests/ai/observability/test_tool_tracing.py tests/ai/observability/test_agent_tracing.py`

Additional verification:

- [ ] Tool spans carry outcome/latency attributes and no argument/result content.
- [ ] Agent spans reflect the actual iteration count and tool-call count of a run.
- [ ] Existing tool and agent test suites still pass unmodified.

**Acceptance**

- Tool execution and agent reasoning are fully observable without any change to their control-flow or authorization logic.

**Exit Criteria**

- Tool/agent tracing tests pass.
- Ready for RAG/Memory/Voice/Workflow tracing (Phase 4).

**Completion Record**

_Filled upon phase completion._

---

# Phase 4 — RAG, Memory, Voice & Workflow Tracing

**Effort:** L

**Objective**

Complete platform-wide trace coverage: RAG retrieval, memory retrieval/extraction, voice sessions, and workflow run/node transitions — including the trace-context propagation strategy for workflow runs that continue on an in-process `asyncio.Task` after their triggering request completes.

**Deliverables**

- `rag_span` wired into `Retriever.retrieve()`
- `memory_span` wired into memory retrieval and extraction entry points
- `voice_span` wired into voice session lifecycle events
- `workflow_span` wired into `WorkflowExecutor.step()` (run + node level)
- Trace-link propagation for background workflow execution
- Integration test suite

**Steps**

### RAG Tracing

- [ ] Wrap `Retriever.retrieve()` with `rag_span("retrieve")`.
- [ ] Record `top_k`, `retrieved_count`, `latency_ms`.

### Memory Tracing

- [ ] Wrap the memory retrieval entry point with `memory_span("retrieve")`.
- [ ] Wrap the memory extraction entry point with `memory_span("extract")`.
- [ ] Record counts/latency only; never memory content.

### Voice Tracing

- [ ] Wrap voice session start/end (`app/ai/voice/session.py`) with `voice_span("session")`.
- [ ] Record session duration and terminal status only.

### Workflow Tracing

- [ ] Wrap `WorkflowExecutor.step()` run-level invocation with `workflow_span("run")`; attributes `run_id`, terminal `status`, `latency_ms`.
- [ ] Wrap each `WorkflowNodeExecution` attempt with `workflow_span("node")`; attributes `node_type`, `attempt`, `status`, `latency_ms`.
- [ ] Capture the originating `trace_id` (if any) at `start_run()` time and store it as a span **link** on the run-level root span opened inside the background `asyncio.Task` (best-effort correlation, per Part I decision).
- [ ] Verify resumed runs (crash recovery) open a fresh run-level span rather than attempting to reopen a closed one.

### Testing

- [ ] Add RAG/memory/voice span tests (attributes present, content absent).
- [ ] Add workflow run/node span tests, including a fork/join scenario (multiple concurrent node spans).
- [ ] Add a background-task trace-link test (run started from a request, span link recorded correctly).
- [ ] Add resume/crash-recovery span tests (fresh root span per resume).

**Verify**

- `pytest tests/ai/observability/test_rag_tracing.py tests/ai/observability/test_memory_tracing.py tests/ai/observability/test_voice_tracing.py tests/ai/observability/test_workflow_tracing.py`

Additional verification:

- [ ] All eight span domains from Part I are wired and produce spans when enabled.
- [ ] Parallel workflow branches produce concurrent, correctly nested node spans.
- [ ] No pipeline's functional behaviour changed.

**Acceptance**

- Every pipeline named in Part I § Tracing Domains emits correlated spans when enabled.
- Workflow run tracing correctly handles the async-launch / background-execution / crash-recovery lifecycle from Epic 06.

**Exit Criteria**

- Full-platform tracing tests pass.
- Ready for token & cost metrics (Phase 5).

**Completion Record**

_Filled upon phase completion._

---

# Phase 5 — Token & Cost Metrics

**Effort:** L

**Objective**

Turn existing `ProviderUsage` token counts into an approximate, versioned dollar cost persisted onto `usage_events`, and emit the OTel counters/histograms Epic 06 pre-declared plus their LLM/tool/agent equivalents.

**Deliverables**

- `ModelPricingTable`, `CostCalculator`
- `usage_events.cost_usd` / `pricing_version` columns + Alembic migration `0008_observability_usage_cost`
- `SqlUsageStore.record()` extended to compute and persist cost
- OTel metric instruments (counters/histograms)
- Integration test suite

**Steps**

### Cost Calculation

- [ ] Implement `ModelPricingTable` — static, versioned per-(provider, model) input/output per-1K-token rates, loaded from configuration.
- [ ] Implement `CostCalculator.price(provider, model, usage: ProviderUsage) -> tuple[float | None, str | None]` (cost, pricing_version).
- [ ] Return `(None, None)` for unknown provider/model or missing usage fields — never raise.
- [ ] Verify (by design, not by code) that no code path recalculates or overwrites a previously persisted `cost_usd`/`pricing_version` when `ModelPricingTable` changes — pricing updates apply to new rows only, per Part I § Pricing table lifecycle.

### Persistence

- [ ] Add `cost_usd numeric(12,6) NULL`, `pricing_version text NULL` to `UsageEvent` ORM model.
- [ ] Create Alembic migration `0008_observability_usage_cost` (additive, nullable columns; new indexes per Part I).
- [ ] Extend `SqlUsageStore.record()` to accept/compute `cost_usd`/`pricing_version` (default behaviour unchanged when Observability is disabled — both remain `NULL`).
- [ ] Verify existing `usage_events` writers (`ChatService`, `ToolChatService`) require no call-site changes beyond the new optional fields.

### Metrics Instruments

- [ ] Implement counters: `llm_requests_total`, `tool_calls_total`, `agent_iterations_total`, `workflow_runs_started`, `workflow_runs_completed`, `workflow_runs_failed`, `workflow_retry_count`.
- [ ] Implement histograms: `llm_token_usage`, `tool_call_latency_ms`, `workflow_node_execution_latency_ms`, `workflow_checkpoint_latency_ms`.
- [ ] Implement gauges/up-down counters: `workflow_approval_pending_count`, `workflow_parallel_branch_count`.
- [ ] Record `llm_cost_usd_total` as a counter incremented by each priced usage event.
- [ ] Restrict every instrument's labels to the Part I § Metric Cardinality Policy allowlist (`provider`, `model`, `tool_name`, `workflow_type`/`node_type`, `status`); add a lint/test guard asserting no instrument accepts `user_id`, `guest_id`, `session_id`, `request_id`, `trace_id`, `run_id`, or `message_id` as a label.
- [ ] Wire instrument recording into the Phase 2–4 span helpers (one place per domain, not duplicated per call site) — the same helper call attaches high-cardinality identifiers to the **span** and only allowlisted values to the **metric**.

### Testing

- [ ] Add `CostCalculator` tests (known model, unknown model, missing usage fields, unknown provider).
- [ ] Add migration upgrade/downgrade smoke test.
- [ ] Add `SqlUsageStore.record()` tests asserting `cost_usd`/`pricing_version` persistence and `NULL` fallback.
- [ ] Add a pricing-version-change test: updating `ModelPricingTable` does not alter previously persisted `cost_usd`/`pricing_version` rows; only new rows use the new table.
- [ ] Add metrics-instrument tests using an in-memory metric reader (counters/histograms increment correctly).
- [ ] Add a metric cardinality guard test asserting every instrument's attribute set is a subset of the Part I allowlist.

**Verify**

- `pytest tests/ai/observability/test_cost_calculator.py tests/ai/observability/test_usage_cost_persistence.py tests/ai/observability/test_metrics_instruments.py`

Additional verification:

- [ ] `usage_events` rows are priced correctly for known models.
- [ ] Unknown/unsupported models never block a usage write.
- [ ] Every metric name declared in Epic 06 § Observability is emitted.

**Acceptance**

- Cost accounting is approximate, versioned, additive, and never blocking.
- The exact metric surface Epic 06 pre-declared is implemented, plus LLM/tool/agent equivalents.

**Exit Criteria**

- Cost and metrics tests pass.
- Ready for the Observability REST API (Phase 6).

**Rollback**

- Downgrade Alembic migration `0008_observability_usage_cost`.
- Disable `OBSERVABILITY_ENABLED`.
- Remove metrics instrument wiring from span helpers.
- Verify `usage_events` writes and existing usage-consuming code paths are unaffected.

**Completion Record**

_Filled upon phase completion._

---

# Phase 6 — Observability REST API & `/metrics`

**Effort:** M

**Objective**

Expose owner-scoped usage/cost summaries and a Prometheus exposition endpoint per Part I, always mounted and gated by `OBSERVABILITY_ENABLED`.

**Deliverables**

- `app/schemas/observability.py`
- `app/routers/observability.py`
- `UsageAggregator`, `ObservabilityStore`
- `GET /api/health` extended with `observability_enabled`
- Integration test suite

**Steps**

### Aggregation

- [ ] Implement `UsageAggregator` — owner-scoped queries over `usage_events` grouped by `day` | `provider` | `model`, with a `since`/`until` date range.
- [ ] Implement `ObservabilityStore` as the thin read façade the router depends on.
- [ ] Ensure queries use the new `(user_id, created_at)` / `(provider, model, created_at)` indexes.
- [ ] Before wiring the router: re-evaluate whether `ObservabilityStore` adds any logic beyond delegating to `UsageAggregator` (per Part I § Storage Architecture). If it does not, collapse it and have the router depend on `UsageAggregator` directly; if it does (e.g., response DTO shaping, multi-source aggregation), keep it. Record the decision in this phase's Completion Record.

### Schemas & Router

- [ ] Define request/response schemas for the usage summary endpoint; never expose other owners' data or internal trace/span IDs.
- [ ] Implement `GET /api/observability/usage` — `Depends(get_current_caller)`, owner-scoped.
- [ ] Implement `GET /metrics` — Prometheus text exposition from `MeterRegistry`'s Prometheus reader; unauthenticated (matches `/api/health` pattern); no owner-identifying labels.
- [ ] Return `503 feature_disabled` from `/api/observability/usage` and `404` from `/metrics` when `OBSERVABILITY_ENABLED=false`.
- [ ] Mount the router in `app/main.py`.

### Health

- [ ] Extend `app/routers/health.py` with `observability_enabled`.

### Error Handling

- [ ] Map validation errors (bad date range, invalid `group_by`) → `422`.
- [ ] Map generic errors → `500` with a safe message.

### Testing

- [ ] Add router tests for the usage endpoint (happy path, date-range filtering, each `group_by` mode).
- [ ] Add owner-isolation tests (caller only ever sees their own rows).
- [ ] Add feature-flag-off tests (`503` on usage endpoint, `404` on `/metrics`).
- [ ] Add `/metrics` content tests (Prometheus format, no owner labels).
- [ ] Add health endpoint tests.

**Verify**

- `pytest tests/test_observability_router.py`

Additional verification:

- [ ] Usage summaries match Part I contract for every `group_by` mode.
- [ ] `/metrics` is scrape-able Prometheus text format.
- [ ] Owner isolation holds.
- [ ] Health endpoint reports `observability_enabled` correctly.

**Acceptance**

- The Observability layer is fully queryable via REST without any other client integration.
- API responses never leak cross-owner data, trace internals, or content.
- Flag-off behaviour matches the platform's `503 feature_disabled` convention (and `404` for `/metrics`).

**Exit Criteria**

- REST API tests pass.
- Ready for evaluation framework extension (Phase 7).

**Completion Record**

_Filled upon phase completion._

---

# Phase 7 — Evaluation Framework: Agent & Workflow Levels

**Effort:** L

**Objective**

Extend the existing V1 evaluation harness with `agent` and `workflow` eval levels, reusing `DefaultAgent`/`WorkflowManager` exactly as the existing `e2e` level reuses `RAGService`.

**Deliverables**

- `AgentEvalRunner`, `WorkflowEvalRunner`
- Extended `EvalLevel`, `EvalCase`, `EvalCaseResult`, `EvalRunReport`
- Reproducibility metadata (`model`, `model_version`, `temperature`, `seed`, `prompt_version`) on `EvalCaseResult`
- Extended `cli.py` (`--level agent`, `--level workflow`, `--level all` includes both)
- Integration test suite

**Steps**

### Dataset Schema

- [ ] Extend `EvalLevel` to `Literal["prompt", "retrieval", "e2e", "agent", "workflow"]`.
- [ ] Add `_parse_agent_case` — goal/instructions, expected tool calls, expected outcome.
- [ ] Add `_parse_workflow_case` — inline workflow definition (or fixture reference), trigger input, expected terminal status.
- [ ] Extend `EvalCaseResult` with `tool_calls_correct: bool | None` and `terminal_status: str | None`.
- [ ] Extend `EvalCaseResult`/`EvalRunReport` with reproducibility metadata fields per Part I § Evaluation Framework Extension: `model`, `model_version`, `temperature`, `seed`, `prompt_version` (each `| None` — never fabricated when a runner/provider doesn't expose one). Bump `REPORT_SCHEMA_VERSION`.
- [ ] Populate these fields from each runner (`PromptEvalRunner` → `prompt_version`; `AgentEvalRunner`/`EndToEndEvalRunner` → `model`/`temperature`/`model_version` from the fake or real provider call; `WorkflowEvalRunner` → `model`/`prompt_version` when the run includes `llm`/`agent` nodes).

### Agent Runner

- [ ] Implement `AgentEvalRunner` using a fake provider/tools (same pattern as `_EvalLLMProvider`/`_FakeEmbeddingProvider`).
- [ ] Assert the agent reaches the expected outcome and/or calls the expected tools.
- [ ] Skip (not fail) with a clear reason when `AGENT_RUNTIME_ENABLED=false`.

### Workflow Runner

- [ ] Implement `WorkflowEvalRunner` using `WorkflowManager` against a real (test) Postgres session, same availability check as `RetrievalEvalRunner`/`EndToEndEvalRunner`.
- [ ] Create a minimal workflow definition from the case, start a run, drive it to a terminal status.
- [ ] Assert the terminal status and (optionally) node output match expectations.
- [ ] Skip (not fail) with a clear reason when `WORKFLOW_ENGINE_ENABLED=false` or Postgres/pgvector is unavailable.

### CLI & Reporting

- [ ] Add `--level agent` / `--level workflow` to `build_parser()`; `--level all` includes both.
- [ ] Extend `print_console_summary` / `_serialize_report` with `agent`/`workflow` sections.
- [ ] Update `Makefile` `eval` target documentation if the default level set changes (default stays `all`).

### Testing

- [ ] Add `AgentEvalRunner` tests (pass, fail, flag-off skip).
- [ ] Add `WorkflowEvalRunner` tests (pass, fail, flag-off skip, Postgres-unavailable skip).
- [ ] Add dataset parsing tests for the new case types.
- [ ] Add CLI tests for `--level agent`/`--level workflow`/`--level all`.
- [ ] Add reproducibility metadata tests: each new runner populates `model`/`temperature`/`prompt_version` (and `model_version`/`seed` when available) on its `EvalCaseResult`, and leaves them `None` (not a placeholder value) when unavailable.

**Verify**

- `pytest tests/ai/evaluation/test_agent_runner.py tests/ai/evaluation/test_workflow_runner.py`

Additional verification:

- [ ] `make eval --level agent` and `make eval --level workflow` run real cases when their flags are on.
- [ ] Both levels skip cleanly (not fail) when their flag is off.
- [ ] Existing `prompt`/`retrieval`/`e2e` levels are unaffected.

**Acceptance**

- The evaluation harness can now exercise agent tool-calling and workflow orchestration behaviour end-to-end, without any parallel evaluation system.

**Exit Criteria**

- Agent/workflow eval tests pass.
- Ready for regression detection and benchmark dataset expansion (Phase 8).

**Completion Record**

_Filled upon phase completion._

---

# Phase 8 — Prompt Regression & Benchmark Datasets

**Effort:** M

**Objective**

Add automated regression detection against a git-tracked baseline, and broaden the benchmark dataset to meaningfully exercise every eval level, including the new `agent`/`workflow` cases.

**Deliverables**

- `RegressionChecker`, `RegressionResult`
- `tests/data/evaluation/baseline-report.json` (git-tracked baseline)
- `cli.py` `--check-regression` / `--update-baseline` flags
- Expanded `tests/data/evaluation/sample.yaml` (or a new, larger dataset file)
- Integration test suite

**Steps**

### Regression Checking

- [ ] Implement `RegressionChecker.compare(current, baseline, *, pass_rate_tolerance_pct, latency_tolerance_pct) -> RegressionResult`.
- [ ] Detect hard regressions: any case passing in the baseline that now fails.
- [ ] Detect soft regressions: per-level pass-rate drop or mean-latency increase beyond configured tolerance.
- [ ] Ensure `RegressionResult` is JSON-serializable and prints a clear console summary.
- [ ] Include each flagged case's reproducibility metadata (Phase 7: `model`, `model_version`, `temperature`, `seed`, `prompt_version`) from both the current and baseline result in the printed/JSON `RegressionResult`, so a regression can be explained by "what changed" rather than left ambiguous.

### Baseline Management

- [ ] Generate the initial `baseline-report.json` from a clean `make eval --level all` run.
- [ ] Add `--check-regression <baseline_path>` to compare a new run against it; non-zero exit on regression.
- [ ] Add `--update-baseline` as an explicit, separate CLI action (never automatic on a normal `make eval` run).

### Benchmark Dataset Expansion

- [ ] Add prompt-level cases covering additional prompt categories/versions in active use.
- [ ] Add retrieval/e2e cases covering additional document fixtures and answer-match modes.
- [ ] Add agent-level cases covering multi-tool-call scenarios.
- [ ] Add workflow-level cases covering sequential, conditional, and approval-node graphs.

### Testing

- [ ] Add `RegressionChecker` tests (no regression, hard regression, pass-rate regression, latency regression).
- [ ] Add a test asserting a `RegressionResult` surfaces reproducibility metadata (e.g., a regression caused by a `model` or `prompt_version` change is visibly attributable, not just "case X failed").
- [ ] Add CLI tests for `--check-regression` and `--update-baseline`.
- [ ] Add dataset validation tests for every newly added case.

**Verify**

- `pytest tests/ai/evaluation/test_regression.py`
- `make eval --level all --check-regression tests/data/evaluation/baseline-report.json`

Additional verification:

- [ ] A deliberately failing case is correctly flagged as a hard regression.
- [ ] A deliberately slower fake provider is correctly flagged as a latency regression.
- [ ] The expanded dataset exercises every eval level with realistic cases.

**Acceptance**

- Prompt, RAG, agent, and workflow quality/latency regressions are detected automatically against a versioned, git-tracked baseline.
- Updating the baseline is always an explicit, auditable action.

**Exit Criteria**

- Regression and dataset tests pass.
- Ready for the frontend Observability dashboard (Phase 9).

**Completion Record**

_Filled upon phase completion._

---

# Phase 9 — Frontend Observability Dashboard

**Effort:** S

**Objective**

Implement a read-only dashboard surfacing the caller's own usage/cost summary, scoped deliberately to DB-native data (no trace/span visualization, per Part I).

**Deliverables**

- Observability dashboard UI
- Frontend API integration
- Integration test suite

**Steps**

### Dashboard UI

- [ ] Add an Observability section to the authenticated app.
- [ ] Display Observability feature availability (via `observability_enabled`).
- [ ] Display usage/cost summary (requests, tokens, estimated cost) grouped by day, provider, and model.
- [ ] Support selecting a date range (default: trailing 30 days).

### API Integration

- [ ] Create `frontend/src/api/observabilityClient.ts`.
- [ ] Create `frontend/src/types/observability.ts`.
- [ ] Create `frontend/src/pages/ObservabilityPage.tsx` (authenticated route).
- [ ] Extend `frontend/src/api/healthClient.ts` with `observability_enabled`.
- [ ] Wire navigation link in the authenticated app shell.

### Feature Flag Integration

- [ ] Hide Observability controls when `OBSERVABILITY_ENABLED=false`.
- [ ] Preserve existing authenticated user experience.
- [ ] Preserve guest user experience.

### Error Handling

- [ ] Handle API failures gracefully.
- [ ] Handle empty-usage-history states with a clear message.
- [ ] Preserve existing application behaviour during frontend failures.

### Testing

- [ ] Add component tests.
- [ ] Add API integration tests.
- [ ] Add feature flag tests.
- [ ] Add accessibility tests.

**Verify**

- Frontend lint
- Frontend tests
- Production build

Additional verification:

- [ ] Observability page renders successfully.
- [ ] Usage/cost summaries load correctly for each `group_by` mode.
- [ ] Feature flag regression passes.

**Acceptance**

- Authenticated users can view their own usage/cost summary entirely through the public Observability API.
- Frontend remains fully functional when Observability is disabled.
- Guest users continue to experience the existing application unchanged.

**Exit Criteria**

- Observability dashboard operational.
- API integration validated.
- Ready for production validation.

**Rollback**

- Hide Observability navigation and page.
- Disable frontend Observability API integration.
- Verify existing application behaviour is unchanged.

**Completion Record**

_Filled upon phase completion._

---

# Phase 10 — Validation & Release

**Effort:** M

**Objective**

Perform comprehensive validation of the completed Observability & Evaluation epic, ensuring all Part I architectural invariants have been preserved, all phases are correctly integrated, and the platform remains fully functional with Observability both enabled and disabled. This phase certifies the epic as production-ready.

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
- [ ] Verify span coverage across all eight tracing domains.
- [ ] Verify cost accounting for known and unknown models.
- [ ] Verify `/metrics` exposition and owner-scoped usage REST API.
- [ ] Verify `agent`/`workflow` eval levels and regression checking.

### Integration Validation

- [ ] Verify `TracerRegistry`/`MeterRegistry` real-vs-no-op behaviour.
- [ ] Verify `TracingLLMProvider` wrapping in `ProviderFactory`.
- [ ] Verify Observability REST API functionality.
- [ ] Verify evaluation CLI functionality (`--level agent`, `--level workflow`, `--check-regression`).

### Regression Testing

- [ ] Execute full backend regression suite.
- [ ] Execute full frontend regression suite.
- [ ] Verify chat, RAG, MCP, memory, voice, agent, tool, and workflow functionality unchanged.
- [ ] Verify streaming responses unchanged.

### Feature Flag Validation

- [ ] Validate `OBSERVABILITY_ENABLED=true`.
- [ ] Validate `OBSERVABILITY_ENABLED=false`.
- [ ] Verify identical platform behaviour when disabled (byte-for-byte `usage_events` shape excluding new nullable columns).
- [ ] Verify graceful feature enablement.

### Performance Validation

- [ ] Measure tracing overhead per instrumented call site (span creation latency).
- [ ] Measure cost-calculation overhead per usage write.
- [ ] Measure `/metrics` exposition latency under representative counter/histogram volume.
- [ ] Verify acceptable production performance.

### Quality Validation

- [ ] Validate no content leakage across all spans, metrics, logs, and REST responses.
- [ ] Validate owner isolation on the usage endpoint.
- [ ] Validate fail-open behaviour (simulated span/metric/cost exceptions never fail the underlying operation).
- [ ] Validate regression detection against intentionally regressed fixtures.

### Production Readiness

- [ ] Review exported trace/metric samples against an OTLP-compatible backend (or console output).
- [ ] Review structured logging trace/span correlation.
- [ ] Verify error handling and failure recovery.
- [ ] Verify deployment configuration (migration `0008` applied).
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
- [ ] Execute evaluation suite (all levels, including `agent`/`workflow`).
- [ ] Execute regression check against baseline.
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
- [ ] Full-platform tracing and cost accounting operational.
- [ ] Frontend Observability dashboard operational.
- [ ] Existing platform functionality unchanged.
- [ ] Production deployment ready.

**Acceptance**

- All Part I architectural constraints have been preserved.
- All implementation phases have been successfully completed.
- Observability integrates seamlessly into the existing platform architecture.
- Existing chat, RAG, MCP, memory, voice, agent, tool, and workflow behaviour remain unchanged when `OBSERVABILITY_ENABLED=false`.
- Performance remains within acceptable production limits.
- All quality gates pass.
- Observability & Evaluation is approved for production deployment.

**Exit Criteria**

- All validation activities completed.
- Regression suite passed.
- Performance validation approved.
- Production readiness confirmed.
- Epic formally completed.

**Rollback**

- Disable `OBSERVABILITY_ENABLED`.
- Downgrade Alembic migration `0008_observability_usage_cost` if required.
- Redeploy the previous stable release if required.
- Verify platform functionality without Observability.
- Confirm rollback validation passes.
- Record rollback outcome if executed.

**Completion Record**

| Metric                                           | Result |
| ------------------------------------------------ | ------ |
| Backend Tests                                    |        |
| Frontend Tests                                   |        |
| Integration Tests                                |        |
| Evaluation Suite (all levels + regression check) |        |
| Performance Validation                           |        |
| Feature Flag Regression                          |        |
| Production Readiness                             |        |
| Release Summary Published                        |        |
| Epic Status                                      |        |

---

# PR Map

One PR per phase.

- v2/epic-07/phase-00-baseline
- v2/epic-07/phase-01-tracing-foundation
- v2/epic-07/phase-02-llm-prompt-tracing
- v2/epic-07/phase-03-tool-agent-tracing
- v2/epic-07/phase-04-rag-memory-voice-workflow-tracing
- v2/epic-07/phase-05-cost-metrics
- v2/epic-07/phase-06-rest-api
- v2/epic-07/phase-07-eval-agent-workflow
- v2/epic-07/phase-08-regression-datasets
- v2/epic-07/phase-09-frontend
- v2/epic-07/phase-10-release

---

# Risks

| Risk                                            | Mitigation                                                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Tracing/metrics overhead in hot paths           | No-op tracer/meter when disabled; span helpers are lightweight context managers; fail-open on error    |
| Content leakage via span/metric attributes      | Reuses `app.core.logging.sanitize_value`; explicit "never attach content" invariant + tests            |
| Vendor lock-in via SDK choice                   | OTel API only; exporter is configuration, not code                                                     |
| Inaccurate cost figures                         | Explicitly "approximate, not billing-grade"; `NULL` for unpriced models rather than a wrong number     |
| Pricing table drift from real provider pricing  | `pricing_version` stamped on every row so historical figures remain interpretable after a table update |
| Regression baseline staleness                   | `--update-baseline` is an explicit, separate, auditable CLI action                                     |
| Workflow background-task trace correlation gaps | Best-effort span link (not a hard dependency); documented as a known limitation, not silently dropped  |
| `/metrics` exposing sensitive data              | Aggregate-only counters/histograms; explicit invariant + tests forbidding owner-identifying labels     |
| Accidental 100% sampling in production          | `otel_traces_sample_ratio` deployment config must override the dev-safe `1.0` default; documented per-environment recommendations (§ Trace Sampling Strategy) |
| Prometheus cardinality explosion                | Fixed metric-label allowlist (§ Metric Cardinality Policy) enforced by a dedicated test guard; high-cardinality identifiers stay on spans, never metrics |
| Feature regression                              | `OBSERVABILITY_ENABLED` flag-off parity                                                                |
| Eval framework fork/divergence                  | Extends `app/ai/evaluation/` in place; no parallel evaluation system                                   |

---

# Observability

Structured metrics this epic itself emits (superset of the fields Epic 06 pre-declared).

| Field                                | Purpose                    |
| ------------------------------------ | -------------------------- |
| `observability_enabled`              | Feature flag state         |
| `llm_requests_total`                 | LLM call volume            |
| `llm_token_usage`                    | Token usage distribution   |
| `llm_cost_usd_total`                 | Approximate spend          |
| `tool_calls_total`                   | Tool call volume           |
| `tool_call_latency_ms`               | Tool execution latency     |
| `agent_iterations_total`             | Agent loop volume          |
| `workflow_runs_started`              | Trigger volume             |
| `workflow_runs_completed`            | Completion volume          |
| `workflow_runs_failed`               | Failure volume             |
| `workflow_node_execution_latency_ms` | Per-node execution latency |
| `workflow_checkpoint_latency_ms`     | Persistence latency        |
| `workflow_retry_count`               | Retry volume               |
| `workflow_approval_pending_count`    | Pending approvals          |
| `workflow_parallel_branch_count`     | Fan-out width distribution |

No prompt, tool, or message content is ever attached to a span, metric, or log field emitted by this epic.

---

# Definition of Done

- [ ] All Part I architectural invariants preserved.
- [ ] Public APIs frozen after Phase 1.
- [ ] Every tracing domain in Part I emits correlated, content-free spans when enabled.
- [ ] `usage_events` cost accounting is additive, versioned, and never blocking.
- [ ] Observability REST API and `/metrics` operate per Part I contract, with strict owner isolation.
- [ ] Evaluation framework extended in place with `agent`/`workflow` levels and regression checking — no parallel evaluation system.
- [ ] `OBSERVABILITY_ENABLED=false` preserves Epic 06 behaviour (full flag-off parity validated in Phase 10).
- [ ] Backend and frontend tests pass; coverage ≥80% on `app/ai/observability/`.
- [ ] Release summary published.
- [ ] User authorizes Epic 08.

---

## Files index

| Path                                                      | Action        | Owner    | Phase   |
| --------------------------------------------------------- | ------------- | -------- | ------- |
| `docs/audits/post-mvp-v2-epic7-phase-0-baseline-audit.md` | create        | Docs     | 0       |
| `pyproject.toml`                                          | modify        | Core     | 1       |
| `app/ai/observability/**`                                 | create        | Core     | 1–5     |
| `app/core/config.py`                                      | modify        | Core     | 1, 5, 8 |
| `backend-python/.env.example`                             | modify        | Docs     | 1       |
| `app/main.py`                                             | modify        | Adapter  | 1, 6    |
| `app/middleware/correlation_id.py`                        | modify        | Adapter  | 1       |
| `app/providers/factory.py`                                | modify        | Core     | 2       |
| `app/ai/prompts/manager.py`                               | modify        | Core     | 2       |
| `app/ai/tools/executor.py`                                | modify        | Core     | 3       |
| `app/ai/agent/runtime/default_agent.py`                   | modify        | Core     | 3       |
| `app/ai/rag/retriever.py`                                 | modify        | Core     | 4       |
| `app/ai/memory/manager.py`                                | modify        | Core     | 4       |
| `app/ai/voice/session.py`                                 | modify        | Core     | 4       |
| `app/ai/workflow/engine/executor.py`                      | modify        | Core     | 4       |
| `app/db/models.py`                                        | modify        | Core     | 5       |
| `app/db/usage.py`                                         | modify        | Core     | 5       |
| `alembic/versions/0008_observability_usage_cost.py`       | create        | Core     | 5       |
| `app/schemas/observability.py`                            | create        | Core     | 6       |
| `app/routers/observability.py`                            | create        | Adapter  | 6       |
| `app/routers/health.py`                                   | modify        | Adapter  | 6       |
| `app/ai/deps.py`                                          | modify        | Adapter  | 1, 6    |
| `app/ai/evaluation/datasets.py`                           | modify        | Core     | 7       |
| `app/ai/evaluation/runners.py`                            | modify        | Core     | 7       |
| `app/ai/evaluation/report.py`                             | modify        | Core     | 7, 8    |
| `app/ai/evaluation/regression.py`                         | create        | Core     | 8       |
| `app/ai/evaluation/cli.py`                                | modify        | Core     | 8       |
| `tests/data/evaluation/sample.yaml`                       | modify        | Tests    | 7, 8    |
| `tests/data/evaluation/baseline-report.json`              | create        | Tests    | 8       |
| `tests/ai/observability/**`                               | create        | Tests    | 1–6     |
| `tests/ai/evaluation/**`                                  | modify/create | Tests    | 7, 8    |
| `tests/test_observability_router.py`                      | create        | Tests    | 6       |
| `tests/fakes.py`                                          | modify        | Tests    | 1–7     |
| `frontend/src/api/observabilityClient.ts`                 | create        | Frontend | 9       |
| `frontend/src/types/observability.ts`                     | create        | Frontend | 9       |
| `frontend/src/pages/ObservabilityPage.tsx`                | create        | Frontend | 9       |
| `frontend/src/api/healthClient.ts`                        | modify        | Frontend | 9       |
| `docs/releases/post-mvp-v2-epic7-release-summary.md`      | create        | Docs     | 10      |

---

## Changelog

| Version | Date       | Changes                                                                                          |
| ------- | ---------- | ------------------------------------------------------------------------------------------------ |
| 1       | 2026-08-07 | Initial epic draft — Part I design + Part II 11-phase execution plan (Phases 0–10). Not started. |
| 1.1     | 2026-08-07 | Added Trace Sampling Strategy, Metric Cardinality Policy, and Span Naming Convention sections; explicit streaming `llm.complete` span lifecycle; pricing table lifecycle clarification (no retroactive recalculation); evaluation reproducibility metadata (`model`/`model_version`/`temperature`/`seed`/`prompt_version`) on `EvalCaseResult`; flagged `ObservabilityStore` as a provisional façade to collapse into `UsageAggregator` in Phase 6 if it adds no logic. Part I + Phases 1, 2, 5, 6, 7, 8 sync. Not started. |
