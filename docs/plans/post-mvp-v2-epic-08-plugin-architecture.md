---
epic: v2-08
title: Plugin Architecture
status: in_progress
version: 1.6
depends_on: [v2-07]
provides:
  [
    PluginManifest,
    PluginRegistry,
    PluginLoader,
    PluginRegistrar,
    PluginContributionKind,
    PluginRecord,
    PluginLoadFailureReason,
    PLUGIN_API_VERSION,
    PLUGINS_ENABLED,
    plugins_router,
  ]
feature_flags: [PLUGINS_ENABLED]
packages: [app/ai/plugins]
test_paths:
  [
    tests/ai/plugins,
    tests/test_plugins_router.py,
    tests/plugins/fixtures,
    frontend/src/pages/PluginsPage.test.tsx,
    frontend/src/api/pluginsClient.test.ts,
  ]
---

# Post-MVP V2 Epic 08 — Plugin Architecture

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "8. Plugin Architecture"

**Predecessor:** [Epic 07 — Observability & Evaluation](./post-mvp-v2-epic-07-observability-and-evaluation.md)

---

# Part I — Design

## Objective

Introduce a unified, in-process **Plugin Architecture** so platform capabilities — tools, prompts, workflow node types, and MCP server registrations — can be extended without modifying core packages. Plugins are discovered from configured directories at process startup, validated against a versioned SDK contract, and wired into the existing registries (`ToolRegistry`, `PromptRepository`, workflow `NodeExecutor` map, `McpServerRegistry`) rather than bypassing them.

Epic 03 deferred "full plugin SDK/versioning/hot-reload" and "dynamic MCP server plugins." Epic 06 deferred "workflow plugin SDK / externally loaded node types" and pointed version diff/rollback UX at this epic. Epic 07 pre-declared bounded metric normalization for plugin/MCP inputs (`other` fallback) and left plugin execution spans as a future consumer. This epic delivers the SDK and the four contribution kinds the strategy lists; it does **not** replace env-based MCP configuration — plugin-provided MCP servers compose with it.

**Delivers:** A versioned Plugin SDK (`PluginManifest`, `PluginRegistrar`, `PluginLoader`, `PluginRegistry`); tool plugins (register `ToolDefinition` + `ToolHandler` through `ToolRegistry`); prompt plugins (register versioned templates into `PromptRepository`); workflow node plugins (register custom executors for `NodeType.PLUGIN` nodes); MCP server plugins (declare server configs consumed by existing `register_mcp_tools`); startup dynamic loading from configured directories with per-plugin fail-open behaviour; semver + platform API compatibility checks; authenticated read-only Plugin REST API; reference plugins and eval coverage; optional frontend plugin inventory page — all behind `PLUGINS_ENABLED=false` (default).

**Does not ship:** Runtime hot-reload or unload without process restart; remote plugin marketplace or signed artifact distribution; sandboxed/isolated plugin execution (plugins run in-process with full process privileges); organization-scoped plugin sharing; RBAC-gated plugin administration; visual plugin authoring UI; plugin-provided LLM providers; JavaScript/WASM plugins; billing or usage quotas per plugin; full workflow definition version diff/rollback UI.

Capabilities:

- Plugin SDK
- Tool plugins
- Prompt plugins
- Workflow plugins
- Dynamic loading
- Versioning

The Plugin Architecture is additive. When disabled, existing chat, RAG, MCP, memory, voice, agent, tool, workflow, and observability pipelines remain unchanged.

---

## Design Principles

- Platform-first — one loader/registrar used for every contribution kind
- Composition over coupling — plugins extend existing registries; never bypass `ToolExecutor`, `PromptManager`, `WorkflowExecutor`, or MCP adapters
- Interface-driven — `PluginRegistrar` exposes typed registration methods; plugins implement small entrypoint functions, not framework subclasses
- Security by default — allowlisted directories and optional plugin-id allowlist; manifest validation fails closed; no arbitrary code from workflow JSON
- Provider-agnostic — plugins may call platform abstractions (`LLMProvider` via DI factories) but do not embed provider SDKs in core
- Explicit lifecycle — load at startup, pin for process lifetime; changing plugins requires restart
- Feature-flag rollout
- Avoid over-engineering — filesystem manifests + Python entrypoints; no custom package index or daemon

---

## Scope

### In Scope

- Plugin SDK core (`app/ai/plugins/`): manifest schema, loader, registry, registrar, exceptions
- `PLUGINS_ENABLED` feature flag (default `false`)
- Dynamic discovery and import of plugins from configured directories at startup
- **Tool plugins** — register tools into `ToolRegistry`; execution flows through existing `ToolExecutor` / `ToolAuthorizer`
- **Prompt plugins** — register Jinja2 templates into `PromptRepository` under namespaced categories
- **Workflow node plugins** — register executors for `NodeType.PLUGIN` nodes; graph validation ensures referenced plugins are loaded
- **MCP server plugins** — declare `McpConnectionConfig` entries merged into startup MCP registration (when `MCP_ENABLED`)
- Versioning — plugin semver in manifest; platform `PLUGIN_API_VERSION` compatibility gate
- Per-plugin fail-open loading (one bad plugin must not prevent startup)
- `PluginRecord.load_duration_ms` and structured `PluginLoadFailureReason` for operational diagnostics
- Reserved manifest fields: `dependencies` (ignored in V2), optional author/discovery metadata, extensible `metadata` bag
- Authenticated read-only Plugin REST API (inventory of loaded plugins)
- Reference/example plugins under `backend-python/plugins/` (git-tracked)
- Observability hooks — `plugin_span`, load success/failure metrics (when `OBSERVABILITY_ENABLED`)
- Evaluation cases exercising a reference tool plugin and reference workflow node plugin
- Frontend plugin inventory page (read-only list)

### Out of Scope

- Runtime hot-reload, unload, or plugin watch/reload on filesystem changes
- Remote download, marketplace, or signed plugin artifacts
- Sandboxed plugin execution (WASM, subprocess isolation)
- Plugin administration RBAC, audit trail, or rate limits (Epic 11)
- Human-in-the-loop approval before plugin tool execution (Epic 09)
- Scheduled background plugin indexing or async plugin jobs (Epic 10)
- Plugin-provided LLM providers or RAG retrievers
- Visual workflow builder or plugin authoring UI
- Full workflow definition version diff/rollback UI (definitions remain API/JSON-authored; plugin metadata exposed read-only only)
- Replacing env-based `mcp_servers` configuration (plugins **add** server declarations; env config remains supported)

---

## High-Level Architecture

```text
Configured plugin directories (filesystem)
            │
            ▼
     PluginLoader.discover()
            │  parse plugin.yaml manifests
            │  validate api_version + semver
            ▼
     PluginLoader.load(manifest)
            │  importlib entrypoint → register(registrar)
            ▼
       PluginRegistrar
            │
   ┌────────┼────────────┬─────────────────┐
   ▼        ▼            ▼                 ▼
ToolRegistry  PromptRepository  WorkflowPluginRegistry  McpConnectionConfig[]
   │              │                  │                  │
   │              │                  │                  │
   ▼              ▼                  ▼                  ▼
ToolExecutor   PromptManager   WorkflowExecutor   register_mcp_tools()
   │              │                  │                  │
   └──────────────┴──────────────────┴──────────────────┘
                         │
              Existing platform pipelines
        (Chat | Agent | Workflow | MCP | RAG | …)
                         │
              plugin_span (when Observability on)
```

**Startup order (when flags on):**

1. Bootstrap observability/tracing (unchanged from Epic 07).
2. **`load_plugins()`** — discover manifests, import entrypoints, populate `PluginRegistry`, `WorkflowPluginRegistry`, and other platform registries.
3. **`register_production_tools()`** — V1 tools (unchanged).
4. **`register_mcp_tools()`** — env `mcp_servers` **plus** plugin-declared MCP servers (dedupe by server name; env wins on conflict).

When `PLUGINS_ENABLED=false`, steps 2 and plugin MCP contributions are skipped entirely.

---

## Locked Architectural Decisions

| Topic                  | Decision                                                                                                                                                                                    | Deferred to                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Plugin runtime model   | In-process Python modules loaded via `importlib`; plugins are **trusted code** with the same privileges as application code                                                                 | Subprocess/WASM sandbox → future            |
| Loading timing         | Startup only; registry state is immutable for process lifetime                                                                                                                              | Hot-reload / watch → future                 |
| Manifest format        | Git-tracked `plugin.yaml` beside plugin package; YAML parsed with strict schema validation                                                                                                  | TOML/pyproject plugin tables → future       |
| Entrypoint contract    | Callable `register(registrar: PluginRegistrar) -> None`; synchronous registration only                                                                                                      | Async registration → future                 |
| Tool contributions     | Must use `PluginRegistrar.register_tool(ToolDefinition, ToolHandler)`; names must be prefixed `{plugin_id}.` (dot-separated) to avoid collisions                                            | Unprefixed tools → forbidden                |
| Prompt contributions   | Templates registered under category `plugin/{plugin_id}`; identity `(category, name, version)` must be unique platform-wide                                                                 | User-owned prompt packs → future            |
| Workflow contributions | New `NodeType.PLUGIN = "plugin"`; node `config` requires `plugin_id` + `plugin_node_type` strings; executor map keyed by `(plugin_id, plugin_node_type)`                                    | Dynamic enum extension → forbidden          |
| MCP contributions      | Plugin declares zero or more `McpConnectionConfig`-compatible dicts; merged before `register_mcp_tools`; duplicate `name` vs env config → **env wins**, plugin entry skipped with warning   | Plugin-only secret vault → Epic 11          |
| Versioning             | Manifest fields: `version` (semver), `api_version` (platform plugin API string); loader rejects unsupported `api_version`                                                                   | Multi-version side-by-side plugins → future |
| Failure mode           | Per-plugin fail-open: log warning, record failure in `PluginRegistry`, continue startup; never crash the process for one plugin; **failed plugins roll back all staged/partial contributions before `mark_failed()`** | Fail-fast mode → future opt-in setting      |
| Authorization          | Tool plugins inherit `ToolAuthorizer` (same as V1/MCP tools); no plugin-specific bypass                                                                                                     | Per-plugin RBAC → Epic 11                   |
| Observability          | `plugin_span(plugin_id, …)` when enabled; load counters `plugins_loaded_total` / `plugin_load_failures_total` with shared bounded `failure_code` label (`none` on success); `plugin_id` span-only | Per-plugin billing → future                 |
| REST API               | Read-only inventory; authenticated callers only; no install/uninstall over HTTP                                                                                                             | Admin CRUD → Epic 11                        |
| Persistence            | No DB tables for plugins in v1; inventory reflects in-memory `PluginRegistry` at startup                                                                                                    | Durable plugin installations → future       |
| Manifest extensibility | Optional reserved fields (`dependencies`, author metadata, `metadata`) parsed and stored; **ignored for load ordering** in V2                                                                | Plugin-to-plugin deps resolution → future   |
| Module loading         | Plugin directory appended to `sys.path` once before entrypoint import; acceptable for V2 startup-only model                                                                                  | Isolated importlib loading per plugin → future |
| Plugin lifecycle states | V2 uses `loaded` \| `failed` only on `PluginRecord`                                                                                                                                    | `disabled`, `degraded` → future             |

