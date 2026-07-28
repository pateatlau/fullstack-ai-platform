# Post-MVP V1.1 Release Summary

**Release name:** Post-MVP V1.1 — Unified Chat (Provider Parity + Streaming Tools/RAG)
**Release date:** 2026-07-22
**Validation:** Phase 6 final acceptance (see [post-mvp-v1.1-implementation-plan.md](../plans/post-mvp-v1.1-implementation-plan.md))

---

## Summary vs V1

V1 delivered a reusable AI platform: tools, ingestion, pgvector, generic RAG, evaluation, and a separate `/documents` + `/api/rag/ask` surface. **V1.1 consolidates the product experience** on the main chat (`/`) while preserving V1 behavior when toggles are off.

| Area | V1 | V1.1 |
| ---- | -- | ---- |
| Web search in chat | Non-streaming; OpenAI-primary validation | All four providers; streaming + non-streaming |
| Document grounding | `/documents` panel + `/api/rag/ask` only | Main chat `use_documents` toggle (+ retained APIs) |
| RAG LLM provider | Global `LLM_PROVIDER` | Per-request `provider` / `model` on chat and `/api/rag/ask` |
| Streaming + tools/RAG | Disabled when streaming on | Unified pipeline via `UnifiedChatService` |
| Chat UX | Plain streaming chat | Optional web search and document toggles for authenticated users |

---

## Sub-tracks Delivered

1. **V1.1a — Provider parity (Phase 1–2)**
   Multi-provider tool calling; `ProviderCapabilities` on health; per-request RAG provider.

2. **V1.1b — Unified chat (Phase 3)**
   `UnifiedChatService` non-streaming orchestration; `use_web_search` / `use_documents` request toggles; frontend composer toggles.

3. **V1.1c — Streaming (Phase 4–5)**
   SSE `tool_start` / `tool_end` / `retrieval_complete`; streaming document pre-retrieval; combined toggle order (retrieval → tools → stream).

---

## API and UX Changes

### Request toggles (authenticated only)

On `POST /api/chat` and `POST /api/chat/stream`:

- `use_web_search` — Tavily web search tool loop (requires `TOOLS_ENABLED=true`)
- `use_documents` — pre-retrieval document grounding (requires `RAG_ENABLED=true`)

### SSE events (additive)

| Event | When |
| ----- | ---- |
| `retrieval_complete` | After document retrieval, before tool loop or answer stream |
| `tool_start` / `tool_end` | Streaming web search tool lifecycle |

### Health endpoint

`GET /api/health` includes `chat_streaming_enabled`, `tools_enabled`, `rag_enabled`, and `capabilities.by_provider`.

### Frontend

- Composer toggles on `/` (authenticated; capability-gated web search)
- Status indicators: "Searching your documents…", "Searching the web…"
- `/documents` retained for upload/list/delete; chat is primary ask surface

---

## Breaking Changes

**None expected.** Existing endpoints and contracts remain. New request fields and SSE events are additive.

---

## Migration / Upgrade Notes

### Environment variables

| Variable | Default | Notes |
| -------- | ------- | ----- |
| `RAG_ENABLED` | `false` | Enable document toggle and `/api/rag/ask` |
| `TOOLS_ENABLED` | `false` | Enable web search toggle; requires `WEB_SEARCH_API_KEY` |
| `CHAT_STREAMING_ENABLED` | `true` | Set `false` for non-streaming-only deployments |
| `WEB_SEARCH_API_KEY` | — | Required when `TOOLS_ENABLED=true` |

See `backend-python/.env.example` for full matrix.

### Rollout

1. Enable flags in staging (`TOOLS_ENABLED`, `RAG_ENABLED` as needed).
2. Verify health capabilities and toggle UX for authenticated users.
3. Confirm V1 plain chat unchanged with toggles off.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Standalone streaming `/api/rag/ask` (`RAGService.ask_stream`) | **Deferred** — non-streaming ask remains |
| `retrieved_chunks` persistence on stream path | **Deferred** — non-streaming path includes metadata |
| Mid-stream / dynamic re-retrieval | V2 |
| Citations UI, hybrid search, reranking | V2 |
| RAG as LLM-invoked tool | V2 |
| Named counters `chat_use_web_search_total` / `chat_use_documents_total` | Not implemented; latency/round fields logged instead |

---

## Verification Metrics (Phase 6 — 2026-07-22)

| Gate | Result |
| ---- | ------ |
| Backend tests | **403 passed**, **86.14%** coverage on `app/`, **20.10s** |
| Backend quality | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Frontend tests | **122 passed**; lint (1 warning), format, build pass |
| Eval CLI | **5/5** passed (`backend-python/.eval/eval-report.json`, timestamp 2026-07-21T23:04:45Z) |
| V1 regression suite | **122** targeted tests pass |
| Docker Compose smoke | Health + frontend **200**; stream protocol verified (provider error with placeholder keys) |

---

## References

- Implementation plan: [docs/plans/post-mvp-v1.1-implementation-plan.md](../plans/post-mvp-v1.1-implementation-plan.md)
- Architecture spec (V1.1 sections): [docs/references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md](../references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
