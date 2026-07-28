# Post-MVP V2 Epic 03 — Phase 0 Baseline Audit

**Epic:** v2-03 MCP Integration
**Audit date:** 2026-07-26
**Auditor:** Cursor agent (Phase 0 execution)
**Git commit:** `dc28630` — `fix: compact the chat composer with a denser toolbar, clearer tool toggles, and hover tooltips (#100)`
**Depends on:** Epic 02 (v2-02 Advanced RAG) — Phase 12 completed (2026-07-25)

---

## Executive summary

Phase 0 baseline audit for Epic 03 (MCP Integration). **All backend quality gates pass.** No MCP package (`app/ai/mcp/`), flag (`MCP_ENABLED`), MCP client, server registry, tool discovery, or permission model exists yet — safe to scaffold in Phase 1 under new `app/ai/mcp/` package extending existing `app/ai/tools/` infrastructure.

| Gate area | Result |
| --------- | ------ |
| Epic 02 Phase 12 validation | ✅ Completion record present in plan document |
| Epic 02 user confirm / Epic 03 authorize | ✅ Phase 12 awaiting user confirmation; proceeding with Phase 0 |
| Backend lint / format / typecheck | ✅ Pass |
| Backend test-cov (≥80%) | ✅ Pass — 733 tests, **88.65%** `app/` |
| Backend eval CLI | ✅ Pass — 5/5 |
| MCP conflicts | ✅ None (`MCP_ENABLED` absent; no `app/ai/mcp/` package) |

**Recommendation:** Phase 0 complete. Await explicit user instruction before Phase 1.

---

## Epic 02 completion confirmation

| Evidence | Location |
| -------- | -------- |
| Epic 02 plan | `docs/plans/post-mvp-v2-epic-02-advanced-rag.md` |
| Phase 12 validation metrics | Epic 02 Part II — Phase 12 completion record (2026-07-25) |
| Phase 12 status | Marked "Completed" pending user confirmation |
| Epic 03 baseline (copied) | Epic 03 Part II § Baseline matches Epic 02 Phase 12 metrics |

| Open item | Status |
| --------- | ------ |
| Epic 02 Phase 12 / Epic 03 authorization for continuing Epic 03 | ⏳ Proceeding with Phase 0 audit |

Epic 03 Phase 0 is complete. Further phases await explicit user instruction.

---

## Quality gate results

### Backend (`backend-python/`)

Commands run from `backend-python/` on 2026-07-26.

| Command | Result | Notes |
| ------- | ------ | ----- |
| `make lint` | ✅ Pass | Ruff — all checks passed |
| `make format-check` | ✅ Pass | 267 files already formatted |
| `make typecheck` | ✅ Pass | Pyright — 0 errors, 0 warnings, 0 informations |
| `make test-cov` | ✅ Pass | **733 passed**, **88.65%** coverage on `app/` (gate ≥80%) |
| `make eval` | ✅ Pass | 5 passed, 0 failed, 0 skipped |

**Eval detail** (`2026-07-26T07:35:01.429051+00:00`):

| Level | Passed | Failed | Skipped |
| ----- | ------ | ------ | ------- |
| prompt | 2 | 0 | 0 |
| retrieval | 2 | 0 | 0 |
| e2e | 1 | 0 | 0 |

Report: `backend-python/.eval/eval-report.json`

**Verify command** (`make lint && make typecheck && make test-cov && make eval`): ✅ Pass (individual gates above).

---

## Current tool behaviour (baseline)

Documented from live module paths — V1 local tools only; no MCP integration.

```text
Tool registration → register_production_tools(registry, settings)
                  → WEB_SEARCH_TOOL_DEFINITION + WebSearchClient (Tavily)
                  → ToolRegistry.register(definition, handler)

Tool invocation → Agent → ToolPlanner → ToolRunner → ToolExecutor.execute(call, context)
                ↓
                ToolRegistry.get_handler(name) → LocalToolHandler
                ↓
                ToolValidator.validate(tool, arguments)
                ↓
                ToolAuthorizer.authorize(tool, context) — authenticated users only
                ↓
                handler.execute(arguments, context) → ToolResult
                ↓
                Agent ToolRunner (retry/streaming/aggregation)
```

