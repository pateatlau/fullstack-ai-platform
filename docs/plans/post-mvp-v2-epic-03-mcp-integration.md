---
epic: v2-03
title: MCP Integration
status: completed
version: 1
depends_on: [v2-02]
provides:
  [
    McpClient,
    McpServerRegistry,
    McpToolDiscovery,
    McpToolExecutionAdapter,
    McpServerCredentials,
    McpConnectionConfig,
    McpPermissionPolicy,
    MCP_ENABLED,
  ]
feature_flags: [MCP_ENABLED]
packages: [app/ai/mcp]
test_paths: [tests/ai/mcp, tests/test_tool_platform.py]
---

# Post-MVP V2 Epic 03 — MCP Integration

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "3. MCP Integration"

**Predecessor:** [Epic 02 — Advanced RAG](./post-mvp-v2-epic-02-advanced-rag.md)

---

# Part I — Design

## Objective

Integrate Model Context Protocol (MCP) client capability to dynamically discover and execute tools from remote MCP servers under `app/ai/mcp/`. Ships behind `MCP_ENABLED=false` (default). When the flag is off, the existing V1 local tool path via `ToolRegistry` → `ToolExecutor` is unchanged.

**Delivers:** MCP client (stdio transport primary), dynamic server registration/unregistration, tool discovery, remote tool execution via existing `ToolExecutor` path, server credentials (env/config-backed), and per-server/per-tool permission model composing with `ToolAuthorizer`.

**Does not ship:** SSE transport (defer to future), full secret vault integration (env/config only), human-in-the-loop approval flows (Epic 8), dynamic plugin hot-reload SDK/versioning (Epic 7), enterprise RBAC/audit logs/rate limits (Epic 11), RAG-as-MCP-tool (RAG stays pre-handoff), or agent-core bypass of tool validation/authorization.

## Principles

Platform-first · composition over coupling · provider-agnostic core (Protocols) · streaming-first (preserve tool streaming events) · async-first · interface-driven · security by default (permission model, authenticated-only by default) · incremental · no over-engineering · extend existing tool infrastructure

## Architecture

```text
Startup → register_mcp_servers(config) → McpServerRegistry
Runtime → discover → McpToolDiscovery → register as ToolHandler
Request → ToolRegistry.get_handler → McpToolExecutionAdapter → McpClient.call_tool
                                    ↓
                            ToolExecutor (existing validation/auth/streaming)
                                    ↓
                            Agent ToolRunner (unchanged)
```

```text
app/ai/mcp/                      # NEW package — do not duplicate app/ai/tools/
├── __init__.py                  # export public API
├── client.py                    # McpClient Protocol + StdioMcpClient
├── registry.py                  # McpServerRegistry — runtime server lifecycle
├── discovery.py                 # list_tools → ToolDefinition mapping
├── executor.py                  # McpToolExecutionAdapter → ToolHandler
├── auth.py                      # McpServerCredentials, env/config resolution
├── permissions.py               # McpPermissionPolicy, compose with ToolAuthorizer
├── transport/
│   ├── stdio.py                 # StdioTransport (primary)
│   └── __init__.py
├── config.py                    # McpConnectionConfig model
└── exceptions.py                # McpConnectionError, McpToolExecutionError

app/ai/tools/                    # extend — wire MCP tools into existing registry
├── registration.py              # register_production_tools + register_mcp_tools
└── (existing executor/registry/authorizer/validator unchanged)

app/ai/deps.py                   # extend — DI for McpServerRegistry + discovery
app/core/config.py               # extend — MCP_ENABLED, mcp_servers JSON/env
```

Epic 01 agent runtime, Epic 02 RAG pipeline, and V1 tool path remain **unchanged** except for additive MCP tool registration when `MCP_ENABLED=true`.

## Components

| Component                         | Role                                                                                                                                  | Key outputs                              |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `McpClient`                       | Protocol for MCP RPC operations (connect, disconnect, list_tools, call_tool)                                                          | JSON-RPC responses                       |
| `StdioMcpClient`                  | Concrete stdio transport client; subprocess lifecycle + JSON-RPC over stdin/stdout; MCP spec 2024-11-05                               | Connected client instance                |
| `McpServerRegistry`               | Process-wide registry for MCP server connections; register/unregister/get by server name; track status (CONNECTING/CONNECTED/FAILED/DISCONNECTED) | Active `McpClient` instances + status    |
| `McpToolDiscovery`                | Map MCP `tools/list` response → `ToolDefinition` + `McpToolExecutionAdapter`; prefix tool names with `{server_name}.{tool_name}`     | `list[ToolDefinition]`, handlers         |
| `McpToolExecutionAdapter`         | Adapter implementing `ToolHandler` Protocol; delegates to `McpClient.call_tool`; preserves tool origin metadata (source, server, transport) | `ToolResult`                             |
| `McpServerCredentials`            | Immutable credential model (env vars, API keys, optional command args)                                                                 | Credential dict for client auth          |
| `McpConnectionConfig`             | Server connection config (name, command, args, env, transport); immutable after startup                                                | Config dict for client construction      |
| `McpPermissionPolicy`             | Per-server/per-tool allowlist; composes with `ToolAuthorizer` (authenticated-only by default)                                          | Authorization error or `None`            |
| `register_mcp_tools`              | Load MCP server configs; validate capabilities; discover tools; register handlers in `ToolRegistry` when flag on; cache results         | None (side effect: registry populated)   |

## Scope

**In:**

- MCP client Protocol + stdio transport (subprocess + JSON-RPC stdin/stdout)
- Dynamic server registration (startup-time from config; runtime register/unregister API)
- Tool discovery (MCP `tools/list` → `ToolDefinition` mapping)
- Remote tool execution via existing `ToolExecutor` path (register discovered tools as `ToolHandler` adapters)
- Server credentials (env/config-backed; no full secret vault)
- Permission model (per-server/per-tool allowlists; compose with `ToolAuthorizer`)
- Feature flag `MCP_ENABLED` (default `false`); V1 local tools unchanged when off
- Tests, docs, release summary

**Out:**

