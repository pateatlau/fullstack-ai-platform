# Post-MVP V1.1 — Phase 0 Baseline Audit

**Date:** 2026-07-21
**Agent run:** Phase 0 baseline audit (Cursor implementation agent)
**Prerequisite:** Post-MVP V1 Phase 13 Complete (verified 2026-07-21)

---

## 1. Quality Gate Results

### Backend (`backend-python/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `make lint` | Pass | ruff check — all checks passed |
| `make format-check` | Pass | 151 files already formatted |
| `make typecheck` | Pass | pyright — 0 errors |
| `make test-cov` | Pass | **344 passed**, **87.99%** coverage on `app/`, **11.78s** |
| `make eval` | Pass | **5/5** passed (prompt=2, retrieval=2, e2e=1) |

### Frontend (`frontend/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `npm run lint` | Pass | 1 warning (`ChatPage.tsx` exhaustive-deps); 0 errors |
| `npm run format:check` | Pass | Prettier clean |
| `npm test -- --run` | Pass | **109 passed** (22 files), **2.21s** |
| `npm run build` | Pass | tsc + vite production build |

### Docker Compose smoke (repository root)

```bash
docker compose --profile python up -d --build
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/health/ready
```

| Check | Result |
| ----- | ------ |
| Build + start | Pass |
| `GET /api/health` | `status: ok`, `chat_streaming_enabled: true` |
| `GET /api/health/ready` | `status: ok`, `db: ok` |

Postgres image: `pgvector/pgvector:pg16` (per `docker-compose.yml`).

---

## 2. Regression vs V1 Phase 13 Baseline

| Metric | Phase 13 (2026-07-21) | Phase 0 (2026-07-21) | Delta / cause |
| ------ | --------------------- | -------------------- | ------------- |
| Backend tests | 342 passed | 344 passed | +2 — pre-V1.1 streaming-toggle tests (`test_chat_stream_disabled_returns_503`, `useChatStreamingEnabled` coverage) |
| Backend coverage | 88.25% | 87.99% | −0.26 pp — within ≥80% gate; new code paths slightly dilute |
| Backend duration | 12.35s | 11.78s | Faster run (environment variance) |
| Frontend tests | 106 passed | 109 passed | +3 — streaming health/transport tests |
| Eval CLI | 5/5 | 5/5 | Unchanged pass count |
| Eval retrieval mean latency | 57.5 ms (Phase 13 record) | 14.5 ms | Both within 150 ms soft target |

V1 Phase 13 completion record confirmed in `docs/plans/post-mvp-v1-implementation-plan.md` (Phase 13 — **Complete**, 2026-07-21).

---

## 3. Provider Adapter Tool-Calling Inventory

| Provider | File | `complete_chat_with_tools` | Return type | Tool call ID | Argument JSON | Empty/malformed handling | Gap vs OpenAI |
| -------- | ---- | -------------------------- | ----------- | ------------ | ------------- | ------------------------ | ------------- |
| OpenAI | `app/providers/openai_provider.py` | **Implemented** | `ProviderToolCompletion` | Native `call.id` from SDK | `_parse_tool_arguments`: empty → `{}`, invalid JSON → `{}`, non-dict → `{}` | Skips non-`function` tool types; empty choices → empty content + no tool calls | Reference implementation |
| Gemini | `app/providers/gemini_provider.py` | **Stub** — raises `NotImplementedError("Tool calling is not supported for Gemini in V1")` | — | — | — | — | Full implementation required (Phase 1) |
| Groq | `app/providers/groq_provider.py` | **Stub** — raises `NotImplementedError("Tool calling is not supported for Groq in V1")` | — | — | — | — | Full implementation required (Phase 1) |
| Anthropic | `app/providers/anthropic_provider.py` | **Stub** — raises `NotImplementedError("Tool calling is not supported for Anthropic in V1")` | — | — | — | — | Full implementation required (Phase 1) |

Protocol contract: `app/providers/base.py` — `LLMProvider.complete_chat_with_tools(...) -> ProviderToolCompletion`.

---

## 4. Provider Test Gap Analysis

Files under `backend-python/tests/providers/`:

| File | Tool-calling coverage |
| ---- | --------------------- |
| `test_openai_tool_calling.py` | **Dedicated** — parses tool calls, direct answer (no tools) |
| `test_gemini_provider.py` | Completion/streaming only — **no** `complete_chat_with_tools` tests |
| `test_groq_provider.py` | Completion/streaming only — **no** `complete_chat_with_tools` tests |
| `test_anthropic_provider.py` | Completion/streaming only — **no** `complete_chat_with_tools` tests |

**Phase 1 gap:** Add `test_gemini_tool_calling.py`, `test_groq_tool_calling.py`, `test_anthropic_tool_calling.py` mirroring OpenAI patterns.

Integration tool tests exist in `tests/test_phase4_chat_tools.py` (OpenAI-primary with `FakeProvider`).

---

## 5. Streaming / RAG / Chat Schema Current State

### Streaming tool skip policy

When `CHAT_STREAMING_ENABLED=true` and `TOOLS_ENABLED=true`:

- `POST /api/chat/stream` routes to `ChatService.stream_chat` only — **does not** use `ToolChatService`.
- `POST /api/chat` uses `ToolChatService` when `TOOLS_ENABLED=true` (`app/routers/chat.py`).

**Test reference:** `tests/test_phase4_chat_tools.py::test_streaming_skips_tools_even_when_enabled` — asserts `tool_completion_calls == 0` on stream path while stream succeeds.

### `CHAT_STREAMING_ENABLED` interim behavior (pre-V1.1)

| Area | Verified behavior | Reference |
| ---- | ----------------- | --------- |
| Config | `chat_streaming_enabled: bool = True` default | `app/core/config.py` |
| Health | `GET /api/health` → `chat_streaming_enabled` | `app/routers/health.py` |
| Stream route | `POST /api/chat/stream` → **503** `feature_disabled` when flag false | `app/routers/chat.py`, `tests/test_chat_stream.py::test_chat_stream_disabled_returns_503` |
| UI transport | `useChatStreamingEnabled` reads health on mount; defaults true while loading | `frontend/src/hooks/useChatStreamingEnabled.ts` |
| Non-stream path | `useChatCompletion` → `POST /api/chat` when streaming off | `frontend/src/pages/ChatPage.tsx` |
| Tools on non-stream | Web search via `ToolChatService` when `TOOLS_ENABLED=true` | `app/routers/chat.py` |
| Pending labels | Composer/MessageBubble: "Streaming" vs "Waiting for response" via `showStreamingStatus` | `frontend/src/components/Composer.tsx`, `MessageBubble.tsx` |

**Note:** Local dev `.env` may set `CHAT_STREAMING_ENABLED=false`; test suite forces `true` via `tests/conftest.py` setdefault for gate isolation.

### RAG global-provider wiring

- `get_rag_service` in `app/ai/deps.py` binds LLM via `ProviderFactory.get_provider(settings=settings)` — uses global `LLM_PROVIDER` from settings.
- `RAGAskRequest` (`app/schemas/rag.py`): `question`, `prompt_template`, `instructions`, `top_k`, `temperature` — **no** `provider` / `model` fields.
- RAG is non-streaming only (`RAGService.ask`).

### Current chat request schema

**Backend** (`ChatRequestSchema` in `app/schemas/chat.py`):

- `messages`, `model`, `provider`, `temperature`, `session_id`, `client_message_id`
- **No** `use_web_search` or `use_documents`

**Frontend** (`ChatRequest` in `frontend/src/types/chat.ts`):

- Same fields mirrored; **no** toggle fields

### V1.1 feature flag behavior (agreed — no code change)

- `RAG_ENABLED`, `TOOLS_ENABLED`, `CHAT_STREAMING_ENABLED` unchanged; streaming defaults `true`.
- Request-level toggles (`use_web_search`, `use_documents`) deferred to **Phase 3**.
- When feature flags off, request toggles are no-ops; behavior = MVP + V1 unchanged.

---

## 6. V1.1 Locked Decisions Acknowledgment

| Decision | Acknowledged |
| -------- | ------------ |
| Single V1.1 release, sub-tracks 1.1a → 1.1b → 1.1c | Yes |
| Web search as LLM tool via request toggle | Yes |
| Document grounding as pre-retrieval context provider (not a tool) | Yes |
| `UnifiedChatService` orchestration (Phase 3) | Yes |
| `/documents` route retained; ask moves to main chat | Yes |
| Guest policy: no tools, no document grounding | Yes |
| Streaming RAG: retrieve before stream starts (Phase 5) | Yes |

---

## 7. Known V1 Gaps → V1.1 Phase Mapping

| Gap | V1 state | V1.1 phase |
| --- | -------- | ---------- |
| Multi-provider tool calling | OpenAI implemented; Gemini/Groq/Anthropic stubs | Phase 1 |
| Provider tool unit tests | OpenAI only | Phase 1 |
| RAG per-request provider | Global `LLM_PROVIDER` | Phase 2 |
| Chat toggles (`use_web_search`, `use_documents`) | Not in schema | Phase 3 |
| Streaming + tools | Tools skipped on stream path | Phase 4 |
| Streaming RAG | Non-streaming only | Phase 5 |

---

## 8. Fixes Applied During Audit

| Fix | Reason |
| --- | ------ |
| `tests/conftest.py`: `os.environ.setdefault("CHAT_STREAMING_ENABLED", "true")` | Local `.env` had `CHAT_STREAMING_ENABLED=false`, causing 9 streaming test failures (503). Test isolation fix only — no product default change. |

---

## 9. Blockers / Risks for Phase 1

| Item | Severity | Notes |
| ---- | -------- | ----- |
| Gemini/Groq/Anthropic tool stubs | Expected | Phase 1 primary deliverable |
| No per-provider tool unit tests (except OpenAI) | Expected | Phase 1 test hardening |
| Coverage 87.99% (down 0.26 pp from Phase 13) | Low | Still above 80% gate |
| Staging smoke | Info | Same as V1 Phase 13 — pending credentials |

**No P0/P1 blockers** for starting Phase 1 (V1.1a).