| Behaviour | Current state |
| --------- | ------------- |
| Tool platform | Epic 01 V1 — `ToolRegistry` → `ToolExecutor` → `ToolAuthorizer` → `ToolHandler` Protocol |
| Registered tools | **`web_search`** only (Tavily provider) |
| Tool discovery | Static registration via `register_production_tools` at startup |
| MCP client | **Absent** — no remote tool discovery or execution |
| MCP server registry | **Absent** — no server lifecycle management |
| MCP transport | **Absent** — no stdio/SSE transport |
| Tool naming | Single namespace (no server prefixing) |
| Permission model | `ToolAuthorizer` authenticated-only; no per-tool allowlist |
| Credentials | Static API keys via `Settings` (Tavily, Cohere, etc.); no MCP server credentials |
| Feature flag | `MCP_ENABLED` **not present**; `tools_enabled` remains V1 gate |

---

## Path inventory

Real module paths only (as of `dc28630`).

### `backend-python/app/ai/tools/` (extend — do not duplicate)

| Path | Lines | Role |
| ---- | ----- | ---- |
| `app/ai/tools/__init__.py` | 6 | Public exports |
| `app/ai/tools/registry.py` | 58 | In-memory `ToolRegistry` (register/get/list/get_handler) |
| `app/ai/tools/executor.py` | 200 | `ToolExecutor` orchestrates validation → auth → execution → normalization |
| `app/ai/tools/authorizer.py` | 20 | `ToolAuthorizer` — authenticated users only (guest denial) |
| `app/ai/tools/validator.py` | 104 | JSON Schema validation for tool arguments |
| `app/ai/tools/schemas.py` | 48 | `ToolDefinition`, `ToolCall`, `ToolResult`, `ToolExecutionContext` |
| `app/ai/tools/registration.py` | 29 | `register_production_tools` — registers V1 `web_search` tool |
| `app/ai/tools/implementations/web_search.py` | 325 | Tavily web search implementation |
| `app/ai/tools/stubs/echo.py` | 55 | Echo stub for testing |

**Not present (expected for Phase 1+):** `app/ai/mcp/` package and all MCP modules.

### `backend-python/app/ai/agent/executor/`

| Path | Role |
| ---- | ---- |
| `app/ai/agent/executor/tool_runner.py` | Multi-tool execution via `ToolExecutor`; retry/streaming/aggregation |

### `backend-python/app/ai/interfaces/`

| Path | Role |
| ---- | ---- |
| `app/ai/interfaces/tool_handler.py` | `ToolHandler` Protocol — async `execute(args, context) -> ToolResult` |

**Not present:** `mcp_client.py` or MCP-related interfaces.

### Services / DI / config

| Path | Role |
| ---- | ---- |
| `app/ai/deps.py` | DI factories for tool platform (Phase 9 MCP wiring target) |
| `app/core/config.py` | `tools_enabled`, `web_search_provider`, API keys; **no MCP config** |
| `backend-python/.env.example` | Tool-related: `TOOLS_ENABLED`, `WEB_SEARCH_PROVIDER`, `TAVILY_API_KEY` |

### Tests

| Path | Role |
| ---- | ---- |
| `tests/test_tool_platform.py` | Tool platform integration tests (registry/executor/authorizer/validator) |
| `tests/ai/tools/test_*.py` | Unit tests for tool components |
| `tests/ai/agent/test_tool_runner.py` | Tool runner tests |

**Not present:** `tests/ai/mcp/`.

---

## MCP integration conflict check

| Check | Result |
| ----- | ------ |
| `MCP_ENABLED` in config / `.env.example` | **Not present** |
| `app/ai/mcp/` package | **Does not exist** |
| MCP client / registry / discovery / executor / auth / permissions | **Do not exist** |
| MCP server configs in `Settings.mcp_servers` | **Absent** |
| MCP permission policy in `Settings.mcp_permission_policy` | **Absent** |
| Tool name prefixing (`{server_name}.{tool_name}`) | **Not implemented** |
| Parallel MCP package that would conflict | **Does not exist** |