- Epic 4 Memory · Epic 5 Workflows · Epic 6 Observability/OTel traces · Epic 7 Plugin hot-reload/SDK/versioning · Epic 8 HITL approval flows · Epic 9 Background jobs · Epic 11 enterprise RBAC/audit logs/rate limits
- SSE transport (`TODO(future):` — defer to future epic or external contribution)
- RAG-as-MCP-tool (RAG stays in `UnifiedChatService` / `RAGService` pre-handoff)
- Agent-core MCP tool bypass (tools flow through `ToolRegistry` → `ToolExecutor` → `ToolRunner`)
- Full secret vault / dynamic credential rotation (env/config static credentials only)
- MCP server SDK authoring / custom MCP server development (client-side integration only)
- Default flip of `MCP_ENABLED` to `true`

## Dependencies

| Requires                                                                                                                                              | Provides to downstream                                                                                                                                                   |
| ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Epic 02 (`v2-02`) complete; Epic 01 tool platform (`ToolRegistry`, `ToolExecutor`, `ToolAuthorizer`, `ToolHandler`, `register_production_tools`) | `McpClient`, `McpServerRegistry`, `McpToolDiscovery`, `McpToolExecutionAdapter`, `McpPermissionPolicy`, `register_mcp_tools`, `MCP_ENABLED` flag                         |

**Future consumers:** Epic 7 (Plugin Architecture — dynamic MCP server plugins), Epic 8 (HITL — MCP tool approval flows), Epic 11 (Security & Governance — per-tool audit logs, rate limits)

## Locked decisions

| Topic                   | Decision                                                                                                                                                    | Deferred to                        |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Package                 | New `app/ai/mcp/`; extend `app/ai/tools/registration.py`; do not fork `app/ai/tools/` or `app/ai/agent/`                                                   | —                                  |
| MCP spec version        | **MCP specification 2024-11-05 (current stable)** target; future protocol revisions require separate epic; version mismatch logged but non-blocking         | Protocol updates (future epic)     |
| Tool naming             | Prefix MCP tools with server name: `{server_name}.{tool_name}` (e.g. `filesystem.read_file`); prevents collisions across servers                           | —                                  |
| MCP ↔ Tool platform     | MCP tools adapt into existing `ToolRegistry` → `ToolExecutor` → agent `ToolRunner` path; no bypass of validation/authorization/streaming                   | —                                  |
| MCP ↔ RAG               | No RAG-as-MCP-tool; RAG remains in `UnifiedChatService` / `RAGService` pre-handoff                                                                          | —                                  |
| Feature flag            | Single master `MCP_ENABLED` (default **false**); local V1 tools unchanged when off                                                                          | Default flag flip                  |
| Primary transport       | **stdio** (subprocess + JSON-RPC stdin/stdout); MCP spec 2024-11-05 compatible                                                                              | SSE transport (future)             |
| Transport adapter       | Protocol `McpClient` (abstract); concrete `StdioMcpClient`; SSE transport deferred                                                                          | Future SSE / other transports      |
| Server lifecycle        | Startup registration from config → connect → discover; optional runtime register/unregister; **no automatic reconnect** on unexpected termination           | Auto-reconnect (future epic)       |
| Server health tracking  | `McpServerRegistry` tracks status (`CONNECTING`, `CONNECTED`, `FAILED`, `DISCONNECTED`) per server for diagnostics                                          | —                                  |
| Discovery timing        | Startup: auto-discover on registration; **results cached** for process lifetime; **no background rediscovery**; runtime: explicit re-discovery API only     | Auto-rediscovery (future)          |
| Discovery caching       | Tool list cached after successful registration; stable for process lifetime unless explicit re-registration                                                 | —                                  |
| Tool handler mapping    | Each MCP tool → one `McpToolExecutionAdapter` instance → registered in `ToolRegistry` as `ToolHandler`                                                      | —                                  |
| Tool origin metadata    | Preserve `source="mcp"`, `server_name`, `transport` in `ToolDefinition` metadata (or adapter) for observability; does not affect execution                 | —                                  |
| Capability validation   | Servers missing required MCP capabilities (`tools/list`, `tools/call`) fail registration gracefully; skipped with warning; no partial registration          | —                                  |
| Config immutability     | MCP server configuration immutable after startup; changes require explicit runtime re-registration or process restart                                       | —                                  |
| Credentials             | `McpServerCredentials` from env/config (env vars, API keys); no secret vault                                                                                | Secret vault (Epic 11 or external) |
| Permission model        | `McpPermissionPolicy` (per-server/per-tool allowlists); compose with `ToolAuthorizer` (authenticated-only inherited); no RBAC/audit                         | Epic 11                            |
| Authentication defaults | MCP server connections require explicit credentials or inherit env; MCP tool invocation inherits `ToolAuthorizer` (authenticated users only)                | Epic 11 (RBAC)                     |
| Observability           | Structured log fields (server_name, tool_name, latency_ms, success); no raw tool arguments/responses in logs by default                                    | Epic 6 (OTel traces)               |
| Error handling          | Connection/transport errors → `ToolResult(success=False, error_code="mcp_connection_error")`; remote tool errors → preserve MCP error in `ToolResult`       | —                                  |
| Tool streaming          | MCP tools emit tool stream events via `ToolRunner` (existing); MCP server streaming results unsupported in v1 (treat as single-shot result)                | MCP streaming (future)             |
| Shutdown flow           | App shutdown: disconnect all servers → wait for graceful shutdown → force terminate remaining subprocesses after timeout                                    | —                                  |
| Plugin SDK              | MCP server discovery/registration thin interface only; full plugin SDK/versioning/hot-reload deferred                                                       | Epic 7                             |
| Human-in-the-loop       | No pause/edit/approve flows; MCP execution is synchronous behind `ToolExecutor`                                                                             | Epic 8                             |
| Dependencies            | Adding official MCP SDK (if needed) or async subprocess lib requires **user approval** at Phase where introduced                                            | —                                  |

## MCP connection & discovery flow

