# Post-MVP V2 Epic 08 Phase 0 — Baseline Audit

**Epic:** v2-08 Plugin Architecture
**Phase:** 0 — Baseline Audit
**Date:** 2026-08-10
**Auditor:** AI Agent
**Status:** Complete
**Git commit (validation base):** `e000787` — fix: copy config/model_pricing.yaml into backend Docker image

---

## Executive Summary

Baseline audit before implementing Epic 08 (Plugin Architecture). All quality gates pass; test count matches Epic 07 Phase 10 (1691 passed). Coverage is **89.17%** (−0.04 pp vs Epic 07 **89.21%**); still above the **80%** gate. Epic 07 is complete with release summary published. **No plugin implementation exists** — clean baseline. All four registry extension points (`ToolRegistry`, `PromptRepository`, workflow node executors, MCP registration) are inventoried and verified present. **Phase 0 complete.** Phase 1 requires separate user confirmation.

**Key findings:**

- ✅ Backend gates pass: lint, format-check, typecheck, **1691 tests**, **89.17%** `app/` coverage (−0.04 pp vs Epic 07 89.21%; ≥80% gate met), eval **15/15**
- ✅ Frontend gates pass: lint, **281 tests** (46 files), build successful
- ✅ Epic 07 complete: `app/ai/observability/` (15 modules, 16 test files); `OBSERVABILITY_ENABLED` present (default `false`); release summary published
- ✅ Extension points verified: `ToolRegistry`, `PromptRepository`, `_create_workflow_manager()` node map, `register_mcp_tools()`, DI singletons in `app/ai/deps.py`
- ✅ PyYAML available (`pyyaml>=6.0.3` in `pyproject.toml`) for manifest parsing (Phase 1)
- ❌ `PLUGINS_ENABLED` absent; `app/ai/plugins/` does not exist (expected Phase 1+)
- ❌ `backend-python/plugins/` reference plugins absent (expected Phase 8)
- ❌ `plugin_span` not implemented (extension point reserved; Phase 7)
- ❌ Frontend plugin inventory absent (expected Phase 9)
- ✅ **Phase 0 authorized** (user requested Phase 0 baseline audit)
- ⬜ **Phase 1 not authorized** — explicit user confirmation pending

---

## 1. Epic 07 Phase 10 Status

**Finding:** Epic 07 Observability & Evaluation Phase 10 validation complete; release summary published.

**Evidence:**

- `docs/plans/post-mvp-v2-epic-07-observability-and-evaluation.md` — Phases 0–10 marked **Completed**
- `docs/releases/post-mvp-v2-epic7-release-summary.md` — published 2026-08-10
- `app/ai/observability/` — 15 Python modules (tracing, metrics, cost, aggregation)
- `app/routers/observability.py` — authenticated usage REST API (route-level `503` when flag off)
- `tests/ai/observability/` — 16 test modules
- Epic 07 Phase 10 baseline: 1691 passed, 89.21%; Phase 0 run: **1691 passed, 89.17%** (−0.04 pp; same `make test-cov` command; ≥80% gate met)

**Recommendation:** Epic 08 **Phase 0** is complete. Do not start Phase 1 until the user explicitly confirms.

---

## 2. Backend Quality Gates

### 2.1 Lint

```bash
Command: cd backend-python && make lint
Result: ✅ PASS — All checks passed!
Duration: ~1.3 s
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS — 468 files already formatted
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS — 0 errors, 0 warnings
Duration: ~7.5 s
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests: 1691 passed, 0 failed
Coverage: 89.17% on app/ (≥80% required)
Duration: ~189 s
```

**Notable coverage (unchanged from Epic 07):**

| Module | Coverage |
| ------ | -------- |
| `app/services/unified_chat_service.py` | 60% (conditional RAG/tools/agent/memory branches) |
| `app/ai/observability/` (aggregate) | High — 16 test modules |
| `app/ai/workflow/` (aggregate) | High — 23 test modules |
| `app/ai/mcp/` | 93%+ |
| `app/ai/tools/` | ToolExecutor, registry, authorizer tested |
| `app/ai/agent/` | 13+ test modules |

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS — 15/15 (--level all)
  prompt: 5, retrieval: 3, e2e: 2, agent: 1, workflow: 4
