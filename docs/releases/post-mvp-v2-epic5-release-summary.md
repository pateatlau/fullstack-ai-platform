# Post-MVP V2 Epic 05 Release Summary

**Release name:** Post-MVP V2 Epic 05 — Memory System
**Release date:** 2026-08-04
**Validation:** Phase 10 final acceptance (see [post-mvp-v2-epic-05-memory-system.md](../plans/post-mvp-v2-epic-05-memory-system.md))
**Git commit (validation base):** `e1bd606` — Epic 05 Phase 10 validation & release

---

## Summary vs Epic 04

Epic 04 shipped bidirectional voice under `VOICE_ENABLED`. **V2 Epic 05 adds a provider-agnostic memory platform** under `app/ai/memory/` (rolling conversation summaries, durable user/project memories, structured preferences, semantic retrieval, lifecycle management, REST API, settings UI) behind `MEMORY_ENABLED` (default **off**).

| Area | Epic 04 / pre-memory chat | V2 Epic 05 |
| ---- | ------------------------- | ---------- |
| Conversation context | V1 `SessionSummary` + `_maybe_summarize` only | Additive retrieval: summary + semantic memories + preferences + project scope |
| Durable memory | None | Async extraction → `memory_records` (pgvector, separate from RAG chunks) |
| User preferences | None | Structured `user_preferences` rows (no embeddings) |
| Chat orchestration | `ChatService` / `UnifiedChatService` | Same boundaries — `_apply_memory_context` / `_maybe_extract_memory` when flag on |
| Prompt injection | RAG `PromptBuilder` / chat templates | Additive `MemoryPromptInjector` + `chat/memory_context/v1` system message |
| Management API | None | Authenticated `GET/PUT/DELETE /api/memory/*` (route-level `503` when flag off) |
| Frontend | Voice settings in Composer | Additive `/settings/memory` page + nav link (hidden when flag off or guest) |
| Voice / MCP / RAG / Tools | Stable | Unchanged when `MEMORY_ENABLED=false`; voice inherits memory transparently when both flags on |

---

## Delivered (Phases 0–10)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Models/enums, `MemoryProvider` protocol, `PgVectorMemoryProvider` scaffold, migration `0006`, `MEMORY_ENABLED`, DI wiring |
| 2 | `ConversationSummaryService` over V1 `SessionSummary`; `build_context_messages` activation |
| 3 | Durable memory extraction (`MemoryExtractor`, `MemoryQualityEvaluator`), async persistence |
| 4 | User preferences persistence/retrieval, `MemoryContextBuilder` |
| 5 | Session-scoped project memory (`project_id` = `chat_session_id`) |
| 6 | `SemanticRetriever` multi-domain retrieval, ranking/dedupe, token budgeting |
| 7 | `LifecycleManager`, `MemoryPolicyEngine`, Memory REST API, `memory_enabled` health field |
| 8 | Full chat integration in `ChatService` + `UnifiedChatService`; `MemoryPromptInjector` |
| 9 | `memoryClient.ts`, `MemorySettingsPage`, health/nav integration |
| 10 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `MemoryProvider`, `MemoryRecord`, `MemoryContext`, `MemoryManager`, `MemoryContextBuilder`, `SemanticRetriever`, `LifecycleManager`, `MemoryPolicyEngine`; memory enums/exceptions; flag-guarded router.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `MEMORY_ENABLED` | `false` | Off: no retrieve/inject/extract in chat; Memory API returns `503 feature_disabled`; settings UI shows unavailable notice; nav link hidden. On: authenticated users get memory-enriched chat and management UI. |

Requires `OPENAI_API_KEY` when using OpenAI embeddings (default). CI uses fakes — no live embedding API calls in unit/integration tests.

**Rollback:** set `MEMORY_ENABLED=false`; chat, voice, RAG, MCP, and streaming paths revert to pre-epic behaviour; in-flight async persistence may complete normally.

---

## Breaking Changes

**None.** Memory is additive behind a master flag. Chat HTTP/SSE contracts unchanged.

---

## Migration / Upgrade Notes

1. Pull release; run `alembic upgrade head` (migration `0006_memory_tables`).
2. Ensure `backend-python/.env.example` includes `MEMORY_*` settings (`MEMORY_ENABLED=false` by default).
3. To exercise locally: set `MEMORY_ENABLED=true`, ensure DB migrated, sign in (guests have no memory), open `/settings/memory` or chat — memories extract asynchronously after turns.
4. Project memory v1 is session-scoped (`project_id` = `chat_session_id`).

---

## Manual E2E Smoke (documented procedure)

Run with `MEMORY_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `memory_enabled: true` |
| 2. Settings nav | "Memory" link visible when signed in; hidden for guests and when flag off |
| 3. Memory page | `/settings/memory` loads preferences, long-term memories, project memories, summary controls |
| 4. Chat turn | Assistant responds normally; memory context injected as system message (no user-visible scores) |
| 5. Preference CRUD | Upsert/delete preference refreshes list |
| 6. Memory delete | Individual/bulk delete with confirmation |
| 7. Summary clear | Session summary deletion with confirmation |
| 8. Flag off | `memory_enabled: false`; API `503`; chat unchanged from pre-epic |

Automated CI covers memory modules, router, chat integration, and frontend with mocks; live LLM/embedding smoke is manual.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Guest memory | Out of scope (authenticated-only) |
| Standalone `projects` entity | Future epic (`project_id` = session id in v1) |
| System / organization memory | Future epic |
| Inferred preferences | Out of scope v1 |
| Background job queue for lifecycle | In-process async only; Epic 10 queue deferred |
| Memory OTel spans, eval harness | Epic 07 |
| Cross-encoder rerank | Optional later |

---

## Verification Metrics (Phase 10 — 2026-08-04)

| Gate | Result |
| ---- | ------ |
| Flag-off `MEMORY_ENABLED=false make test-cov` | **1305 passed**, **89.66%** coverage on `app/` |
| Flag-on full `make test-cov` | **1305 passed**, **89.68%** coverage on `app/` |
| Memory package `app/ai/memory/` | **89%** (gate ≥80%) |
| Memory test paths (`tests/ai/memory`, router, chat integration) | **214 passed** |
| Chat/Voice/RAG/MCP flag-off parity spot checks | **8 passed** (memory skip, router 503, voice/MCP/unified unchanged) |
| `make eval` | **5/5** passed (`.eval/eval-report.json`, timestamp `2026-08-04T13:47:53Z`) |
| Backend lint + typecheck | Clean |
| Frontend lint + format + build | Clean |
| Frontend Vitest | **251** tests (41 files) — all pass |
| Memory frontend tests (3 files) | **20 passed** (`MemorySettingsPage`, `memoryClient`, nav/health) |

**Architectural invariants (Part I):** Memory orchestrated only via `ChatService` / `UnifiedChatService`; RAG and Memory independent; injection via `MemoryPromptInjector` only; no guest memory; lifecycle + REST API operational; flag-off parity confirmed.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-05-memory-system.md](../plans/post-mvp-v2-epic-05-memory-system.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic5-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic5-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic4-release-summary.md](./post-mvp-v2-epic4-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