```text
1. Startup (when MCP_ENABLED=true):
   load MCP server configs → McpServerRegistry.register(server_name, config)
     ↓
   status = CONNECTING
     ↓
   McpClient.connect() → subprocess spawn + JSON-RPC initialize handshake
     ↓
   validate capabilities (tools/list, tools/call) → fail gracefully if missing
     ↓
   status = CONNECTED
     ↓
   McpToolDiscovery.discover(client, server_name) → tools/list → parse → prefix with {server_name}.{tool_name}
     ↓
   for each tool: ToolRegistry.register(definition, adapter) — cache results
     ↓
   preserve tool origin metadata (source="mcp", server_name, transport)

2. Runtime (optional re-discovery or new server):
   McpServerRegistry.register(server_name, new_config) — config immutable; explicit re-registration required
     ↓
   repeat discovery → register new tools → update cache

3. Tool invocation (unchanged from V1):
   Agent → ToolPlanner → ToolRunner → ToolExecutor.execute(call, context)
     ↓
   ToolRegistry.get_handler(name) → McpToolExecutionAdapter (e.g. "filesystem.read_file")
     ↓
   ToolAuthorizer.authorize(tool, context) — inherited; checks authenticated-only + MCP allowlist
     ↓
   McpToolExecutionAdapter.execute(arguments, context) → McpClient.call_tool(...)
     ↓
   parse MCP result → ToolResult

4. Shutdown:
   App lifespan shutdown → McpServerRegistry.disconnect_all()
     ↓
   for each server: client.disconnect() → wait for graceful shutdown
     ↓
   after timeout: force terminate remaining subprocesses (SIGTERM → SIGKILL)
```

Owner scope (`user_id`) and guest denial enforced at `ToolAuthorizer` level (same as V1).

## MCP server configuration (env/config)

Config-driven server list via `mcp_servers` JSON array in settings or env var:

```python
# Settings model
mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
```

```json
# Example .env or JSON config
MCP_SERVERS='[
  {
    "name": "filesystem",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/mcp-workspace"],
    "env": {},
    "transport": "stdio"
  },
  {
    "name": "github",
    "command": "uvx",
    "args": ["mcp-server-github"],
    "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}" },
    "transport": "stdio"
  }
]'
```

Credential resolution: env vars interpolated at startup; static for process lifetime.

## Permission model (per-server / per-tool)

```python
# Settings model
mcp_permission_policy: dict[str, Any] = Field(default_factory=dict)
```

```json
# Example .env or JSON config
MCP_PERMISSION_POLICY='{
  "allowed_servers": ["filesystem", "github"],
  "allowed_tools": {
    "filesystem": ["read_file", "list_directory"],
    "github": ["*"]
  }
}'
```

**Rules:**

- `allowed_servers` empty or absent → all configured servers allowed
- `allowed_tools` absent → all tools from allowed servers allowed
- `allowed_tools[server_name] = ["*"]` → all tools from server allowed
- `allowed_tools[server_name] = ["tool1", "tool2"]` → only listed tools allowed
- `ToolAuthorizer` inherited: authenticated users only (guest denial)
- Compose: both `ToolAuthorizer` and `McpPermissionPolicy` must pass

## Retry rules (MCP tool execution)

| Condition                                            | Retry? |
| ---------------------------------------------------- | ------ |
| Connection refused, timeout, subprocess crash        | Yes    |
| JSON-RPC parse error, invalid MCP response           | No     |
| MCP server error (e.g. `tools/call` error response)  | No     |
| Tool validation, auth denial (Epic 01 path)          | No     |

Defaults: inherit `ToolRetryPolicy` (`max_retries=3`, `retry_base_delay_seconds=1.0`). Wrap `retry_async` via Epic 01 retry framework.

## Streaming strategy

- Core: MCP tools flow through Epic 01 `ToolRunner` → existing tool stream events (`tool_start`, `tool_end`)
- MCP server-side streaming (if spec supports) unsupported in v1; treat as single-shot result
- Future: if MCP spec adds result streaming, map to incremental `tool_data` events

## Public APIs (stable after Phase 1)

| API                                                                                                                       | Kind                |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| `McpClient`, `McpServerRegistry`, `McpToolDiscovery`, `McpPermissionPolicy`                                               | Protocol / Registry |
| `McpConnectionConfig`, `McpServerCredentials`, `McpToolCall`, `McpToolResult` (internal)                                  | Model               |
| `McpConnectionError`, `McpToolExecutionError`, `McpDiscoveryError`, `McpAuthenticationError`, `McpPermissionDeniedError`  | Exception           |
| `register_mcp_tools(registry, mcp_registry, settings)` (extend `register_production_tools`)                               | Function            |

Internal (may evolve): `StdioMcpClient`, `StdioTransport`, `McpToolExecutionAdapter`, DI wiring, subprocess management.

## Configuration defaults

| Setting                | Default                                                                      |
| ---------------------- | ---------------------------------------------------------------------------- |
| `MCP_ENABLED`          | **`false`**                                                                  |
| `mcp_servers`          | `[]` (empty list)                                                            |
| `mcp_permission_policy`| `{}` (empty dict → all configured servers/tools allowed by default)          |
| `mcp_connection_timeout_seconds` | 10                                                                  |
| `mcp_tool_timeout_seconds`       | 30 (aligned with `request_timeout_seconds` default)                 |
| Existing               | `tools_enabled`, `agent_runtime_enabled`, tool retry defaults unchanged       |

## Design acceptance

- Flag off: V1 local tools via `ToolRegistry` → `ToolExecutor` unchanged
- Flag on: MCP servers connect; tools discovered; handlers registered; tool invocation flows through existing Epic 01 validation/auth/streaming path
- Authenticated users only (inherited from `ToolAuthorizer`); per-server/per-tool allowlist enforced
- Connection/transport errors → graceful `ToolResult` failure; no crash on MCP server unavailable
- Core `app/ai/mcp/` depends on `McpClient` Protocol — no tight coupling to subprocess or transport internals
- No imports from `app/ai/agent/` into MCP core (adapter pattern only)
- Coverage ≥80% on `app/` and `app/ai/mcp/`
- `IndexingJob`, RAG pipeline, agent runtime untouched