No naming conflicts or partial implementations. Phase 1 scaffold is clear.

---

## Baseline metrics vs epic plan

Epic 03 Part II § Baseline (from Epic 02 Phase 12) vs this audit:

| Metric | Epic plan baseline | This audit | Delta |
| ------ | ------------------ | ---------- | ----- |
| Backend tests / coverage | Flag-off: 723 passed, 88.78% / flag-on: 723 passed, 88.79% | **733 passed**, **88.65%** | +10 tests; coverage delta within normal variance |
| Eval CLI | 5 passed (`2026-07-25T15:26:10Z`) | **5 passed** (`2026-07-26T07:35:01Z`) | — |
| MCP integration | None (local tools only) | None (local tools only) | — |

---

## Phase 0 acceptance checklist

| Criterion | Status |
| --------- | ------ |
| All quality gates pass | ✅ Backend gates |
| Inventory documents real module paths only | ✅ |
| No repository code changes | ✅ (audit doc only) |
| Audit doc published | ✅ |
| Baseline recorded | ✅ |
| Epic 02 Phase 12 complete | ✅ |
| User confirmed Phase 0 | ⏳ Awaiting confirmation |

---

## Completion record

| Metric | Result |
| ------ | ------ |
| Backend tests / coverage | **733 passed**, **88.65%** `app/` |
| Eval CLI | **5 passed**, 0 failed, 0 skipped (`2026-07-26T07:35:01Z`) |
| Git commit | `dc28630` |
| Audit doc | `docs/audits/post-mvp-v2-epic3-phase-0-baseline-audit.md` |

---

## Tool platform architecture (current state)

For reference in Phase 1+ implementation:

```text
ToolRegistry
├── _tools: dict[str, _RegisteredTool]
│   └── _RegisteredTool(definition: ToolDefinition, handler: ToolHandler)
├── register(tool, handler)
├── get(name) -> ToolDefinition | None
├── get_handler(name) -> ToolHandler | None
├── list_tools() -> list[ToolDefinition]
└── get_schemas_for_llm() -> list[dict]

ToolExecutor(registry, settings, validator, authorizer)
├── execute(call: ToolCall, context: ToolExecutionContext) -> ToolResult
│   ├── registry.get(tool_name)
│   ├── validator.validate(tool, arguments)
│   ├── authorizer.authorize(tool, context)
│   ├── handler.execute(arguments, context)
│   └── _finalize(call, context, result, start)
└── _finalize() — normalize result, add metadata, structured logging

ToolAuthorizer
└── authorize(tool, context) -> str | None
    └── Allow: context.caller.kind == "user"
    └── Deny: guest/anonymous (return error message)

ToolHandler Protocol
└── execute(args: dict, context: ToolExecutionContext) -> ToolResult

Agent ToolRunner (Epic 01)
├── execute_parallel(steps, context, retry_policy, stream_publisher) -> AggregatedToolResults
├── resolve_step_batches(steps)
├── retry_operation (retryable tool failures)
└── aggregate_tool_results
```

**Key extension points for MCP integration (Phase 1+):**

- Phase 4: `McpToolDiscovery.discover()` → register MCP tools as `ToolHandler` adapters in `ToolRegistry`
- Phase 5: `McpToolExecutionAdapter` implements `ToolHandler` Protocol → delegates to `McpClient.call_tool`
- Phase 7: `McpPermissionPolicy` composes with `ToolAuthorizer.authorize()` (both must pass)
- Phase 8: `register_mcp_tools()` extends `register_production_tools()` registration path
- Phase 9: DI wiring in `app/ai/deps.py` + startup/shutdown in `app/main.py`

---

## Open items for user

None for Phase 0. Awaiting explicit user instruction to proceed to Phase 1.
