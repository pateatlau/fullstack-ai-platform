# Post-MVP V2 Epic 01 Release Summary

**Release name:** Post-MVP V2 Epic 01 — Agent Framework
**Release date:** 2026-07-24
**Validation:** Phase 12 final acceptance (see [post-mvp-v2-epic-01-agent-framework.md](../plans/post-mvp-v2-epic-01-agent-framework.md))
**Git commit (validation base):** `c77eb94` — Phase 11 chat adapter (+ streaming UI fix)

---

## Summary vs V1.1.1

V1.1.1 polished unified chat UX and demo protection on the V1.1 orchestration path (`UnifiedChatService` → `ToolChatService` / `ChatService`). **V2 Epic 01 adds a reusable, provider-agnostic agent runtime** under `app/ai/agent/` and wires it into web-search chat behind `AGENT_RUNTIME_ENABLED` (default **off**).

| Area | V1.1.1 | V2 Epic 01 |
| ---- | ------ | ---------- |
| Agent runtime | None | `DefaultAgent` + planner / executor / scratchpad / reflection / retry / streaming |
| Web-search chat path | `ToolChatService` tool loop | Same when flag off; `ChatAgentAdapter` when `AGENT_RUNTIME_ENABLED=true` |
| Streaming | SSE via chat services | Core `StreamPublisher`; adapter maps to existing SSE frame names |
| RAG in agent | N/A | Still pre-handoff in `UnifiedChatService` (Epic 2) |
| Public chat API | Unchanged | Unchanged (flag is additive) |

---

## Delivered (Phases 0–11)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Package scaffold, models, Protocols, `AGENT_RUNTIME_ENABLED` |
| 2 | `AgentStateManager` / execution lifecycle |
| 3 | Ephemeral scratchpad |
| 4 | Retry classification wrapping `retry_async` |
| 5 | `StreamPublisher` + SSE frame mapping helper |
| 6 | `ReActPlanner` + `planner.v1.j2` |
| 7 | Multi-tool runner (parallel / `depends_on`) |
| 8 | `AgentExecutor` loop + finalizer |
| 9 | Optional reflection engine + `reflection.v1.j2` |
| 10 | `DefaultAgent`, factory, `get_agent_runtime()` |
| 11 | Chat adapters + `UnifiedChatService` flag branches |

**Stable public APIs** (Phase 1 freeze): `Agent`, `Planner`, `Executor`, `RetryPolicy`, `StreamPublisher`; request/response/plan/config models; `AgentError` family.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `AGENT_RUNTIME_ENABLED` | `false` | Off: V1.1 `ToolChatService` path unchanged. On: unified web-search chat (non-streaming + streaming) uses `app/ai/agent/adapters/`. |

RAG document grounding remains in `UnifiedChatService` before agent handoff. Guest denial, persistence, usage, `tools_used`, and `retrieved_chunks` are preserved by the adapter.

**Rollback:** set `AGENT_RUNTIME_ENABLED=false` (no API contract change).

---

## Breaking Changes

**None.** Chat API contracts unchanged. Agent runtime is opt-in behind a default-off flag.

---

## Migration / Upgrade Notes

1. Pull release; ensure `backend-python/.env.example` includes `AGENT_RUNTIME_ENABLED=false`.
2. Keep the flag **off** in production until you intentionally enable the agent path.
3. To exercise the agent path locally: set `AGENT_RUNTIME_ENABLED=true` with `TOOLS_ENABLED=true` and a valid `WEB_SEARCH_API_KEY`.
4. No database migrations for this epic.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| RAG-in-agent, MCP, durable memory, workflows, HITL, plugins | V2 Epic 2+ / V3 |
| E2E tool-round streaming parity beyond adapter mapping | Out of Epic 01 scope (Part I) |
| Provider package relocation | Deferred |

---

## Verification Metrics (Phase 12 — 2026-07-24)

| Gate | Result |
| ---- | ------ |
| Flag-off `make test-cov` | **604 passed**, **88.19%** coverage on `app/` |
| Flag-on `make test-cov` (`AGENT_RUNTIME_ENABLED=true`) | **604 passed**, **87.61%** coverage on `app/` |
| `app/ai/agent/` coverage | **91.12%** (144 agent tests; gate ≥80%) |
| `make eval` | **5/5** passed (`backend-python/.eval/eval-report.json`, timestamp 2026-07-23T23:55:38Z) |
| Frontend | lint / format / **170** Vitest / build — all pass |
| Docker Compose smoke (`--profile python`) | Health **200**, ready **200** (`db: ok`), frontend **200** |

**Test isolation note:** `tests/test_ai_settings.py::test_feature_flags_default_off` clears feature-flag process env vars so default-off assertions remain valid when the suite is run with `AGENT_RUNTIME_ENABLED=true`.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-01-agent-framework.md](../plans/post-mvp-v2-epic-01-agent-framework.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic1-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic1-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v1.1.1-release-summary.md](./post-mvp-v1.1.1-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