## Architectural invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Tool platform boundary** — MCP tools adapt into existing `ToolRegistry` → `ToolExecutor` → agent `ToolRunner` path; no bypass of validation/authorization/streaming.
- **Pre-handoff RAG boundary** — No RAG-as-MCP-tool; RAG remains in `UnifiedChatService` / `RAGService` before agent handoff.
- **Extend, don't fork** — New `app/ai/mcp/` package; extend `app/ai/tools/registration.py`; do not duplicate tool infrastructure.
- **Flag-off parity** — `MCP_ENABLED=false` leaves V1 local tool path behaviour unchanged.
- **MCP spec version lock** — Target MCP specification 2024-11-05 (current stable); future protocol revisions require separate epic to avoid protocol drift.
- **Tool naming collision prevention** — Prefix MCP tools with server name: `{server_name}.{tool_name}` to prevent collisions across multiple servers.
- **Discovery caching** — Tool discovery results cached after registration; no automatic background rediscovery; tools stable for process lifetime unless explicit re-registration.
- **Tool identity stability** — Tool identity (name, schema) remains stable for the lifetime of the process after successful registration; no automatic tool removal or replacement in this epic.
- **Transport abstraction** — `McpClient` Protocol (abstract); concrete `StdioMcpClient`; SSE/other transports deferred with `TODO(future):`.
- **Server health tracking** — `McpServerRegistry` tracks server status (`CONNECTING`, `CONNECTED`, `FAILED`, `DISCONNECTED`) for diagnostics.
- **No automatic reconnect** — MCP servers do not auto-reconnect on unexpected termination; reconnect logic deferred to future epic.
- **Config immutability** — MCP server configuration immutable after startup; changes require explicit runtime re-registration or process restart.
- **Capability validation** — Servers missing required MCP capabilities (`tools/list`, `tools/call`) fail registration gracefully and are skipped (no partial registration).
- **Tool origin metadata** — Preserve `source="mcp"`, `server_name`, `transport` in tool metadata for observability without affecting execution.
- **Credential isolation** — `McpServerCredentials` from env/config; no secret vault; no runtime credential rotation.
- **Permission composition** — `McpPermissionPolicy` composes with `ToolAuthorizer` (authenticated-only inherited); both must pass.
- **Process-scoped registry** — `McpServerRegistry` is process-wide singleton; server lifecycle managed at startup (from config) and optional runtime API.
- **Tool handler contract** — Each MCP tool registers as one `ToolHandler` adapter; `ToolExecutor.execute` lifecycle unchanged.
- **Single-shot execution** — MCP tool calls are synchronous; no MCP server streaming in v1.
- **Graceful shutdown** — App shutdown: disconnect all registered MCP servers → wait for graceful shutdown → force terminate remaining subprocesses after timeout.
- **Additive APIs** — Preserve existing `register_production_tools`; add `register_mcp_tools`; do not break V1 tool contracts.
- **Content-safe logs** — No raw MCP tool arguments, responses, or credentials in structured logs by default (names, ids, counts, latencies only).
- **No Epic 7/8/11 behaviour early** — Plugin SDK/hot-reload, HITL approval flows, RBAC/audit logs — `TODO(epic-N):` only.
- **Public APIs stable after Phase 1** — Changes to frozen Protocols/models require user approval.

---

# Part II — Execution

## Reuse existing components

**DO NOT REIMPLEMENT:**

| Component                                                             | Location                                      |
| --------------------------------------------------------------------- | --------------------------------------------- |
| `ToolRegistry`, `ToolExecutor`, `ToolAuthorizer`, `ToolValidator`     | `app/ai/tools/`                               |
| `ToolDefinition`, `ToolCall`, `ToolResult`, `ToolExecutionContext`    | `app/ai/tools/schemas.py`                     |
| `ToolHandler` Protocol                                                | `app/ai/interfaces/tool_handler.py`           |
| `register_production_tools`                                           | `app/ai/tools/registration.py`                |
| `ToolRunner` (agent executor)                                         | `app/ai/agent/executor/tool_runner.py`        |
| `retry_async`, `is_retryable_exception`                               | `app/core/retry.py`                           |
| `ToolRetryPolicy`, retry framework                                    | `app/ai/agent/retry/`                         |
| `AgentStreamEvent`, `StreamPublisher`, tool stream events             | `app/ai/agent/streaming/`, `models/events.py` |
| `Settings`, config validation                                         | `app/core/config.py`                          |
| DI factories                                                          | `app/ai/deps.py`                              |
| Agent runtime, RAG pipeline, chat/RAG services                        | `app/ai/agent/`, `app/ai/rag/`, `app/services/` |

## Not allowed

- Refactor unrelated code beyond documented integration steps
- Rename packages or move `app/ai/tools/` or `app/ai/agent/`
- Add dependencies without user approval (especially official MCP SDK, if any)
- Change existing tool/agent API contracts (additive only)
- Implement Epic 4+ behaviour (Memory, Workflows, Plugin SDK hot-reload, HITL, RBAC, full secret vault)
- Move RAG into MCP or agent-core tool replacement
- Bypass tool validation/authorization for MCP tools
- Log raw tool arguments/responses or credentials by default
- Change `AGENT_RUNTIME_ENABLED`, `ADVANCED_RAG_ENABLED` defaults or APIs

## Baseline

_Copied from Epic 02 Phase 12 completion record._

| Area                                                | State                                                                                          |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Backend tests / coverage                            | Flag-off: **723 passed**, **88.78%** `app/`; flag-on: **723 passed**, **88.79%** `app/`        |
| Advanced RAG package coverage                       | **95.62%** epic packages (`ai/rag` **96.95%**); gate ≥80%                                      |
| Eval CLI                                            | **5 passed**, 0 failed (`2026-07-25T15:26:10Z`)                                                |
| Flag-off regression (`ADVANCED_RAG_ENABLED=false`)  | **Pass** — `ADVANCED_RAG_ENABLED=false make test-cov`                                          |
| Flag-on advanced parity                             | **Pass** — `ADVANCED_RAG_ENABLED=true make test-cov`                                           |
| Agent flag regression                               | **Pass** — `AGENT_RUNTIME_ENABLED=false/true make test-cov` (**89.30%** / **88.79%**)          |
| Orchestration                                       | RAG pre-handoff in `UnifiedChatService`; agent optional; tools via `ToolRegistry` → `ToolExecutor` |
| MCP integration                                     | None (local tools only)                                                                        |