---

## Plugin Manifest Schema

Each plugin directory contains a **`plugin.yaml`** at its root (or path configured relative to the directory entry).

```yaml
plugin_id: com.example.echo # required; stable reverse-DNS id; unique across loaded plugins
name: Echo Reference Plugin # required; human-readable
version: 1.0.0 # required; semver
api_version: '1' # required; must match a supported PLUGIN_API_VERSION
description: Optional short description
entrypoint: echo_plugin.register:register # required; module:callable
contributions: # optional declarative hint for REST inventory / validation
  - tool
  - prompt
  - workflow_node
  - mcp_server
# --- Optional author / discovery metadata (inventory UI, docs, future marketplace) ---
author: Example Corp
homepage: https://example.com
repository: https://github.com/example/echo-plugin
documentation: https://example.com/docs/echo-plugin
license: MIT
# --- Reserved for future plugin-to-plugin dependencies (ignored in V2) ---
dependencies: []
# or, when dependency resolution ships:
# dependencies:
#   - plugin_id: com.example.memory
#     version: '>=1.0.0'
# --- Reserved extensible key-value bag (ignored by loader logic in V2) ---
metadata: {}
mcp_servers: # optional; only when contributions include mcp_server
  - name: echo-mcp
    command: uvx
    args: ['mcp-server-echo']
    transport: stdio
# --- Optional platform hint (informational only; not enforced in V2) ---
min_platform_version: '0.1.0' # optional string; stored on PluginManifest; no load gate
```

**Validation rules (fail the plugin, not the process):**

- `plugin_id` — non-empty, matches `^[a-z][a-z0-9.-]*$`, unique among loaded plugins
- `version` — valid SemVer 2.0.0 string (`MAJOR.MINOR.PATCH`, optional `-pre-release` and `+build` metadata); format validation only in V2 (no version ordering or constraint evaluation)
- `api_version` — must equal a value in `SUPPORTED_PLUGIN_API_VERSIONS` (initially `{"1"}`)
- `entrypoint` — `module:attr` form; module importable from the plugin directory on `sys.path`
- `contributions` — if present, each item ∈ `{tool, prompt, workflow_node, mcp_server}`
- `mcp_servers` — each entry validates as `McpConnectionConfig` when MCP contribution is declared
- `dependencies` — if present, each entry must have `plugin_id` (non-empty string) and optional `version` constraint string; **parsed and stored on `PluginManifest` but not evaluated in V2** (no load-order changes, no transitive loading)
- `author`, `homepage`, `repository`, `documentation`, `license` — optional strings; surfaced in inventory when present
- `min_platform_version` — optional string; parsed and stored on `PluginManifest`; **informational only in V2** — not compared to the running platform version and does not affect load success/failure (compatibility is gated by `api_version` only)
- `metadata` — optional JSON object (string keys, JSON-serializable values); stored verbatim; **not interpreted by the loader in V2**

**Reserved fields policy:** Unknown top-level manifest keys are rejected at parse time (strict schema) — except `metadata`, which is the designated extension point. Future manifest fields require a Part I update and `PLUGIN_API_VERSION` bump.

---

## Plugin Loading Lifecycle

```text
lifespan startup
  │
  ├─ PLUGINS_ENABLED=false → skip (PluginRegistry empty stub)
  │
  └─ PLUGINS_ENABLED=true
        │
        ├─ For each path in plugin_directories:
        │     scan immediate child dirs as discovery candidates (one record each)
        │
        ├─ If plugin_allowlist non-empty → filter to listed plugin_ids
        │
        ├─ For each discovery candidate (deterministic sorted order; see § PluginRecord):
        │     if plugin.yaml missing → mark_failed (code=manifest_not_found); identity fields null
        │     else parse + validate schema + api_version
        │     on parse/validation failure → mark_failed (code=invalid_manifest); identity fields null or partial
        │     on api_version mismatch → mark_failed with PluginLoadFailureReason
        │         (code=unsupported_api_version, expected=SUPPORTED, actual=manifest.api_version)
        │     record load_start = monotonic clock
        │     import entrypoint module (plugin dir appended to sys.path)
        │     create per-plugin PluginRegistrar (staging context)
        │     invoke register(PluginRegistrar) under plugin_load_timeout_seconds guard
        │     on success → registrar.commit() → PluginRegistry.mark_loaded(manifest, contributions, load_duration_ms)
        │     on failure/timeout → registrar.rollback() → log warning; PluginRegistry.mark_failed(manifest, reason, load_duration_ms)
        │     record load_duration_ms
        │
        └─ Return PluginLoadReport (counts, failures, total_load_duration_ms)

register_production_tools(...)   # unchanged ordering after plugins
register_mcp_tools(..., extra_servers=PluginRegistry.list_mcp_servers())
```

**Determinism:** Plugins load in ascending `plugin_id` order so registration conflicts are reproducible. First successful registration wins; later duplicate tool names raise inside `ToolRegistry` and fail that plugin only.

**Atomic registration:** `PluginRegistrar.register_*()` calls stage contributions only; `commit()` promotes staged tools, prompts, workflow executors, and MCP entries to platform registries after `register()` returns without error. Any exception, validation error, or timeout during `register()` triggers `rollback()` before `mark_failed()` — a failed plugin must leave no partial live registry side effects.

**Registration timeout (V2, in-process):** `register(registrar)` runs in a **worker thread**; the loader waits up to `plugin_load_timeout_seconds` (wall clock) for it to return. This is the enforceable V2 boundary — Python cannot safely interrupt arbitrary synchronous plugin code in-process. On timeout: do **not** call `commit()`; call `registrar.rollback()` to discard staged contributions; mark the plugin `failed` with `PluginLoadFailureReason(code="timeout", message="Plugin registration exceeded {N}s timeout")` (no paths/stack traces); continue loading other plugins. The worker thread may still run until `register()` returns (daemon thread); the registrar enters a **closed** state after timeout so late `register_*()` calls raise `PluginRegistrationError` and cannot mutate staging. Because platform registries are written only in `commit()`, a timed-out plugin leaves **no live shared registry side effects** unless plugin code bypasses `PluginRegistrar` (unsupported). `load_all()` catches timeout and all other per-plugin errors internally — **never** raises uncaught exceptions to lifespan.

**sys.path hygiene:** Each plugin directory is appended once before its entrypoint import; paths are not removed (acceptable for process lifetime; see § Future Enhancements for isolated loading).

---

## PluginRecord & Load Diagnostics

Each **discovery candidate** (immediate child directory under a configured plugin directory) produces exactly one **`PluginRecord`** in `PluginRegistry` — regardless of outcome. Missing or malformed manifests still yield a failed record; they are never skipped silently.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `plugin_id` | `str \| None` | From manifest when valid; `null` when `plugin.yaml` is missing or required identity fields fail validation |
| `name` | `str \| None` | From manifest when valid; `null` when unavailable (REST may display `"Unknown plugin"` placeholder) |
| `version` | `str \| None` | Semver from manifest when valid; `null` when missing or invalid |
| `api_version` | `str \| None` | From manifest when valid; `null` when missing or invalid |
| `status` | `PluginStatus` | V2: `loaded` \| `failed` only |
| `contributions` | `list[PluginContributionKind]` | Populated on success; empty on failure |
| `load_duration_ms` | `float` | Wall time for validate + import + `register()` + `commit()`/`rollback()`; always set (`0.0` if failed before timing started) |
| `author`, `homepage`, `repository`, `documentation`, `license` | `str \| None` | Optional manifest metadata; all `null` when manifest not successfully parsed |
| `dependencies` | `list[PluginDependency]` | Parsed from manifest when valid; empty when manifest missing/malformed; **informational in V2** |
| `metadata` | `dict[str, object]` | Opaque extension bag from manifest; `{}` when manifest not successfully parsed; **stored internally; omitted from REST** |
| `failure` | `PluginLoadFailureReason \| None` | Set when `status=failed` |

**Missing or malformed manifests:**

| Situation | Record identity fields | `failure.code` | Load steps skipped |
| --------- | ---------------------- | -------------- | ------------------ |
| Child dir has no `plugin.yaml` | `plugin_id`, `name`, `version`, `api_version` all `null` | `manifest_not_found` | import, `register()`, `commit()` |
| `plugin.yaml` unreadable or fails strict schema / required-field validation | Partial values when safely parsed (e.g. invalid `plugin_id` → `plugin_id=null`); otherwise `null` | `invalid_manifest` | import, `register()`, `commit()` unless schema sufficient to proceed |
| Valid manifest, later failure (API version, entrypoint, registration, timeout) | Identity fields from manifest | per existing codes | failure point determines skipped steps |
| `register()` exceeds `plugin_load_timeout_seconds` | Identity fields from manifest | `timeout` | `commit()` skipped; `rollback()` clears staging; `status=failed` |

Internal discovery ordering uses `plugin_id` when present; records with `plugin_id=null` sort after resolvable ids (stable discovery ordinal tie-break). **Never** persist or expose filesystem paths, directory names, or `plugin.yaml` locations in `PluginRecord`, logs safe fields, REST, health, or spans.

**`PluginLoadFailureReason`** (structured, JSON-serializable):

| Field | Purpose |
| ----- | ------- |
| `code` | Machine-readable reason: `manifest_not_found`, `invalid_manifest`, `unsupported_api_version`, `entrypoint_import_error`, `registration_error`, `timeout`, `allowlist_excluded`, … |
| `message` | Short human-readable summary (no stack traces, no filesystem paths). When `code=timeout`: include configured limit only (e.g. `"Plugin registration exceeded 30s timeout"`) |
| `expected_api_versions` | Populated when `code=unsupported_api_version` — copy of `SUPPORTED_PLUGIN_API_VERSIONS` |
| `manifest_api_version` | Populated when `code=unsupported_api_version` — the manifest's `api_version` value |

Startup structured logs and `plugin_span` attributes reuse the same `code` + duration fields. Operators diagnose API mismatches from logs/registry without reading raw exceptions.

**REST exposure (safe subset):** `GET /api/plugins` includes **all** records (loaded and failed), including those with `plugin_id=null`. Nullable identity fields serialize as JSON `null`; UI may substitute `"Unknown plugin"` for display only. `GET /api/plugins/{plugin_id}` returns detail when `plugin_id` is non-null and matches; otherwise **404** (unresolvable failed records are list-only). List/detail responses include `load_duration_ms`, bounded author/discovery fields when parsed, detail-only informational `dependencies`, and when failed: `failure.code`, `failure.message`, and for API mismatches only `failure.expected_api_versions` + `failure.manifest_api_version`. The manifest **`metadata` bag is omitted** from all REST responses (stored internally on `PluginRecord` only). Never expose stack traces, directory names, `plugin.yaml` paths, `sys.path` entries, or entrypoint module paths.

