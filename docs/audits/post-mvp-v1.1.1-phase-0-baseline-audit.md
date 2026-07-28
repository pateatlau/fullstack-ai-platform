# Post-MVP V1.1.1 — Phase 0 Baseline Audit

**Date:** 2026-07-22
**Agent run:** Cursor implementation agent (Phase 0 readiness audit)
**Prerequisite:** V1.1 Phase 6 Complete (verified 2026-07-22 in `docs/plans/post-mvp-v1.1-implementation-plan.md`)

---

## 1. Quality gate results

### Backend (`backend-python/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `make lint` | **Pass** | Ruff — all checks passed |
| `make format-check` | **Pass** | 158 files already formatted |
| `make typecheck` | **Pass** | Pyright — 0 errors |
| `make test-cov` | **Pass** | **403 passed**, **86.14%** coverage on `app/`, **19.96s** |
| `make eval` | **Pass** | **5/5** passed |

**Prerequisite:** Local Postgres required (`DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot`). Started via `docker compose --profile python up postgres -d` before `make test-cov`. Without Postgres, 1 integration test fails (connection refused) and counts drop.

### Frontend (`frontend/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `npm run lint` | **Pass** | 1 warning: `ChatPage.tsx:588` exhaustive-deps on `useMemo` (pre-existing) |
| `npm run format:check` | **Pass** | Prettier clean |
| `npm test -- --run` | **Pass** | **122 passed** (24 files), **3.37s** |
| `npm run build` | **Pass** | Vite production build succeeded |

### Eval CLI vs baseline (`.eval/eval-report.json`)

| Metric | Phase 6 baseline (2026-07-21) | Phase 0 re-run (2026-07-22) |
| ------ | ----------------------------- | --------------------------- |
| Passed / failed | 5 / 0 | 5 / 0 |
| Skipped | 0 | 0 |
| Timestamp | 2026-07-21T23:04:45Z | 2026-07-22T05:33:15Z |
| Retrieval mean latency | 27 ms | 15.5 ms |
| E2E mean latency | 148 ms | 134 ms |

Pass count unchanged; latency variance within normal local noise.

### Docker Compose smoke (recommended)

```bash
docker compose --profile python up -d --build
```

| Check | Result |
| ----- | ------ |
| `GET http://localhost:8000/api/health` | **200** — `status: ok`, `chat_streaming_enabled: true`, `tools_enabled: false`, `rag_enabled: false`, all four providers in capabilities |
| `GET http://localhost:8000/api/health/ready` | **200** — `db: ok` |
| `GET http://localhost/` (frontend) | **200** |

Image: `pgvector/pgvector:pg16` (postgres service).

### V1.1 regression spot-check

| Target | Result |
| ------ | ------ |
| `uv run pytest tests/test_unified_chat.py tests/test_chat_sessions.py -q` | **36 passed** |
| `npm test -- --run useChatStream ChatPage.sessionExpiry` | **4 passed** (3 files) |

---

## 2. Session API inventory

### Endpoints (router: `backend-python/app/routers/chat.py`)

| Method | Path | Lines |
| ------ | ---- | ----- |
| GET | `/api/chat/sessions` | 305 |
| POST | `/api/chat/sessions` | 320 |
| GET | `/api/chat/sessions/{session_id}` | 333 |

**No DELETE route.** Grep for `@router.delete` and `/api/chat/sessions` delete handlers: none.

### Store / service

| Layer | File | Delete support |
| ----- | ---- | -------------- |
| Store | `app/db/chat.py` (`SqlChatStore`) | `create_session`, `get_owned_session`, list/append — **no `delete_session`** |
| Service | `app/services/chat_service.py` | `create_session`, `get_session_transcript`, `list_sessions` — **no delete method** |

### Ownership

`SqlChatStore.get_owned_session` (lines 40–69) filters by `user_id` or `guest_id`; authenticated users also match linked guest sessions via `GuestIdentity.linked_user_id`. Mismatch returns `None` → `SessionNotFoundError` (404).

### DB cascade (ready for Phase 2, not wired)

`app/db/models.py`: FK `ondelete="CASCADE"` on `chat_messages`, `session_summaries`, `usage_events` referencing `chat_sessions.id`.

### Frontend

| File | Session API |
| ---- | ----------- |
| `frontend/src/api/chatClient.ts` | `listChatSessions`, `createChatSession`, `getChatSession` — GET/POST only |
| `frontend/src/pages/ChatPage.tsx` | Sidebar lists sessions; **no delete control** on saved session rows |

Note: `frontend/src/api/documentsClient.ts` has `DELETE` for **documents**, not chat sessions.

---

## 3. Title derivation (`_derive_title`)

**Location:** `ChatService._derive_title` in `app/services/chat_service.py` (lines 422–427).

```python
for message in request.messages:
    if message.role == "user":
        return message.content[:80]
return None
```

### When titles are set