## Phase status

| Phase | Name                         | Effort | Status      |
| ----- | ---------------------------- | ------ | ----------- |
| 0     | Baseline Audit               | XS     | Completed   |
| 1     | Scaffold, Models, Interfaces | M      | Completed   |
| 2     | MCP Client (stdio)           | M      | Completed   |
| 3     | Server Registry              | S      | Completed   |
| 4     | Tool Discovery               | M      | Completed   |
| 5     | Tool Execution Adapter       | M      | Completed   |
| 6     | Credentials & Auth           | S      | Completed   |
| 7     | Permission Model             | M      | Completed   |
| 8     | Startup Registration         | S      | Completed   |
| 9     | Integration & DI Wiring      | M      | Completed   |
| 10    | Validation & Release         | S      | Completed   |

---

## Phase 0 — Baseline Audit

**Effort:** XS

**Deliverables:** `docs/audits/post-mvp-v2-epic3-phase-0-baseline-audit.md`

**Steps:**

- [ ] Confirm Epic 02 Phase 12 complete / authorized for Epic 03
- [ ] Run backend gates: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
- [ ] Inventory paths: `app/ai/tools/**` (registry, executor, authorizer, validator, registration, schemas, handler Protocol), `app/ai/agent/executor/tool_runner.py`, `app/ai/deps.py` (tool DI), `app/core/config.py`, `tests/test_tool_platform.py`
- [ ] Record current tool behaviour (local web_search only; no MCP)
- [ ] Write audit doc; record metrics below
- [ ] Phase 0 complete — user confirmed

**Verify:** `make lint && make typecheck && make test-cov && make eval`

**Acceptance:**

- All quality gates pass; no repository code changes
- Inventory documents real module paths only (no MCP package yet)

**Exit criteria:**

- Audit published; baseline recorded; user confirmed Phase 0

**Completion record:**

| Metric                   | Result |
| ------------------------ | ------ |
| Backend tests / coverage |        |
| Eval CLI                 |        |
| Git commit               |        |
| Audit doc                |        |

---

## Phase 1 — Scaffold, Models, Interfaces

**Effort:** M

**Deliverables:** MCP models/Protocols; `MCP_ENABLED=false`; package layout stubs; public API exports

**Steps:**

- [ ] Add `MCP_ENABLED` to `app/core/config.py` + `backend-python/.env.example` (default **false**)
- [ ] Create `app/ai/mcp/` package tree (`__init__.py`, `client.py`, `registry.py`, `discovery.py`, `executor.py`, `auth.py`, `permissions.py`, `transport/`, `config.py`, `exceptions.py`)
- [ ] Add Protocol: `McpClient` with methods `connect()`, `disconnect()`, `list_tools()`, `call_tool(name, arguments)` in `client.py`
- [ ] Add models: `McpConnectionConfig`, `McpServerCredentials`, `McpToolCall` (internal), `McpToolResult` (internal) in `config.py` / `auth.py`
- [ ] Add exceptions: `McpConnectionError`, `McpToolExecutionError`, `McpDiscoveryError`, `McpAuthenticationError`, `McpPermissionDeniedError` in `exceptions.py`
- [ ] Export public API from `app/ai/mcp/__init__.py`
- [ ] Add `tests/ai/mcp/test_models.py`, `test_interfaces.py`
- [ ] Phase 1 complete — user confirmed

**Verify:** `make typecheck && pytest tests/ai/mcp/test_models.py tests/ai/mcp/test_interfaces.py`

**Acceptance:**

- Imports clean; flag default false; tool hot path untouched
- Public APIs match Part I freeze list

**Exit criteria:**

- Tests pass; public API finalized; user confirmed Phase 1

---

## Phase 2 — MCP Client (stdio)

**Effort:** M

**Deliverables:** `transport/stdio.py`, `StdioMcpClient`; subprocess lifecycle; JSON-RPC stdin/stdout

**Steps:**

- [ ] Implement `StdioTransport` (subprocess spawn, stdin/stdout streams, JSON-RPC protocol wrapper)
- [ ] Implement `StdioMcpClient` (implements `McpClient` Protocol; wraps `StdioTransport`)
- [ ] `connect()` → spawn subprocess via `command` + `args` from `McpConnectionConfig`; handshake with MCP server (e.g. `initialize` request if spec requires)
- [ ] `disconnect()` → graceful subprocess shutdown; timeout then SIGTERM/SIGKILL if needed
- [ ] `list_tools()` → JSON-RPC `tools/list` request; parse response
- [ ] `call_tool(name, arguments)` → JSON-RPC `tools/call` request; parse result or error
- [ ] Timeout enforcement: `mcp_connection_timeout_seconds` for connect/list; `mcp_tool_timeout_seconds` for call_tool
- [ ] Add `tests/ai/mcp/test_stdio_client.py` (fake subprocess/echo server; mock JSON-RPC responses)
- [ ] Phase 2 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_stdio_client.py`

**Acceptance:**

- Connect/disconnect/list/call tested with fakes
- Timeout/error handling covered
- No actual external MCP server required for unit tests

**Exit criteria:**

- Tests pass; user confirmed Phase 2

---

## Phase 3 — Server Registry

**Effort:** S

**Deliverables:** `registry.py` (`McpServerRegistry`); process-scoped singleton with health tracking

**Steps:**

- [ ] Implement `McpServerRegistry` (in-memory dicts: `server_name` → `McpClient` instance, `server_name` → status)
- [ ] Server status enum: `CONNECTING`, `CONNECTED`, `FAILED`, `DISCONNECTED`
- [ ] `register(server_name, config)` → set status `CONNECTING`; instantiate `StdioMcpClient`; call `connect()`; on success set `CONNECTED`; on failure set `FAILED`; store in registry
- [ ] `unregister(server_name)` → call `disconnect()`; set status `DISCONNECTED`; remove from registry
- [ ] `get(server_name)` → return active client or `None`
- [ ] `get_status(server_name)` → return server status
- [ ] `list_servers()` → return list of registered server names with status
- [ ] `disconnect_all()` → graceful shutdown for all servers (used in app shutdown)
- [ ] Singleton via DI: `get_mcp_server_registry()` in `app/ai/deps.py` (lru_cache)
- [ ] Add `tests/ai/mcp/test_registry.py` (fake `McpClient`; register/unregister/get lifecycle; status transitions)
- [ ] Phase 3 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_registry.py`

