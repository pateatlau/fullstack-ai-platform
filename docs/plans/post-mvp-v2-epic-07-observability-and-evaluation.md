---
epic: v2-07
title: Observability & Evaluation
status: in_progress
version: 1.19
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
- Non-blocking — telemetry-boundary failures (span creation, metric recording, cost computation) never fail the wrapped operation; business exceptions from providers, tools, and request handlers always propagate
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

Provider call site (ProviderFactory → TracingLLMProvider: spans/metrics only)
        │
ChatService / ToolChatService → SqlUsageStore.record(...)  ← single usage-recording boundary
        │  CostCalculator (ProviderUsage + ModelPricingTable → cost_usd, pricing_version)
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

| Topic                        | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Deferred to                                                           |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Tracing SDK                  | OpenTelemetry API/SDK only; console exporter by default (dev), OTLP/HTTP exporter when `otel_exporter_otlp_endpoint` is configured; no vendor SDK imported into core packages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Vendor-specific auto-instrumentation bundle → future                  |
| Flag semantics               | `OBSERVABILITY_ENABLED=false` installs a process-wide no-op `TracerProvider`/`MeterProvider` (OTel API's own `NoOpTracer`, not a bespoke reimplementation) — zero spans, zero metrics, zero overhead beyond a cheap flag check; every instrumented call site's return value and behavior is unchanged                                                                                                                                                                                                                                                                                                                                                                                                                         | —                                                                     |
| Span/metric content policy   | Attributes carry identifiers, counts, durations, provider/model names, and status only; reuses `app.core.logging.sanitize_value` for any dynamic attribute; prompt text, tool arguments/results, and chat message content are never attached to a span or metric                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Opt-in payload capture for debugging → future                         |
| Trace context propagation    | Per-HTTP-request trace context flows through OTel's built-in context + the existing `correlation_id` middleware (`request_id` and `trace_id`/`span_id` both bound into log context). Workflow runs that continue on an in-process `asyncio.Task` after the triggering request completes start a new root span with a span **link** to the full originating `SpanContext` (`trace_id`, `span_id`, `trace_flags`, `trace_state`) captured at `WorkflowManager.start_run()` (or at `flush_deferred_run_schedules()` when `defer_schedule=True`); link omitted when context is invalid/unavailable (best-effort, never blocks scheduling). `resume()` / `reconcile_orphaned_runs()` open a fresh run-level root span with no link | Fully distributed cross-service trace propagation → future            |
| Cost accounting              | Static, versioned per-(provider, model) rates in git-tracked `config/model_pricing.yaml`; `pricing_version` version-lock at startup; computed at `SqlUsageStore.record()` from `ProviderUsage`; approximate, not billing-grade; unknown provider/model → `cost_usd=NULL`, never blocks the usage write                                                                                                                                                                                                                                                                                                                                                                                                                        | Real-time reconciliation with provider invoices → future              |
| Metrics exposition           | OTel `MeterProvider` backed by a Prometheus reader, exposed at `GET /metrics` in Prometheus text format; contains only aggregate counters/histograms (no owner-identifying labels); owner-scoped cost/usage detail is served only via the authenticated REST API, never `/metrics`                                                                                                                                                                                                                                                                                                                                                                                                                                            | Push-based metrics backend (CloudWatch, Datadog) integration → future |
| Evaluation framework         | Extends `app/ai/evaluation/` in place; `agent`/`workflow` levels reuse `DefaultAgent`/`WorkflowManager`. Targeted `--level agent`/`--level workflow` may skip when prerequisites are missing; **`--level all` and `--update-baseline` require** enabled agent/workflow runtimes and Postgres — no skipped agent/workflow cases in comparable baselines                                                                                                                                                                                                                                                                                                                                                                        |
| Regression baseline          | Git-tracked `tests/data/evaluation/baseline-report.json` from `EvalRunReport`; includes **run environment metadata** (feature flags + Postgres availability); `RegressionChecker` rejects or flags non-comparable baselines before pass-rate/latency comparison                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Dashboard scope              | The frontend Observability dashboard shows only DB-native, owner-scoped cost/usage summaries sourced from `usage_events`; trace/span visualization is explicitly out of scope — operators point any OTLP-compatible backend (Jaeger, Tempo, Grafana, Datadog) at the configured exporter                                                                                                                                                                                                                                                                                                                                                                                                                                      | Embedded trace explorer UI → future                                   |
| Instrumentation failure mode | Telemetry boundaries only: exceptions from **span creation**, **metric recording**, or **cost computation** are caught, logged at `warning`, and suppressed (fail-open). Exceptions from the wrapped provider call, tool execution, or other business/request logic are **never** caught by observability helpers — they propagate unchanged. Tests (Phase 1+) must cover both categories: telemetry failure → operation succeeds; business failure → exception propagates.                                                                                                                                                                                                                                                   | —                                                                     |
| Trace sampling               | `ParentBased(TraceIdRatioBased(otel_traces_sample_ratio))` sampler; ratio defaults are environment-dependent (see § Trace Sampling Strategy) — 100% sampling is a dev-only default, never assumed safe in production                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Tail-based / adaptive sampling → future                               |
| Metric cardinality           | Metric label **keys and values** are bounded (see § Metric Cardinality Policy): fixed allowlisted label names, value registries with normalization to `other` for unknown/plugin-defined inputs; no per-user, per-session, per-request, or per-trace label                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

---

## Trace Sampling Strategy

Sampling controls trace volume/cost without disabling observability. The platform uses OTel's standard `ParentBased(TraceIdRatioBased(ratio))` sampler — a child span always follows its parent's sampling decision, and root spans are sampled at `otel_traces_sample_ratio`.

| Environment         | Recommended `otel_traces_sample_ratio` | Rationale                                                                                     |
| ------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| Development / local | `1.0` (100%)                           | Full visibility while iterating; low volume                                                   |
| Staging / CI        | `0.25` (25%)                           | Enough signal to catch regressions without excessive exporter/storage load                    |
| Production          | `0.05` (5%)                            | Representative sampling at sustained volume; raise temporarily when investigating an incident |

`otel_traces_sample_ratio` defaults to `1.0` (dev-safe) in `app/core/config.py`; deployment configuration (`.env` per environment) **must** override it for staging/production. This is called out explicitly so a 100% default is never silently carried into production. Sampling never affects metrics or cost accounting — `CostCalculator` and the OTel `Meter` instruments record every request regardless of trace sampling decisions, since cost/metric accuracy must not depend on trace volume.

---

## High-Level Flow

**Request tracing**

Incoming HTTP request
→ `correlation_id_middleware` mints/propagates `request_id`
→ (Phase 1, when `OBSERVABILITY_ENABLED`) the same middleware opens an explicit root span **`http.server`** for the request (ASGI/middleware-created — not OTel HTTP auto-instrumentation) and binds `trace_id`/`span_id` into log context alongside `request_id`
→ Downstream pipeline code opens child spans via the span helpers as it calls providers, prompts, tools, RAG, memory, voice, agent, or workflow subsystems
→ Spans close with duration + status; exporter ships them to console (dev) or the configured OTLP endpoint
→ Response returned; log context cleared (unchanged from Epic 06 baseline)

**Cost accounting**

LLM call via `ProviderFactory.get_provider()`
→ (if enabled) `TracingLLMProvider` wraps the provider for spans/metrics only — **no cost write**
→ Terminal `ProviderUsage` available at the existing usage-recording call site (`ChatService` / `ToolChatService`; streaming: terminal chunk only)
→ `SqlUsageStore.record(session_id, user_id|guest_id, message_id, provider, model, token counts, …)` — **single owner** of usage persistence and cost calculation; invokes `CostCalculator.price(provider, model, usage)` internally when `OBSERVABILITY_ENABLED`
→ Exactly one `usage_events` row persisted per generation with `cost_usd`, `pricing_version`, and the caller-supplied identity fields (`session_id`, owner, `message_id`)
→ `UsageAggregator` later aggregates rows for `GET /api/observability/usage`

**Evaluation & regression**

`make eval` (unchanged entry point)
→ `EvalDataset` loaded (extended schema: `agent`, `workflow` cases alongside existing `prompt`/`retrieval`/`e2e`)
→ Runners execute each case (`--level all` requires enabled agent/workflow runtimes + Postgres; no skipped agent/workflow in baseline-eligible runs)
→ `EvalRunReport` produced with `run_environment` + per-case reproducibility metadata
→ `RegressionChecker.compare(report, baseline)` (opt-in via `--check-regression`; rejects environment-incompatible baselines first)
→ CLI exits non-zero on any failing case, environment mismatch, or a regression finding beyond tolerance

---

## End-to-End Sequence

```text
Client
 │
 │ POST /api/chat  (or any instrumented pipeline entrypoint)
 ▼
correlation_id_middleware
 │  binds request_id; (if enabled) opens explicit root span "http.server" + binds trace_id/span_id into log context
 ▼
Router → Service (ChatService / RAGService / DefaultAgent / WorkflowExecutor / ...)
 │
 ├── prompt_span → span "prompt.render" ──► PromptManager.render()
 │
 ├── llm_span → span "llm.complete" ──► LLMProvider.complete_chat() / stream_chat()
 │        │     (streaming: span covers whole call; ends after final chunk; no cost write in provider wrapper)
 │
(at existing usage-recording boundary — ChatService / ToolChatService)
 SqlUsageStore.record(session_id, user_id|guest_id, message_id, provider, model, terminal usage, …)
 │        └── CostCalculator.price(...) inside record() → cost_usd, pricing_version (exactly once)
 │
 ├── tool_span → span "tool.execute" ──► ToolExecutor.execute()
 │
 ├── agent_span → span "agent.iteration" ──► DefaultAgent reasoning loop
 │
 ├── rag_span "rag.retrieve" / memory_span "memory.retrieve"|"memory.extract" / voice_span "voice.session"
 │
 └── workflow_span → span "workflow.run"|"workflow.node" ──► WorkflowExecutor.step()
          (background asyncio.Task — fresh root span; links to originating SpanContext when captured at start_run)
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
SqlUsageStore.record(...)   ← single usage-recording boundary
      │
CostCalculator (invoked inside record(); ProviderUsage + ModelPricingTable → cost_usd, pricing_version)
      │
ModelPricingTable (`config/model_pricing.yaml`, version-locked at startup)
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

`SqlUsageStore.record()` is the **single usage-recording boundary** — the only place token usage is persisted and cost is calculated. `CostCalculator` is invoked exclusively inside `record()`, not in `TracingLLMProvider` or any other wrapper.

Call sites (`ChatService`, `ToolChatService`, …) pass terminal `ProviderUsage` plus existing identity fields (`session_id`, `user_id`/`guest_id`, `message_id`, `provider`, `model`, token counts, `request_id` where applicable). `record()` computes `cost_usd`/`pricing_version` once (when `OBSERVABILITY_ENABLED`) and writes exactly one append-only row. Streaming paths pass terminal-chunk usage only — mirroring the existing `ChatService` usage-write pattern.

`CostCalculator` responsibilities (called from `record()` only):

- Look up a `(provider, model)` entry in the active `ModelPricingTable` (per-1K-token input/output rates)
- Compute `cost_usd = (prompt_tokens / 1000) * input_rate + (completion_tokens / 1000) * output_rate`
- Return `None` (not zero, not an exception) when the provider/model has no pricing entry, or when `ProviderUsage` fields are `None` — cost is unknown, not free
- Stamp every priced record with the pricing table's `pricing_version` so historical rows remain interpretable after a price update
- Never raise past the caller — pricing errors are fail-open (`cost_usd=NULL`), consistent with the existing "estimated" `token_source` fallback pattern

### ModelPricingTable — canonical source

**Single source of truth:** git-tracked `backend-python/config/model_pricing.yaml`, loaded at process startup by `ModelPricingTable` (`app/ai/observability/cost/pricing.py`). Rates are **not** overridable per deployment via environment variables — only the file contents (released with the app) define prices. The setting `observability_cost_pricing_version` selects/validates the active table version; it does **not** carry rate values.

**Schema** (YAML):

```yaml
pricing_version: '2026-08' # required; immutable identifier for this exact rate set
models:
  - provider: openai # must match ProviderFactory provider name
    model: gpt-4o # exact model string used in usage_events.model
    input_usd_per_1k: 0.0025 # USD per 1,000 prompt/input tokens (≥ 0)
    output_usd_per_1k: 0.0100 # USD per 1,000 completion/output tokens (≥ 0)
```

**Validation rules** (fail fast at startup — process must not serve with an invalid table):

- `pricing_version` present, non-empty string
- `models` is a non-empty list
- Each entry has `provider`, `model`, `input_usd_per_1k`, `output_usd_per_1k`
- `provider`/`model` are non-empty strings; rates are finite numbers ≥ 0
- No duplicate `(provider, model)` pairs
- **Version lock:** file `pricing_version` **must equal** `settings.observability_cost_pricing_version` — startup error on mismatch (prevents two deployments from stamping the same version label with different rates)
- Unknown `(provider, model)` at `record()` time → `cost_usd=NULL`, `pricing_version=NULL` (no startup failure)

**`pricing_version` update process:**

1. Edit `backend-python/config/model_pricing.yaml` with new/changed rates.
2. Bump the file's top-level `pricing_version` (e.g. `"2026-08"` → `"2026-09"`) — **required whenever any rate changes**.
3. Update deployment config (`observability_cost_pricing_version`) to the same string.
4. Release/deploy; new `usage_events` rows stamp the new version; historical rows are never recalculated.

Two deployments running the same `pricing_version` value must load identical rate data (same release artifact or same committed file). Per-environment rate overrides are forbidden.

**Pricing table lifecycle:**

- A `usage_events.cost_usd` value is computed **once**, at write time, and is never recalculated retroactively when `ModelPricingTable` is later updated (a price change is not backfilled onto historical rows).
- `pricing_version` is the permanent, immutable record of which pricing table produced a given row's `cost_usd`; it is what makes a historical estimate interpretable after the table has since changed (e.g., "this row used the `2026-08` table, not today's").
- Comparing cost trends across a pricing-version boundary is a reporting-layer concern (`UsageAggregator` surfaces `pricing_version` alongside aggregates); it is not solved by mutating stored data.

---

## Metric Cardinality Policy

Prometheus-backed metrics degrade badly under high label cardinality — unbounded label **names** or **values** create unbounded time series. Every OTel metric instrument this epic defines (Phase 5) uses **only** allowlisted label keys, and every label value is normalized through a fixed registry before recording.

### Allowed label keys (forbidden keys never attached)

| Allowed label keys                                                       | Forbidden label keys                                                                                                                                                      |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `provider`, `model`, `tool_name`, `workflow_type`, `node_type`, `status` | `user_id`, `guest_id`, `session_id`, `request_id`, `trace_id`, `span_id`, `workflow_run_id`, `workflow_node_id`, `message_id`, prompt text, error messages, arbitrary IDs |

### Value registries and normalization

All metric recording goes through `normalize_metric_label(dimension, raw_value) -> str` (`app/ai/observability/metrics/labels.py`). Unknown, empty, or plugin/MCP-defined raw values map to **`other`** — never emitted as-is.

| Label key       | Accepted values (registry)                                                        | Normalization rule                                                                                                          |
| --------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `provider`      | `openai`, `gemini`, `groq`, `anthropic`, `other`                                  | Match `ProviderFactory` provider names; else `other`                                                                        |
| `model`         | Each `model` key in the active `ModelPricingTable`, plus `other`                  | Exact match against pricing-table registry loaded at startup; else `other` (raw model strings never pass through unbounded) |
| `tool_name`     | `web_search`, `workflow_execution`, `other`                                       | Match registered production tool names (`ToolRegistry`); MCP and future plugin tools → `other`                              |
| `node_type`     | `task`, `llm`, `agent`, `router`, `fork`, `join`, `approval`, `terminal`, `other` | Match `NodeType` (`app/ai/workflow/models/definition.py`); else `other`                                                     |
| `workflow_type` | `standard`, `other`                                                               | Platform/API-authored workflow definitions → `standard`; plugin-defined or unrecognized → `other`                           |
| `status`        | `succeeded`, `failed`, `skipped`, `other`                                         | Map domain terminal outcomes to this set; unrecognized → `other`                                                            |

Registries are code-defined constants (and pricing-table-derived model keys) — not free-form runtime strings. Adding a new **metric-visible** provider, production tool, or node type requires updating the registry (and tests); adding a priced model requires a `model_pricing.yaml` entry (existing model key or falls through to `other` until added).

This distinguishes **metrics** (Prometheus labels — bounded keys **and** values) from **spans** (OTel trace attributes, which tolerate higher cardinality like `run_id` and raw model/tool names). A `run_id` or raw MCP tool name may appear as a **span attribute** but must never appear as a **metric label value**. Phase 5 tests assert both permitted label keys and normalized label values.

---

## Evaluation Framework Extension

Builds on the existing `EvalLevel = Literal["prompt", "retrieval", "e2e"]` (`app/ai/evaluation/datasets.py`) rather than replacing it.

| New level  | Runner               | Reuses                                                                                             | Skip / fail policy                                                                                                                                                                |
| ---------- | -------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent`    | `AgentEvalRunner`    | `DefaultAgent`, fake provider/tools (same pattern as `_EvalLLMProvider`)                           | **`--level agent` only:** skip (not fail) when `AGENT_RUNTIME_ENABLED=false`. **`--level all` / `--update-baseline`:** hard fail if agent runtime disabled                        |
| `workflow` | `WorkflowEvalRunner` | `WorkflowManager`, `PostgresWorkflowStore` (same Postgres-availability check as `retrieval`/`e2e`) | **`--level workflow` only:** skip when `WORKFLOW_ENGINE_ENABLED=false` or Postgres unavailable. **`--level all` / `--update-baseline`:** hard fail if either prerequisite missing |

Each new case type extends `EvalCase`/`EvalDataset` parsing (`_parse_agent_case`, `_parse_workflow_case`) with the same "fail fast on malformed dataset" validation style as existing case parsers. `EvalCaseResult` gains only the fields needed for these levels (e.g., `tool_calls_correct: bool | None`, `terminal_status: str | None`) — no unrelated schema churn.

`RegressionChecker` is a pure function: `compare(current: EvalRunReport, baseline: EvalRunReport, *, pass_rate_tolerance_pct, latency_tolerance_pct) -> RegressionResult`.

1. **Environment comparability** — compare `run_environment` on both reports (see below). Mismatch → `RegressionResult.environment_mismatch` with a hard failure (non-zero CLI exit); do not compare pass rates/latencies across incompatible environments.
2. **Hard regressions** — any case that passed in the baseline and fails now (regardless of tolerance).
3. **Soft regressions** — per-level pass-rate drop or mean-latency increase beyond configured tolerance.

`RegressionResult` is JSON-serializable and printed alongside the existing console summary; it never mutates the baseline file itself — updating the baseline is an explicit `--update-baseline` CLI action, never automatic.

**Run environment metadata:** `EvalRunReport` gains a top-level `run_environment` object (persisted in `baseline-report.json` and every `.eval/eval-report.json`), captured once per run:

| Field                     | Purpose                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------- |
| `agent_runtime_enabled`   | `settings.agent_runtime_enabled` at run time                                                      |
| `workflow_engine_enabled` | `settings.workflow_engine_enabled` at run time                                                    |
| `postgres_available`      | Result of the same Postgres connectivity check used by `RetrievalEvalRunner`/`WorkflowEvalRunner` |
| `pgvector_available`      | Result of `pgvector_available()` (required for `retrieval`/`e2e`)                                 |

Baselines used for regression (`--update-baseline`, CI `--check-regression`) must be produced from `--level all` with all four prerequisites satisfied and **zero skipped `agent`/`workflow` cases**. `RegressionChecker` rejects baselines where `run_environment` indicates disabled flags/unavailable Postgres or where any `agent`/`workflow` result has `skipped=true`.

**Per-case reproducibility metadata:**

| Field            | Purpose                                                                                                                                                                     |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model`          | The concrete model used for the case (already implied by `settings_snapshot`, now recorded per-case for `agent`/`e2e`/`workflow` levels where a model override is possible) |
| `model_version`  | Provider-reported model version/snapshot id, when the provider exposes one                                                                                                  |
| `temperature`    | Sampling temperature used for the case                                                                                                                                      |
| `seed`           | Deterministic seed, when the provider supports one (`None` otherwise — never fabricated)                                                                                    |
| `prompt_version` | The `PromptManager` category/name/version rendered for the case                                                                                                             |

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
    │   │   ├── instruments.py      # counter/histogram definitions (names match Epic 06 § Observability)
    │   │   └── labels.py           # metric label registries + normalize_metric_label()
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

| Component          | Responsibility                                                                                                                                                                                                          | Inputs                                                                  | Outputs                            | Dependencies                                      |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------- |
| TracerRegistry     | Provides the process-wide OTel `Tracer` (real or no-op) based on `OBSERVABILITY_ENABLED`                                                                                                                                | Settings                                                                | `Tracer`                           | OpenTelemetry SDK                                 |
| MeterRegistry      | Provides the process-wide OTel `Meter` (real or no-op) and the Prometheus reader                                                                                                                                        | Settings                                                                | `Meter`, Prometheus registry       | OpenTelemetry SDK                                 |
| Span helpers       | Thin context managers that open a named span, attach sanitized attributes, and record status/duration                                                                                                                   | Domain-specific args (provider, tool name, node id, ...)                | Closed span                        | TracerRegistry, `app.core.logging.sanitize_value` |
| TracingLLMProvider | Wraps a concrete `LLMProvider` to emit `llm_span`s and record token/latency metrics without touching provider adapters                                                                                                  | Wrapped `LLMProvider`                                                   | Same `LLMProvider` interface       | Span helpers, MeterRegistry                       |
| CostCalculator     | Converts `ProviderUsage` into an approximate `cost_usd` — invoked only from `SqlUsageStore.record()`                                                                                                                    | provider, model, `ProviderUsage`                                        | `float \| None`, `pricing_version` | ModelPricingTable; `SqlUsageStore`                |
| ModelPricingTable  | Loads git-tracked `config/model_pricing.yaml`; validates schema and version lock at startup                                                                                                                             | `observability_cost_pricing_file`, `observability_cost_pricing_version` | Pricing entry or `None` per lookup | YAML file on disk; `Settings` version lock        |
| UsageAggregator    | Owner-scoped aggregation queries over `usage_events` (by day / provider / model)                                                                                                                                        | owner id, date range, group-by                                          | Usage/cost summary rows            | SQLAlchemy `AsyncSession`                         |
| ObservabilityStore | Read façade the router depends on (keeps router thin, mirrors `WorkflowStore`-style separation) — **provisional; collapse into `UsageAggregator` in Phase 6 if it adds no logic of its own (see Storage Architecture)** | Aggregation requests                                                    | Summary DTOs                       | UsageAggregator                                   |
| AgentEvalRunner    | Runs an `agent`-level eval case through `DefaultAgent` with fake provider/tools                                                                                                                                         | `EvalCase`                                                              | `EvalCaseResult`                   | `DefaultAgent`, `AGENT_RUNTIME_ENABLED`           |
| WorkflowEvalRunner | Runs a `workflow`-level eval case through `WorkflowManager` to a terminal run status                                                                                                                                    | `EvalCase`                                                              | `EvalCaseResult`                   | `WorkflowManager`, `WORKFLOW_ENGINE_ENABLED`      |
| RegressionChecker  | Compares a new `EvalRunReport` against the git-tracked baseline                                                                                                                                                         | Current + baseline reports, tolerances                                  | `RegressionResult`                 | —                                                 |

---

## Span Naming Convention

Every span helper opens a span under a **fixed, dot-namespaced name** — `{domain}.{action}` — regardless of the dynamic provider/tool/node involved. The dynamic detail (which provider, which tool, which node) is an **attribute**, never part of the name. This keeps span names low-cardinality and consistent across every OTLP-compatible backend, and is a distinct concern from the § Metric Cardinality Policy (which governs Prometheus _labels_, not trace _span names_).

| Helper                 | Span name                                                | Fixed regardless of                       |
| ---------------------- | -------------------------------------------------------- | ----------------------------------------- |
| HTTP root (middleware) | `http.server`                                            | route / method / status (attributes)      |
| `prompt_span`          | `prompt.render`                                          | category / name / version (attributes)    |
| `llm_span`             | `llm.complete`                                           | provider / model / streaming (attributes) |
| `tool_span`            | `tool.execute`                                           | tool name (attribute)                     |
| `agent_span`           | `agent.iteration`, `agent.tool_call`, `agent.reflection` | iteration index / tool name (attributes)  |
| `rag_span`             | `rag.retrieve`                                           | top_k / retrieved_count (attributes)      |
| `memory_span`          | `memory.retrieve`, `memory.extract`                      | —                                         |
| `workflow_span`        | `workflow.run`, `workflow.node`                          | run id / node type / attempt (attributes) |
| `voice_span`           | `voice.session`                                          | —                                         |

Every span helper function signature in Part I (e.g. `llm_span(provider, model, streaming)`) takes these dynamic values as **arguments used to populate attributes**, not to construct the span name.

---

## Tracing Domains

### LLM Spans — `llm.complete`

`llm_span(provider, model, streaming: bool)` wraps every `LLMProvider.complete_chat` / `complete_chat_with_tools` / `stream_chat` call via `TracingLLMProvider`, installed once in `ProviderFactory.get_provider()` rather than in each provider adapter. Attributes: `provider`, `model`, `streaming`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`, `latency_ms`. No message content.

**Streaming span lifecycle** (`stream_chat`): a single `llm.complete` span covers the entire stream, not one span per chunk.

1. The span **starts** immediately before the provider request is issued (before the first chunk is awaited).
2. Each streamed chunk is consumed inside the span's context but does **not** create a child span or record usage — chunks are not individually observable events, only the overall call is.
3. The span **ends** only after the final streamed chunk has been emitted (i.e., after the async generator is exhausted / `finish_reason` is set).
4. `prompt_tokens`/`completion_tokens`/`total_tokens` span attributes come **only from the terminal chunk's usage payload**; `cost_usd` is computed exclusively inside `SqlUsageStore.record()` at the service-layer usage boundary (not in `TracingLLMProvider`). Intermediate chunks never trigger cost calculation or usage persistence.

### Prompt Spans — `prompt.render`

`prompt_span(category, name, version)` wraps `PromptManager.render()`. Attributes: `category`, `name`, `version`, `variable_count`, `rendered_length_chars`. Never the rendered text itself.

### Tool Spans — `tool.execute`

`tool_span(tool_name)` wraps `ToolExecutor.execute()`. Attributes: `tool_name`, `success`, `retry_count`, `latency_ms`, `authorization_result`. Never tool arguments or results.

### Agent Spans — `agent.iteration` / `agent.tool_call` / `agent.reflection`

`agent_span("iteration" | "tool_call" | "reflection")` wraps the `DefaultAgent` reasoning loop per iteration. Attributes: `iteration_index`, `tool_calls_count`, `finish_reason`, `latency_ms`. Never scratchpad/reasoning content.

### RAG / Memory / Voice Spans — `rag.retrieve` / `memory.retrieve` / `memory.extract` / `voice.session`

`rag_span("retrieve")`, `memory_span("retrieve" | "extract")`, `voice_span("session")` wrap `Retriever.retrieve()`, the Memory retrieval/extraction entry points, and voice session lifecycle events respectively. Attributes: counts (`retrieved_count`, `top_k`), latency, and status only.

### Workflow Spans — `workflow.run` / `workflow.node`

`workflow_span("run" | "node")` wraps `WorkflowExecutor.step()` at both the run level (one span per `start_run`/`resume` invocation) and the node level (one span per `WorkflowNodeExecution` attempt). Attributes: `node_type`, `attempt`, `status`, `latency_ms`; run-level spans additionally carry `run_id` as a **span attribute** (never a metric label — see § Metric Cardinality Policy). Because the executor continues on an in-process `asyncio.Task` that can outlive the triggering HTTP request, the run-level root span is opened inside the background task (not as a child of the request span) and carries a span **link** to the originating `SpanContext` when one was captured — best-effort correlation, not a hard parent-child dependency.

**Background trace-link contract:** At `WorkflowManager.start_run()` (or `flush_deferred_run_schedules()` when `defer_schedule=True`), snapshot the active OTel `SpanContext` via `trace.get_current_span().get_span_context()` when `SpanContext.is_valid`, recording `trace_id`, `span_id`, `trace_flags`, and `trace_state`. Pass the snapshot immutably into `_schedule_run()` / the background `asyncio.Task` (do not rely on OTel context propagation across the task boundary). Inside the task, open a fresh run-level root span (`workflow.run`) and attach a span link to the snapshot when valid; omit the link when the snapshot is missing or invalid — scheduling and execution must never fail because of trace capture. For crash recovery (`resume()`, `reconcile_orphaned_runs()`), the originating request span is unavailable: open a fresh run-level root span with **no** span link (attributes only, e.g. `run_id`, resume reason).

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
- **Cost accounting** — `CostCalculator` runs exclusively inside `SqlUsageStore.record()`; existing call sites (`ChatService`/`ToolChatService`) pass terminal usage and identity fields unchanged; no cost logic in `TracingLLMProvider`.
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

Internal (may evolve): OTel SDK bootstrap internals, Prometheus registry wiring, baseline snapshot file format, `ObservabilityStore` query internals.

---

## Configuration defaults

| Setting                                            | Default                                                                                             |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `OBSERVABILITY_ENABLED`                            | **`false`**                                                                                         |
| `otel_service_name`                                | `"fullstack-ai-platform"`                                                                           |
| `otel_exporter_otlp_endpoint`                      | `""` (empty = console exporter)                                                                     |
| `otel_traces_sample_ratio`                         | `1.0` (dev-safe default — **override per environment**; see § Trace Sampling Strategy)              |
| `observability_cost_pricing_file`                  | `"config/model_pricing.yaml"` (relative to `backend-python/`; git-tracked canonical table)          |
| `observability_cost_pricing_version`               | `"2026-08"` (must match `pricing_version` in the pricing file at startup — see § ModelPricingTable) |
| `observability_regression_pass_rate_tolerance_pct` | `5.0`                                                                                               |
| `observability_regression_latency_tolerance_pct`   | `20.0`                                                                                              |

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
- `make eval --level agent` and `make eval --level workflow` skip cleanly (not fail) when their respective prerequisite is missing; **`make eval --level all`** and **`--update-baseline`** require enabled agent/workflow runtimes and available Postgres — hard fail otherwise (no skipped agent/workflow in comparable baselines)
- `make eval --check-regression` rejects environment-incompatible baselines before comparing metrics; otherwise detects hard/soft regressions against the git-tracked baseline
- A tracing, metrics, or cost-calculation failure never fails the underlying request, tool call, or node execution
- Every metric instrument uses only the § Metric Cardinality Policy allowlisted label keys and registry-normalized values; no owner/session/request/trace identifier or raw unbounded string ever appears as a metric label
- Deployment configuration overrides `otel_traces_sample_ratio` per § Trace Sampling Strategy — 100% sampling is never the production default
- Coverage ≥80% on `app/` and `app/ai/observability/`
- No prompt, tool argument/result, or message content in spans, metrics, or the Observability REST API responses

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **No content in telemetry** — spans, metrics, and Observability REST responses carry identifiers, counts, durations, and status only; `app.core.logging.sanitize_value` is reused for any dynamic attribute, never bypassed.
- **No vendor lock-in** — tracing/metrics code depends only on the OpenTelemetry API; exporter selection is configuration (`otel_exporter_otlp_endpoint`), never a per-vendor code branch.
- **Reuse, don't reimplement** — cost figures derive only from the existing `ProviderUsage`; `agent`/`workflow` eval levels reuse `DefaultAgent`/`WorkflowManager` exactly; the evaluation framework is extended in place, not forked.
- **Non-blocking instrumentation** — observability helpers catch and log (at `warning`) only telemetry-boundary failures (span creation, metric recording, cost computation); they never catch or alter exceptions raised by the wrapped operation. Business/provider/tool/request exceptions always propagate.
- **Flag-off parity** — `OBSERVABILITY_ENABLED=false` preserves Epic 06 behaviour on every hot path, including exact `usage_events` write shape (minus the new nullable columns) and existing eval level results.
- **Owner isolation** — `GET /api/observability/usage` is strictly caller-scoped; `GET /metrics` never contains owner-identifying labels.
- **Bounded metric cardinality** — every counter/histogram uses only the § Metric Cardinality Policy allowlisted label keys; all label values pass through `normalize_metric_label()` and a fixed registry (`other` for unknown/plugin/MCP inputs); high-cardinality raw identifiers are span attributes only, never metric labels.
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

_Reverified in Phase 0 (2026-08-07). See [Phase 0 baseline audit](../audits/post-mvp-v2-epic7-phase-0-baseline-audit.md)._

| Area                     | State                                                                                                                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend tests / coverage | 1551 passed, 89.05% `app/`                                                                                                                                                                      |
| Frontend tests           | 268 passed (43 files); lint + build pass                                                                                                                                                        |
| Integration tests        | Workflow suite 241 passed; router 23; tool 11; streaming 26                                                                                                                                     |
| Eval CLI                 | Unit + CLI smoke pass (`sample.yaml`; report schema v2; `--level all` prerequisite gate); full agent/workflow Postgres integration sign-off pending |
| Chat pipeline            | Stable — `ChatService` + `UnifiedChatService`, Memory fully wired                                                                                                                               |
| Agent Framework          | Completed (Epic 01); `AGENT_RUNTIME_ENABLED` behind flag                                                                                                                                        |
| Memory subsystem         | Completed (Epic 05); `MEMORY_ENABLED` behind flag                                                                                                                                               |
| Workflow Engine          | Completed (Epic 06); `WORKFLOW_ENGINE_ENABLED` behind flag                                                                                                                                      |
| Observability            | Phases 4–6 complete (spans, cost/metrics, REST API + `/metrics`); Phase 7 in progress (agent/workflow eval unit + CLI checks done; Postgres integration checks pending); Phases 8–10 remain |

---

## Phase Status

| Phase | Name                                          | Effort | Status      |
| ----- | --------------------------------------------- | ------ | ----------- |
| 0     | Baseline Audit                                | XS     | Completed   |
| 1     | Tracing & Metrics Foundation                  | M      | Completed   |
| 2     | LLM Provider & Prompt Tracing                 | M      | Completed   |
| 3     | Tool & Agent Tracing                          | M      | Completed   |
| 4     | RAG, Memory, Voice & Workflow Tracing         | L      | Completed   |
| 5     | Token & Cost Metrics                          | L      | Completed   |
| 6     | Observability REST API & `/metrics`           | M      | Completed   |
| 7     | Evaluation Framework: Agent & Workflow Levels | L      | In Progress |
| 8     | Prompt Regression & Benchmark Datasets        | M      | Not Started |
| 9     | Frontend Observability Dashboard              | S      | Not Started |
| 10    | Validation & Release                          | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS

**Objective**

Establish a verified implementation baseline before introducing Observability instrumentation. Confirm the existing platform is stable, all architectural dependencies are understood, and Epic 07 scope (OTel tracing, metrics, cost accounting, REST API, evaluation extensions) is not yet implemented — aside from the pre-existing `UsageEvent` / `SqlUsageStore.record()` token-usage recording.

**Deliverables**

- `docs/audits/post-mvp-v2-epic7-phase-0-baseline-audit.md`
- Architecture inventory
- Dependency verification
- Feature flag verification
- Platform readiness assessment
- Baseline quality metrics
- Implementation readiness checklist

**Steps**

## Platform Verification

- [x] Confirm Epic 06 Phase 12 complete / authorized for Epic 07.
- [x] Inventory `app/core/logging.py`, `app/middleware/correlation_id.py`.
- [x] Inventory `app/db/usage.py`, `UsageEvent`, `ProviderUsage`.
- [x] Inventory `app/ai/evaluation/` (datasets, runners, metrics, report, cli) and `make eval`.
- [x] Verify chat, RAG, MCP, memory, voice, agent, tool, and workflow pipelines all remain operational.
- [x] Verify streaming responses remain operational.

## Architecture Review

- [x] Review the frozen Part I architecture.
- [x] Verify all architectural invariants are understood.
- [x] Identify every existing call site each span helper will instrument.
- [x] Identify existing extension points (`ProviderFactory`, DI factories, feature flag infra).
- [x] Confirm no Epic 07 OpenTelemetry, metrics, cost-accounting, or REST API implementation exists (pre-existing `UsageEvent` / `SqlUsageStore.record()` token usage only).
- [x] Record implementation assumptions.

## Dependency Verification

- [x] Verify PostgreSQL configuration and `usage_events` current schema.
- [x] Confirm target OpenTelemetry SDK/exporter package versions.
- [x] Verify existing provider abstractions (`LLMProvider`, `ProviderFactory`).
- [x] Verify dependency injection configuration (`app/ai/deps.py`).
- [x] Verify feature flag infrastructure.

## Codebase Inventory

- [x] Inventory `PromptManager`, `ToolExecutor`, `DefaultAgent`, `WorkflowExecutor`, RAG/Memory/Voice entry points.
- [x] Inventory existing Alembic migrations and numbering (`0007_workflow_tables` is latest).
- [x] Record components to be reused vs. newly introduced.

## Baseline Quality Validation

- [x] Execute lint.
- [x] Execute type checking.
- [x] Execute unit tests.
- [x] Execute integration tests.
- [x] Execute evaluation suite.
- [x] Record baseline quality metrics.

## Implementation Readiness

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

- [x] Chat, RAG, memory, voice, agent, tool, and workflow functionality verified.
- [x] Streaming functionality verified.
- [x] All quality gates pass.

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

- [x] No rollback required.
- [x] This phase introduces no functional code changes.

**Completion Record**

| Metric                   | Result                                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------------------------- |
| Lint                     | ✅ PASS                                                                                                 |
| Format check             | ✅ PASS                                                                                                 |
| Typecheck                | ✅ PASS                                                                                                 |
| Unit Tests               | ✅ 1551 passed                                                                                          |
| Integration Tests        | ✅ Workflow 241; router 23; tool 11; streaming 26                                                       |
| Evaluation Suite         | ✅ 5/5                                                                                                  |
| Frontend tests           | ✅ 268 passed (43 files); build pass                                                                    |
| Platform Readiness       | ✅ Confirmed                                                                                            |
| Baseline Audit Published | ✅ [post-mvp-v2-epic7-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic7-phase-0-baseline-audit.md) |

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

## Package Structure

- [x] Create the `app/ai/observability/` package with `tracing/`, `metrics/`, `cost/`, `aggregation/` subpackages.
- [x] Add package exports through `__init__.py`.
- [x] Verify package imports are dependency-cycle free.

## Dependencies

- [x] Add `opentelemetry-api`, `opentelemetry-sdk` to `pyproject.toml`.
- [x] Add an OTLP HTTP exporter package and a Prometheus exporter/reader package.
- [x] Pin versions consistent with `requires-python = ">=3.12"`.

## Tracer / Meter Registry

- [x] Implement `TracerRegistry.get_tracer(name) -> Tracer` — returns a real tracer when `OBSERVABILITY_ENABLED=true`, else OTel's `NoOpTracer`.
- [x] Configure a console `SpanExporter` by default; switch to an OTLP/HTTP exporter when `otel_exporter_otlp_endpoint` is set.
- [x] Configure a `ParentBased(TraceIdRatioBased(otel_traces_sample_ratio))` sampler per Part I § Trace Sampling Strategy; verify `.env.example` documents environment-specific recommended ratios (dev `1.0` / staging `0.25` / production `0.05`).
- [x] Implement `MeterRegistry.get_meter(name) -> Meter` with the same real/no-op split, backed by a Prometheus reader.
- [x] Ensure both registries initialize once per process (idempotent bootstrap in `app/main.py` startup).
- [x] Verify metric/cost recording paths (Phase 5) are wired independently of the trace sampler — a sampled-out span must never suppress a metric or cost record.

## Span Helper Scaffold

- [x] Implement the span helper module (`spans.py`) with the context-manager signatures frozen in Part I (bodies are no-ops / generic until Phases 2–4 wire real call sites).
- [x] Ensure span helpers sanitize any dynamic attribute via `app.core.logging.sanitize_value`.
- [x] Ensure span helpers catch and log (not raise) only OTel/telemetry exceptions at span open, attribute set, span close, and metric hooks — never wrap or catch the `yield` body / wrapped call.
- [x] Add fail-open tests covering both exception categories: (1) simulated telemetry failure → wrapped operation completes, warning logged; (2) simulated business failure from wrapped call → exception propagates unchanged.

## Logging Correlation

- [x] Extend `correlation_id_middleware` to open an explicit per-request root span **`http.server`** when Observability is enabled (middleware/ASGI-created — no OTel HTTP auto-instrumentation).
- [x] Bind `trace_id`/`span_id` into log context alongside `request_id` from that root span when Observability is enabled.
- [x] Verify log context is unaffected (no `trace_id`/`span_id` keys) when the flag is off.

## Configuration

- [x] Add `OBSERVABILITY_ENABLED` feature flag (default `false`).
- [x] Add `otel_service_name`, `otel_exporter_otlp_endpoint`, `otel_traces_sample_ratio` settings.
- [x] Preserve backward compatibility when disabled.

## Testing

- [x] Add `TracerRegistry`/`MeterRegistry` real-vs-no-op tests.
- [x] Add in-memory span exporter tests verifying span helper attribute sanitization.
- [x] Add logging-correlation tests (flag on/off).
- [x] Add package import tests.

**Verify**

- `make lint`
- `make typecheck`
- `pytest tests/ai/observability/test_tracer_registry.py tests/ai/observability/test_meter_registry.py`

Additional verification:

- [x] Flag off yields a genuine no-op tracer/meter (zero exporter calls).
- [x] Flag on yields a real tracer/meter with a console exporter by default.
- [x] No circular imports detected.
- [x] Feature flag defaults to disabled.

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

| Metric             | Result                                         |
| ------------------ | ---------------------------------------------- |
| Lint               | ✅ PASS                                        |
| Typecheck          | ✅ PASS                                        |
| Phase 1 unit tests | ✅ 17 passed (`tests/ai/observability/`)       |
| Public APIs frozen | ✅ TracerRegistry, MeterRegistry, span helpers |
| Pipeline wiring    | ✅ None (infrastructure-only)                  |
| User confirmation  | ⏳ Pending                                     |

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

## LLM Tracing

- [x] Implement `TracingLLMProvider` wrapping `complete_chat`, `complete_chat_with_tools`, and `stream_chat`.
- [x] Open a span named `llm.complete` (`llm_span(provider, model, streaming)`) around each call; record `provider`, `model`, `streaming`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`, `latency_ms` as span attributes (per Part I § Tracing Domains, `llm.complete` is the fixed span name; provider/model are attributes, not name components).
- [x] Implement the streaming span lifecycle exactly per Part I § LLM Spans: start the `llm.complete` span immediately before issuing the `stream_chat` request; keep it open across every yielded chunk (no per-chunk child span); close it only after the async generator is exhausted.
- [x] Record `prompt_tokens`/`completion_tokens`/`total_tokens` on the span from the terminal chunk's usage payload only — never from intermediate chunks; **do not** call `CostCalculator` or write usage here (cost is owned by `SqlUsageStore.record()` at the service layer).
- [x] Wrap the provider returned by `ProviderFactory.get_provider()` only when `OBSERVABILITY_ENABLED=true`; return the concrete provider unmodified otherwise.
- [x] Verify no individual provider adapter file (`openai_provider.py`, `anthropic_provider.py`, `gemini_provider.py`, `groq_provider.py`) is modified.

## Prompt Tracing

- [x] Wrap `PromptManager.render()` with `prompt_span(category, name, version)`.
- [x] Record `variable_count`, `rendered_length_chars` as attributes; never the rendered text.
- [x] Verify rendering behaviour and return value are unchanged.

## Testing

- [x] Add `TracingLLMProvider` tests (fake provider + in-memory span exporter) for `complete_chat`, `complete_chat_with_tools`, `stream_chat`.
- [x] Add a streaming lifecycle test: exactly one `llm.complete` span per `stream_chat` call (not one per chunk), span duration covers the full stream; usage/cost persistence verified at `SqlUsageStore.record()` (exactly once, terminal chunk).
- [x] Add `ProviderFactory` wrapping tests (flag on/off).
- [x] Add `PromptManager` span tests (attributes present, content absent).
- [x] Add failure-mode tests: a span/attribute error never fails the LLM call or prompt render.

**Verify**

- `pytest tests/ai/observability/test_llm_tracing.py tests/ai/observability/test_prompt_tracing.py`

Additional verification:

- [x] LLM spans carry token/latency attributes and no message content.
- [x] Prompt spans carry category/name/version and no rendered content.
- [x] Chat, RAG, and tool-calling flows behave identically with the flag on or off.

**Acceptance**

- Every LLM call and prompt render produces a correlated span when enabled, with zero reimplementation of provider or prompt logic.
- No content leakage in any span attribute.

**Exit Criteria**

- LLM/prompt tracing tests pass.
- Ready for tool and agent tracing (Phase 3).

**Completion Record**

| Metric               | Result                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------- |
| Lint                 | ✅ PASS                                                                                 |
| Typecheck            | ✅ PASS                                                                                 |
| Phase 2 unit tests   | ✅ 13 passed (`test_llm_tracing.py`, `test_prompt_tracing.py`)                          |
| Provider adapters    | ✅ Unmodified (`openai`, `anthropic`, `gemini`, `groq`)                                 |
| Pipeline wiring      | ✅ `TracingLLMProvider`, `ProviderFactory`, `PromptManager.render()`                    |
| User confirmation    | ⏳ Pending                                                                              |

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

## Tool Tracing

- [x] Wrap `ToolExecutor.execute()` with `tool_span(tool_name)`.
- [x] Record `success`, `retry_count`, `latency_ms`, `authorization_result` as attributes.
- [x] Verify tool arguments/results are never attached to the span.
- [x] Verify authorization failures and tool errors are recorded as span status, not exceptions raised from the span helper.

## Agent Tracing

- [x] Wrap each `DefaultAgent` reasoning iteration with `agent_span("iteration")`.
- [x] Record `iteration_index`, `tool_calls_count`, `finish_reason`, `latency_ms`.
- [x] Add a nested `agent_span("tool_call")` around each tool dispatch within an iteration (parents to the existing `tool_span`, not a duplicate).
- [x] Verify no change to the agent's reasoning/tool-selection behaviour.

## Testing

- [x] Add `ToolExecutor` span tests (success, failure, retry, authorization-denied cases).
- [x] Add `DefaultAgent` span tests (multi-iteration run, fake provider/tools).
- [x] Add failure-mode tests: a span error never fails a tool call or agent iteration.

**Verify**

- `pytest tests/ai/observability/test_tool_tracing.py tests/ai/observability/test_agent_tracing.py`

Additional verification:

- [x] Tool spans carry outcome/latency attributes and no argument/result content.
- [x] Agent spans reflect the actual iteration count and tool-call count of a run.
- [x] Existing tool and agent test suites still pass unmodified.

**Acceptance**

- Tool execution and agent reasoning are fully observable without any change to their control-flow or authorization logic.

**Exit Criteria**

- Tool/agent tracing tests pass.
- Ready for RAG/Memory/Voice/Workflow tracing (Phase 4).

**Completion Record**

| Metric             | Result                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------- |
| Lint               | ✅ PASS                                                                                 |
| Typecheck          | ✅ PASS                                                                                 |
| Phase 3 unit tests | ✅ 8 passed (`test_tool_tracing.py`, `test_agent_tracing.py`)                           |
| Pipeline wiring    | ✅ `ToolExecutor.execute()`, `DefaultAgent` iteration loop + nested `agent.tool_call`   |
| User confirmation  | ⏳ Pending                                                                              |

---

# Phase 4 — RAG, Memory, Voice & Workflow Tracing

**Effort:** L

**Objective**

Complete platform-wide trace coverage: RAG retrieval, memory retrieval/extraction, voice sessions, and workflow run/node transitions — including the trace-context propagation strategy for workflow runs that continue on an in-process `asyncio.Task` after their triggering request completes.

**Deliverables**

- `rag_span` wired into `Retriever.retrieve()`
- `memory_span` wired into memory retrieval and extraction entry points
- `voice_span` wired into voice session lifecycle events
- `workflow_run_root_span` wired into `WorkflowManager._run_with_store()`; `workflow_span("node")` wired into `WorkflowExecutor._execute_node()`
- Trace-link propagation for background workflow execution
- Integration test suite

**Steps**

## RAG Tracing

- [x] Wrap `Retriever.retrieve()` with `rag_span("retrieve")`.
- [x] Record `top_k`, `retrieved_count`, `latency_ms`.

## Memory Tracing

- [x] Wrap the memory retrieval entry point with `memory_span("retrieve")`.
- [x] Wrap the memory extraction entry point with `memory_span("extract")`.
- [x] Record counts/latency only; never memory content.

## Voice Tracing

- [x] Wrap voice session start/end (`app/ai/voice/session.py`) with `voice_span("session")`.
- [x] Record session duration and terminal status only.

## Workflow Tracing

- [x] Wrap `WorkflowManager._run_with_store()` (background task entry) with `workflow_run_root_span` (`workflow.run`); attributes `run_id`, terminal `status`, `latency_ms`; span **link** to captured origin context when valid.
- [x] Wrap each `WorkflowNodeExecution` attempt in `WorkflowExecutor._execute_node()` with `workflow_span("node")`; attributes `node_type`, `attempt`, `status`, `latency_ms`.
- [x] At `WorkflowManager.start_run()` (or `flush_deferred_run_schedules()` when `defer_schedule=True`), snapshot the active OTel `SpanContext` when `SpanContext.is_valid` — capture `trace_id`, `span_id`, `trace_flags`, and `trace_state` — and pass the immutable snapshot into `_schedule_run()` for the background `asyncio.Task`.
- [x] Inside the background task, open a fresh run-level root span and add a span **link** to the captured `SpanContext` when valid; omit the link when no valid context exists (best-effort — never fail run scheduling or execution).
- [x] For `resume()` and `reconcile_orphaned_runs()` (crash recovery / orphan reattach): open a fresh run-level root span with **no** span link; record `run_id` and resume reason as span attributes only.
- [x] Verify resumed runs never attempt to reopen or continue a closed run-level span from a prior execution attempt.

## Testing

- [x] Add RAG/memory/voice span tests (attributes present, content absent).
- [x] Add workflow run/node span tests, including a fork/join scenario (multiple concurrent node spans).
- [x] Add a background-task trace-link test (run started from a request with active span context — link includes `trace_id`, `span_id`, `trace_flags`, `trace_state`).
- [x] Add a no-valid-context test (invalid/missing span context at schedule time — fresh run-level root span created, no link, run succeeds).
- [x] Add resume/crash-recovery span tests (fresh root span per resume/reconcile, no span link).

**Verify**

- `cd backend-python && pytest tests/ai/observability/test_rag_tracing.py tests/ai/observability/test_memory_tracing.py tests/ai/observability/test_voice_tracing.py tests/ai/observability/test_workflow_tracing.py`

Additional verification:

- [x] All eight span domains from Part I are wired and produce spans when enabled.
- [x] Parallel workflow branches produce concurrent, correctly nested node spans.
- [x] No pipeline's functional behaviour changed.

**Acceptance**

- Every pipeline named in Part I § Tracing Domains emits correlated spans when enabled.
- Workflow run tracing correctly handles the async-launch / background-execution / crash-recovery lifecycle from Epic 06.

**Exit Criteria**

- Full-platform tracing tests pass.
- Ready for token & cost metrics (Phase 5).

**Completion Record**

| Metric             | Result                                                                                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lint               | ✅ PASS                                                                                                                                                                     |
| Typecheck          | ✅ PASS                                                                                                                                                                     |
| Phase 4 unit tests | ✅ 14 passed (`test_rag_tracing.py`, `test_memory_tracing.py`, `test_voice_tracing.py`, `test_workflow_tracing.py`)                                                         |
| Observability suite| ✅ 53 passed (`tests/ai/observability/`)                                                                                                                                    |
| Pipeline wiring    | ✅ `Retriever`, `MemoryManager`, `VoiceSessionManager`, `WorkflowExecutor` (node), `WorkflowManager` (run + span links via `SpanContextSnapshot`)                           |
| User confirmation  | ⏳ Pending                                                                                                                                                                  |

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

## Cost Calculation

- [x] Add `backend-python/config/model_pricing.yaml` — canonical rate table per Part I § ModelPricingTable schema (`pricing_version` + `models[]` with `provider`, `model`, `input_usd_per_1k`, `output_usd_per_1k`).
- [x] Implement `ModelPricingTable` — load the YAML file at startup; enforce validation rules and **version lock** (`file.pricing_version == settings.observability_cost_pricing_version`); reject duplicate `(provider, model)` and invalid rates.
- [x] Add `observability_cost_pricing_file` and `observability_cost_pricing_version` to `app/core/config.py` and `.env.example` (version documents active table; rates are file-only, not env-overridable).
- [x] Implement `CostCalculator.price(provider, model, usage: ProviderUsage) -> tuple[float | None, str | None]` (cost, pricing_version).
- [x] Return `(None, None)` for unknown provider/model or missing usage fields — never raise.
- [x] Verify (by design, not by code) that no code path recalculates or overwrites a previously persisted `cost_usd`/`pricing_version` when `ModelPricingTable` changes — pricing updates apply to new rows only, per Part I § Pricing table lifecycle.

## Persistence

- [x] Add `cost_usd numeric(12,6) NULL`, `pricing_version text NULL` to `UsageEvent` ORM model.
- [x] Create Alembic migration `0008_observability_usage_cost` (additive, nullable columns; new indexes per Part I).
- [x] Extend `SqlUsageStore.record()` to invoke `CostCalculator.price(...)` internally and persist `cost_usd`/`pricing_version` alongside caller-supplied `session_id`, owner (`user_id`/`guest_id`), `message_id`, and terminal token counts — exactly one row per call (default behaviour unchanged when Observability is disabled — both cost fields remain `NULL`).
- [x] Verify existing `usage_events` writers (`ChatService`, `ToolChatService`) require no call-site changes — they continue passing terminal usage and identity fields; cost logic stays inside `record()`.

## Metrics Instruments

- [x] Implement counters: `llm_requests_total`, `tool_calls_total`, `agent_iterations_total`, `workflow_runs_started`, `workflow_runs_completed`, `workflow_runs_failed`, `workflow_retry_count`.
- [x] Implement histograms: `llm_token_usage`, `tool_call_latency_ms`, `workflow_node_execution_latency_ms`, `workflow_checkpoint_latency_ms`.
- [x] Implement gauges/up-down counters: `workflow_approval_pending_count`, `workflow_parallel_branch_count`.
- [x] Record `llm_cost_usd_total` as a counter incremented by each priced usage event.
- [x] Implement `normalize_metric_label()` and value registries per Part I § Metric Cardinality Policy (`app/ai/observability/metrics/labels.py`).
- [x] Restrict every instrument's label keys to the Part I allowlist; route all label values through `normalize_metric_label()` (unknown/plugin/MCP → `other`).
- [x] Add a metric cardinality guard test: (1) every instrument's label-key set is a subset of the allowlist; (2) no forbidden keys (`user_id`, `session_id`, `trace_id`, etc.); (3) sample raw inputs (unknown model, MCP tool, bad node type) normalize to registry values or `other`, never raw unbounded strings.
- [x] Wire instrument recording into the Phase 2–4 span helpers (one place per domain, not duplicated per call site) — the same helper call attaches high-cardinality identifiers to the **span** and only normalized registry values to the **metric**.

## Testing

- [x] Add `ModelPricingTable` loader tests (valid file, duplicate key rejection, negative rate rejection, version-mismatch startup failure).
- [x] Add `CostCalculator` tests (known model, unknown model, missing usage fields, unknown provider).
- [x] Add migration upgrade/downgrade smoke test.
- [x] Add `SqlUsageStore.record()` tests asserting `cost_usd`/`pricing_version` persistence and `NULL` fallback.
- [x] Add a pricing-version-change test: updating `ModelPricingTable` does not alter previously persisted `cost_usd`/`pricing_version` rows; only new rows use the new table.
- [x] Add metrics-instrument tests using an in-memory metric reader (counters/histograms increment correctly; emitted label values are registry members only).

**Verify**

- `pytest tests/ai/observability/test_cost_calculator.py tests/ai/observability/test_usage_cost_persistence.py tests/ai/observability/test_metrics_instruments.py`

Additional verification:

- [x] `usage_events` rows are priced correctly for known models.
- [x] Unknown/unsupported models never block a usage write.
- [x] Every metric name declared in Epic 06 § Observability is emitted.

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

| Metric              | Result                                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------------- |
| Lint / typecheck    | ✅ PASS (pre-commit)                                                                                        |
| Phase 5 unit tests  | ✅ 24 passed (`test_cost_calculator.py`, `test_usage_cost_persistence.py`, `test_metrics_instruments.py`)   |
| Pricing table       | ✅ `config/model_pricing.yaml` + version lock via `observability_cost_pricing_version`                    |
| Migration           | ✅ `0008_observability_usage_cost` (`cost_usd`, `pricing_version` on `usage_events`)                        |
| Cost boundary       | ✅ `SqlUsageStore.record()` — no call-site changes to chat/tool writers                                     |
| Metric cardinality  | ✅ Allowlisted label keys + `normalize_metric_label()` registry tests                                       |
| User confirmation   | ⏳ Pending                                                                                                  |

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

## Aggregation

- [x] Implement `UsageAggregator` — owner-scoped queries over `usage_events` grouped by `day` | `provider` | `model`, with a `since`/`until` date range.
- [x] Collapse provisional `ObservabilityStore` — router depends on `UsageAggregator` directly (no pass-through façade; see Completion Record).
- [x] Ensure queries use the new `(user_id, created_at)` / `(provider, model, created_at)` indexes.
- [x] Before wiring the router: re-evaluate whether `ObservabilityStore` adds any logic beyond delegating to `UsageAggregator` (per Part I § Storage Architecture). If it does not, collapse it and have the router depend on `UsageAggregator` directly; if it does (e.g., response DTO shaping, multi-source aggregation), keep it. Record the decision in this phase's Completion Record.

## Schemas & Router

- [x] Define request/response schemas for the usage summary endpoint; never expose other owners' data or internal trace/span IDs.
- [x] Implement `GET /api/observability/usage` — `Depends(get_current_caller)`, owner-scoped.
- [x] Implement `GET /metrics` — Prometheus text exposition from `MeterRegistry`'s Prometheus reader; unauthenticated (matches `/api/health` pattern); no owner-identifying labels.
- [x] Return `503 feature_disabled` from `/api/observability/usage` and `404` from `/metrics` when `OBSERVABILITY_ENABLED=false`.
- [x] Mount the router in `app/main.py`.

## Health

- [x] Extend `app/routers/health.py` with `observability_enabled`.

## Error Handling

- [x] Map validation errors (bad date range, invalid `group_by`) → `422`.
- [x] Map generic errors → `500` with a safe message.

## Testing

- [x] Add router tests for the usage endpoint (happy path, date-range filtering, each `group_by` mode).
- [x] Add owner-isolation tests (caller only ever sees their own rows).
- [x] Add feature-flag-off tests (`503` on usage endpoint, `404` on `/metrics`).
- [x] Add `/metrics` content tests (Prometheus format, no owner labels).
- [x] Add health endpoint tests.

**Verify**

- `pytest tests/test_observability_router.py`

Additional verification:

- [x] Usage summaries match Part I contract for every `group_by` mode.
- [x] `/metrics` is scrape-able Prometheus text format.
- [x] Owner isolation holds.
- [x] Health endpoint reports `observability_enabled` correctly.

**Acceptance**

- The Observability layer is fully queryable via REST without any other client integration.
- API responses never leak cross-owner data, trace internals, or content.
- Flag-off behaviour matches the platform's `503 feature_disabled` convention (and `404` for `/metrics`).

**Exit Criteria**

- REST API tests pass.
- Ready for evaluation framework extension (Phase 7).

**Completion Record**

| Metric                   | Result                                                                                                      |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Lint / typecheck         | ✅ PASS (pre-commit)                                                                                        |
| Phase 6 integration tests | ✅ 15 passed (`tests/test_observability_router.py`)                                                      |
| Usage API                | ✅ `GET /api/observability/usage` — owner-scoped, `group_by` day/provider/model, date range                 |
| Prometheus               | ✅ `GET /metrics` — unauthenticated exposition; `404` when flag off; no owner labels                        |
| Health                   | ✅ `observability_enabled` on `/api/health`                                                                 |
| `ObservabilityStore`     | ✅ Collapsed — router depends on `UsageAggregator` directly (no pass-through façade)                        |
| User confirmation        | ⏳ Pending                                                                                                  |

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

## Dataset Schema

- [x] Extend `EvalLevel` to `Literal["prompt", "retrieval", "e2e", "agent", "workflow"]`.
- [x] Add `_parse_agent_case` — goal/instructions, expected tool calls, expected outcome.
- [x] Add `_parse_workflow_case` — inline workflow definition (or fixture reference), trigger input, expected terminal status.
- [x] Extend `EvalCaseResult` with `tool_calls_correct: bool | None` and `terminal_status: str | None`.
- [x] Extend `EvalCaseResult`/`EvalRunReport` with reproducibility metadata fields per Part I § Evaluation Framework Extension: `model`, `model_version`, `temperature`, `seed`, `prompt_version` (each `| None` — never fabricated when a runner/provider doesn't expose one). Add top-level `run_environment` (`agent_runtime_enabled`, `workflow_engine_enabled`, `postgres_available`, `pgvector_available`). Bump `REPORT_SCHEMA_VERSION`.
- [x] Populate these fields from each runner (`PromptEvalRunner` → `prompt_version`; `AgentEvalRunner`/`EndToEndEvalRunner` → `model`/`temperature`/`model_version` from the fake or real provider call; `WorkflowEvalRunner` → `model`/`prompt_version` when the run includes `llm`/`agent` nodes).

## Agent Runner

- [x] Implement `AgentEvalRunner` using a fake provider/tools (same pattern as `_EvalLLMProvider`/`_FakeEmbeddingProvider`).
- [x] Assert the agent reaches the expected outcome and/or calls the expected tools.
- [x] Skip (not fail) with a clear reason when `AGENT_RUNTIME_ENABLED=false`.

## Workflow Runner

- [x] Implement `WorkflowEvalRunner` using `WorkflowManager` against a real (test) Postgres session, same availability check as `RetrievalEvalRunner`/`EndToEndEvalRunner`.
- [x] Create a minimal workflow definition from the case, start a run, drive it to a terminal status.
- [x] Assert the terminal status and (optionally) node output match expectations.
- [x] Skip (not fail) with a clear reason when `WORKFLOW_ENGINE_ENABLED=false` or Postgres/pgvector is unavailable.

## CLI & Reporting

- [x] Add `--level agent` / `--level workflow` to `build_parser()`; `--level all` includes both **and enforces prerequisites** (`AGENT_RUNTIME_ENABLED`, `WORKFLOW_ENGINE_ENABLED`, Postgres + pgvector available) — exit non-zero with a clear message if any required prerequisite is missing (no skipped agent/workflow cases).
- [x] Capture `run_environment` once per run and persist it in JSON output / `baseline-report.json`.
- [x] Extend `print_console_summary` / `_serialize_report` with `agent`/`workflow` sections.
- [x] Update `Makefile` `eval` target documentation if the default level set changes (default stays `all`).

## Testing

- [x] Add `AgentEvalRunner` tests (pass, fail, flag-off skip).
- [x] Add `WorkflowEvalRunner` tests (pass, fail, flag-off skip, Postgres-unavailable skip).
- [x] Add dataset parsing tests for the new case types.
- [x] Add CLI tests for `--level agent`/`--level workflow`/`--level all`.
- [x] Add reproducibility metadata tests: each new runner populates `model`/`temperature`/`prompt_version` (and `model_version`/`seed` when available) on its `EvalCaseResult`, and leaves them `None` (not a placeholder value) when unavailable.
- [ ] Add Postgres integration tests for `AgentEvalRunner` and `WorkflowEvalRunner` (real runtime with flags on; verify pass/fail outcomes and teardown leaves no eval artifacts).

**Verify**

- `pytest tests/ai/evaluation/test_agent_runner.py tests/ai/evaluation/test_workflow_runner.py`

Additional verification:

- [ ] `make eval --level agent` and `make eval --level workflow` run real cases when their flags are on and Postgres/pgvector are available.
- [x] Both targeted levels skip cleanly when their flag is off; `--level all` hard-fails instead of skipping when agent/workflow prerequisites are missing.
- [x] Existing `prompt`/`retrieval`/`e2e` levels are unaffected.

**Acceptance**

- The evaluation harness can now exercise agent tool-calling and workflow orchestration behaviour end-to-end, without any parallel evaluation system.

**Exit Criteria**

- Agent/workflow unit + CLI tests pass; Postgres integration checks pass.
- Ready for regression detection and benchmark dataset expansion (Phase 8).

**Completion Record**

| Metric                    | Result                                                                                                      |
| ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Lint / typecheck          | ✅ PASS (pre-commit)                                                                                        |
| Phase 7 unit tests        | ✅ 47 passed (`tests/ai/evaluation/`, `tests/test_evaluation_*.py`; includes CLI smoke; Postgres integration sign-off still pending) |
| Phase 7 CLI checks        | ✅ `--level agent`/`workflow`/`all` smoke tests; prerequisite gate exit 2; flag-off skip behaviour verified |
| Agent/workflow integration | ⏳ Pending — `make eval --level agent`/`workflow` against Postgres/pgvector not yet signed off            |
| Report schema             | ✅ v2 (`run_environment`, reproducibility metadata, agent/workflow result fields)                            |
| Sample dataset            | ✅ 7 cases (`prompt`=2, `retrieval`=2, `e2e`=1, `agent`=1, `workflow`=1)                                  |
| Existing levels unchanged | ✅ `prompt`/`retrieval`/`e2e` runners and offline prompt eval verified                                    |
| User confirmation         | ⏳ Pending                                                                                                  |

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

## Regression Checking

- [ ] Implement `RegressionChecker.compare(...)` with an **environment comparability gate** first: reject when `run_environment` differs between current and baseline, or when the baseline contains skipped `agent`/`workflow` cases.
- [ ] Detect hard regressions: any case passing in the baseline that now fails.
- [ ] Detect soft regressions: per-level pass-rate drop or mean-latency increase beyond configured tolerance.
- [ ] Surface `environment_mismatch` in `RegressionResult` (distinct from metric regressions) with the differing fields listed.
- [ ] Ensure `RegressionResult` is JSON-serializable and prints a clear console summary.
- [ ] Include each flagged case's reproducibility metadata (Phase 7: `model`, `model_version`, `temperature`, `seed`, `prompt_version`) from both the current and baseline result in the printed/JSON `RegressionResult`, so a regression can be explained by "what changed" rather than left ambiguous.

## Baseline Management

- [ ] Generate the initial `baseline-report.json` from `make eval --level all` with agent/workflow runtimes enabled and Postgres/pgvector available (prerequisites enforced — no skipped agent/workflow rows).
- [ ] `--update-baseline` uses the same `--level all` prerequisite gate; refuse to write a baseline when environment checks fail or any agent/workflow case would be skipped.
- [ ] Add `--check-regression <baseline_path>` to compare a new run against it; non-zero exit on regression.
- [ ] Add `--update-baseline` as an explicit, separate CLI action (never automatic on a normal `make eval` run).

## Benchmark Dataset Expansion

- [ ] Add prompt-level cases covering additional prompt categories/versions in active use.
- [ ] Add retrieval/e2e cases covering additional document fixtures and answer-match modes.
- [ ] Add agent-level cases covering multi-tool-call scenarios.
- [ ] Add workflow-level cases covering sequential, conditional, and approval-node graphs.

## Testing

- [ ] Add `RegressionChecker` tests (environment mismatch, skipped-agent/workflow baseline rejection, no regression, hard regression, pass-rate regression, latency regression).
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

## Dashboard UI

- [ ] Add an Observability section to the authenticated app.
- [ ] Display Observability feature availability (via `observability_enabled`).
- [ ] Display usage/cost summary (requests, tokens, estimated cost) grouped by day, provider, and model.
- [ ] Support selecting a date range (default: trailing 30 days).

## API Integration

- [ ] Create `frontend/src/api/observabilityClient.ts`.
- [ ] Create `frontend/src/types/observability.ts`.
- [ ] Create `frontend/src/pages/ObservabilityPage.tsx` (authenticated route).
- [ ] Extend `frontend/src/api/healthClient.ts` with `observability_enabled`.
- [ ] Wire navigation link in the authenticated app shell.

## Feature Flag Integration

- [ ] Hide Observability controls when `OBSERVABILITY_ENABLED=false`.
- [ ] Preserve existing authenticated user experience.
- [ ] Preserve guest user experience.

## Error Handling

- [ ] Handle API failures gracefully.
- [ ] Handle empty-usage-history states with a clear message.
- [ ] Preserve existing application behaviour during frontend failures.

## Testing

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

## Functional Validation

- [ ] Verify all implementation phases have been completed.
- [ ] Verify all Part I architectural invariants.
- [ ] Verify span coverage across all eight tracing domains.
- [ ] Verify cost accounting for known and unknown models.
- [ ] Verify `/metrics` exposition and owner-scoped usage REST API.
- [ ] Verify `agent`/`workflow` eval levels and regression checking.

## Integration Validation

- [ ] Verify `TracerRegistry`/`MeterRegistry` real-vs-no-op behaviour.
- [ ] Verify `TracingLLMProvider` wrapping in `ProviderFactory`.
- [ ] Verify Observability REST API functionality.
- [ ] Verify evaluation CLI functionality (`--level agent`, `--level workflow`, `--check-regression`).

## Regression Testing

- [ ] Execute full backend regression suite.
- [ ] Execute full frontend regression suite.
- [ ] Verify chat, RAG, MCP, memory, voice, agent, tool, and workflow functionality unchanged.
- [ ] Verify streaming responses unchanged.

## Feature Flag Validation

- [ ] Validate `OBSERVABILITY_ENABLED=true`.
- [ ] Validate `OBSERVABILITY_ENABLED=false`.
- [ ] Verify identical platform behaviour when disabled (byte-for-byte `usage_events` shape excluding new nullable columns).
- [ ] Verify graceful feature enablement.

## Performance Validation

- [ ] Measure tracing overhead per instrumented call site (span creation latency).
- [ ] Measure cost-calculation overhead per usage write.
- [ ] Measure `/metrics` exposition latency under representative counter/histogram volume.
- [ ] Verify acceptable production performance.

## Quality Validation

- [ ] Validate no content leakage across all spans, metrics, logs, and REST responses.
- [ ] Validate owner isolation on the usage endpoint.
- [ ] Validate fail-open behaviour: simulated telemetry exceptions (span/metric/cost) are suppressed; simulated business exceptions from instrumented operations still propagate.
- [ ] Validate regression detection against intentionally regressed fixtures.

## Production Readiness

- [ ] Review exported trace/metric samples against an OTLP-compatible backend (or console output).
- [ ] Review structured logging trace/span correlation.
- [ ] Verify error handling and failure recovery.
- [ ] Verify deployment configuration (migration `0008` applied).
- [ ] Publish production readiness report.

## Documentation

- [ ] Update implementation documentation.
- [ ] Update architecture documentation where required.
- [ ] Publish release summary.
- [ ] Record implementation metrics.
- [ ] Update Epic status.

## Testing

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

| Risk                                            | Mitigation                                                                                                                                                        |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tracing/metrics overhead in hot paths           | No-op tracer/meter when disabled; span helpers are lightweight context managers; fail-open on error                                                               |
| Content leakage via span/metric attributes      | Reuses `app.core.logging.sanitize_value`; explicit "never attach content" invariant + tests                                                                       |
| Vendor lock-in via SDK choice                   | OTel API only; exporter is configuration, not code                                                                                                                |
| Inaccurate cost figures                         | Explicitly "approximate, not billing-grade"; `NULL` for unpriced models rather than a wrong number                                                                |
| Pricing table drift from real provider pricing  | Git-tracked `config/model_pricing.yaml` + `pricing_version` version-lock; every row stamped so historical figures remain interpretable after a table update       |
| Regression baseline staleness                   | `--update-baseline` is an explicit, separate, auditable CLI action                                                                                                |
| Workflow background-task trace correlation gaps | Best-effort full-`SpanContext` span link at `start_run()`; link omitted when invalid/unavailable (incl. crash recovery); fresh run-level root span always created |
| `/metrics` exposing sensitive data              | Aggregate-only counters/histograms; explicit invariant + tests forbidding owner-identifying labels                                                                |
| Accidental 100% sampling in production          | `otel_traces_sample_ratio` deployment config must override the dev-safe `1.0` default; documented per-environment recommendations (§ Trace Sampling Strategy)     |
| Prometheus cardinality explosion                | Bounded label keys **and** values via `normalize_metric_label()` + registries; MCP/plugin/unknown inputs → `other`; high-cardinality raw values stay on spans     |
| Feature regression                              | `OBSERVABILITY_ENABLED` flag-off parity                                                                                                                           |
| Eval framework fork/divergence                  | Extends `app/ai/evaluation/` in place; no parallel evaluation system                                                                                              |

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
| `backend-python/config/model_pricing.yaml`                | create        | Core     | 5       |
| `backend-python/.env.example`                             | modify        | Docs     | 1, 5    |
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

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1       | 2026-08-07 | Initial epic draft — Part I design + Part II 11-phase execution plan (Phases 0–10). Not started.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 1.1     | 2026-08-07 | Added Trace Sampling Strategy, Metric Cardinality Policy, and Span Naming Convention sections; explicit streaming `llm.complete` span lifecycle; pricing table lifecycle clarification (no retroactive recalculation); evaluation reproducibility metadata (`model`/`model_version`/`temperature`/`seed`/`prompt_version`) on `EvalCaseResult`; flagged `ObservabilityStore` as a provisional façade to collapse into `UsageAggregator` in Phase 6 if it adds no logic. Part I + Phases 1, 2, 5, 6, 7, 8 sync. Not started. |
| 1.2     | 2026-08-07 | Phase 0 complete: baseline audit published; quality gates verified (1551 backend, 268 frontend, eval 5/5, 89.05% coverage). Part II only.                                                                                                                                                                                                                                                                                                                                                                                   |
| 1.3     | 2026-08-07 | Clarify instrumentation failure mode: telemetry-only fail-open; business exceptions propagate; dual-category tests required. Part I + Phases 1, 10 sync.                                                                                                                                                                                                                                                                                                                                                                    |
| 1.4     | 2026-08-07 | Request tracing: explicit Phase 1 middleware-created `http.server` root span (no OTel HTTP auto-instrumentation). Part I + End-to-End sequence + Span Naming sync.                                                                                                                                                                                                                                                                                                                                                          |
| 1.5     | 2026-08-07 | Cost accounting: `SqlUsageStore.record()` is the single usage-recording boundary; `CostCalculator` invoked inside `record()` only (not in `TracingLLMProvider`). Part I + Phases 2, 5 sync.                                                                                                                                                                                                                                                                                                                                 |
| 1.6     | 2026-08-07 | ModelPricingTable canonical source: `config/model_pricing.yaml` schema, validation, version lock, update process. Part I § Configuration defaults + Phase 5 sync.                                                                                                                                                                                                                                                                                                                                                           |
| 1.7     | 2026-08-07 | Metric Cardinality Policy: bound label values via registries + `normalize_metric_label()` (`other` fallback); Phase 5 value-cardinality tests. Part I + Phase 5 sync.                                                                                                                                                                                                                                                                                                                                                       |
| 1.8     | 2026-08-07 | Eval baseline/regression: `--level all`/`--update-baseline` require agent/workflow + Postgres; `run_environment` in baseline-report.json; regression rejects non-comparable baselines. Part I + Phases 7, 8 sync.                                                                                                                                                                                                                                                                                                           |
| 1.9     | 2026-08-07 | Remove `observability_usage_retention_days` from Configuration defaults (no retention behavior/owner in epic). Part I only.                                                                                                                                                                                                                                                                                                                                                                                                 |
| 1.10    | 2026-08-07 | Phase 0/Baseline status: acknowledge existing `UsageEvent`/`SqlUsageStore.record()`; Epic 07 OTel/metrics/cost/API/eval extensions remain unimplemented. Part II only.                                                                                                                                                                                                                                                                                                                                                      |
| 1.11    | 2026-08-07 | Fix Part II `# Phase …` heading hierarchy: promote Steps subsections from `###` to `##` (Phases 0–10). Part II only.                                                                                                                                                                                                                                                                                                                                                                                                        |
| 1.12    | 2026-08-07 | Workflow background tracing: capture full originating `SpanContext` at `start_run()` for run-level span links; best-effort + crash-recovery (no link) behavior. Part I § Workflow Spans + Phase 4 sync.                                                                                                                                                                                                                                                                                                                     |
| 1.13    | 2026-08-07 | Phase 1 complete: OTel TracerRegistry/MeterRegistry bootstrap, span helper scaffolds, trace/span-id log correlation, 17 unit tests. Part II only.                                                                                                                                                                                                                                                                                                                                                                           |
| 1.14    | 2026-08-07 | Phase 2 complete: TracingLLMProvider, ProviderFactory wrapping, PromptManager prompt_span wiring, token-count span attribute allowlist, 13 unit tests. Part II only.                                                                                                                                                                                                                                                                                                                                                          |
| 1.15    | 2026-08-08 | Phase 3 complete: ToolExecutor tool_span, DefaultAgent agent_span (iteration + tool_call), fail-open telemetry tests, 8 unit tests. Part II only.                                                                                                                                                                                                                                                                                                                                                                             |
| 1.16    | 2026-08-08 | Phase 4 complete: RAG/memory/voice/workflow span wiring, workflow background SpanContext snapshot + span links, resume/reconcile fresh-root spans, 14 unit tests (53 total observability). Part II only.                                                                                                                                                                                                                                                                                                                      |
| 1.17    | 2026-08-08 | Phase 7 implementation landed: `AgentEvalRunner`/`WorkflowEvalRunner`, eval schema v2 + `run_environment`, CLI `--level agent`/`workflow`/`all` prerequisite gate, 36 eval unit/CLI tests, `sample.yaml` expanded to 7 cases. Integration checks tracked separately. Part II only.                                                                                                                                                                                                                                                                                                             |
| 1.18    | 2026-08-08 | Phases 5–6 completion records: cost/metrics (24 tests, `0008` migration, `model_pricing.yaml`); REST API + `/metrics` (15 router tests); `ObservabilityStore` collapsed to `UsageAggregator`. Part II only.                                                                                                                                                                                                                                                                                                                   |
| 1.19    | 2026-08-09 | Phase 7 status corrected: **In Progress** until agent/workflow Postgres integration checks pass; baseline Observability summary and completion record distinguish unit/CLI vs integration. Part II only.                                                                                                                                                                                                                                                                                                                         |