| Path | Title behavior |
| ---- | -------------- |
| Chat turn creates new session (`_resolve_session` → `create_session`) | First user message content **[:80]** passed as `title` |
| `POST /api/chat/sessions` (`create_session` service, line 908) | `create_session(user_id=...)` with **no title arg** → **`title: null`** |
| First message in existing session | **No title update** (gap for V1.1.1 Phase 3) |

Unified chat uses shared `ChatService` session resolution — same title behavior.

### Frontend null-title display

| Context | Fallback copy |
| ------- | ------------- |
| Active session header (`ChatPage.tsx` ~572) | `'New conversation'` |
| Saved sidebar rows (`ChatPage.tsx` ~598) | `'New conversation'` |
| Raw `{session.title}` in list buttons (~779, ~829) | Renders empty/null as blank in DOM (mapped sessions use fallback in `savedSessions` memo) |

---

## 4. Auth expiry flow

| Component | File | Behavior |
| --------- | ---- | -------- |
| Auth state | `frontend/src/context/AuthContext.tsx` | On mount, expired JWT cleared via `readStoredSession()`; sets `sessionExpired: true` (lines 80–82, 92) |
| Invalid token handler | same | `handleInvalidAccessToken()` clears token, drops to guest, sets `sessionExpired` (lines 127–133) |
| Banner | `frontend/src/components/PageBanner.tsx` | "Your session expired. Sign in again…" with Dismiss (lines 18–33) |
| Chat page | `frontend/src/pages/ChatPage.tsx` | Uses `PageBanner`; handles `invalid_access_token` on completion (~190) and stream paths (~378, ~398) |
| Backend | `app/core/security.py` | Emits `code="invalid_access_token"` |
| Tests | `frontend/src/pages/ChatPage.sessionExpiry.test.tsx`, `AuthContext.test.tsx` | Coverage for mount expiry and stream error |

### Gaps vs V1.1.1 Phase 4

- No centralized `ProtectedRoute` component.
- `/documents` shows inline `DocumentsLoginPrompt` when unauthenticated — no redirect to `/`.
- Session expiry banner on chat only; documents page does not redirect on expiry mid-visit.

---

## 5. Routing inventory

**File:** `frontend/src/App.tsx` (lines 11–14)

| Route | Component | Auth |
| ----- | ----------- | ---- |
| `/` | `ChatPage` | Public (guest + authenticated) |
| `/documents` | `DocumentsPage` | Inline `DocumentsLoginPrompt` when guest (`DocumentsPage.tsx` line 98) |

**No catch-all (`*`) route** — unknown paths render blank SPA shell (V1.1.1 Phase 5 gap).

---

## 6. Rate limits, guest quota, upload settings

From `app/core/config.py`:

| Setting | Current default | V1.1.1 Phase 1 target |
| ------- | --------------- | --------------------- |
| `guest_daily_message_quota` | **20** | Unchanged |
| `rate_limit_anonymous_per_minute` | **30** | Review for demo profile |
| `rate_limit_authenticated_per_minute` | **120** | Review for demo profile |
| `document_upload_max_bytes` | **10_485_760** (10 MB) | Unchanged; add daily count quota in Phase 1 |
| `default_max_tokens` | **None** (optional global) | — |
| Guest output token cap | **None** | `guest_max_output_tokens` in Phase 1 |
| Daily upload count quota | **None** | Phase 1 |

Enforcement: `QuotaService` (`app/services/quota_service.py`), `RateLimitMiddleware` (`app/middleware/rate_limit.py`).

---

## 7. Provider error normalization + frontend display

### Backend (`normalize_chat_error` in `app/services/chat_service.py`, lines 241–268)

| Normalized code | Class | HTTP |
| --------------- | ----- | ---- |
| `provider_timeout` | `ProviderTimeoutError` | 504 |
| `provider_rate_limited` | `ProviderRateLimitedError` | 429 |
| `provider_error` | `ProviderError` | 502 |

Call sites: `ChatService`, `UnifiedChatService`, `ToolChatService`, `app/routers/rag.py`. Web search tool path uses same normalization via tool chat service.

### Frontend gaps vs V1.1.1 Phase 7

- **No `friendlyErrors.ts`** — errors shown via `error.message` from API (`ChatPage.tsx` SET_ERROR / STREAM_ERROR paths, lines ~198–202, ~385–411).
- Connection errors mapped via `toConnectionErrorMessage()` (~57–60).
- Stream error frames may surface backend message text directly in message bubble (`chatReducer` STREAM_ERROR).
- Provider codes (`provider_timeout`, etc.) stored in reducer state but not mapped to user-friendly copy.

---

## 8. Loading / empty-state UX baseline