**Acceptance:**

- Registry lifecycle tested (register duplicate name → error; unregister missing → no-op)
- Status transitions tested (CONNECTING → CONNECTED/FAILED → DISCONNECTED)
- DI factory ready

**Exit criteria:**

- Tests pass; user confirmed Phase 3

---

## Phase 4 — Tool Discovery

**Effort:** M

**Deliverables:** `discovery.py` (`McpToolDiscovery`); map MCP `tools/list` → `ToolDefinition` with collision prevention

**Steps:**

- [ ] Implement `McpToolDiscovery.discover(client: McpClient, server_name: str) -> list[tuple[ToolDefinition, McpToolExecutionAdapter]]`
- [ ] Validate MCP server capabilities: check for `tools/list`, `tools/call` support; raise `McpDiscoveryError` if missing
- [ ] Call `client.list_tools()` → parse MCP tool schema (name, description, input_schema → JSON Schema)
- [ ] **Prefix tool names** with `{server_name}.{tool_name}` to prevent collisions (e.g. `filesystem.read_file`, `github.read_file`)
- [ ] Map MCP tool schema → `ToolDefinition(name, description, parameters)` (MCP `inputSchema` → OpenAI function-calling `parameters`)
- [ ] Preserve tool origin metadata: add `metadata={"source": "mcp", "server_name": server_name, "transport": "stdio", "original_name": tool_name}` to `ToolDefinition`
- [ ] For each tool: instantiate `McpToolExecutionAdapter(server_name, tool_name, client)` (Phase 5)
- [ ] Return list of `(ToolDefinition, adapter)` tuples
- [ ] Add `tests/ai/mcp/test_discovery.py` (fake `McpClient` with mock `list_tools` response; test name prefixing, metadata preservation, capability validation)
- [ ] Phase 4 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_discovery.py`

**Acceptance:**

- MCP schema → `ToolDefinition` mapping tested (name prefixed, description, parameters JSON Schema preserved)
- Tool origin metadata present (source, server_name, transport, original_name)
- Capability validation tested (missing `tools/list` or `tools/call` → `McpDiscoveryError`)
- Empty tool list → empty result (no crash)
- Name collision prevention tested (two servers with same tool name → distinct prefixed names)

**Exit criteria:**

- Tests pass; user confirmed Phase 4

---

## Phase 5 — Tool Execution Adapter

**Effort:** M

**Deliverables:** `executor.py` (`McpToolExecutionAdapter`); implements `ToolHandler` Protocol

**Steps:**

- [ ] Implement `McpToolExecutionAdapter(server_name, tool_name, client: McpClient)` (implements `ToolHandler`)
- [ ] `execute(arguments: dict[str, object], context: ToolExecutionContext) -> ToolResult`
- [ ] Call `client.call_tool(tool_name, arguments)` → parse MCP result or error
- [ ] Map MCP result → `ToolResult(success=True, data=...)` or `ToolResult(success=False, error=..., error_code="mcp_error")`
- [ ] Connection/transport errors → `ToolResult(success=False, error_code="mcp_connection_error")`
- [ ] Timeout → `ToolResult(success=False, error_code="timeout")`
- [ ] Structured log: `server_name`, `tool_name`, `latency_ms`, `success` (no raw arguments/response)
- [ ] Add `tests/ai/mcp/test_executor.py` (fake `McpClient`; success/error/timeout/connection-error paths)
- [ ] Phase 5 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_executor.py`

**Acceptance:**

- Adapter implements `ToolHandler` Protocol (type-checks)
- All error paths tested; no raw data in logs

**Exit criteria:**

- Tests pass; user confirmed Phase 5

---

## Phase 6 — Credentials & Auth

**Effort:** S

**Deliverables:** `auth.py` (`McpServerCredentials`, credential resolution from env/config)

**Steps:**

- [ ] Implement `McpServerCredentials` model (env_vars dict, api_keys dict, optional command_args list)
- [ ] Credential resolution: interpolate env var placeholders (e.g. `${GITHUB_PERSONAL_ACCESS_TOKEN}`) at startup
- [ ] Wire into `McpConnectionConfig`: add `credentials: McpServerCredentials | None` field
- [ ] Pass credentials to `StdioMcpClient.connect()` → merge into subprocess env
- [ ] Add `tests/ai/mcp/test_auth.py` (credential interpolation, missing env var → error or fallback)
- [ ] Phase 6 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_auth.py`

**Acceptance:**

- Env var interpolation tested (present, absent, nested)
- No credentials logged by default

**Exit criteria:**

- Tests pass; user confirmed Phase 6

---

## Phase 7 — Permission Model

**Effort:** M

**Deliverables:** `permissions.py` (`McpPermissionPolicy`); compose with `ToolAuthorizer`

**Steps:**

- [ ] Implement `McpPermissionPolicy` (config: `allowed_servers`, `allowed_tools` dict)
- [ ] `authorize_server(server_name: str) -> str | None` (check `allowed_servers`)
- [ ] `authorize_tool(server_name: str, tool_name: str) -> str | None` (check `allowed_tools[server_name]` with `*` wildcard)
- [ ] Compose with `ToolAuthorizer`: in `ToolExecutor.execute`, call `McpPermissionPolicy` **after** `ToolAuthorizer.authorize` (both must pass)
- [ ] Extend `app/core/config.py`: add `mcp_permission_policy: dict[str, Any]` (default empty dict)
- [ ] Add `tests/ai/mcp/test_permissions.py` (allowlist/denylist, wildcard, guest denial inherited from `ToolAuthorizer`)
- [ ] Phase 7 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_permissions.py tests/test_tool_platform.py`