Duration: ~3.5 s
```

---

## 3. Frontend Quality Gates

```bash
npm run lint          ✅ PASS
npm test -- --run     ✅ PASS — 46 files, 281 tests
npm run build         ✅ PASS — 525 kB JS bundle
```

No plugin UI modules exist (`pluginsClient`, `PluginsPage` absent — expected Phase 9).

---

## 4. Startup Order Inventory (`app/main.py`)

Current `lifespan` startup sequence (Epic 07 baseline):

| Step | Action | Condition |
| ---- | ------ | --------- |
| 1 | `setup_logging(settings)` | Always |
| 2 | `TracerRegistry.initialize(settings)` | Always (no-op when flag off) |
| 3 | `MeterRegistry.initialize(settings)` | Always (no-op when flag off) |
| 4 | `CostRegistry.initialize(settings)` + `set_model_registry(...)` | Always |
| 5 | `MetricInstruments.initialize()` | Always |
| 6 | `settings.log_development_warnings(logger)` | Always |
| 7 | `register_production_tools(get_tool_registry(), settings)` | `tools_enabled` |
| 8 | `register_mcp_tools(registry, mcp_registry, settings)` | `mcp_enabled` |
| 9 | `reconcile_workflow_runs_at_startup(settings)` | `workflow_engine_enabled` |

**Epic 08 planned insertion (Part I § Startup order):**

- Step **6.5** (new): `load_plugins()` when `PLUGINS_ENABLED=true` — **before** `register_production_tools()`
- Step 8 extension: `register_mcp_tools(..., extra_servers=PluginRegistry.list_mcp_servers())` (Phase 5)

**Current gap:** No plugin load step; production tools register immediately after observability bootstrap.

---

## 5. Extension Point Inventory

### 5.1 ToolRegistry (`app/ai/tools/registry.py`)

| Aspect | Current state | Epic 08 role |
| ------ | ------------- | ------------ |
| Registration API | `register(tool: ToolDefinition, handler: ToolHandler)` | Tool plugins via `PluginRegistrar.register_tool()` |
| Lookup | `get()`, `get_handler()`, `list_tools()`, `get_schemas_for_llm()` | Plugin tools execute through existing `ToolExecutor` |
| Collision rule | `ToolAlreadyRegisteredError` on duplicate `tool.name` | Plugin tools must use `{plugin_id}.` prefix; deterministic load order by `plugin_id` |
| Singleton | `get_tool_registry()` — `@lru_cache` in `app/ai/deps.py` | Shared process-wide registry |

**Production registration:** `register_production_tools()` in `app/ai/tools/registration.py` registers `web_search` and (when workflow flag on) `workflow_execution` tool.

### 5.2 PromptRepository (`app/ai/prompts/repository.py`)

| Aspect | Current state | Epic 08 role |
| ------ | ------------- | ------------ |
| Template identity | `(category, name, version)` tuple key | Plugin category: `plugin/{plugin_id}` |
| Resolution | Filesystem under `prompts_root / category / {name}.v{version}.j2` | Phase 3: in-memory overlay for inline/file plugin templates |
| Renderer | Jinja2 `Environment` with `StrictUndefined` | Same rules for plugin templates |
| Facade | `PromptManager.render(category, name, version, variables)` wraps `prompt_span` | Callers use `plugin/{plugin_id}` category |

**Collision rule:** Duplicate `(category, name, version)` in cache or collision with built-in filesystem prompts must fail plugin registration (Phase 3).

### 5.3 Workflow Node Executors (`app/ai/deps.py` → `_create_workflow_manager()`)

Current `node_executors` map (`NodeType` → executor):

| NodeType | Executor |
| -------- | -------- |
| `TASK` | `TaskNodeExecutor(tool_executor)` |
| `LLM` | `LLMNodeExecutor(prompt_manager, settings)` |
| `AGENT` | `AgentNodeExecutor(agent_runtime, settings)` |
| `ROUTER` | `RouterNodeExecutor(ConditionEvaluator())` |
| `FORK` | `ForkNodeExecutor(max_parallel_branches=...)` |
| `JOIN` | `JoinNodeExecutor()` |
| `APPROVAL` | `ApprovalNodeExecutor()` |

**Missing (Epic 08 Phase 4):** `NodeType.PLUGIN` → `PluginNodeExecutor` dispatcher routing by `(plugin_id, plugin_node_type)`.

**Graph validation:** `GraphValidator.validate()` in `app/ai/workflow/graph/validator.py` — Phase 4 extends to verify plugin references.

**NodeExecutor protocol:** `app/ai/workflow/nodes/base.py` — `NodeExecutor` Protocol with `execute(node, context, request)`.

### 5.4 MCP Registration (`app/ai/tools/registration.py`)

| Aspect | Current state | Epic 08 role |
| ------ | ------------- | ------------ |
| Config source | `settings.mcp_servers: list[dict[str, Any]]` in `app/core/config.py` | Plugin manifest `mcp_servers` + programmatic `register_mcp_server()` |
| Entry point | `register_mcp_tools(registry, mcp_registry, settings, permission_policy?)` | Phase 5: add `extra_servers` param; env wins on name conflict |
| Config model | `McpConnectionConfig` in `app/ai/mcp/config.py` | Plugin declarations validate against same model |
| Registry | `McpServerRegistry` via `get_mcp_server_registry()` in deps | Unchanged discovery path |
| Fail-open | Per-server errors logged; startup continues | Same semantics for plugin-declared servers |

**Current signature:** No `extra_servers` parameter — plugin MCP merge not yet wired.

---

## 6. Feature Flag Inventory (`app/core/config.py`)

| Flag | Default | Present | Epic 08 notes |
| ---- | ------- | ------- | ------------- |
| `tools_enabled` | `false` | ✅ | Unchanged |
| `mcp_enabled` | `false` | ✅ | Plugin MCP contributions skipped when off |
| `agent_runtime_enabled` | `false` | ✅ | Unchanged |
| `workflow_engine_enabled` | `false` | ✅ | Plugin workflow nodes require flag on |
| `observability_enabled` | `false` | ✅ | `plugin_span` Phase 7 |
| `memory_enabled` | `false` | ✅ | Unchanged |
| `voice_enabled` | `false` | ✅ | Unchanged |
| `rag_enabled` | `false` | ✅ | Unchanged |
| **`plugins_enabled`** | **`false` (planned)** | **❌** | **Phase 1 deliverable** |
| `plugin_directories` | — | ❌ | Phase 1 |
| `plugin_allowlist` | — | ❌ | Phase 1 |
| `plugin_load_timeout_seconds` | — | ❌ | Phase 1 |

**Health endpoint (`app/routers/health.py`):** Exposes `observability_enabled`, `workflow_engine_enabled`, etc. Plugin fields (`plugins_enabled`, `plugins_loaded_count`, `plugins_failed_count`) — **Phase 6**.

---

## 7. NodeType Enum & PLUGIN Extension

**Current values** (`app/ai/workflow/models/definition.py`):

```text
TASK | LLM | AGENT | ROUTER | FORK | JOIN | APPROVAL | TERMINAL
```

**Planned (Phase 4):** `PLUGIN = "plugin"` with required node `config` keys `plugin_id` + `plugin_node_type`.

**Validation when flag off:** Reject workflow definitions containing `type: plugin` at create/update (defensive; Phase 4).

---

## 8. Plugin Subsystem Absence

Confirmed no prior plugin implementation:

| Path / symbol | Status |
| ------------- | ------ |
| `app/ai/plugins/` | ❌ Does not exist |
| `app/routers/plugins.py` | ❌ Does not exist |
| `app/schemas/plugins.py` | ❌ Does not exist |
| `PLUGINS_ENABLED` in config | ❌ Not present |
| `PLUGIN_API_VERSION` | ❌ Not present |
| `PluginLoader`, `PluginRegistry`, `PluginRegistrar` | ❌ Not present |
| `PluginNodeExecutor` | ❌ Not present |
| `backend-python/plugins/` (reference plugins) | ❌ Does not exist |
| `tests/ai/plugins/` | ❌ Does not exist |
| `tests/plugins/fixtures/` | ❌ Does not exist |
| `tests/test_plugins_router.py` | ❌ Does not exist |
| Frontend `PluginsPage` / `pluginsClient` | ❌ Not present |
| `plugin_span` in `app/ai/observability/tracing/spans.py` | ❌ Not present (domain spans only) |
| `load_plugins()` in lifespan | ❌ Not wired |

**Existing span helpers (reuse for plugin execution attribution):** `tool_span`, `workflow_span`, `prompt_span`, etc. — all present in `spans.py`.

**Metric label normalization:** `normalize_metric_label()` in `app/ai/observability/metrics/labels.py` — bounded `tool_name` registry; plugin tools will fall through to `other` until registry extended (Part I).

---

## 9. Architecture Review

### 9.1 Part I Architectural Invariants — Phase 0 Evidence

| Invariant | Phase 0 status | Notes |
| --------- | -------------- | ----- |
| Plugins extend registries; never bypass executors | **Verified (prerequisite)** | `ToolExecutor`, `PromptManager.render()`, `WorkflowExecutor` paths exist and are tested |
| Tool name prefix `{plugin_id}.` | **Planned** | `ToolAlreadyRegisteredError` on collision — prefix enforcement in Phase 2 |
| Prompt category `plugin/{plugin_id}` | **Planned** | `PromptRepository` filesystem-only today; overlay API Phase 3 |
| Env wins on MCP name conflict | **Planned** | Single config source today; merge policy Phase 5 |
| Per-plugin fail-open loading | **Planned** | Analogous fail-open in MCP registration and memory extraction |
| `PLUGINS_ENABLED=false` flag-off parity | **Planned (baseline verified)** | No plugin code exists; full parity unverified until Phase 1+ / Phase 10 |
| No hot-reload | **Verified (design)** | Startup-only registration pattern matches existing MCP/tool model |
| Trusted in-process code | **Verified (design)** | Same privilege model as application modules |
| Public APIs stable after Phase 1 | **Planned** | No plugin public APIs exist yet |

### 9.2 Epic 08 Integration Points (by Phase)

| Phase | Integration point |
| ----- | ----------------- |
| 1 | `app/ai/plugins/` package, manifest/loader/registry/registrar, `PLUGINS_ENABLED`, unit tests with fixtures |
| 2 | `load_plugins()` in lifespan before `register_production_tools()`; tool wiring |
| 3 | `PromptRepository` overlay; `register_prompt_template()` |
| 4 | `NodeType.PLUGIN`, `PluginNodeExecutor`, `GraphValidator`, `_create_workflow_manager()` |
| 5 | `register_mcp_tools(..., extra_servers=...)`, MCP merge policy |
| 6 | `app/routers/plugins.py`, health plugin fields |
| 7 | `plugin_span`, load metrics |
| 8 | `backend-python/plugins/` reference plugins, eval cases |
| 9 | Frontend `PluginsPage` |
| 10 | Full validation & release |

### 9.3 Components to Reuse (DO NOT REIMPLEMENT)

Per Part II **Reuse Existing Components** — all verified present:

- `ToolRegistry`, `ToolExecutor`, `ToolAuthorizer`, `ToolDefinition`, `ToolHandler` — `app/ai/tools/`
- `register_production_tools`, `register_mcp_tools` — `app/ai/tools/registration.py`
- `PromptManager`, `PromptRepository`, `PromptRenderer` — `app/ai/prompts/`
- `WorkflowManager`, `WorkflowExecutor`, `GraphValidator`, `NodeExecutor` — `app/ai/workflow/`
- `McpServerRegistry`, `McpConnectionConfig`, `McpPermissionPolicy` — `app/ai/mcp/`
- Span helpers — `app/ai/observability/tracing/spans.py`
- `get_current_caller`, `CallerContext` — `app/core/caller.py`
- Feature flag infrastructure — `app/core/config.py`
- DI factories — `app/ai/deps.py`
- Evaluation harness — `app/ai/evaluation/` (`make eval` 15/15)

---

## 10. Dependency Verification

| Dependency | Phase 0 status | Notes |
| ---------- | -------------- | ----- |
| Epic 07 Observability (predecessor) | **Verified** | Phases 0–10 complete; release summary published |
| Epic 06 Workflow Engine | **Verified** | `NodeExecutor`, `GraphValidator`, postgres store operational |
| Epic 03 MCP Integration | **Verified** | `register_mcp_tools`, `McpConnectionConfig`, permission policy |
| Epic 01 Tool platform | **Verified** | `ToolRegistry` + `ToolExecutor` path tested |
| Built-in prompt system | **Verified** | `PromptManager.render()` + filesystem templates |
| PyYAML | **Verified** | `pyyaml>=6.0.3` in `pyproject.toml` |
| Feature flag / DI patterns | **Verified** | `@lru_cache` singletons in `app/ai/deps.py`; bool flags in `Settings` |
| PostgreSQL / Alembic | **Verified** | Head: `0008_observability_usage_cost.py` (8 migrations) |
| Python | **Verified** | `requires-python = ">=3.12"` |

**No new DB tables** required for plugin inventory in v1 (in-memory `PluginRegistry` only).

---

## 11. Platform Test-Backed Baseline

Automated test evidence only — no live manual smoke tests in this audit:

| Capability | Automated test evidence | Count (this audit) |
| ---------- | ----------------------- | ------------------ |
| Chat (plain + persistence) | `tests/test_chat_persistence.py`, `test_summarization_and_linking.py` | — |
| Streaming SSE | `tests/test_unified_chat.py`, `tests/test_chat_stream.py` | 9 (chat_stream) + unified chat |
| RAG | `tests/test_rag_api.py`, `tests/ai/rag/` | — |
| Workflow package | `tests/ai/workflow/` | **207** |
| Workflow router | `tests/test_workflow_router.py` | **23** |
| Workflow execution tool | `tests/test_workflow_tool.py` | **11** |
| **Workflow integration total** | `tests/ai/workflow/` + router + workflow tool | **241** |
| Tools / web search | `tests/test_tool_platform.py`, `tests/test_phase4_chat_tools.py` | **14** (tool platform; separate from workflow tool above) |
| MCP | `tests/ai/mcp/` | — |
| Agent runtime | `tests/ai/agent/` | 13+ modules |
| Memory | `tests/ai/memory/`, `tests/test_memory_router.py` | — |
| Voice | `tests/ai/voice/`, `tests/test_voice_router.py` | — |
| Observability router | `tests/test_observability_router.py` | **15** |
| Observability package | `tests/ai/observability/` | 16 test files |

Epic 08 plan baseline **241** matches the workflow integration total above: **207** (`tests/ai/workflow/`) + **23** (`tests/test_workflow_router.py`) + **11** (`tests/test_workflow_tool.py`). The **11** “tool” count in the plan refers to the workflow execution tool suite, not `tests/test_tool_platform.py` (14 tests).

---

## 12. Alembic Migrations

| Migration | Purpose |
| --------- | ------- |
| `0001`–`0007` | Chat, documents, pgvector, quotas, FTS, memory, workflow |
| `0008_observability_usage_cost.py` | Epic 07 — `cost_usd`, `pricing_version` on `usage_events` |

**Epic 08:** No plugin-related migrations planned (in-memory inventory only).

---

## 13. Implementation Assumptions (from Part I)

| Assumption | Value |
| ---------- | ----- |
| Master flag | `PLUGINS_ENABLED=false` (default) |
| Plugin directories | `["plugins"]` relative to `backend-python/` |
| Plugin allowlist | `[]` (empty → all discovered) |
| Load timeout | `30` seconds per plugin |
| Platform API | `PLUGIN_API_VERSION = "1"` |
| Manifest format | `plugin.yaml` beside plugin package |
| Entrypoint contract | `register(registrar: PluginRegistrar) -> None` |
| Lifecycle | Startup load only; immutable for process lifetime |

---

## 14. Acceptance (Phase 0)

| Criterion | Status |
| --------- | ------ |
| Existing platform fully operational | ✅ All gates pass |
| All extension points identified | ✅ §5 |
| No plugin implementation present | ✅ §8 |
| Baseline metrics recorded | ✅ §2–3, §11 |
| Epic 07 complete / authorized for Epic 08 | ✅ §1 |
| Part I architecture reviewed | ✅ §9 |

---

## 15. Exit Criteria & Authorization

| Item | Status |
| ---- | ------ |
| Baseline audit published | ✅ This document |
| User confirmation to proceed to Phase 1 | ⬜ **Pending** |

**Rollback:** Not applicable — Phase 0 made no code changes.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-08-plugin-architecture.md](../plans/post-mvp-v2-epic-08-plugin-architecture.md)
- Predecessor release: [docs/releases/post-mvp-v2-epic7-release-summary.md](../releases/post-mvp-v2-epic7-release-summary.md)
- Epic 07 Phase 0 audit (template): [docs/audits/post-mvp-v2-epic7-phase-0-baseline-audit.md](./post-mvp-v2-epic7-phase-0-baseline-audit.md)
- V2 strategy § Plugin Architecture: [docs/references/fullstack-ai-platform-v2-architecture-implementation-strategy.md](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md)
