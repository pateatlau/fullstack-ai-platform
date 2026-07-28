# Post-MVP V2 Epic 03 Release Summary

**Release name:** Post-MVP V2 Epic 03 — MCP Integration
**Release date:** 2026-07-28
**Validation:** Phase 10 final acceptance (see [post-mvp-v2-epic-03-mcp-integration.md](../plans/post-mvp-v2-epic-03-mcp-integration.md))
**Git commit (validation base):** `8a95a46` — Phase 9 DI wiring and lifecycle integration

---

## Summary vs Epic 02

Epic 02 extended document retrieval with hybrid search, reranking, and citations. **V2 Epic 03 adds Model Context Protocol (MCP) client integration** under `app/ai/mcp/` so remote MCP servers can register tools into the existing tool platform behind `MCP_ENABLED` (default **off**).

| Area | Epic 02 / V1 tools | V2 Epic 03 |
| ---- | ------------------ | ---------- |
| Tool sources | Local registry (e.g. web search) | Same when flag off; remote MCP tools when on |
| Discovery | Static registration | Dynamic `tools/list` from configured MCP servers |
| Transport | In-process handlers | Stdio subprocess + JSON-RPC (MCP spec 2024-11-05) |
| Authorization | `ToolAuthorizer` (authenticated-only) | Composed with `McpPermissionPolicy` per-server/per-tool allowlists |
| Agent / RAG path | Unchanged | Unchanged — MCP tools flow through `ToolRegistry` → `ToolExecutor` → `ToolRunner` |

---

## Delivered (Phases 0–10)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Package scaffold, models, `McpClient` Protocol, `MCP_ENABLED` |
| 2 | Stdio transport and `StdioMcpClient` (JSON-RPC subprocess) |
| 3 | `McpServerRegistry` lifecycle and health tracking |
| 4 | `McpToolDiscovery` (`tools/list` → `ToolDefinition`) |
| 5 | `McpToolExecutionAdapter` → `ToolHandler` |
| 6 | `McpServerCredentials` with env placeholder resolution |
| 7 | `McpPermissionPolicy` composing with `ToolAuthorizer` |
| 8 | Startup registration from `MCP_SERVERS` config |
| 9 | DI wiring, startup/shutdown hooks, integration tests |
| 10 | Validation gates, release summary, documentation updates |

**Stable public APIs** (Phase 1 freeze): `McpClient`, `McpServerRegistry`, `McpToolDiscovery`, `McpPermissionPolicy`; `McpConnectionConfig`, `McpServerCredentials`; MCP exception family; `register_mcp_tools`.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `MCP_ENABLED` | `false` | Off: V1 local tools via `ToolRegistry` → `ToolExecutor` unchanged. On: connect configured MCP servers, discover tools, register as `{server_name}.{tool_name}`, execute through existing `ToolExecutor` path. |

**Rollback:** set `MCP_ENABLED=false` (no API contract change).

---

## Breaking Changes

**None.** MCP integration is additive behind a default-off flag. Local tool contracts unchanged.

---

## Migration / Upgrade Notes

1. Pull release; ensure `backend-python/.env.example` includes `MCP_ENABLED=false` and `MCP_SERVERS` / `MCP_PERMISSION_POLICY` examples.
2. Keep the flag **off** in production until you intentionally enable remote MCP tools.
3. To exercise locally: set `MCP_ENABLED=true`, configure `MCP_SERVERS` JSON (stdio transport), sign in (guests denied for tool execution), enable `TOOLS_ENABLED=true` for chat tool loops.
4. Tool naming: `{server_name}.{tool_name}` (e.g. `filesystem.read_file`).

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| SSE / non-stdio MCP transports | Future |
| Secret vault / dynamic credential rotation | Epic 11 / external |
| MCP server streaming results | Single-shot in v1 |
| Plugin SDK hot-reload | Epic 7 |
| HITL approval for MCP tools | Epic 8 |
| RBAC / audit logs for MCP | Epic 11 |

---

## Verification Metrics (Phase 10 — 2026-07-29)

| Gate | Result |
| ---- | ------ |
| Flag-off `MCP_ENABLED=false make test-cov` | **1076 passed**, **89.42%** coverage on `app/` |
| Flag-on `MCP_ENABLED=true make test-cov` | **1076 passed**, **89.52%** coverage on `app/` |
| MCP package `app/ai/mcp/` | **96%** (190 tests; gate ≥80%) |
| Agent / RAG / MCP regression (`test_chat_stream`, `test_unified_chat`, `test_integration`) | **34 passed** (Epic 04 validation pass) |
| `make eval` | **5/5** passed (`.eval/eval-report.json`, timestamp `2026-07-28T19:37:53Z`) |

**CI note:** MCP tests use fake MCP servers and mocked subprocess I/O — no live external MCP dependencies in CI.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-03-mcp-integration.md](../plans/post-mvp-v2-epic-03-mcp-integration.md)
- Prior release: [docs/releases/post-mvp-v2-epic2-release-summary.md](./post-mvp-v2-epic2-release-summary.md)
- Next release: [docs/releases/post-mvp-v2-epic4-release-summary.md](./post-mvp-v2-epic4-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