**Acceptance:**

- Authenticated-only inherited from `ToolAuthorizer`
- Per-server/per-tool allowlist enforced
- Guest denial + MCP permission denial both tested

**Exit criteria:**

- Tests pass; tool platform regression green; user confirmed Phase 7

---

## Phase 8 — Startup Registration

**Effort:** S

**Deliverables:** `register_mcp_tools` function; load MCP server configs; discover + register at startup; cache results

**Steps:**

- [ ] Extend `app/core/config.py`: add `mcp_servers: list[dict[str, Any]]` (default empty list), `mcp_connection_timeout_seconds`, `mcp_tool_timeout_seconds`
- [ ] Add MCP spec version constant: `MCP_SPEC_VERSION = "2024-11-05"` for logging/validation
- [ ] Implement `register_mcp_tools(registry: ToolRegistry, mcp_registry: McpServerRegistry, settings: Settings, permission_policy: McpPermissionPolicy | None = None)`
- [ ] For each server config in `settings.mcp_servers`:
  - Parse `McpConnectionConfig` from dict (config immutable after startup)
  - Call `mcp_registry.register(server_name, config)` → status tracking (CONNECTING → CONNECTED/FAILED)
  - Validate server capabilities (`tools/list`, `tools/call`) → skip gracefully with warning if missing
  - Call `McpToolDiscovery.discover(client, server_name)` → prefixed tool names + origin metadata
  - **Cache discovery results** in memory (no background rediscovery)
  - For each `(ToolDefinition, adapter)`: check `permission_policy.authorize_server/tool`; if allowed, `registry.register(definition, adapter)`
