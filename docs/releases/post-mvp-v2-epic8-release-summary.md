# Post-MVP V2 Epic 08 Release Summary

**Release name:** Post-MVP V2 Epic 08 — Plugin Architecture
**Release date:** 2026-08-11
**Validation:** Phase 10 final acceptance (see [post-mvp-v2-epic-08-plugin-architecture.md](../plans/post-mvp-v2-epic-08-plugin-architecture.md))
**Git commit (validation base):** `856666a` — Epic 08 Phase 10 validation & release

---

## Summary vs Epic 07

Epic 07 shipped observability and evaluation under `OBSERVABILITY_ENABLED`. **V2 Epic 08 adds a unified in-process Plugin Architecture** under `app/ai/plugins/` (manifest, loader, registry, registrar, four contribution kinds, REST inventory API, observability hooks, reference plugins, eval level, frontend inventory page) behind `PLUGINS_ENABLED` (default **off**).

| Area | Epic 07 / pre-plugin platform | V2 Epic 08 |
| ---- | ----------------------------- | ---------- |
| Tool extension | Built-in + MCP tools only | Additive tool plugins via `ToolRegistry` (prefixed `{plugin_id}.` names) |
| Prompt templates | Built-in categories only | Additive prompt plugins under `plugin/{plugin_id}` |
| Workflow nodes | Built-in `NodeType` executors | Additive `NodeType.PLUGIN` nodes via `WorkflowPluginRegistry` |
| MCP servers | Env `mcp_servers` only | Additive plugin-declared servers (env wins on name conflict) |
| Management API | Observability usage REST | Authenticated `/api/plugins` inventory (route-level `503` when flag off) |
| Frontend | Observability dashboard | Additive `/plugins` inventory page + nav link (hidden when flag off or guest) |
| Chat / RAG / MCP / Memory / Voice / Agent / Tools / Workflows / Observability | Stable | Unchanged when `PLUGINS_ENABLED=false` |

---

## Delivered (Phases 0–10)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | `PluginManifest`, `PluginLoader`, `PluginRegistry`, `PluginRegistrar`, `PLUGINS_ENABLED` flag |
| 2 | Tool plugins → `ToolRegistry` |
| 3 | Prompt plugins → `PromptRepository` |
| 4 | Workflow node plugins → `WorkflowPluginRegistry` / `NodeType.PLUGIN` |
| 5 | MCP server plugins + env-wins merge in `register_mcp_tools` |
| 6 | Plugin REST API, health plugin counts, `PluginsStore` |
| 7 | `plugin_span`, `plugins_loaded_total`, `plugin_load_failures_total` |
| 8 | Reference plugins (`echo-tool`, `echo-workflow-node`), `--level plugin` eval |
| 9 | `PluginsPage`, `pluginsClient.ts`, health/nav integration |
| 10 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `PluginManifest`, `PluginRegistry`, `PluginLoader`, `PluginRegistrar`, `PluginRecord`, `PluginLoadFailureReason`, `PLUGIN_API_VERSION`, `plugins_router`.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `PLUGINS_ENABLED` | `false` | Off: no plugin discovery/load; Plugin REST API returns `503 feature_disabled`; `/plugins` nav hidden; chat/RAG/MCP/memory/voice/agent/tool/workflow/observability unchanged. On: startup load from `PLUGIN_DIRECTORIES`; inventory API and frontend page active. |

Additional settings (see `backend-python/.env.example`): `PLUGIN_DIRECTORIES`, `PLUGIN_ALLOWLIST`, `PLUGIN_REGISTRATION_WAIT_TIMEOUT_SECONDS`.

**Rollback:** set `PLUGINS_ENABLED=false`; redeploy. Platform reverts to Epic 07 behaviour on hot paths.

---

## Breaking Changes

**None.** Plugin architecture is additive behind a master flag. No DB migrations. Existing tool, prompt, workflow, and MCP paths unchanged when the flag is off.

---

## Migration / Upgrade Notes

1. Pull release; no Alembic migration required for plugins.
2. Ensure `backend-python/.env.example` includes plugin settings: `PLUGINS_ENABLED` (default `false`), `PLUGIN_DIRECTORIES`, `PLUGIN_ALLOWLIST`, and `PLUGIN_REGISTRATION_WAIT_TIMEOUT_SECONDS`.
3. To exercise locally: set `PLUGINS_ENABLED=true`, ensure `PLUGIN_DIRECTORIES` includes `plugins`, restart API, sign in, open `/plugins`.
4. Reference plugins: `backend-python/plugins/echo-tool/`, `backend-python/plugins/echo-workflow-node/`.
5. Run plugin eval: `uv run python -m app.ai.evaluation.cli --level plugin` (requires flags + Postgres/pgvector for workflow case).

---

## Manual E2E Smoke (documented procedure)

Run with `PLUGINS_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `plugins_enabled: true`, plugin counts |
| 2. Nav | "Plugins" link visible when signed in; hidden for guests and when flag off |
| 3. Inventory page | `/plugins` lists loaded/failed plugins with contribution kinds |
| 4. Plugins API | `GET /api/plugins` returns inventory; `GET /api/plugins/{plugin_id}` returns detail |
| 5. Reference tool | `com.example.echo.ping` registered when echo-tool loads |
| 6. Eval | `--level plugin` passes 3/3 when prerequisites enabled |
| 7. Flag off | `plugins_enabled: false`; API `503`; nav hidden; chat unchanged from pre-epic |

Automated CI covers plugin SDK, contribution kinds, router, observability, reference plugins, eval, and frontend with mocks/fakes.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Runtime hot-reload / unload | Out of scope — restart required |
| Remote marketplace / signed artifacts | Out of scope |
| Sandboxed plugin execution | Out of scope — trusted in-process code |
| Plugin admin RBAC / install over HTTP | Epic 11 |
| HITL before plugin tool execution | Epic 09 |
| `dependencies` manifest resolution | Reserved; stored but ignored in V2 |
| Per-plugin billing metrics | Out of scope — aggregate load counters only |

---

## Verification Metrics (Phase 10 — 2026-08-11)

| Gate | Result |
| ---- | ------ |
| Backend `make lint` + `format-check` + `typecheck` | **Clean** |
| Flag-on `make test-cov` | **1778 passed**, **89.17%** coverage on `app/` |
| Plugin package `app/ai/plugins/` | **91%** (gate ≥80%) |
| Epic 08 test paths | **81 passed** (`tests/ai/plugins/`, `tests/test_plugins_router.py`) |
| Integration paths (workflow + MCP + plugins router) | **408 passed** |
| `make eval --level all` | **15/15** passed |
| `--level plugin` | **3/3** passed |
| `--check-regression` | **No regressions detected** |
| Flag-off full suite (`PLUGINS_ENABLED=false make test-cov`) | **1778 passed**, **89.18%** |
| Frontend lint + format + build | **Clean** |
| Frontend Vitest | **291** tests (48 files) — all pass |
| Plugin frontend tests (2 files) | **9 passed** (`PluginsPage`, `pluginsClient`) |

**Architectural invariants (Part I):** plugins extend existing registries only; prefixed tool names; prompt category namespacing; `NodeType.PLUGIN` dispatch; env-wins MCP merge; per-plugin fail-open load; no content in spans/API; flag-off parity confirmed; `plugin_id` span-only (not metric labels).

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-08-plugin-architecture.md](../plans/post-mvp-v2-epic-08-plugin-architecture.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic8-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic8-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic7-release-summary.md](./post-mvp-v2-epic7-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