| Surface | File(s) | Current pattern |
| ------- | ------- | --------------- |
| Session list loading | `ChatPage.tsx` ~810–817 | Pulse skeleton (`animate-pulse` bars) |
| Transcript loading | `ChatPage.tsx` ~849–852, ~921 | Text: "Loading conversation…" |
| Web search in progress | `StreamingIndicator.tsx`, `ChatPage.tsx` ~484–489 | Variant `searching_web` — "searching web…" |
| Document retrieval | same | Variant `searching_documents` — "searching docs…" |
| Document list | `DocumentList.tsx` ~65–68 | Text: "Loading documents…" |
| Document upload | `DocumentUpload.tsx` ~82, ~86 | Button "Uploading…" |
| Empty sessions | `ChatPage.tsx` ~837–844 | "No saved conversations yet" + contextual subcopy |
| Empty documents | `DocumentList.tsx` ~69–70 | "No documents yet. Upload one to get started." |
| Null session titles | `ChatPage.tsx` ~572, ~598 | Fallback: **"New conversation"** |

Baseline for V1.1.1 Phases 6 (loading) and 8 (empty states).

---

## 9. Nine V1.1.1 gap inventory (current → target)

| # | Gap | Current state | V1.1.1 target | Code references |
| - | --- | ------------- | --------------- | --------------- |
| 1 | Delete session | GET/POST sessions only; DB cascade exists; no store/router/UI delete | DELETE API + confirm UI + post-delete selection | `chat.py` router; `SqlChatStore`; `ChatPage.tsx` sidebar |
| 2 | Session titles | 80-char slice at session **create** only; POST empty session → null; no update on first message | ~50 char trimmed title on **first persisted user message** when null | `_derive_title`; `create_session` line 908 |
| 3 | Expired session on routes | Banner on chat; documents inline login | `ProtectedRoute` for `/documents`; redirect to `/` + banner | `AuthContext.tsx`; `DocumentsLoginPrompt.tsx` |
| 4 | Unknown URLs | Two routes only; blank shell for unknown paths | Branded `NotFoundPage` catch-all | `App.tsx` |
| 5 | Public demo cost | Rate limit 30/120; guest 20 msgs/day; 10 MB upload; no token/upload count caps | `guest_max_output_tokens`, upload quotas, ops docs | `config.py`; `QuotaService` |
| 6 | Loading feedback | Ad-hoc pulse skeletons and text labels | Shared `LoadingIndicator` component | `ChatPage.tsx`, `DocumentList.tsx`, `DocumentUpload.tsx` |
| 7 | Provider errors in UI | Backend normalized; frontend shows raw/generic API messages | `friendlyErrors.ts` mapping with retry hints | `ChatPage.tsx`; `chatReducer.ts` |
| 8 | Mobile polish | Responsive Tailwind present; no formal checklist | Checklist-driven CSS fixes only | Various pages |
| 9 | Empty screens | Partial copy ("No saved conversations yet", minimal documents empty) | Shared `EmptyState` with CTAs | `ChatPage.tsx`; `DocumentList.tsx` |

---

## 10. V1.1.1 locked decisions acknowledgment

Reviewed `docs/plans/post-mvp-v1.1.1-implementation-plan.md` **V1.1.1 Locked Decisions** table. **Agreed** — no changes requested in Phase 0:

- Single V1.1.1 release, phases 0 → 10
- DELETE session API style; post-delete auto-select / new session
- Rule-based ~50 char title from first user message (not AI)
- `ProtectedRoute` for `/documents` only; `/` public
- Session expiry redirect to `/` + `PageBanner`
- Branded 404 catch-all
- Config-driven demo protection
- Shared `LoadingIndicator` and `EmptyState`
- Provider friendly error mapping
- Mobile checklist-only fixes
- After V1.1.1: freeze V1.x; begin V2

---

## 11. V1.1 unified chat regression baseline

Confirmed unchanged (audit only — no code modifications):

| Area | Status |
| ---- | ------ |
| Feature flags | `RAG_ENABLED`, `TOOLS_ENABLED`, `CHAT_STREAMING_ENABLED` — defaults unchanged |
| Request toggles | `use_web_search`, `use_documents` on chat request schema and `ChatPage` composer |
| Guest policy | Guests denied tools and document grounding (existing tests pass) |
| Generic RAG framework | `rg` — no `UnifiedChatService` / toggle refs in `app/ai/rag/` |
| Orchestration | Single `UnifiedChatService` pipeline; 36 unified/session tests pass |
| Provider parity | Health endpoint reports all four providers with tool calling support |

---

## 12. Blockers / risks for Phase 1

| Item | Severity | Notes |
| ---- | -------- | ----- |
| Local Postgres for full backend test suite | Ops | Document in README/dev setup; CI provides DB |
| Pre-existing frontend lint warning | Low | `ChatPage.tsx:588` exhaustive-deps — non-blocking |
| No code fixes required in Phase 0 | — | All gates pass with Postgres running |

---

## 13. Fixes applied during audit

**None** (code). Infrastructure only: started Postgres and Docker Compose stack to run quality gates and smoke tests accurately.