- [ ] Connection/discovery errors → log warning; skip server; do not crash startup
- [ ] Log MCP spec version mismatch as warning (non-blocking)
- [ ] Add `backend-python/.env.example` entries for `MCP_ENABLED`, `MCP_SERVERS`, `MCP_PERMISSION_POLICY`, timeout settings; include example with filesystem/github servers
- [ ] Add `tests/ai/mcp/test_registration.py` (fake `McpClient`, fake configs, success/error/permission-denied paths, capability validation, caching)
- [ ] Phase 8 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_registration.py`

**Acceptance:**

- Config-driven server list parsed (immutable after startup)
- Discovery errors and missing capabilities do not crash startup (graceful skip with warning)
- Permission-denied tools are skipped
- Discovery results cached (no re-discovery unless explicit API call)
- Tool names prefixed with server name
- Tool origin metadata preserved

**Exit criteria:**

- Tests pass; user confirmed Phase 8

---

## Phase 9 — Integration & DI Wiring

**Effort:** M

**Deliverables:** DI wiring in `app/ai/deps.py`; startup/shutdown hooks in `main.py`; parity tests

**Steps:**

- [ ] Add `get_mcp_server_registry()` to `app/ai/deps.py` (lru_cache; returns `McpServerRegistry()`)
- [ ] Add `get_mcp_permission_policy()` to `app/ai/deps.py` (returns `McpPermissionPolicy(settings.mcp_permission_policy)`)
- [ ] Wire `register_mcp_tools` at app startup: in `backend-python/app/main.py` lifespan event, call `register_mcp_tools(...)` when `MCP_ENABLED=true`
- [ ] Wire MCP shutdown: in `main.py` lifespan shutdown, call `mcp_registry.disconnect_all()` → wait for graceful shutdown → force terminate remaining subprocesses after timeout (e.g. 5s)
- [ ] Flag off: skip MCP registration/shutdown; existing `register_production_tools` unchanged
- [ ] Update `ToolExecutor` (or `ToolAuthorizer`) to call `McpPermissionPolicy` when MCP tool detected (check `metadata["source"] == "mcp"` or adapter type)
- [ ] Add `tests/ai/mcp/test_integration.py` (end-to-end: fake MCP server, register, invoke via `ToolExecutor`, success/error/permission-denied, shutdown flow)
- [ ] Update `README.md` + backend `README.md` with MCP setup instructions + `.env.example` reference; document tool naming convention (`{server_name}.{tool_name}`)
- [ ] Phase 9 complete — user confirmed

**Verify:** `pytest tests/ai/mcp/test_integration.py tests/test_tool_platform.py`

**Acceptance:**

- Flag off: V1 local tools unchanged
- Flag on: MCP tools discovered, registered, invokable via existing `ToolExecutor` path
- Tool names prefixed with server name (collision-free)
- Shutdown flow tested (graceful disconnect + force terminate)
- Agent runtime untouched (tools flow through `ToolRunner` as before)

**Exit criteria:**

- Parity tests pass; user confirmed Phase 9

**Rollback:**

- Set `MCP_ENABLED=false`; remove MCP DI branches from startup/shutdown; re-run `pytest tests/test_tool_platform.py tests/ai/agent/test_tool_runner.py`
- Revert PR if needed

---

## Phase 10 — Validation & Release

**Effort:** S

**Steps:**

- [ ] Full suite: `MCP_ENABLED=false` then `true` (with fake MCP server; no real external MCP dependencies for CI)
- [ ] Also confirm `AGENT_RUNTIME_ENABLED`, `ADVANCED_RAG_ENABLED` flag-off/on still green (no regressions from Epic 01/02)
- [ ] Docker smoke (optional: add fake MCP server to compose for integration smoke)
- [ ] `make eval`
- [ ] Write `docs/releases/post-mvp-v2-epic3-release-summary.md`
- [ ] Set Phase status rows to **Completed**; tick DoD
- [ ] Phase 10 complete — user confirmed; Epic 4 authorized

**Verify:** `make test-cov && make eval`

**Acceptance:**

- Part I design acceptance met; coverage ≥80% on `app/` and `app/ai/mcp/`
- Flag-off parity pass

**Exit criteria:**

- Release summary published; user confirmed Phase 10; next epic authorized

**Completion record:**

| Metric                                          | Result                                           |
| ----------------------------------------------- | ------------------------------------------------ |
| Backend tests / coverage                        | 923 passed, 89.02% `app/` coverage               |
| MCP package coverage                            | 190 passed, 96% `app/ai/mcp/` coverage           |
| Eval CLI                                        | 5 passed, 0 failed (2026-07-27T11:35:22Z)        |
| Flag-off regression (`MCP_ENABLED=false`)       | ✅ Pass — 923 passed, 89.02% coverage            |
| Flag-on MCP parity                              | ✅ Pass — 923 passed, 89.04% coverage            |
| Agent / RAG flag regressions                    | ✅ Pass — AGENT=false 89.47%, RAG=false 89.00%   |

---

## Files index

| Path                                                          | Action | Owner    | Phase    |
| ------------------------------------------------------------- | ------ | -------- | -------- |
| `docs/audits/post-mvp-v2-epic3-phase-0-baseline-audit.md`     | create | Docs     | 0        |
| `app/core/config.py`                                          | modify | Core     | 1, 7, 8  |
| `backend-python/.env.example`                                 | modify | Docs     | 1, 8     |
| `app/ai/mcp/__init__.py`                                      | create | Core     | 1        |
| `app/ai/mcp/client.py`                                        | create | Core     | 1, 2     |
| `app/ai/mcp/registry.py`                                      | create | Core     | 1, 3     |
| `app/ai/mcp/discovery.py`                                     | create | Core     | 1, 4     |
| `app/ai/mcp/executor.py`                                      | create | Adapter  | 1, 5     |
| `app/ai/mcp/auth.py`                                          | create | Core     | 1, 6     |
| `app/ai/mcp/permissions.py`                                   | create | Core     | 1, 7     |
| `app/ai/mcp/transport/stdio.py`                               | create | Core     | 2        |
| `app/ai/mcp/config.py`                                        | create | Core     | 1, 6     |
| `app/ai/mcp/exceptions.py`                                    | create | Core     | 1        |
| `app/ai/tools/registration.py`                                | modify | Adapter  | 8        |
| `app/ai/deps.py`                                              | modify | Adapter  | 3, 9     |
| `app/main.py`                                                 | modify | Adapter  | 9        |
| `tests/ai/mcp/**`                                             | create | Tests    | 1–9      |
| `backend-python/README.md`, root `README.md`                  | modify | Docs     | 9, 10    |
| `docs/releases/post-mvp-v2-epic3-release-summary.md`          | create | Docs     | 10       |

## PR map

One PR per phase; branch `v2/epic-03/phase-{pp}-{slug}`.

## Risks

| Risk                                      | Mitigation                                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------------------- |
| Breaks V1 local tools                     | Flag default off; Phase 9 rollback; flag-off parity tests                                   |
| MCP server unavailable / subprocess crash | Connection errors → graceful `ToolResult` failure; do not crash startup                      |
| MCP spec compliance drift                 | Stub spec version in Phase 1; document MCP spec v1.0 target; test with known MCP servers    |
| Subprocess management complexity          | Timeout enforcement; graceful shutdown; SIGTERM/SIGKILL fallback; test with fake subprocess |
| Permission bypass                         | Compose `ToolAuthorizer` + `McpPermissionPolicy`; both must pass; test guest denial + allowlist |
| Tool platform coupling                    | Adapter pattern; MCP tools register as `ToolHandler`; `ToolExecutor` unchanged              |
| Agent boundary erosion                    | Invariant: no agent-core MCP bypass; reuse table forbids tool platform duplication          |
| Scope creep into Epic 7/8/11              | `TODO(epic-N):` markers for plugin SDK, HITL, RBAC; defer with explicit out-of-scope        |
| New dependency surprises                  | Explicit user approval gate if MCP SDK or async subprocess lib introduced                    |

## Observability

Structured log fields (no raw tool arguments/responses/credentials by default):

| Field                       | Purpose                                          |
| --------------------------- | ------------------------------------------------ |
| `mcp_enabled`               | Flag state for the request                      |
| `mcp_spec_version`          | MCP specification version (e.g. "2024-11-05")   |
| `server_name`               | MCP server identifier                            |
| `server_status`             | Server health (CONNECTING/CONNECTED/FAILED/DISCONNECTED) |
| `tool_name`                 | Tool identifier (local or MCP prefixed)          |
| `tool_source`               | `"local"` or `"mcp"`                             |
| `tool_original_name`        | MCP tool name before server prefix               |
| `mcp_connection_latency_ms` | Connection time                                  |
| `mcp_discovery_tool_count`  | Tools discovered per server                      |
| `mcp_discovery_cached`      | Bool (result from cache vs fresh discovery)      |
| `mcp_connection_failed`     | Bool                                             |
| `mcp_capability_missing`    | Bool (server missing required capabilities)      |
| `mcp_tool_execution_failed` | Bool (in addition to `success`)                  |
| `mcp_permission_denied`     | Bool                                             |

## Definition of done

- [x] Part I components delivered; Part I design acceptance met
- [x] Public APIs stable per Phase 1
- [x] MCP path behind `MCP_ENABLED`; V1 local tools unchanged when off; parity when on
- [x] MCP tools flow through existing `ToolRegistry` → `ToolExecutor` → agent `ToolRunner` path
- [x] `tests/ai/mcp/` complete; coverage ≥80% on `app/ai/mcp/` and `app/`
- [x] `make eval` passes; release summary published
- [x] All phases **Completed**; user confirmed each
- [x] Program DoD: [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md)
- [ ] User authorizes Epic 4

## Changelog

| Date       | Change                                                                                                                                                                                                                                                                                                                                 |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-07-26 | Initial plan (Part I + Part II). Locked: stdio transport (MCP spec 2024-11-05); tool naming `{server_name}.{tool_name}`; discovery caching (no auto-rediscovery); server health tracking (status enum); capability validation; tool origin metadata; config immutability; graceful shutdown flow; tool identity stability invariant. |