**Health (`GET /api/health`):** `plugins_failed_count` includes manifest discovery/parse failures (`manifest_not_found`, `invalid_manifest`) plus all other failed records; `plugins_loaded_count` counts only `status=loaded`. Aggregate counts only — no per-plugin paths or failure stack traces.

---

## Tool Plugin Contract

Plugins register tools via:

```python
def register(registrar: PluginRegistrar) -> None:
    registrar.register_tool(
        ToolDefinition(name="com.example.echo.ping", description="…", parameters={…}),
        handler=EchoToolHandler(),
    )
```

**Rules:**

- Tool `name` **must** start with `{plugin_id}.` (the manifest's `plugin_id` plus `.`)
- Handler must implement `ToolHandler` (`app.ai.interfaces.tool_handler`)
- Execution path: `ToolExecutor` → `ToolAuthorizer` → handler (identical to V1/MCP)
- Receipt-aware tools may set `supports_execution_receipt` on the handler class for workflow crash-safe replay (Epic 06 protocol)
- Plugins must not mutate global `ToolRegistry` directly — only through `PluginRegistrar`

---

## Prompt Plugin Contract

Plugins register templates via:

```python
registrar.register_prompt_template(
    name="greeting",
    version="1",
    source="Hello {{ user_name }}!",  # inline source
)
# or
registrar.register_prompt_template(
    name="greeting",
    version="1",
    path="templates/greeting.v1.j2",  # relative to plugin dir
)
```

**Rules:**

- Effective category is always `plugin/{plugin_id}` — callers use `PromptManager.render(category="plugin/com.example.echo", name="greeting", version="1", variables=…)`
- `(category, name, version)` must be unique; collision with built-in or another plugin fails that plugin's registration
- Templates use the same Jinja2 `StrictUndefined` renderer as built-in prompts
- Prompt plugins do not bypass `prompt_span` — rendering goes through `PromptManager.render()`

---

## Workflow Node Plugin Contract

Epic 06's `NodeType` enum gains **`PLUGIN = "plugin"`**.

**Workflow node shape:**

```json
{
  "id": "echo_step",
  "type": "plugin",
  "config": {
    "plugin_id": "com.example.echo",
    "plugin_node_type": "echo",
    "message_key": "input_text"
  }
}
```

Plugins register executors via:

```python
registrar.register_workflow_node_type(
    node_type="echo",
    executor_factory=lambda ctx: EchoNodeExecutor(settings=ctx.settings),
    config_schema={…},  # JSON Schema object for validation/documentation
)
```

**Rules:**

- `plugin_node_type` is a short identifier unique within the plugin (not globally prefixed)
- Executor must satisfy `NodeExecutor` Protocol (`app.ai.workflow.nodes.base`)
- `GraphValidator` (extended) verifies `plugin_id` is **loaded** in `PluginRegistry` and `(plugin_id, plugin_node_type)` exists in the shared **`WorkflowPluginRegistry`**
- Side-effecting plugin nodes should respect `NodeExecutionRequest.execution_receipt_id` when performing external IO (same guidance as Epic 06 task/LLM/agent nodes)
- Plugin node executors are merged into `WorkflowManager`'s `node_executors` map under `NodeType.PLUGIN` via a dispatching `PluginNodeExecutor` that routes by config keys

**Dispatch pattern:**

```text
WorkflowExecutor → node.type == PLUGIN
  → PluginNodeExecutor.execute(node, context, request)
       → lookup (node.config.plugin_id, node.config.plugin_node_type)
       → delegate to registered executor
```

---

## MCP Server Plugin Contract

When `MCP_ENABLED=true`, plugins may declare MCP servers in manifest `mcp_servers` and/or register programmatically via `registrar.register_mcp_server(config)`.

**Merge policy with env config:**

| Situation                  | Behaviour                                                   |
| -------------------------- | ----------------------------------------------------------- |
| Server name only in plugin | Registered normally                                         |
| Server name only in env    | Unchanged (Epic 03 behaviour)                               |
| Same `name` in both        | **Env config wins**; plugin server skipped with warning log |
| MCP disabled               | Plugin MCP declarations ignored (debug log only)            |

Discovery and tool registration follow Epic 03 exactly — no parallel MCP path.

---

## Versioning & Compatibility

| Layer                  | Mechanism                                                                                                                                                      |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform plugin API    | Constant `PLUGIN_API_VERSION = "1"`; `SUPPORTED_PLUGIN_API_VERSIONS = frozenset({"1"})`                                                                        |
| Manifest `api_version` | Must be ∈ `SUPPORTED_PLUGIN_API_VERSIONS` or plugin load fails with `PluginLoadFailureReason(code=unsupported_api_version, …)`                               |
| Manifest `min_platform_version` | Optional string on `PluginManifest`; informational in inventory only — **not enforced** in V2 (no comparison to running platform version)                    |
| Manifest `version`     | Semver string for inventory/display; no automatic migration                                                                                                    |
| Manifest `dependencies` | Reserved; stored on `PluginManifest` / `PluginRecord` but **not resolved** in V2 — no transitive load, no semver constraint evaluation                        |
| Workflow definitions   | Reference plugin nodes by `plugin_id` + `plugin_node_type`; if plugin not loaded at startup, graph validation fails at definition create/update time           |
| Prompt templates       | Pinned by `(category, name, version)` at call site — same as built-in prompts                                                                                  |
| Breaking SDK changes   | Require bumping `PLUGIN_API_VERSION` and maintaining loader support for prior version until deprecation window (documented in changelog; single version in v1) |

**API mismatch diagnostic example (internal / logs / REST safe fields):**

```json
{
  "status": "failed",
  "failure": {
    "code": "unsupported_api_version",
    "message": "Plugin api_version '2' is not supported",
    "expected_api_versions": ["1"],
    "manifest_api_version": "2"
  }
}
```

---

## Security Model

Plugins are **trusted in-process extensions**, not a security boundary.

| Control            | v1 behaviour                                                                                        |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| Code execution     | Full Python process privileges                                                                      |
| Filesystem access  | Unrestricted within OS user                                                                         |
| Network access     | Unrestricted                                                                                        |
| Secret handling    | Plugins read env vars; no secret vault (Epic 11)                                                    |
| Tool authorization | Inherited `ToolAuthorizer` — guests denied; authenticated users subject to existing tool policy     |
| MCP permissions    | Inherited `McpPermissionPolicy` for plugin-declared servers                                         |
| Config injection   | Workflow node `config` is JSON data validated against optional `config_schema`; never `eval`/`exec` |
| Allowlisting       | `plugin_allowlist` empty → all discovered plugins; non-empty → only listed `plugin_id`s load        |

Operators must treat plugin directories like application source code — only deploy plugins from trusted origins.

---

## High-Level Flow

**Plugin load (startup)**

`lifespan` → `PluginLoader.load_all(settings)` → for each manifest: validate → import entrypoint → `register(registrar)` → contributions wired into registries → `PluginRegistry` records loaded/failed state

**Tool invocation (runtime)**

Agent/chat → `ToolPlanner` → `ToolExecutor.execute()` → `ToolRegistry.get_handler("com.example.echo.ping")` → plugin handler → `ToolResult` (unchanged envelope)

**Prompt render (runtime)**

Service → `PromptManager.render("plugin/com.example.echo", "greeting", "1", vars)` → `PromptRepository` (plugin template) → Jinja2 render inside `prompt_span`

**Workflow plugin node (runtime)**

`WorkflowExecutor` → `PluginNodeExecutor` → plugin-specific `NodeExecutor` → node output dict → checkpoint (unchanged Epic 06 persistence)

**MCP plugin server (startup)**

Plugin manifest `mcp_servers` → merged list → `register_mcp_tools()` → existing Epic 03 discovery path

---

## End-to-End Sequence

```text
Operator adds plugin dir to PLUGIN_DIRECTORIES
  │
  ▼
Process start → PLUGINS_ENABLED=true
  │
  ▼
PluginLoader discovers plugin.yaml files
  │
  ▼
entrypoint register(registrar)
  ├─ register_tool → ToolRegistry
  ├─ register_prompt_template → PromptRepository
  ├─ register_workflow_node_type → PluginNodeExecutor registry
  └─ register_mcp_server → pending MCP list
  │
  ▼
register_production_tools() + register_mcp_tools(extra_servers=…)
  │
  ▼
Runtime request (chat / workflow / agent)
  │
  ├─ tool call → ToolExecutor (plugin tool handler)
  ├─ prompt render → PromptManager (plugin template)
  └─ workflow step → WorkflowExecutor → PluginNodeExecutor

Observability (optional):
  plugin_span around entrypoint registration (load)
  tool_span / workflow_span on execution (existing helpers; plugin_id as span attribute)
```

---

## Storage Architecture

No new database tables in v1.

```text
Filesystem plugin directories
        │
PluginRegistry (in-memory, startup snapshot)
        │
GET /api/plugins → PluginsStore → PluginInventory DTO
```

Workflow definitions that reference plugin nodes persist `plugin_id` / `plugin_node_type` in existing `workflow_definitions.nodes` JSON — no schema migration required beyond `NodeType.PLUGIN` enum support in application code and validation.

---

## Package Structure

```text
app/
└── ai/
    └── plugins/
        ├── __init__.py
        ├── manifest.py          # PluginManifest model + YAML loader
        ├── loader.py            # PluginLoader.discover/load_all
        ├── registry.py          # PluginRegistry (loaded/failed state, MCP server list)
        ├── registrar.py         # PluginRegistrar (register_tool/prompt/workflow/mcp)
        ├── models.py            # PluginLoadReport, PluginRecord, PluginStatus,
        │                        #   PluginLoadFailureReason, PluginDependency, PluginContributionKind
        ├── exceptions.py        # PluginLoadError, PluginManifestError, …
        └── workflow/
            ├── registry.py        # WorkflowPluginRegistry — (plugin_id, node_type) → executor factory
            └── plugin_node.py     # PluginNodeExecutor dispatcher

app/routers/plugins.py           # NEW — authenticated inventory API
app/schemas/plugins.py           # NEW — response schemas
app/core/config.py               # extend — PLUGINS_ENABLED, plugin_directories, plugin_allowlist
app/main.py                      # modify — load_plugins in lifespan before tool/MCP registration
app/ai/deps.py                   # extend — get_plugin_registry, get_workflow_plugin_registry, wire PluginNodeExecutor
app/ai/tools/registration.py     # modify — accept optional plugin MCP server list (no behaviour change when flag off)
app/ai/prompts/repository.py     # extend — register_plugin_template(), list_plugin_templates()
app/ai/workflow/models/definition.py  # modify — NodeType.PLUGIN
app/ai/workflow/graph/validator.py           # extend — validate plugin node references
app/ai/workflow/engine/executor.py # unchanged dispatch via node_executors map
app/ai/observability/tracing/spans.py  # plugin_span (Phase 7 — complete)

backend-python/plugins/          # reference plugins (git-tracked)
├── echo-tool/
│   ├── plugin.yaml
│   └── echo_plugin/
│       ├── __init__.py
│       └── register.py
└── echo-workflow-node/
    ├── plugin.yaml
    └── echo_workflow_plugin/
        ├── __init__.py
        └── register.py

tests/plugins/fixtures/          # minimal plugins for unit tests (not loaded in production)
```

---

## Core Components

- `PluginManifest`
- `PluginLoader`
- `PluginRegistry`
- `WorkflowPluginRegistry`
- `PluginRegistrar`
- `PluginNodeExecutor`
- `PluginsStore` (thin read façade for router)
- `PLUGIN_API_VERSION` / `SUPPORTED_PLUGIN_API_VERSIONS`

---

## Component Responsibilities

| Component                   | Responsibility                                                             | Inputs                          | Outputs                    | Dependencies                                                    |
| --------------------------- | -------------------------------------------------------------------------- | ------------------------------- | -------------------------- | --------------------------------------------------------------- |
| `PluginLoader`              | Discover manifests, validate, import entrypoints, invoke registration      | Settings paths, allowlist       | `PluginLoadReport`         | `importlib`, YAML                                               |
| `PluginRegistrar`           | Typed facade writing into platform registries; **sole writer** to `WorkflowPluginRegistry` via staged `register_workflow_node_type()` → `commit()` | Registration calls from plugins | Side effects on registries | `ToolRegistry`, `PromptRepository`, `WorkflowPluginRegistry`, MCP pending list |
| `PluginRegistry`            | Process-wide inventory of loaded/failed plugins, load timing, failure reasons, aggregated MCP configs | Loader results                  | Query API for store/router | —                                                               |
| `WorkflowPluginRegistry`    | Process-wide map `(plugin_id, plugin_node_type)` → executor factory; populated at plugin load, immutable after startup | `PluginRegistrar.commit()`      | Lookup for dispatch/validation | — (singleton via `app/ai/deps.py`)                              |
| `PluginNodeExecutor`        | Dispatch `NodeType.PLUGIN` executions to plugin-provided executors         | `WorkflowNode`, context         | Node output dict           | `WorkflowPluginRegistry`                                        |
| `PluginsStore`              | Router-facing read model                                                   | `PluginRegistry`                | Inventory DTOs             | `PluginRegistry`                                                |
| `GraphValidator` (extended) | Reject definitions referencing unknown plugin/node types                   | `WorkflowDefinition`            | Validation errors          | `PluginRegistry` (load status), `WorkflowPluginRegistry` (node types) |

---

## Existing V1/V2 Assets (reuse, do not duplicate)

| Asset                                                | Location                                                       | Epic 08 role                                               |
| ---------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------- |
| `ToolRegistry`, `ToolExecutor`, `ToolAuthorizer`     | `app/ai/tools/`                                                | Tool plugin execution path                                 |
| `ToolDefinition`, `ToolHandler`                      | `app/ai/tools/schemas.py`, `app/ai/interfaces/tool_handler.py` | Tool plugin types                                          |
| `register_production_tools`, `register_mcp_tools`    | `app/ai/tools/registration.py`                                 | Startup ordering; MCP merge                                |
| `PromptManager`, `PromptRepository`                  | `app/ai/prompts/`                                              | Prompt plugin rendering                                    |
| `NodeExecutor`, `WorkflowExecutor`, `GraphValidator` | `app/ai/workflow/`                                             | Workflow plugin nodes                                      |
| `McpServerRegistry`, `McpConnectionConfig`           | `app/ai/mcp/`                                                  | MCP server plugins                                         |
| `McpPermissionPolicy`                                | `app/ai/mcp/permissions.py`                                    | Authorization for plugin MCP servers                       |
| `plugin_span` hook point                             | `app/ai/observability/tracing/spans.py`                        | Load + optional execution attribution                      |
| `normalize_metric_label`                             | `app/ai/observability/metrics/labels.py`                       | Plugin tool/node metrics → `other` until registry extended |
| Feature flag infrastructure                          | `app/core/config.py`                                           | `PLUGINS_ENABLED`                                          |
| DI factories                                         | `app/ai/deps.py`                                               | Wire registries after load                                 |
| `get_current_caller`                                 | `app/core/caller.py`                                           | Authenticated Plugin REST API                              |

When `PLUGINS_ENABLED=false`, none of the above behaviours change.

---

## Platform Integration Strategy

Unlike Workflows (new orchestration surface) or Observability (cross-cutting spans), Plugins **extend registration boundaries** that already exist:

- **Tool plugins** — populate `ToolRegistry` before `register_production_tools`; production tools still register after plugins so core names cannot be shadowed accidentally (production registration would fail on collision anyway).
- **Prompt plugins** — extend `PromptRepository` with an in-memory overlay map; `PromptManager.render()` unchanged for callers.
- **Workflow plugins** — single shared `WorkflowPluginRegistry` singleton (via `get_workflow_plugin_registry()` in `app/ai/deps.py`); `PluginRegistrar.commit()` is the sole writer; `PluginNodeExecutor` and `GraphValidator` read the same instance; inject `PluginNodeExecutor` into `node_executors[NodeType.PLUGIN]` in `_create_workflow_manager()`; `PluginRegistry` tracks load status only (not executor map ownership).
- **MCP plugins** — pass aggregated server list into existing `register_mcp_tools()`; no fork of MCP client code.

**Flag off:** No plugin scan, no `sys.path` mutation, `PluginRegistry` reports zero plugins, Plugin REST routes return `503 feature_disabled`, reference plugin directories ignored, workflow graphs cannot use `NodeType.PLUGIN` (validation error if flag off and type present — defensive).

**Flag on:** Configured plugins load; inventory API reflects startup snapshot; reference plugins demonstrate each contribution kind.

---

## Plugin REST API

Authenticated-only (`Depends(get_current_caller)`). Router: `app/routers/plugins.py`. Mounted in `app/main.py`; returns `503 feature_disabled` when `PLUGINS_ENABLED=false`.

| Method | Path                       | Purpose                                                                                                                                                                                                 |
| ------ | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/plugins`             | List all plugin records (`plugin_id` nullable) with `name`, `version`, `api_version`, `contributions`, `status` (`loaded` \| `failed`), `load_duration_ms`, optional author/discovery fields (`author`, `homepage`, `repository`, `documentation`, `license`), and safe `failure` object when failed — **excludes** manifest `metadata` bag |
| `GET`  | `/api/plugins/{plugin_id}` | Detail for one plugin when `plugin_id` is non-null and known; **404** otherwise. Same bounded fields as list plus informational `dependencies` (`plugin_id`, optional `version` constraint per entry). **Omits** manifest `metadata` bag — opaque keys are stored on `PluginRecord` only, never returned verbatim |

**Health:** extend `GET /api/health` with `plugins_enabled: bool`, `plugins_loaded_count: int`, and `plugins_failed_count: int` (0 when flag off). Failed count includes `manifest_not_found` and `invalid_manifest` records.

**Response rules:** Responses expose inventory fields only — never filesystem paths, env var values, MCP credentials, handler source code, stack traces, entrypoint module paths, prompt template bodies, or the manifest **`metadata` bag** (may contain secrets or personal data). `failure` objects follow § PluginRecord & Load Diagnostics safe subset. `PluginsStore` maps `PluginRecord` → bounded list/detail DTOs with an explicit field allowlist; do not serialize `PluginRecord.metadata` to JSON.

---

## Public APIs (stable after Phase 1)

| API                                                                                                                            | Kind           |
| ------------------------------------------------------------------------------------------------------------------------------ | -------------- |
| `PLUGIN_API_VERSION`, `SUPPORTED_PLUGIN_API_VERSIONS`                                                                          | Constants      |
| `PluginManifest`                                                                                                               | Model          |
| `PluginContributionKind`                                                                                                       | Enum           |
| `PluginRegistrar`                                                                                                              | Class          |
| `PluginRegistry`                                                                                                               | Class          |
| `PluginLoader`                                                                                                                 | Class          |
| `PluginLoadReport`, `PluginRecord`, `PluginStatus`, `PluginLoadFailureReason`, `PluginDependency`                              | Models         |
| `PluginLoadError`, `PluginManifestError`, `PluginRegistrationError`                                                            | Exception      |
| `WorkflowPluginRegistry`                                                                                                       | Class          |
| `load_plugins(settings, *, tool_registry, prompt_repository, workflow_plugin_registry, plugin_registry, …) -> PluginLoadReport` | Function       |
| `PluginNodeExecutor`                                                                                                           | Class          |
| Plugins REST router export                                                                                                     | FastAPI router |

Internal (may evolve): YAML parser details, `sys.path` management, `PluginsStore` mapping, test fixture helpers.

---

## Configuration defaults

| Setting                       | Default                                              |
| ----------------------------- | ---------------------------------------------------- |
| `PLUGINS_ENABLED`             | **`false`**                                          |
| `plugin_directories`          | `["plugins"]` (relative to `backend-python/`)        |
| `plugin_allowlist`            | `[]` (empty → all discovered plugins allowed)        |
| `plugin_load_timeout_seconds` | `30` (wall-clock limit for `register(registrar)` in a worker thread; see § Registration timeout) |

Existing flags (`MCP_ENABLED`, `WORKFLOW_ENGINE_ENABLED`, `TOOLS_ENABLED`, `OBSERVABILITY_ENABLED`, …) unchanged.

---

## Dependencies

| Requires                                                                      | Provides to downstream                                                 |
| ----------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Epic 07 Observability (stable platform + span helpers)                        | `PluginRegistry`, `PluginRegistrar`, `PluginLoader`, `PLUGINS_ENABLED` |
| Epic 06 Workflow Engine (`NodeExecutor`, `GraphValidator`)                    | `NodeType.PLUGIN`, workflow node plugin dispatch                       |
| Epic 03 MCP Integration (optional; MCP plugins meaningful when `MCP_ENABLED`) | Plugin-declared MCP servers                                            |
| Epic 01 Tool platform                                                         | Tool plugin execution path                                             |
| Built-in prompt system                                                        | Prompt plugin rendering path                                           |

**Future consumers:** Epic 09 Human-in-the-Loop (approval before plugin tool execution); Epic 10 Background Jobs (async plugin package install — if ever added); Epic 11 Security & Governance (plugin RBAC, signed plugins, audit logs).

---

## Future Enhancements (Out of V2 Scope)

Documented extension points reserved by this epic's manifest and registry design — **not implemented in V2**:

| Enhancement | Motivation | V2 foundation |
| ----------- | ---------- | ------------- |
| **Plugin dependency resolution** | Load order / semver constraints via manifest `dependencies` | Field parsed and stored; ignored at load time |
| **Isolated module loading** | Reduce `sys.path` pollution and cross-plugin import collisions when the ecosystem grows | Startup-only `sys.path` append documented as interim approach |
| **Richer lifecycle states** | Operational visibility beyond binary success/failure (`disabled`, `degraded`, …) | `PluginStatus` enum documents V2 as `loaded` \| `failed` only |
| **Marketplace / remote install** | Discover and install plugins outside git-tracked directories | Author metadata + `metadata` bag reserved |

These items require explicit Part I updates and should remain `TODO(future):` during V2 implementation.

---

## Design acceptance

- Flag off: zero plugins loaded; Plugin REST returns `503`; workflow definitions with `type: plugin` rejected at validation when submitted; all other platform paths byte-for-byte unchanged
- Flag on: reference tool plugin callable via agent/chat tool path; reference prompt renderable via `PromptManager`; reference workflow node executable in a workflow run; plugin inventory API returns accurate metadata
- One failing plugin does not prevent process startup or other plugins from loading
- Tool plugins cannot register without `{plugin_id}.` name prefix
- Plugin MCP servers merge with env config per locked decision (env wins on name conflict)
- No prompt/tool/workflow template bodies or secrets in REST responses or span attributes
- Every `PluginRecord` includes `load_duration_ms`; API version failures include structured `PluginLoadFailureReason` with expected vs manifest api_version
- Manifest `dependencies` and `metadata` parse successfully and are stored on `PluginRecord`; `dependencies` appear in detail REST only; `metadata` is **not** exposed via REST
- Coverage ≥80% on `app/` and `app/ai/plugins/`

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Registry boundary** — Plugins write only through `PluginRegistrar`; direct mutation of `ToolRegistry`, `PromptRepository`, or MCP internals from plugin code outside registrar is unsupported and may break in future phases.
- **Execution path reuse** — Tool plugins always execute via `ToolExecutor`; workflow plugin nodes via `WorkflowExecutor`; prompts via `PromptManager.render()`.
- **No hot-reload** — Plugin state is fixed after startup; documentation and API responses state that restart is required after plugin changes.
- **Trusted code** — Plugins are not sandboxed; operators must control plugin directories.
- **Fail-open per plugin** — Load/registration exceptions fail the plugin, not the process (unless `PLUGINS_ENABLED` misconfiguration prevents reading directories entirely — that logs error and continues with zero plugins).
- **Flag-off parity** — `PLUGINS_ENABLED=false` preserves Epic 07 behaviour on every hot path.
- **No content in telemetry** — span attributes for plugins carry `plugin_id`, contribution kind, counts, status — never template bodies or tool arguments/results.
- **Public APIs stable after Phase 1** — `PluginRegistrar` method signatures and manifest schema require user approval to change.
- **No Epic 09+ behaviour early** — HITL approval for plugin tools, plugin RBAC, signed marketplace, hot-reload — `TODO(epic-N):` only.

---

## Acceptance Criteria

- Platform operators can add capabilities by dropping a plugin directory and enabling `PLUGINS_ENABLED`, without editing core packages.
- Tool, prompt, workflow node, and MCP server extensions integrate through existing registries and authorization paths.
- Plugin manifests are version-checked against `PLUGIN_API_VERSION`.
- Authenticated users can inspect loaded plugin inventory via REST.
- Reference plugins and eval cases demonstrate end-to-end behaviour.
- When plugins are disabled, the platform behaves identically to Epic 07.

---

# Part II — Execution

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement Part II phase-by-phase. Part I is frozen and is the architectural source of truth. Do not redesign architecture during implementation.

## Phase integration rules

Early phases build **plugin infrastructure in isolation** (unit tests with fixture plugins under `tests/plugins/fixtures/`). Each contribution kind integrates with an existing registry in its own phase. REST API, observability, eval, and frontend follow once all contribution kinds work.

| Phase | Builds                                                              | Wiring                               |
| ----- | ------------------------------------------------------------------- | ------------------------------------ |
| 1     | Plugin SDK foundation (manifest, loader, registry, registrar, flag) | None                                 |
| 2     | Tool plugins                                                        | `ToolRegistry`                       |
| 3     | Prompt plugins                                                      | `PromptRepository` / `PromptManager` |
| 4     | Workflow node plugins                                               | `WorkflowManager` / `GraphValidator` |
| 5     | MCP server plugins + versioning hardening                           | `register_mcp_tools`                 |
| 6     | Plugin REST API + health                                            | REST only                            |
| 7     | Observability (`plugin_span`, load metrics)                         | Internal                             |
| 8     | Reference plugins + eval cases                                      | CLI + git-tracked plugins            |
| 9     | Frontend plugin inventory page                                      | Frontend                             |
| 10    | Validation & release                                                | —                                    |

## Reuse Existing Components

**DO NOT REIMPLEMENT**

| Component                                                               | Location                                |
| ----------------------------------------------------------------------- | --------------------------------------- |
| `ToolRegistry`, `ToolExecutor`, `ToolAuthorizer`, `ToolDefinition`      | `app/ai/tools/`                         |
| `register_production_tools`, `register_mcp_tools`                       | `app/ai/tools/registration.py`          |
| `PromptManager`, `PromptRepository`, `PromptRenderer`                   | `app/ai/prompts/`                       |
| `WorkflowManager`, `WorkflowExecutor`, `GraphValidator`, `NodeExecutor` | `app/ai/workflow/`                      |
| `McpServerRegistry`, `McpConnectionConfig`, `McpPermissionPolicy`       | `app/ai/mcp/`                           |
| `plugin_span`, load metrics (`plugins_loaded_total`, `plugin_load_failures_total`) | `app/ai/observability/tracing/spans.py`, `app/ai/observability/metrics/` |
| `get_current_caller`, `CallerContext`                                   | `app/core/caller.py`                    |
| Feature flag infrastructure                                             | `app/core/config.py`                    |
| DI factories                                                            | `app/ai/deps.py`                        |
| `app/ai/evaluation/` harness                                            | `app/ai/evaluation/`                    |

When `PLUGINS_ENABLED=false`, existing platform behaviour must remain unchanged.

---

## Not Allowed

- Bypass `ToolExecutor`, `PromptManager`, or `WorkflowExecutor` for plugin contributions
- Allow unprefixed tool names from plugins
- Implement runtime hot-reload or filesystem watchers
- Add DB persistence for plugin inventory in v1
- Execute arbitrary code from workflow JSON configs
- Shadow env-configured MCP servers without warning
- Attach prompt bodies, tool arguments, or secrets to spans/logs/API responses
- Implement Epic 09+ HITL/RBAC/marketplace behaviour
- Break feature-flag parity

---

## Baseline

_Copy from Epic 07 Phase 10 completion record._

| Area                     | State                                                                   |
| ------------------------ | ----------------------------------------------------------------------- |
| Backend tests / coverage | 1691 passed, 89.21% `app/`                                              |
| Frontend tests           | 281 passed (46 files); lint + build pass                                |
| Integration tests        | Workflow **241** (207 package + 23 router + 11 workflow tool); observability router 15; streaming 26 |
| Eval CLI                 | 15/15 `--level all`; regression check clean                             |
| Observability            | Completed (Epic 07); `OBSERVABILITY_ENABLED` behind flag                |
| Plugin Architecture      | Phases 0–7 complete — SDK, tool, prompt, workflow node, MCP server plugins, REST inventory API, and plugin load observability implemented |

---

## Phase Status

| Phase | Name                            | Effort | Status      |
| ----- | ------------------------------- | ------ | ----------- |
| 0     | Baseline Audit                  | XS     | Completed   |
| 1     | Plugin SDK Foundation           | M      | Completed   |
| 2     | Tool Plugins                    | M      | Completed   |
| 3     | Prompt Plugins                  | M      | Completed   |
| 4     | Workflow Node Plugins           | L      | Completed   |
| 5     | MCP Server Plugins & Versioning | M      | Completed   |
| 6     | Plugin REST API & Health        | S      | Completed   |
| 7     | Plugin Observability            | S      | Completed   |
| 8     | Reference Plugins & Eval Cases  | M      | Not Started |
| 9     | Frontend Plugin Inventory       | S      | Not Started |
| 10    | Validation & Release            | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS
**Status:** Completed (2026-08-10 — see [post-mvp-v2-epic8-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic8-phase-0-baseline-audit.md))

**Objective**

Establish a verified implementation baseline before introducing the Plugin Architecture. Confirm Epic 07 is complete, inventory all registration/extension points plugins will use, and verify no plugin implementation exists yet.

**Deliverables**

- `docs/audits/post-mvp-v2-epic8-phase-0-baseline-audit.md`
- Architecture inventory
- Extension point verification (`ToolRegistry`, `PromptRepository`, workflow executors, MCP registration)
- Feature flag verification
- Baseline quality metrics

**Steps**

## Platform Verification

- [x] Confirm Epic 07 Phase 10 complete / authorized for Epic 08.
- [x] Inventory `app/ai/tools/registration.py` startup order in `app/main.py`.
- [x] Inventory `ToolRegistry`, `PromptRepository`, `_create_workflow_manager()` node executor map.
- [x] Inventory `register_mcp_tools()` and env-based `mcp_servers`.
- [x] Verify chat, RAG, MCP, memory, voice, agent, tool, workflow, and observability pipelines operational.

## Architecture Review

- [x] Review frozen Part I architecture.
- [x] Confirm `NodeType` enum current values and extension approach for `PLUGIN`.
- [x] Identify collision rules for tool names and prompt identities.
- [x] Confirm no `app/ai/plugins/` package exists.

## Dependency Verification

- [x] Verify PyYAML (or existing YAML loader) availability for manifest parsing.
- [x] Verify DI and feature flag patterns in `app/ai/deps.py` / `app/core/config.py`.

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
- No plugin implementation present.
- Baseline metrics recorded.

**Exit criteria**

- [x] Baseline audit published.
- [x] User confirmation to proceed to Phase 1.

**Rollback**

- [x] No rollback required (no code changes).

**Completion Record**

| Metric              | Result                                      |
| ------------------- | ------------------------------------------- |
| Lint                | ✅ PASS                                     |
| Format check        | ✅ PASS                                     |
| Typecheck           | ✅ PASS                                     |
| Backend tests       | ✅ 1691 passed, 89.17% `app/` coverage      |
| Eval CLI            | ✅ 15/15 (`--level all`)                    |
| Frontend tests      | ✅ 281 passed (46 files); build pass          |
| Baseline audit      | ✅ [Published](../audits/post-mvp-v2-epic8-phase-0-baseline-audit.md) |
| Phase 0 status      | ✅ Completed                                |
| Phase 1 authorized  | ✅ Authorized                               |

---

# Phase 1 — Plugin SDK Foundation

**Effort:** M
**Status:** Completed (2026-08-11)

**Objective**

Introduce the core plugin package: manifest model, loader skeleton, registry, registrar stub, feature flag, and unit tests — without wiring into production registries yet.

**Deliverables**

- `app/ai/plugins/` package scaffold
- `PluginManifest`, `PluginLoadReport`, `PluginRecord`, `PluginStatus`, `PluginContributionKind`
- `PluginLoadFailureReason`, `PluginDependency` (structured failure + reserved deps)
- `PluginLoader.discover()` / `load_all()` with manifest validation and entrypoint import
- `PluginRegistry` (in-memory loaded/failed tracking)
- `PluginRegistrar` with registration methods that accept contributions into internal staging dicts (wired in later phases)
- `PLUGINS_ENABLED`, `plugin_directories`, `plugin_allowlist`, `plugin_load_timeout_seconds`
- `PLUGIN_API_VERSION = "1"`
- Unit tests with `tests/plugins/fixtures/minimal-plugin/`

**Steps**

## Package Structure

- [x] Create `app/ai/plugins/` per Part I package layout.
- [x] Export public API from `__init__.py`.
- [x] Verify import cycle freedom.

## Manifest & Validation

- [x] Implement `PluginManifest` Pydantic model matching Part I schema (including optional `author`/`homepage`/`repository`/`documentation`/`license`, reserved `dependencies`, reserved `metadata`).
- [x] Implement YAML loader with field-level error messages (no secret leakage in errors).
- [x] Validate `api_version` against `SUPPORTED_PLUGIN_API_VERSIONS`; on mismatch build `PluginLoadFailureReason(code=unsupported_api_version, expected_api_versions=…, manifest_api_version=…)`.
- [x] Validate semver for `version`.
- [x] Parse `dependencies` into `PluginDependency` list; do not resolve or reorder loads in V2.

## Loader & Registry

- [x] Implement directory scan (immediate children containing `plugin.yaml`).
- [x] Apply `plugin_allowlist` when non-empty.
- [x] Sort manifests by `plugin_id` for deterministic load order.
- [x] Import entrypoint via `importlib`; append plugin directory to `sys.path` once.
- [x] Measure `load_duration_ms` (monotonic clock) around validate + import + `register()` (includes time spent waiting on timeout).
- [x] Run `register(registrar)` in a worker thread with `plugin_load_timeout_seconds` wall-clock limit (`concurrent.futures` or equivalent); on normal return call `registrar.commit()` then `mark_loaded()`; on exception call `registrar.rollback()` then `mark_failed()` with `registration_error` (or other code); on timeout call `registrar.rollback()`, close the registrar to reject late staging, then `mark_failed()` with `PluginLoadFailureReason(code="timeout", …)` — never call `commit()`.
- [x] Record success/failure in `PluginRegistry` with `load_duration_ms` and structured `PluginLoadFailureReason`; wrap each plugin load in try/except — `load_all()` never raises uncaught exceptions.

## Registrar Stub

- [x] Implement `PluginRegistrar` with methods: `register_tool`, `register_prompt_template`, `register_workflow_node_type`, `register_mcp_server`, plus `commit()` / `rollback()`.
- [x] Phase 1: `register_*()` append to internal staging lists; `commit()` promotes staged contributions (assertions in tests); `rollback()` clears staging. Platform registry wiring deferred to Phases 2–5.

## Configuration

- [x] Add `PLUGINS_ENABLED` (default `false`) and plugin path settings to `app/core/config.py`.
- [x] Document settings in `backend-python/.env.example`.

## Testing

- [x] Fixture plugin with no-op `register()`.
- [x] Tests: valid manifest, invalid api_version (assert structured failure reason fields), duplicate plugin_id, entrypoint ImportError, registrar timeout, allowlist filtering, optional author metadata round-trip, `dependencies`/`metadata` parsed but ignored for load order.

**Verify**

- `make lint`
- `make typecheck`
- `pytest tests/ai/plugins/`

**Acceptance**

- Public APIs match Part I § Public APIs (foundation subset).
- `load_all()` with flag off is a no-op returning empty report.
- No changes to runtime tool/prompt/workflow behaviour yet.

**Exit criteria**

- [x] Foundation tests pass.
- [x] Public manifest/registrar/loader APIs frozen.
- [x] User confirmation to proceed to Phase 2.

**Rollback**

- Remove `app/ai/plugins/` package and config flags.
- Verify application builds without plugin modules.

**Completion Record**

| Metric             | Result                                |
| ------------------ | ------------------------------------- |
| Lint               | ✅ PASS                               |
| Typecheck          | ✅ PASS                               |
| Plugin SDK tests   | ✅ PASS (`tests/ai/plugins/test_plugin_sdk.py`) |
| Phase 1 status     | ✅ Completed                          |
| Phase 2 authorized | ✅ Authorized                         |

---

# Phase 2 — Tool Plugins

**Effort:** M
**Status:** Completed (2026-08-11)

**Objective**

Wire `PluginRegistrar.register_tool()` into the process-wide `ToolRegistry` with enforced name prefixing; load tool plugins at startup before `register_production_tools()`.

**Deliverables**

- Tool registration wiring in `PluginRegistrar`
- `load_plugins()` orchestration function
- `app/main.py` lifespan integration (before production tools)
- Reference fixture tool plugin in tests
- Integration tests via `ToolExecutor`

**Steps**

## Registration Wiring

- [x] Enforce tool name prefix `{plugin_id}.` — raise `PluginRegistrationError` if violated.
- [x] Stage tool definitions/handlers during `register_tool()`; `commit()` writes all staged tools to `ToolRegistry`; `rollback()` clears staging without touching live registries (no platform writes occur until `commit()`).
- [x] Track tool contributions on `PluginRecord` for inventory API.

## Startup Integration

- [x] Implement `load_plugins(settings, tool_registry=..., prompt_repository=..., workflow_plugin_registry=..., plugin_registry=...)` — single shared `WorkflowPluginRegistry` instance created/obtained from DI before the load loop and injected into each `PluginRegistrar`.
- [x] Call from `lifespan` when `PLUGINS_ENABLED=true` before `register_production_tools()`.
- [x] Ensure flag off skips `load_plugins()` entirely.

## Testing

- [x] Fixture plugin registering `tests.plugins.fixtures.tool-plugin` echo tool.
- [x] Test: tool callable through `ToolExecutor` with fake authorizer context.
- [x] Test: unprefixed name rejected.
- [x] Test: duplicate tool name fails plugin, subsequent plugins still load.
- [x] Test: flag off — tool not registered.

**Verify**

- `pytest tests/ai/plugins/test_tool_plugins.py tests/test_tool_platform.py`

**Acceptance**

- Tool plugins execute through existing `ToolExecutor` path.
- Production tools still register after plugins.
- Flag-off parity preserved.

**Exit criteria**

- [x] Tool plugin integration tests pass.
- [x] User confirmation to proceed to Phase 3.

**Rollback**

- Remove lifespan `load_plugins()` call.
- Disable `PLUGINS_ENABLED`.
- Re-run tool platform tests.

**Completion Record**

| Metric              | Result                                          |
| ------------------- | ----------------------------------------------- |
| Lint                | ✅ PASS                                         |
| Typecheck           | ✅ PASS                                         |
| Tool plugin tests   | ✅ PASS (`tests/ai/plugins/test_tool_plugins.py`) |
| Phase 2 status      | ✅ Completed                                    |
| Phase 3 authorized  | ✅ Authorized                                   |

---

# Phase 3 — Prompt Plugins

**Effort:** M
**Status:** Completed (2026-08-11)

**Objective**

Allow plugins to register Jinja2 prompt templates under `plugin/{plugin_id}` namespace via extended `PromptRepository`.

**Deliverables**

- `PromptRepository.register_plugin_template()` (or equivalent overlay API)
- `PluginRegistrar.register_prompt_template()` wired to repository
- Inline and file-based template sources
- Tests via `PromptManager.render()`

**Steps**

## PromptRepository Extension

- [x] Add in-memory overlay keyed by `(category, name, version)` where category is `plugin/{plugin_id}`.
- [x] Extend `get_template()` to consult overlay after filesystem lookup (or before — document order; plugin must not override built-in categories outside `plugin/`).
- [x] Reuse existing Jinja2 `Environment` / `StrictUndefined` rules.

## Registrar Wiring

- [x] Resolve `path` relative to plugin directory; reject path traversal (`..`).
- [x] Record prompt contributions on `PluginRecord`.

## Testing

- [x] Fixture prompt plugin registers greeting template.
- [x] Test render via `PromptManager.render("plugin/…", ...)`.
- [x] Test collision with existing built-in prompt fails plugin load.
- [x] Test `prompt_span` still wraps render (Observability on/off).

**Verify**

- `pytest tests/ai/plugins/test_prompt_plugins.py`

**Acceptance**

- Prompt plugins render identically to built-in templates from caller perspective.
- No filesystem prompts removed or altered.

**Exit criteria**

- [x] Prompt plugin tests pass.
- [x] User confirmation to proceed to Phase 4.

**Rollback**

- Revert `PromptRepository` overlay; disable flag.

**Completion Record**

| Metric               | Result                                              |
| -------------------- | --------------------------------------------------- |
| Lint                 | ✅ PASS                                             |
| Typecheck            | ✅ PASS                                             |
| Prompt plugin tests  | ✅ PASS (`tests/ai/plugins/test_prompt_plugins.py`) |
| Phase 3 status       | ✅ Completed                                        |
| Phase 4 authorized   | ✅ Authorized                                       |

---

# Phase 4 — Workflow Node Plugins

**Effort:** L
**Status:** Completed (2026-08-11)

**Objective**

Add `NodeType.PLUGIN`, implement `PluginNodeExecutor`, extend `GraphValidator`, and wire plugin executors into `WorkflowManager`.

**Deliverables**

- `NodeType.PLUGIN` in `definition.py`
- `app/ai/plugins/workflow/registry.py` — `WorkflowPluginRegistry` (mapping `(plugin_id, node_type)` → executor factory)
- `app/ai/plugins/workflow/plugin_node.py` — `PluginNodeExecutor` dispatcher
- Extended `GraphValidator` for plugin node references
- `get_workflow_plugin_registry()` in `app/ai/deps.py`; `_create_workflow_manager()` wires `NodeType.PLUGIN: PluginNodeExecutor(...)` with the shared singleton
- Workflow integration tests

**Steps**

## Models & Registry

- [x] Add `NodeType.PLUGIN = "plugin"`.
- [x] Document required `config` keys: `plugin_id`, `plugin_node_type`.
- [x] Implement `WorkflowPluginRegistry` with register/get methods; process-wide singleton via `get_workflow_plugin_registry()` (empty stub when flag off).

## PluginNodeExecutor

- [x] Validate config keys present; map missing `WorkflowPluginRegistry` entry → `WorkflowNodeExecutionError`.
- [x] Delegate to plugin executor implementing `NodeExecutor` Protocol.
- [x] Propagate `NodeExecutionRequest.execution_receipt_id` to tool calls inside plugin nodes when applicable.

## Graph Validation

- [x] When `PLUGINS_ENABLED=false`, reject workflow definitions containing `type: plugin` at create/update with clear validation error.
- [x] When flag on, verify `plugin_id` is **loaded** in `PluginRegistry` and `(plugin_id, plugin_node_type)` exists in the shared `WorkflowPluginRegistry`.

## DI Wiring

- [x] `load_plugins()` receives the same `WorkflowPluginRegistry` instance passed into each `PluginRegistrar`; `commit()` writes executor factories there.
- [x] Pass that same singleton into `_create_workflow_manager()` / `GraphValidator` (not via `PluginRegistry`).
- [x] Ensure eval/workflow tests can inject fixture `WorkflowPluginRegistry`.

## Testing

- [x] Fixture workflow plugin registering `echo` node type copying a context key.
- [x] End-to-end workflow run with single PLUGIN node → terminal success.
- [x] Test unknown plugin_id / node_type fails validation.
- [x] Test flag off rejects plugin node definitions.

**Verify**

- `pytest tests/ai/plugins/test_workflow_plugins.py tests/ai/workflow/`

**Acceptance**

- Plugin workflow nodes checkpoint and recover using Epic 06 semantics.
- Built-in node types unaffected.

**Exit criteria**

- [x] Workflow plugin tests pass.
- [x] User confirmation to proceed to Phase 5.

**Rollback**

- Remove `NodeType.PLUGIN` wiring from manager; disable flag.
- Re-run workflow test suite.

**Completion Record**

| Metric                 | Result                                                                 |
| ---------------------- | ---------------------------------------------------------------------- |
| Lint                   | ✅ PASS                                                                |
| Typecheck              | ✅ PASS                                                                |
| Workflow plugin tests  | ✅ 8 passed (`tests/ai/plugins/test_workflow_plugins.py`)              |
| Workflow suite         | ✅ 215 passed (`tests/ai/workflow/`)                                   |
| Plugin test suite      | ✅ 46 passed (`tests/ai/plugins/`)                                     |
| Phase 4 status         | ✅ Completed                                                           |
| Phase 5 authorized     | ✅ Authorized                                                          |

---

# Phase 5 — MCP Server Plugins & Versioning

**Effort:** M
**Status:** Completed (2026-08-11)

**Objective**

Merge plugin-declared MCP servers into startup registration; finalize versioning rules and load reports.

**Deliverables**

- `PluginRegistrar.register_mcp_server()` and manifest `mcp_servers` parsing
- Extended `register_mcp_tools(..., extra_servers=...)` with env-wins merge policy
- `PluginLoadReport` summary logged at startup
- Versioning integration tests

**Steps**

## MCP Merge

- [x] Aggregate MCP configs from loaded plugins + manifest YAML.
- [x] Dedupe by server `name`; env `settings.mcp_servers` overrides plugin entry with warning log.
- [x] Skip all plugin MCP when `MCP_ENABLED=false` (debug log).

## Versioning Hardening

- [x] Reject manifest missing `api_version` or unsupported version with `PluginLoadFailureReason`.
- [x] Include `version`, `api_version`, `contributions`, `load_duration_ms`, author metadata, `dependencies`, `metadata` on `PluginRecord`.

## Startup Logging

- [x] Emit structured log: plugins_loaded, plugins_failed, tool/prompt/workflow/mcp contribution counts, per-plugin `load_duration_ms`, failure `code` when failed.

## Testing

- [x] Test env vs plugin name conflict (env wins).
- [x] Test MCP plugin skipped when MCP disabled.
- [x] Test invalid MCP config fails plugin only.
- [x] Test api_version mismatch produces `unsupported_api_version` reason with expected vs manifest values.

**Verify**

- `pytest tests/ai/plugins/test_mcp_plugins.py tests/ai/mcp/`

**Acceptance**

- MCP plugins use Epic 03 discovery path exclusively.
- Version incompatibilities fail single plugin, not startup.

**Exit criteria**

- [x] MCP plugin tests pass.
- [x] User confirmation to proceed to Phase 6.

**Rollback**

- Remove `extra_servers` parameter; disable flags.

**Completion Record**

| Metric              | Result                                                          |
| ------------------- | --------------------------------------------------------------- |
| Lint                | ✅ PASS                                                         |
| Typecheck           | ✅ PASS                                                         |
| MCP plugin tests    | ✅ 7 passed (`tests/ai/plugins/test_mcp_plugins.py`)            |
| MCP suite           | ✅ 197 passed (`tests/ai/plugins/test_mcp_plugins.py` + `tests/ai/mcp/`) |
| Plugin test suite   | ✅ 57 passed (`tests/ai/plugins/`)                              |
| Phase 5 status      | ✅ Completed                                                    |
| Phase 6 authorized  | ✅ Authorized                                                   |

---

# Phase 6 — Plugin REST API & Health

**Effort:** S
**Status:** Completed (2026-08-11)

**Objective**

Expose read-only plugin inventory via authenticated REST endpoints and health check fields.

**Deliverables**

- `app/schemas/plugins.py`
- `app/routers/plugins.py`
- `PluginsStore` read façade
- Router tests

**Steps**

## API Implementation

- [x] `GET /api/plugins` — list all records including `plugin_id=null` manifest failures (metadata + `load_duration_ms` + safe `failure` object).
- [x] `GET /api/plugins/{plugin_id}` — detail or `404` when `plugin_id` is null/unknown; include informational `dependencies`; **omit** manifest `metadata` bag (assert DTO allowlist excludes `PluginRecord.metadata`).
- [x] Return `503 feature_disabled` when `PLUGINS_ENABLED=false`.

## Health Extension

- [x] Add `plugins_enabled`, `plugins_loaded_count`, `plugins_failed_count` to health payload.

## Mount Router

- [x] Include router in `app/main.py`.

## Testing

- [x] Router tests with flag on/off.
- [x] Assert responses exclude paths, credentials, template bodies, stack traces, and manifest `metadata` keys/values; assert `manifest_not_found` / `invalid_manifest` records expose no directory or file paths.
- [x] Assert api_version failure exposes safe `failure.expected_api_versions` + `failure.manifest_api_version`.
- [x] Assert `load_duration_ms` present on loaded and failed records.

**Verify**

- `pytest tests/test_plugins_router.py`

**Acceptance**

- Authenticated callers can inspect inventory.
- No secret/template leakage.

**Exit criteria**

- [x] Router tests pass.
- [x] User confirmation to proceed to Phase 7.

**Rollback**

- Remove router mount; disable flag.

**Completion Record**

| Metric              | Result                                              |
| ------------------- | --------------------------------------------------- |
| Lint                | ✅ PASS                                             |
| Typecheck           | ✅ PASS                                             |
| Plugin router tests | ✅ 11 passed (`tests/test_plugins_router.py`)       |
| Health tests        | ✅ PASS (`tests/test_health.py` — plugin fields)    |
| Phase 6 status      | ✅ Completed                                        |
| Phase 7 authorized  | ✅ Completed                                        |

---

# Phase 7 — Plugin Observability

**Effort:** S
**Status:** Completed (2026-08-11)

**Objective**

Add plugin load spans/metrics and ensure execution continues to use existing domain spans.

**Deliverables**

- `plugin_span(plugin_id, kind)` in `app/ai/observability/tracing/spans.py`
- Load instrumentation in `PluginLoader`
- Counters: `plugins_loaded_total`, `plugin_load_failures_total` — shared bounded label contract (see § Metrics below; `failure_code` only)
- Tests

**Steps**

## Span Helper

- [x] Implement `plugin_span` with fixed name `plugin.load` (attributes: `plugin_id`, `contribution_kind`, `status`, `load_duration_ms`, `failure_code` when failed).
- [x] Wrap each plugin entrypoint registration when `OBSERVABILITY_ENABLED=true`.

## Metrics

**Load counter label contract (both counters):** one shared label dimension — `failure_code` (bounded registry; `plugin_id` is **span-only**, never a metric label).

| Label | Allowed values | Usage |
| ----- | -------------- | ----- |
| `failure_code` | `none`, `manifest_not_found`, `invalid_manifest`, `unsupported_api_version`, `entrypoint_import_error`, `registration_error`, `timeout`, `allowlist_excluded`, `other` | Shared by **both** counters; normalize via `normalize_metric_label("failure_code", …)` (extend `app/ai/observability/metrics/labels.py`) |

| Counter | Increment when | `failure_code` value |
| ------- | -------------- | -------------------- |
| `plugins_loaded_total` | Plugin load succeeds (`status=loaded`) | `none` |
| `plugin_load_failures_total` | Plugin load fails (`mark_failed`) | `PluginLoadFailureReason.code`, mapped to registry (`unknown` → `other`) |

No other metric labels on these counters (`kind`, `status`, `contribution_kind`, and `plugin_id` are **span attributes only**).

- [x] Record load success/failure counters using the contract above; extend `ALLOWED_LABEL_KEYS` / `FAILURE_CODE_REGISTRY` in `labels.py`.
- [x] Do not add unbounded `plugin_id` metric labels — use span attributes only.

## Testing

- [x] In-memory span exporter tests for successful/failed load.
- [x] Verify flag off → no plugin spans.

**Verify**

- `pytest tests/ai/plugins/test_plugin_observability.py tests/ai/observability/`

**Acceptance**

- Plugin telemetry follows Epic 07 content-free invariant.
- Execution spans (`tool_span`, `workflow_span`) unchanged.

**Exit criteria**

- [x] Observability tests pass.
- [ ] User confirmation to proceed to Phase 8.

**Rollback**

- Remove plugin span/metric hooks only.

**Completion Record**

| Metric                    | Result                                                                 |
| ------------------------- | ---------------------------------------------------------------------- |
| Lint                      | ✅ PASS                                                                |
| Plugin observability tests | ✅ 8 passed (`tests/ai/plugins/test_plugin_observability.py`)         |
| Observability suite       | ✅ 87 passed (`test_plugin_observability.py` + `tests/ai/observability/`) |
| Plugin test suite         | ✅ 65 passed (`tests/ai/plugins/`)                                     |
| Phase 7 status            | ✅ Completed                                                           |
| Phase 8 authorized        | ⬜ Pending user confirmation                                           |

---

# Phase 8 — Reference Plugins & Eval Cases

**Effort:** M

**Objective**

Ship git-tracked reference plugins demonstrating each contribution kind and extend the evaluation harness with plugin coverage.

**Deliverables**

- `backend-python/plugins/echo-tool/`
- `backend-python/plugins/echo-workflow-node/`
- Optional `echo-prompt` templates in echo-tool plugin
- Eval cases: plugin tool invocation + plugin workflow node (when prerequisites enabled)
- README section in `backend-python/README.md`

**Steps**

## Reference Plugins

- [ ] Implement `echo-tool` plugin (`com.example.echo`) with prefixed tool name.
- [ ] Implement `echo-workflow-node` plugin with `echo` node type.
- [ ] Add `plugin.yaml` files with correct `api_version`.

## Eval Extension

- [ ] Add eval dataset cases (or `--level plugin` smoke cases gated on `PLUGINS_ENABLED`) following existing harness patterns.
- [ ] Document skip policy when plugins disabled (similar to agent/workflow levels).

## Documentation

- [ ] Document operator steps: enable flag, configure directories, restart.

## Testing

- [ ] Integration test loading reference plugins from `backend-python/plugins/`.
- [ ] Eval cases pass in CI when flags enabled.

**Verify**

- `pytest tests/ai/plugins/test_reference_plugins.py`
- `make eval` (with plugin flags on in test env)

**Acceptance**

- Reference plugins load in dev configuration.
- Eval covers plugin tool and workflow node happy paths.

**Exit criteria**

- Reference plugin tests pass.
- User confirmation to proceed to Phase 9.

**Rollback**

- Remove reference plugins from default `plugin_directories` config.

---

# Phase 9 — Frontend Plugin Inventory

**Effort:** S

**Objective**

Add a read-only Plugins page listing loaded plugins from the REST API.

**Deliverables**

- `frontend/src/api/pluginsClient.ts`
- `frontend/src/types/plugins.ts`
- `frontend/src/pages/PluginsPage.tsx`
- Route registration (e.g. `/plugins`)
- Component tests

**Steps**

## API Client

- [ ] Fetch `GET /api/plugins` with auth headers.
- [ ] Handle `503 feature_disabled` with friendly empty state.

## UI

- [ ] Table/card list: name, plugin_id, version, api_version, contributions, status, load duration, optional author/homepage links.
- [ ] Failed plugins: show `failure.code` and safe diagnostic fields (e.g. API version mismatch).
- [ ] Link from settings or admin nav (match existing Observability/Workflows patterns).

## Testing

- [ ] MSW/mock tests for list and disabled states.

**Verify**

- Frontend lint, tests, build

**Acceptance**

- Page renders plugin inventory when backend flag on.
- No secrets or paths displayed.

**Exit criteria**

- Frontend tests pass.
- User confirmation to proceed to Phase 10.

**Rollback**

- Remove route and page.

---

# Phase 10 — Validation & Release

**Effort:** M

**Objective**

Full-platform validation, flag-off regression, release summary, and epic completion.

**Deliverables**

- `docs/releases/post-mvp-v2-epic8-release-summary.md`
- Updated epic Phase status and completion records
- Changelog entry

**Steps**

## Validation

- [ ] Full backend test suite + coverage ≥80% on `app/ai/plugins/`.
- [ ] Frontend tests + build.
- [ ] Integration tests (workflow, tool, MCP, plugins router).
- [ ] Eval suite + regression check.
- [ ] Flag-off regression: entire suite with `PLUGINS_ENABLED=false`.

## Documentation

- [ ] Publish release summary.
- [ ] Update `backend-python/.env.example` with plugin settings.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`
- Frontend lint, tests, production build

**Acceptance**

- All Part I architectural invariants preserved.
- Flag-off parity confirmed.
- Reference plugins documented and loadable.

**Exit criteria**

- Release summary published.
- User authorizes Epic 09.

**Rollback**

- Disable `PLUGINS_ENABLED`.
- Redeploy previous release if needed.

**Completion Record**

| Metric                    | Result |
| ------------------------- | ------ |
| Backend Tests             | _TBD_  |
| Frontend Tests            | _TBD_  |
| Integration Tests         | _TBD_  |
| Eval Suite                | _TBD_  |
| Feature Flag Regression   | _TBD_  |
| Release Summary Published | _TBD_  |
| Epic Status               | _TBD_  |

---

# PR Map

One PR per phase.

- v2/epic-08/phase-00-baseline
- v2/epic-08/phase-01-plugin-sdk-foundation
- v2/epic-08/phase-02-tool-plugins
- v2/epic-08/phase-03-prompt-plugins
- v2/epic-08/phase-04-workflow-node-plugins
- v2/epic-08/phase-05-mcp-plugins-versioning
- v2/epic-08/phase-06-rest-api
- v2/epic-08/phase-07-observability
- v2/epic-08/phase-08-reference-plugins-eval
- v2/epic-08/phase-09-frontend
- v2/epic-08/phase-10-release

---

# Risks

| Risk                                             | Mitigation                                                                    |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| Untrusted plugin code compromises process        | Document trusted-code model; directory allowlist; optional `plugin_allowlist` |
| Tool name collisions                             | Enforce `{plugin_id}.` prefix; deterministic load order                       |
| Plugin load order nondeterminism                 | Sort by `plugin_id`; test duplicate scenarios                                 |
| MCP server name conflicts                        | Env-wins policy with warning logs                                             |
| Workflow plugin nodes without receipt discipline | Document side-effect guidance; reuse Epic 06 receipt protocol                 |
| Metric cardinality from plugin ids               | Never label metrics with `plugin_id`; spans only                              |
| Startup latency from many plugins                | Per-plugin timeout; fail-open; log load report                                |
| Prompt plugin path traversal                     | Reject `..` in template paths; resolve relative to plugin root                |
| Feature regression                               | `PLUGINS_ENABLED` flag-off parity tests in Phase 10                           |

---

# Observability

Metrics/spans this epic adds (when respective flags enabled):

| Field                        | Purpose                                                                                         |
| ---------------------------- | ----------------------------------------------------------------------------------------------- |
| `plugin.load` span           | Per-plugin registration; attributes: `plugin_id`, `contribution_kind`, `status`, `load_duration_ms`, `failure_code` when failed (span-only — not metric labels except as below) |
| `plugins_loaded_total`       | Successful plugin loads — label `failure_code=none`                                             |
| `plugin_load_failures_total` | Failed plugin loads — label `failure_code` ∈ bounded registry (same dimension as success counter) |
| `plugins_enabled`            | Health field                                                                                    |
| `plugins_failed_count`       | Health field                                                                                    |

Execution of plugin tools and workflow nodes continues to emit existing `tool_span` / `workflow_span` events — no duplicate execution spans required.

---

# Definition of Done

- [ ] All Part I architectural invariants preserved.
- [ ] Public APIs frozen after Phase 1.
- [ ] Tool, prompt, workflow node, and MCP plugin kinds operational.
- [ ] Version checking against `PLUGIN_API_VERSION` enforced.
- [ ] Plugin REST API and frontend inventory page operational.
- [ ] Reference plugins and eval coverage shipped.
- [ ] `PLUGINS_ENABLED=false` preserves Epic 07 behaviour.
- [ ] Backend coverage ≥80% on `app/ai/plugins/`.
- [ ] Release summary published.
- [ ] User authorizes Epic 09.

---

## Files index

| Path                                                      | Action | Owner    | Phase   |
| --------------------------------------------------------- | ------ | -------- | ------- |
| `docs/audits/post-mvp-v2-epic8-phase-0-baseline-audit.md` | create | Docs     | 0       |
| `app/ai/plugins/**`                                       | create | Core     | 1–5     |
| `app/core/config.py`                                      | modify | Core     | 1       |
| `backend-python/.env.example`                             | modify | Docs     | 1, 10   |
| `app/main.py`                                             | modify | Adapter  | 2, 5, 6 |
| `app/ai/tools/registration.py`                            | modify | Core     | 5       |
| `app/ai/prompts/repository.py`                            | modify | Core     | 3       |
| `app/ai/workflow/models/definition.py`                    | modify | Core     | 4       |
| `app/ai/workflow/graph/validator.py`                      | modify | Core     | 4       |
| `app/ai/deps.py`                                          | modify | Adapter  | 4, 6    |
| `app/ai/observability/tracing/spans.py`                   | modify | Core     | 7       |
| `app/ai/observability/metrics/instruments.py`             | modify | Core     | 7       |
| `app/schemas/plugins.py`                                  | create | Core     | 6       |
| `app/routers/plugins.py`                                  | create | Adapter  | 6       |
| `app/routers/health.py`                                   | modify | Adapter  | 6       |
| `backend-python/plugins/echo-tool/**`                     | create | Core     | 8       |
| `backend-python/plugins/echo-workflow-node/**`            | create | Core     | 8       |
| `tests/ai/plugins/**`                                     | create | Tests    | 1–8     |
| `tests/plugins/fixtures/**`                               | create | Tests    | 1–5     |
| `tests/test_plugins_router.py`                            | create | Tests    | 6       |
| `tests/ai/evaluation/**`                                  | modify | Tests    | 8       |
| `frontend/src/api/pluginsClient.ts`                       | create | Frontend | 9       |
| `frontend/src/types/plugins.ts`                           | create | Frontend | 9       |
| `frontend/src/pages/PluginsPage.tsx`                      | create | Frontend | 9       |
| `docs/releases/post-mvp-v2-epic8-release-summary.md`      | create | Docs     | 10      |

---

## Changelog

| Version | Date       | Changes                                                                                          |
| ------- | ---------- | ------------------------------------------------------------------------------------------------ |
| 1       | 2026-08-10 | Initial epic draft — Part I design + Part II 11-phase execution plan (Phases 0–10). Not started. |
| 1.1     | 2026-08-10 | Reserved manifest fields (`dependencies`, author metadata, `metadata`); `PluginRecord.load_duration_ms`; structured `PluginLoadFailureReason` for API version diagnostics; § Future Enhancements (isolated loading, richer lifecycle states). Part I + Phases 1, 5–7, 9 sync. |
| 1.2     | 2026-08-10 | Phase 0 complete: baseline audit published; phase table + Phase 0 completion record updated. PR review clarifications (GraphValidator path, SemVer, coverage, workflow test scope, manifest fields, atomic registration, malformed manifests, REST metadata omission, WorkflowPluginRegistry ownership, load metrics label contract). Part II only. |
| 1.3     | 2026-08-11 | Phases 1–4 complete: plugin SDK foundation, tool plugins, prompt plugins, workflow node plugins. Phase status table, step checklists, exit criteria, and completion records updated. Epic status → `in_progress`. |
| 1.4     | 2026-08-11 | Phase 5 complete: MCP server plugins, env-wins merge in `register_mcp_tools`, startup load logging, versioning hardening. Phase status table, step checklists, exit criteria, and completion record updated. |
| 1.5     | 2026-08-11 | Phase 6 complete: authenticated plugin inventory REST API (`GET /api/plugins`, `GET /api/plugins/{plugin_id}`), `PluginsStore`, health plugin counts. Phase status table, step checklists, exit criteria, and completion record updated. |
| 1.6     | 2026-08-11 | Phase 7 complete: `plugin_span`, `record_plugin_load_outcome`, load counters (`plugins_loaded_total`, `plugin_load_failures_total`) with bounded `failure_code` label, `PluginLoader` instrumentation. Phase status table, step checklists, exit criteria, and completion record updated. |
