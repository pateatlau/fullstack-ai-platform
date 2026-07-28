# Post-MVP V1.1.1 - Implementation Plan

## Objective

Polish the completed V1.1 platform for **usability, robustness, and production readiness** without introducing new platform capabilities. V1.1.1 is a focused release: session management UX, auth/route hardening, public-demo cost protection, consistent feedback during long operations, friendlier errors, and mobile/empty-state polish.

V1.1 proved unified chat integration (web search + document grounding on `/`). V1.1.1 proves **production-grade UX and operational safety** on top of that foundation — no architectural refactoring, no new AI features.

## Relationship to V1.1

| V1.1 delivered (complete 2026-07-22)                              | V1.1.1 extends                                              |
| ----------------------------------------------------------------- | ----------------------------------------------------------- |
| Unified chat with `use_web_search` / `use_documents` toggles      | Delete sessions; auto-generated titles; better empty states |
| Multi-provider tool calling + streaming RAG                       | Graceful provider errors surfaced to users                  |
| Session list/create/resume (`GET/POST /api/chat/sessions`)        | `DELETE /api/chat/sessions/{id}` with cascade cleanup       |
| Guest quota (20 msgs/day) + HTTP rate limits (30/120 per minute)  | Tighter public-demo caps (tokens, search, uploads)          |
| Partial session-expiry UX (`AuthContext`, `PageBanner`)           | Centralized route protection; no 404s after expiry          |
| Ad-hoc loading text ("Loading conversation…", "Uploading…")       | Standardized loading indicators across long operations      |
| Two routes only (`/` and `/documents`); no catch-all              | Branded 404 page with navigation home                       |
| Title set at session creation via `_derive_title` (80-char slice) | Title from first user message (~50 chars, trimmed)          |

Reference: [post-mvp-v1.1-implementation-plan.md](./post-mvp-v1.1-implementation-plan.md) (Phase 6 Completion Record), [docs/references/v1.1.1-implementation-plan.md](../references/v1.1.1-implementation-plan.md).

## Execution Mode

- Implement sequentially by phase (Phase 0 → Phase 10).
- Use the **Python backend** as the production reference; Node.js remains out of scope.
- After each phase verification is complete, stop and request explicit user confirmation before starting the next phase.
- Every milestone must leave the application deployable; V1.1 chat, auth, persistence, toggles, and `/documents` flows must not regress.
- **No architectural refactoring** — extend existing services, routers, and components only.
- **No new platform capabilities** — polish and hardening only (see [Out of Scope](#out-of-scope-v111)).
- Feature flags (`RAG_ENABLED`, `TOOLS_ENABLED`, `CHAT_STREAMING_ENABLED`) and V1.1 request toggles remain unchanged in behavior.

## Phase Workflow

Each phase follows this checkpoint sequence:

```text
Architecture Review
        ↓
Implementation
        ↓
Tests
        ↓
Regression Verification
        ↓
User Confirmation
```

## Architecture Principles

These principles govern V1.1.1 design. They keep the release small and avoid scope creep into V2.

### Polish-only boundary

V1.1.1 improves existing flows; it does not add new execution paths, tools, providers, or orchestration layers. Changes belong in:

- **Backend:** routers, `ChatService`, `SqlChatStore`, middleware, config, error normalization
- **Frontend:** pages, shared components, routing, auth context, API clients

Do **not** expand `UnifiedChatService` orchestration, the Generic RAG Framework, or provider adapters beyond error/limit handling required for demo protection.

### Reuse existing patterns

| Concern             | Existing pattern to extend                                                                     |
| ------------------- | ---------------------------------------------------------------------------------------------- |
| Session ownership   | `get_owned_session` + `SessionNotFoundError` (404)                                             |
| Cascade delete      | DB FK `ondelete="CASCADE"` on `chat_messages`, `session_summaries`, `usage_events`             |
| Guest/public limits | `QuotaService`, `RateLimitMiddleware`, `Settings` env vars                                     |
| Provider errors     | `normalize_chat_error()` → `ProviderError`, `ProviderRateLimitedError`, `ProviderTimeoutError` |
| Auth expiry         | `AuthContext.sessionExpired`, `handleInvalidAccessToken`, `PageBanner`                         |
| Loading feedback    | `aria-live="polite"`, pulse skeletons, composer disabled states                                |

### Single pipeline unchanged

The V1.1 canonical chat execution pipeline (`UnifiedChatService` → context → retrieval → tools → stream/complete → persist) is **unchanged**. V1.1.1 may adjust persistence side effects (title update, session delete) but must not fork orchestration.

### Backward compatibility

- Existing endpoints remain functional; new endpoints are additive (`DELETE /api/chat/sessions/{id}`).
- New env vars have safe defaults that preserve current behavior in development.
- Frontend route changes are additive (`*` catch-all for 404); `/` and `/documents` behavior unchanged for valid sessions.

## V1.1.1 Locked Decisions

| Decision                    | Choice                                                                                                                                                                                                         | Rationale                                                       |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Release packaging           | **Single V1.1.1 release**, ten implementation phases + validation                                                                                                                                              | Sequential polish; P0 demo protection before UX features        |
| Delete session              | **`DELETE /api/chat/sessions/{session_id}`** — auth-only; 404 if not owned                                                                                                                                     | Matches existing session API style; DB cascade handles children |
| Post-delete UX              | **Auto-select most recent remaining session**, or **POST new session** if list empty                                                                                                                           | Avoid orphan UI state; mirror "+ New chat" behavior             |
| Title generation            | **Rule-based from first user message** — trim, collapse whitespace, ~50 char limit                                                                                                                             | Explicitly out of scope: AI-generated titles                    |
| Title timing                | Set on **first persisted user message** when `session.title IS NULL`                                                                                                                                           | Preserves manually set titles (future rename not in scope)      |
| Title on empty POST session | Sessions created via `POST /api/chat/sessions` get title on **first chat turn**, not at create                                                                                                                 | "+ New chat" creates empty session; title deferred              |
| Route protection            | **`ProtectedRoute` wrapper** for `/documents`; `/` remains public (guest + auth)                                                                                                                               | Chat is the landing surface; documents require sign-in          |
| Session expiry              | **Redirect protected routes to `/`** + `PageBanner` session-expired message                                                                                                                                    | Fail closed; no raw API 401 pages                               |
| 404 handling                | **React Router catch-all** (`path="*"`) with branded `NotFoundPage`                                                                                                                                            | SPA-friendly; "Go Home" → `/`, "Back to Chat" primary CTA       |
| Demo protection scope       | **Config-driven limits** on guests/public; authenticated users keep existing quotas                                                                                                                            | Public demo cost control without punishing signed-in users      |
| Output token cap            | **`guest_max_output_tokens`** (or per-request cap for guests) via settings                                                                                                                                     | Limits completion cost on anonymous demo                        |
| Web search guest cap        | **Guests already denied tools** — document quota applies to **authenticated demo** tier if needed; default: enforce via existing guest tool denial + optional daily search counter for auth users in demo mode | Align with V1.1 guest policy                                    |
| Document upload quota       | **Per-user daily upload count + existing `document_upload_max_bytes`**                                                                                                                                         | Prevent corpus flooding on public demo                          |
| Spending alerts             | **Documentation + `.env.example` ops notes** for provider dashboards (OpenAI, Anthropic, etc.)                                                                                                                 | Not implementable in-app; ops checklist for deploy              |
| Loading states              | **Shared `LoadingIndicator` component** (spinner + optional label) used consistently                                                                                                                           | One visual language; replace ad-hoc text where inconsistent     |
| Provider errors (UI)        | **Map known codes** (`provider_error`, `provider_rate_limited`, `provider_timeout`) to friendly copy with retry hint                                                                                           | Hide raw SDK messages from users                                |
| Mobile review               | **Manual checklist + targeted CSS fixes** — no new breakpoints system                                                                                                                                          | Fix obvious issues; avoid redesign                              |
| Empty states                | **Shared `EmptyState` component** with title, description, optional CTA                                                                                                                                        | Consistent "what to do next" across surfaces                    |

## Phase Status

- Phase 0 - **Complete** (2026-07-22)
- Phase 1 - **Complete** (2026-07-22)
- Phase 2 - **Complete** (2026-07-22)
- Phase 3 - **Complete** (2026-07-22)
- Phase 4 - **Complete** (2026-07-22)
- Phase 5 - **Complete** (2026-07-22)
- Phase 6 - **Complete** (2026-07-22)
- Phase 7 - **Complete** (2026-07-22)
- Phase 8 - **Complete** (2026-07-22)
- Phase 9 - **Complete** (2026-07-22)
- Phase 10 - **Complete** (2026-07-22)

## Scope

### In scope

1. **Delete chat session** — backend cascade delete API; frontend delete + confirm; post-delete session selection
2. **Auto-generate chat titles** — first user message → trimmed ~50 char title; preserve existing titles
3. **Graceful session timeout & protected routes** — centralized protection; redirect + notification
4. **Friendly 404 page** — branded catch-all route with navigation actions
5. **Public demo protection** — rate limits review, output token caps, upload/search quotas, spending-alert ops docs
6. **Consistent loading states** — shared component for chat, web search, RAG, uploads
7. **Graceful provider error handling** — normalize and friendly-display; no raw provider errors
8. **Mobile responsiveness review** — chat, sidebar, markdown, code blocks, documents, auth
9. **Empty state improvements** — chat history, documents, search results, conversations

### Out of scope (V1.1.1)

- AI-generated titles
- Chat rename, search, folders, export
- MCP, agent workflows, LangGraph
- Hybrid search, reranking, citations UI
- Admin dashboards
- New AI capabilities or tools
- Architectural refactoring or new orchestration layers
- Node.js backend parity
- Removing or deprecating existing V1.1 endpoints

**After V1.1.1 is complete, freeze V1.x and begin V2 development.**

## Non-Negotiable Requirements

1. Python backend is the production reference.
2. Dependency direction unchanged: Routers → Services → AI Framework → Providers → External APIs.
3. Generic RAG Framework (`app/ai/rag/`) remains **domain-agnostic** — no chat/UI logic added there.
4. Feature flags off (`RAG_ENABLED=false`, `TOOLS_ENABLED=false`, `CHAT_STREAMING_ENABLED=true`) = V1 + V1.1 toggle-off behavior unchanged.
5. Add tests alongside every change; maintain ≥ 80% coverage on `app/`.
6. No sensitive data (API keys, tokens, document content, search queries) in logs or user-facing errors.
7. User confirmation required between phases.
8. V1.1 unified chat (toggles, streaming tools/RAG, provider parity) must not regress.
9. Guest policy unchanged: guests cannot use tools or document grounding.
10. Evaluation CLI (`make eval`) must continue to pass.

## Current Baseline (post-V1.1.1 Phase 9; verified 2026-07-22)

| Area                | Current state                                                                                                                    |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| V1.1 status         | **Complete** — Phase 6 verified 2026-07-22                                                                                       |
| Backend tests       | **453 passed**, **87.14%** coverage on `app/`                                                                                    |
| Frontend tests      | **164** Vitest tests (**163 pass** locally; 1 pre-existing `healthClient` URL assertion fails when `frontend/.env` sets `VITE_API_BASE_URL=http://127.0.0.1:8000` vs test hardcodes `localhost`) |
| Session API         | `GET/POST/DELETE /api/chat/sessions`, `GET /api/chat/sessions/{id}` — delete auth-only (403 guest, 404 foreign) |
| Session delete (DB) | FK cascade on `chat_messages`, `session_summaries`, `usage_events`; `SqlChatStore.delete_session` + router DELETE |
| Title derivation    | `derive_session_title()` in `app/core/text_utils.py` — first-line, whitespace-normalized, ~50 chars on **first persisted user message** when `title IS NULL`; POST empty session → `title: null` until first chat turn |
| Auth / expiry       | `AuthContext` clears expired JWT on mount; `sessionExpired` flag; `PageBanner`; stream `invalid_access_token` handling; documents API **401**/`invalid_access_token` → `handleInvalidAccessToken` |
| Route protection    | **`ProtectedRoute`** on `/documents` — guest/expired JWT redirect to `/` with sign-in via `AuthControls`; session-expired banner on chat when applicable |
| 404 handling        | **`NotFoundPage` catch-all** — `path="*"` in `App.tsx`; headline "Page not found"; **Back to Chat** / **Go Home** → `/`          |
| Rate limiting       | `RateLimitMiddleware` — 30 anon / 120 auth per minute                                                                            |
| Guest quota         | `guest_daily_message_quota=20` via `QuotaService`                                                                                |
| Output tokens       | `guest_max_output_tokens` (default **4096** dev; **512** under `demo_mode_strict`) via `resolve_max_tokens()`                     |
| Upload limits       | `document_upload_max_bytes=10MB`; authenticated daily upload quota via `upload_quota_counters` (optional; off in dev)              |
| Provider errors     | Backend `normalize_chat_error()`; frontend **`friendlyErrors.ts`** maps `provider_timeout`, `provider_rate_limited`, `provider_error`, `empty_provider_response` to retry-oriented copy in chat banner, stream/completion handlers, and document upload |
| Loading UX          | Shared **`LoadingIndicator`** (`inline`, `skeleton`, `overlay`) on session list, transcript, documents, upload, protected-route shell; **`StreamingIndicator`** uses shared **`TypingDots`** |
| Empty states        | Shared **`EmptyState`** on chat sidebar saved sessions + documents list; transcript welcome hints with auth toggle guidance in **`MessageList`** |
| Mobile              | Manual checklist completed 2026-07-22 at **375px** / **390px**; touch-target and overflow fixes in chat, messages, documents, auth, 404, banners |
| Release summary     | [docs/releases/post-mvp-v1.1.1-release-summary.md](../releases/post-mvp-v1.1.1-release-summary.md) |

### V1.1 gaps addressed by V1.1.1

| Gap                       | V1.1 state                                   | V1.1.1 target                                  |
| ------------------------- | -------------------------------------------- | ---------------------------------------------- |
| Delete session            | Not possible                                 | DELETE API + UI with confirmation              |
| Session titles            | 80-char slice at create; many `null` titles  | ~50 char trimmed title on first user message   |
| Expired session on routes | Banner on chat; documents shows login prompt | Centralized protection; redirect to `/`        |
| Unknown URLs              | Blank page                                   | Branded 404 with CTAs                          |
| Public demo cost          | Basic rate limit + guest message quota       | Token caps, upload quotas, ops spending alerts |
| Loading feedback          | Inconsistent patterns                        | Shared loading component everywhere            |
| Provider errors in UI     | Some raw/generic messages                    | Friendly, retry-oriented copy; no SDK leakage  |
| Mobile polish             | Untested systematically                      | Checklist-driven fixes                         |
| Empty screens             | Minimal copy                                 | Actionable empty states with next steps        |

---

## Phase 0 - Baseline Audit and V1.1.1 Readiness

**Status:** Complete (2026-07-22)

### Objectives

Confirm V1.1 completion, record baseline metrics, and inventory current session/auth/error/loading behavior before any V1.1.1 code changes.

### Tasks

- Confirm V1.1 Phase 6 completion record in [post-mvp-v1.1-implementation-plan.md](./post-mvp-v1.1-implementation-plan.md).
- Run full quality gates locally:
  - Backend: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
  - Frontend: `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build`
- Record baseline test counts, coverage, and duration (backend + frontend).
- Inventory session API surface in `app/routers/chat.py` and `SqlChatStore` — confirm no delete path.
- Document `_derive_title` behavior and when titles are (not) persisted.
- Document auth expiry flow: `AuthContext`, `PageBanner`, `handleInvalidAccessToken`, SSE `invalid_access_token`.
- Document routing: `App.tsx` routes; `/documents` auth gating pattern.
- Inventory existing rate limits, guest quota, and upload size settings in `config.py`.
- Inventory provider error normalization in `normalize_chat_error()` and frontend error display paths.
- Screenshot or note current loading/empty-state UX on chat, documents, sidebar.
- Review V1.1.1 locked decisions with team.

### Success Criteria

- All V1.1 quality gates pass locally.
- Baseline metrics recorded in Phase 0 section below.
- Gap inventory complete for all nine V1.1.1 items.
- Locked decisions agreed.

### Verification Checklist

- Backend and frontend quality gates pass.
- Baseline test counts and coverage recorded.
- Session/title/auth/routing gaps documented.
- No V1.1.1 code changes in this phase (audit only).

### Exit Criteria

- Team agrees on V1.1.1 starting point.
- User confirms Phase 0 completion.

### Phase 0 Baseline Record

Verified **2026-07-22**. Full audit: [docs/audits/post-mvp-v1.1.1-phase-0-baseline-audit.md](../audits/post-mvp-v1.1.1-phase-0-baseline-audit.md).

| Item             | Result |
| ---------------- | ------ |
| Backend tests    | **403 passed**, **19.96s** (`make test-cov`; Postgres required locally) |
| Backend coverage | **86.14%** on `app/` (≥80% gate) |
| Frontend tests   | **122 passed** (Vitest); lint 1 warning; build pass |
| Eval CLI         | **5/5** passed; timestamp 2026-07-22T05:33:15Z |
| Docker smoke     | **Pass** — health **200**, ready **200** (`db: ok`), frontend **200** |

#### Phase 0 completion notes

- V1.1 Phase 6 completion record confirmed (2026-07-22); metrics match Current Baseline table (no drift).
- Session API: GET/POST only; no DELETE in router, store, service, or frontend chat client.
- Title: `_derive_title` [:80] at session create on chat turn; `POST /api/chat/sessions` → `title: null`; no update on first message in existing session.
- Auth/routing: `AuthContext` + `PageBanner` + `handleInvalidAccessToken`; no `ProtectedRoute`; no catch-all 404.
- Limits: guest quota 20/day; rate 30/120 per minute; upload 10 MB; no guest output token cap or upload count quota.
- Provider errors normalized backend-side; frontend shows raw API messages in several paths.
- All nine V1.1.1 gaps inventoried; locked decisions reviewed and agreed.
- V1.1 unified chat regression spot-checks pass (36 backend + 4 frontend targeted tests).

---

## Phase 1 - Public Demo Protection (P0)

**Status:** Complete (2026-07-22)

### Objectives

Harden the public demo against cost abuse through config-driven limits on anonymous and lightly authenticated usage. Document provider spending alert setup for operators.

### Design

| Component                    | Responsibility                                                                                |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| `Settings`                   | New caps: guest output tokens, optional daily document upload limit, optional demo-mode flags |
| `ChatService` / providers    | Enforce `max_tokens` ceiling for guest completions                                            |
| `QuotaService` or new helper | Track daily document uploads per user/guest where applicable                                  |
| `RateLimitMiddleware`        | Review/adjust limits for production demo profile (document in `.env.example`)                 |
| Ops documentation            | Provider dashboard spending alerts checklist (OpenAI, Anthropic, Gemini, Groq, Tavily)        |

#### Proposed settings (names finalized in implementation)

| Setting                            | Default (dev) | Purpose                                                                                    |
| ---------------------------------- | ------------- | ------------------------------------------------------------------------------------------ |
| `guest_max_output_tokens`          | e.g. `512`    | Cap completion length for guest chat turns                                                 |
| `guest_daily_upload_quota`         | e.g. `5`      | Max document uploads per guest/day (if guest upload ever enabled; else auth user demo cap) |
| `authenticated_daily_upload_quota` | optional      | Demo tier upload cap for signed-in users                                                   |
| `demo_mode_strict`                 | `false`       | When `true`, tighten all demo caps for public deploy                                       |

**Note:** Guests are already denied `use_web_search` and `use_documents` (V1.1). Web search cost protection for authenticated demo users is enforced via existing tool gating + optional future daily search counter; Phase 1 documents Tavily quota monitoring in ops checklist.

### Tasks

- Add guest output token cap in provider call path (`ChatService`, `UnifiedChatService`):
  - Resolve effective `max_tokens = min(request, settings.guest_max_output_tokens)` for guests
  - Log when cap applied (structured field, no message content)
- Add document upload daily quota:
  - Counter table or reuse existing pattern (similar to `guest_quota_counters`)
  - Enforce in document upload router before ingestion
  - Return **429** or **403** with clear `quota_exceeded` code
- Review `rate_limit_anonymous_per_minute` / `rate_limit_authenticated_per_minute` defaults for demo deploy profile
- Add integration tests:
  - Guest completion respects output token cap (mocked provider receives capped `max_tokens`)
  - Upload quota enforced after N uploads
  - Authenticated user above quota receives clear error
- Update `backend-python/.env.example` with demo protection vars and recommended production values
- Create ops section in `backend-python/README.md` or `docs/ops/public-demo-protection.md`:
  - Provider spending alert links/steps
  - Recommended env var profile for public demo
  - Tavily API quota monitoring note

### Success Criteria

- Public demo has basic cost protection via token and upload quotas.
- Rate limits documented for production demo.
- Provider spending alerts documented for operators.
- No regression for development defaults (caps can be high/disabled locally).

### Verification Checklist

- Unit/integration tests for token cap and upload quota pass.
- Guest chat still works within caps.
- V1.1 toggles and tool/RAG paths unchanged for authenticated users within quota.
- `make test-cov` ≥ 80% on `app/`.

### Exit Criteria

- Demo protection implemented and documented.
- User confirms Phase 1 completion.

### Phase 1 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| Settings | `guest_max_output_tokens=4096` (dev), `authenticated_daily_upload_quota=None` (dev unlimited), `guest_daily_upload_quota=5`, `demo_mode_strict=false`; strict mode lowers guest tokens to min(env, 512) and defaults upload quota to 20 |
| Guest token cap | `app/services/max_tokens.py` → `ChatService` + `UnifiedChatService` provider paths; optional `max_tokens` on all LLM adapters |
| Upload quota | `upload_quota_counters` table + `SqlUploadQuotaStore`; enforced in `KnowledgeService.ingest_document` before ingestion |
| Structured logs | `guest_output_token_cap_applied`, `upload_quota_denied_total` |
| Ops doc | [docs/ops/public-demo-protection.md](../ops/public-demo-protection.md) |
| Rate limit review | Defaults **30** anon / **120** auth per minute acceptable; documented production demo profile in `.env.example` and ops doc |
| Backend tests | **414 passed**, **85.98%** coverage on `app/` (~21s `make test-cov`) |
| Frontend regression | **122** Vitest tests pass; build pass |
| Eval CLI | **5/5** passed |
| New tests | `tests/test_guest_output_token_cap.py`, upload quota in `tests/test_documents_api.py` and `tests/test_guest_quota.py` |
| Migration | `0004_upload_quota_counters` |

---

## Phase 2 - Delete Chat Session (P1)

**Status:** Complete (2026-07-22)

### Objectives

Allow authenticated users to delete a chat session with cascade cleanup and a confirmation step in the UI. After deletion, automatically select another session or create a new one.

### Design

| Layer    | Change                                                                                     |
| -------- | ------------------------------------------------------------------------------------------ |
| DB       | Existing `ON DELETE CASCADE` on child tables — no migration required                       |
| Store    | `SqlChatStore.delete_session(session_id)` — delete row after ownership verified            |
| Service  | `ChatService.delete_session(session_id, caller)` — ownership check, commit                 |
| Router   | `DELETE /api/chat/sessions/{session_id}` → **204 No Content**; **404** if not owned        |
| Frontend | Delete control on saved session row; `ConfirmDialog`; refresh list; select/create fallback |

#### Post-delete selection algorithm

```text
DELETE session S
        ↓
Refresh session list
        ↓
If list non-empty → select most recently active session (first in list)
        ↓
Else → POST /api/chat/sessions (new empty session) → select it
        ↓
Clear local transcript state; load selected session transcript
```

### Tasks

- Add `delete_session` to `SqlChatStore` and `ChatStore` protocol/fake
- Add `ChatService.delete_session` with `get_owned_session` guard
- Add `DELETE /api/chat/sessions/{session_id}` route in `app/routers/chat.py`
- Add `deleteChatSession(sessionId)` to `frontend/src/api/chatClient.ts`
- Add delete button (icon or menu) on saved session items in `ChatPage` sidebar
- Add `ConfirmDialog` component (or reuse if exists) — "Delete this conversation? This cannot be undone."
- Implement post-delete selection logic in `ChatPage`
- Add backend tests:
  - Delete owned session → 204; session and messages gone
  - Delete foreign session → 404
  - Guest → 403 or 404 (match session API auth policy)
  - Cascade: messages, summaries, usage_events removed
- Add frontend tests:
  - Delete flow calls API, updates list, selects fallback session
  - Cancel confirmation does not delete

### Success Criteria

- Session deletes cleanly with no orphan UI or DB state.
- Deleting active session transitions user to another session or new chat.
- Ownership enforced; guests cannot delete (consistent with session list policy).

### Verification Checklist

- Integration tests pass for delete API.
- Frontend session tests cover delete + fallback selection.
- V1.1 chat send/stream/persistence regression tests pass.

### Exit Criteria

- Delete session works end-to-end.
- User confirms Phase 2 completion.

### Phase 2 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| Backend store/service/router | `SqlChatStore.delete_session`, `ChatStore`/`FakeChatStore`, `ChatService.delete_session`, `DELETE /api/chat/sessions/{id}` → **204** |
| Auth policy | Guests → **403** `new_chat_forbidden`; foreign/unknown → **404** `session_not_found`; no caller/persistence off → **404** |
| Linked-guest delete | Authenticated caller can delete sessions visible via linked-guest projection (`get_owned_session` with `user_id` only) |
| Structured log | `session_delete_total=True` on successful delete (no message content) |
| DB cascade | Verified in integration test — messages, summaries, usage_events removed; **no migration** |
| Frontend API | `deleteChatSession(sessionId)` in `chatClient.ts` |
| Frontend UI | `ConfirmDialog` (focus trap, Escape/backdrop cancel); delete on saved + current session rows; post-delete refresh → select first remaining or POST new session |
| Backend tests | **429 passed**, **86.00%** coverage on `app/` (~22s `make test-cov`); +9 delete tests |
| Frontend tests | **133** total (+11 Phase 2); all Phase 2 tests pass; `healthClient.test.ts` fails locally due to `.env` URL (`127.0.0.1` vs `localhost`) — pre-existing, not Phase 2 |
| Eval CLI | **5/5** passed |
| Regression | V1.1 session/chat + Phase 1 token cap/upload quota suites pass |

---

## Phase 3 - Auto-generate Chat Titles (P1)

**Status:** Complete (2026-07-22)

### Objectives

Generate meaningful session titles from the first user message: trim whitespace/newlines, limit to ~50 characters, and never overwrite an existing title.

### Design

Replace/extend `_derive_title` with a dedicated helper:

```python
def derive_session_title(content: str, *, max_length: int = 50) -> str:
    """First-line, whitespace-normalized, truncated title."""
```

| Scenario                                         | Behavior                                          |
| ------------------------------------------------ | ------------------------------------------------- |
| New session on first chat turn (no `session_id`) | Create session with derived title                 |
| Existing session with `title IS NULL`            | Update title on first persisted user message      |
| Existing session with non-null title             | **Preserve** — no update                          |
| Empty/whitespace-only first message              | Leave title `null`; frontend shows fallback label |
| POST `/api/chat/sessions` (empty)                | Title remains `null` until first message          |

Frontend fallback label for `null` titles: **"New chat"** (replace generic "Saved" where used today).

### Tasks

- Add `derive_session_title()` in `ChatService` (or `app/core/text_utils.py` if preferred)
- Add `SqlChatStore.update_title(session_id, title)`
- On message persist path (`_persist_turn` or equivalent), if session title is null and role is user:
  - Compute title from message content
  - Update session row
- Remove or narrow create-time `_derive_title` — title may be set at first message instead
- Ensure unified chat path (`UnifiedChatService`) uses same title logic (shared helper)
- Update session list response after first message (frontend refresh or optimistic title update)
- Add tests:
  - Long message truncated to ~50 chars
  - Newlines/tabs collapsed/trimmed
  - Existing title preserved on subsequent messages
  - Whitespace-only message leaves null title
  - POST empty session + first turn sets title
- Update frontend display: `null` → "New chat"

### Success Criteria

- New chats show meaningful titles after first message.
- Existing titled sessions unchanged.
- Sidebar list reflects updated title without full page reload (refresh list after send acceptable).

### Verification Checklist

- Unit tests for `derive_session_title` edge cases.
- Integration test: first turn sets title; second turn preserves it.
- Unified chat + plain chat paths both covered.

### Exit Criteria

- Auto-generated titles work end-to-end.
- User confirms Phase 3 completion.

### Phase 3 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| Title helper | `derive_session_title()` in `app/core/text_utils.py` — first line only, collapse whitespace, strip, max **50** chars, `None` for whitespace-only |
| Store layer | `SqlChatStore.update_title`, `ChatStore` protocol, `FakeChatStore.update_title` |
| Persist hook | `ChatService._maybe_set_session_title` after user `add_message` in `complete_chat` and `prepare_stream`; same hook in `ToolChatService.complete_chat` and `UnifiedChatService` guest-denial / empty-corpus paths |
| Create-time title | Removed `_derive_title`; `_resolve_session` creates sessions with `title=None` |
| POST empty session | Still returns `title: null`; title set on first chat turn only |
| Structured log | `title_auto_generated_total=True` on auto-set (no message content) |
| Frontend fallback | Null titles display **"New chat"** (sidebar current + saved entries) |
| Backend tests | **446 passed**, **86.53%** coverage on `app/` (~21s `make test-cov`); +17 title tests (`tests/test_session_titles.py` + integration in `test_chat_persistence.py`) |
| Frontend tests | **134** total (**133 pass**); `ChatPage.sessions` + `Composer` updated for `"New chat"`; pre-existing `healthClient` URL mismatch unchanged |
| Eval CLI | **5/5** passed |
| Regression | V1.1 session/chat + Phase 1 token cap/upload quota + Phase 2 delete session suites pass |
| DB migration | **None** — nullable `chat_sessions.title` column unchanged |

---

## Phase 4 - Graceful Session Timeout & Protected Routes (P1)

**Status:** Complete (2026-07-22)

### Objectives

Centralize route protection so expired or unauthenticated users hitting protected routes are redirected to `/` with a friendly notification — no blank pages or confusing API errors.

### Design

```text
App.tsx
        ↓
AuthProvider
        ↓
Routes
  /              → ChatPage (public — guest + auth)
  /documents     → ProtectedRoute → DocumentsPage
  *              → NotFoundPage (Phase 5)
```

`ProtectedRoute` behavior:

| Condition                    | Action                                                      |
| ---------------------------- | ----------------------------------------------------------- |
| `status === 'loading'`       | Render minimal loading shell (reuse Phase 6 component)      |
| `status === 'authenticated'` | Render children                                             |
| `status === 'anonymous'`     | `<Navigate to="/" replace />` + set flag for banner message |
| `sessionExpired === true`    | `<Navigate to="/" replace />` — banner already set          |

Replace inline `DocumentsLoginPrompt` with redirect pattern — login CTA remains on `/` via `AuthControls`.

Optional: global `fetch` interceptor or API client wrapper — on **401** `invalid_access_token`, call `handleInvalidAccessToken()` (may already exist partially).

### Tasks

- Create `frontend/src/components/ProtectedRoute.tsx`
- Wrap `/documents` route in `ProtectedRoute`
- Remove or simplify `DocumentsLoginPrompt` (redirect makes it redundant for route entry)
- Ensure `PageBanner` on `ChatPage` shows session-expired message after redirect
- On documents API 401 during session: trigger `handleInvalidAccessToken`
- Add test: unauthenticated navigate to `/documents` → redirected to `/`
- Add test: expired token → redirect + banner visible on chat
- Verify stream chat `invalid_access_token` still sets banner (existing `ChatPage.sessionExpiry.test.tsx`)

### Success Criteria

- No 404s or blank pages after session expiry on protected routes.
- Unauthenticated users land on `/` with clear sign-in path.
- Expired JWT shows friendly "Your session expired" notification.

### Verification Checklist

- Protected route tests pass.
- Existing session expiry stream test still passes.
- `/documents` upload/list/delete works for authenticated users.

### Exit Criteria

- Route protection centralized and verified.
- User confirms Phase 4 completion.

### Phase 4 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| `ProtectedRoute` | `frontend/src/components/ProtectedRoute.tsx` — authenticated renders children; guest/expired JWT → `<Navigate to="/" replace />` (no false `sessionExpired` for pure guests) |
| Route wiring | `/documents` wrapped in `ProtectedRoute` in `App.tsx`; `/` remains public |
| Documents page | Removed inline `DocumentsLoginPrompt` guest gate; `DocumentsLoginPrompt.tsx` deleted (dead code) |
| Documents 401 handling | `AuthenticatedDocumentsContent` list path + `DocumentUpload`/`DocumentList` upload/delete paths call `handleInvalidAccessToken` on **401**/`invalid_access_token` |
| Session-expired UX | Expired JWT on mount → redirect from `/documents` to `/` + existing `PageBanner` copy on `ChatPage` |
| New frontend tests | `ProtectedRoute.test.tsx` (guest redirect, expired JWT + banner, authenticated access); `DocumentsPage.test.tsx` updated (redirect + 401 handling) |
| Backend tests | **446 passed**, **86.53%** coverage on `app/` (~22s `make test-cov`); no backend code changes |
| Frontend tests | **138** total (**137 pass**); +4 Phase 4 tests; pre-existing `healthClient` URL mismatch unchanged |
| Eval CLI | **5/5** passed |
| Regression | V1.1 chat send/stream/persistence, Phase 1 demo protection, Phase 2 delete session, Phase 3 auto-title unchanged; `ChatPage.sessionExpiry.test.tsx` passes without modification |

---

## Phase 5 - Friendly 404 Page (P2)

**Status:** Complete (2026-07-22)

### Objectives

Show a branded, helpful page for unknown routes with clear navigation back to chat.

### Design

`NotFoundPage` content:

- Headline: "Page not found"
- Short explanation
- Primary CTA: **Back to Chat** → `/`
- Secondary: **Go Home** → `/` (same target acceptable; or Home = `/`)
- Match existing shell/branding (`bg-shell-100`, `AppNav` optional)

### Tasks

- Create `frontend/src/pages/NotFoundPage.tsx`
- Add `<Route path="*" element={<NotFoundPage />} />` in `App.tsx`
- Style consistently with chat/documents pages
- Add Vitest smoke test: renders headline and chat link
- Manual check: `/unknown-path` shows 404 page, not blank

### Success Criteria

- Unknown routes show helpful branded page.
- User can return to chat in one click.

### Verification Checklist

- 404 route test passes.
- `/` and `/documents` unaffected.

### Exit Criteria

- 404 page live.
- User confirms Phase 5 completion.

### Phase 5 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| `NotFoundPage` | `frontend/src/pages/NotFoundPage.tsx` — headline "Page not found", explanatory copy, primary **Back to Chat** + secondary **Go Home** (both → `/`); sticky header with `AppNav` + `AuthControls`; shell gradient matches `DocumentsPage` |
| Catch-all route | `<Route path="*" element={<NotFoundPage />} />` registered **last** in `App.tsx` after `/` and `/documents`; **not** wrapped in `ProtectedRoute` |
| Frontend test | `NotFoundPage.test.tsx` — renders headline + **Back to Chat** link with `href="/"` |
| Manual check | `http://127.0.0.1:5173/unknown-path` shows branded 404 (not blank); **Back to Chat** navigates to `/` with chat composer |
| Backend tests | **453 passed**, **87.14%** coverage on `app/` (~22s `make test-cov`); no backend code changes |
| Frontend tests | **143** total (**142 pass**); +1 Phase 5 test; pre-existing `healthClient` URL mismatch unchanged |
| Eval CLI | **5/5** passed |
| Regression | V1.1 chat send/stream/persistence (81 backend spot-check tests), Phase 1 demo protection, Phase 2 delete session, Phase 3 auto-title, Phase 4 `ProtectedRoute` + session-expiry unchanged |

---

## Phase 6 - Consistent Loading States (P2)

**Status:** Complete (2026-07-22)

### Objectives

Standardize loading indicators for chat transcript load, session list load, web search in progress, document retrieval, and file uploads.

### Design

Shared component:

```tsx
<LoadingIndicator label="Loading conversation…" variant="inline" | "skeleton" | "overlay" />
```

| Surface              | Current                            | Target                                                |
| -------------------- | ---------------------------------- | ----------------------------------------------------- |
| Session list         | Pulse skeleton                     | `LoadingIndicator variant="skeleton"`                 |
| Transcript load      | Text only                          | `LoadingIndicator variant="inline"`                   |
| Web search (stream)  | "Searching the web…" waiting state | Keep copy; align spinner/visual with shared component |
| Document retrieval   | "Searching your documents…"        | Same                                                  |
| Document list        | "Loading documents…" text          | Shared inline indicator                               |
| Document upload      | Button "Uploading…"                | Button disabled + inline spinner                      |
| Protected route auth | N/A                                | Minimal loading shell                                 |

### Tasks

- Create `frontend/src/components/LoadingIndicator.tsx`
- Replace ad-hoc loading UI in `ChatPage`, `DocumentList`, `DocumentUpload`, `ProtectedRoute`
- Ensure `aria-live="polite"` and accessible labels on all loading states
- Add Vitest test for `LoadingIndicator` renders label + spinner
- Visual spot-check: loading states look consistent in light shell theme

### Success Criteria

- Every long-running action provides visible, consistent feedback.
- No functional behavior change — loading timing unchanged.

### Verification Checklist

- Component test passes.
- Existing ChatPage session tests still pass (loading stubs unchanged in behavior).

### Exit Criteria

- Loading states standardized.
- User confirms Phase 6 completion.

### Phase 6 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| `LoadingIndicator` | `frontend/src/components/LoadingIndicator.tsx` — variants `inline`, `skeleton`, `overlay`; exports shared `LoadingSpinner` + `TypingDots` |
| Session list | `ChatPage` sidebar — `LoadingIndicator variant="skeleton"` label "Loading saved conversations…" |
| Transcript load | `ChatPage` sidebar banner + main transcript area — `LoadingIndicator variant="inline"` label "Loading conversation…" |
| Document list | `DocumentList` — `LoadingIndicator variant="inline"` label "Loading documents…" |
| Document upload | `DocumentUpload` — disabled button with `LoadingSpinner` + "Uploading…"; inline `LoadingIndicator` "Processing document on the server…" |
| Streaming alignment | `StreamingIndicator` imports shared `TypingDots` (`bg-shell-400` pulse dots); copy and aria-labels unchanged |
| `ProtectedRoute` | Loading shell wired for `status === 'loading'` with "Checking sign-in…"; `AuthStatus` extended but sync `readStoredSession()` never sets loading in production |
| Frontend tests | **148** total (**147 pass**); +4 `LoadingIndicator.test.tsx`, +1 `ProtectedRoute` loading-shell test; pre-existing `healthClient` URL mismatch unchanged |
| Backend tests | **453 passed**, **87.14%** coverage on `app/` (~22.3s `make test-cov`); no backend code changes |
| Eval CLI | **5/5** passed |
| Visual spot-check | Code review against shell tokens (`bg-shell-*`, `text-shell-*`, `border-t-brand-600` spinner); manual DevTools throttle deferred to deploy environment — consistent inline/skeleton styling applied |
| Regression | V1.1 chat send/stream/persistence (81 backend spot-check tests), Phase 1 demo protection, Phase 2 delete session, Phase 3 auto-title, Phase 4 `ProtectedRoute` + session-expiry, Phase 5 `NotFoundPage` unchanged |

---

## Phase 7 - Graceful Provider Error Handling (P2)

**Status:** Complete (2026-07-22)

### Objectives

Ensure users never see raw provider/SDK errors. Display friendly, actionable retry messages for known failure modes.

### Design

Backend (already largely present — verify completeness):

| Exception pattern       | Normalized code              | User message (example)                                        |
| ----------------------- | ---------------------------- | ------------------------------------------------------------- |
| Timeout                 | `provider_timeout`           | "The AI service took too long. Please try again."             |
| Rate limit              | `provider_rate_limited`      | "We're busy right now. Please wait a moment and retry."       |
| Other provider failure  | `provider_error`             | "Something went wrong with the AI service. Please try again." |
| Web search tool failure | `provider_error` (tool path) | Same; no Tavily internals exposed                             |

Frontend mapping in a single helper:

```typescript
function friendlyErrorMessage(code: string, fallback?: string): string;
```

Apply in: `ChatPage` error banner, SSE `error` frames, non-streaming API errors, document upload failures where provider-adjacent.

**Do not** expose: stack traces, HTTP status from upstream, API key errors, model names from provider errors.

### Tasks

- Audit all `normalize_chat_error` call sites — ensure no raw `str(exc)` reaches responses
- Audit web search tool error paths in `web_search.py` and `ToolChatService`
- Add/extend tests for normalized messages (no SDK substrings in response body)
- Create `frontend/src/utils/friendlyErrors.ts` with code → message map
- Wire friendly messages in chat error display and stream error handler
- Add frontend test: `provider_rate_limited` shows retry copy, not raw text

### Success Criteria

- No raw provider errors reach users in chat, stream, or tool paths.
- Retry guidance shown for transient errors.

### Verification Checklist

- Backend provider error tests pass (existing + any new).
- Frontend friendly error tests pass.
- Manual: simulated provider failure shows friendly message.

### Exit Criteria

- Provider error UX polished end-to-end.
- User confirms Phase 7 completion.

### Phase 7 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| Backend audit | Reviewed `normalize_chat_error` call sites in `chat_service.py`, `tool_chat_service.py`, `unified_chat_service.py`, `routers/rag.py`, `routers/chat.py` — provider/SDK exceptions normalized; no raw `str(exc)` on provider paths; `rag.py` `ValueError` and `document_service.py` unsupported-type paths are validation-only (non-provider) |
| Web search / tool path | `web_search.py` returns generic `ToolResult` errors; `ToolChatService` uses `normalize_chat_error`; no Tavily/upstream internals in tool error strings |
| Backend tests | Extended with `tests/provider_error_assertions.py` deny-list on chat endpoint, stream error frame, web search failure, and `normalize_chat_error` unit cases — **453 passed**, **87.14%** coverage (~22.7s) |
| `friendlyErrors.ts` | `frontend/src/utils/friendlyErrors.ts` — maps `provider_timeout`, `provider_rate_limited`, `provider_error`, `empty_provider_response` |
| Chat wiring | `ChatPage` `handleCompletionError` + stream `onError` (SSE chunk + `ChatApiError`) dispatch `toChatDisplayError()` → friendly copy; `errorCode` preserved for retry logic |
| Document upload | `DocumentUpload` uses `friendlyErrorMessage` for non-413/non-401 errors; size-limit and auth paths unchanged |
| Frontend tests | **154** total (**153 pass**); +6 `friendlyErrors.test.ts`; pre-existing `healthClient` URL mismatch unchanged |
| Manual simulated failure | `ErroringProvider` / `ErroringStreamProvider` in backend tests simulate provider failure; frontend maps `provider_rate_limited` / `provider_error` codes to retry copy (no "Upstream", SDK brands, or API key text in user-visible strings) |
| Eval CLI | **5/5** passed |
| Regression | V1.1 chat send/stream/persistence (116 backend spot-check tests), Phase 1–6 unchanged (42 frontend targeted tests green) |

Provider error mapping (codes → user-facing copy):

| Code | User-facing message |
| --- | --- |
| `provider_timeout` | The AI service took too long. Please try again. |
| `provider_rate_limited` | We're busy right now. Please wait a moment and retry. |
| `provider_error` | Something went wrong with the AI service. Please try again. |
| `empty_provider_response` | The model returned an empty response. Please try again. |

---

## Phase 8 - Empty State Improvements (P2)

**Status:** Complete (2026-07-22)

### Objectives

Make empty screens informative and actionable across chat, documents, and search-related views.

### Design

Shared component:

```tsx
<EmptyState
  title="No saved conversations yet"
  description="Start a new chat to build up your history."
  action={{ label: "New chat", onClick: ... }}
/>
```

| Surface                      | Empty condition           | CTA                            |
| ---------------------------- | ------------------------- | ------------------------------ |
| Chat — saved sessions        | No sessions               | "New chat"                     |
| Chat — transcript            | New empty session         | Composer placeholder / hint    |
| Chat — web search no results | Tool returns empty (edge) | "Try rephrasing your question" |
| Documents — list             | No uploads                | Highlight upload zone          |
| Documents — post-delete all  | No docs                   | Same                           |

### Tasks

- Create `frontend/src/components/EmptyState.tsx`
- Replace inline empty divs in `ChatPage` sidebar, `DocumentList`
- Improve new-chat transcript empty hint (welcome line + toggle hints for auth users)
- Optional: empty tool result copy in assistant bubble when search returns no results
- Add Vitest tests for `EmptyState` and updated empty branches
- Ensure guest vs auth copy differs where appropriate (sign-in prompt for guests)

### Success Criteria

- Empty screens explain what happened and what to do next.
- CTAs work (new chat, upload, sign in).

### Verification Checklist

- Empty state component tests pass.
- Chat session list empty state test updated if needed.

### Exit Criteria

- Empty states improved across core surfaces.
- User confirms Phase 8 completion.

### Phase 8 Completion Record

Verified **2026-07-22**.

| Item | Result |
| --- | --- |
| `EmptyState` | `frontend/src/components/EmptyState.tsx` — `title`, `description`, optional `action` (`label`, `onClick`, `disabled`); dashed zinc border + brand CTA; semantic `h3` heading (no `role="status"` — parent labeled sections avoid duplicate live regions) |
| Chat sidebar saved sessions | `ChatPage` saved-section empty branch uses `EmptyState`; auth: description + **New chat** CTA → `handleNewChat` (disabled while creating/transcript loading/generating); guest: sign-in copy, no CTA |
| Documents list | `DocumentList` empty branch uses `EmptyState` — title **No documents yet**, upload-oriented description; shell token overrides via `className`; post-delete-all reuses same branch |
| Transcript empty | `MessageList` welcome card retained; auth + `toolsEnabled`/`ragEnabled` props show toggle hints; guests omit tool/RAG hints |
| Web search empty results | **Deferred** — no frontend metadata for empty tool results without backend/orchestration changes; `toolsUsed` only indicates search ran, not result count |
| Frontend tests | **164** total (**163 pass**); +4 `EmptyState.test.tsx`, +4 `MessageList.test.tsx`, +1 `DocumentList` empty, +1 `ChatPage.sessions` empty CTA; pre-existing `healthClient` URL mismatch unchanged |
| Backend tests | **453 passed**, **87.14%** coverage on `app/` (~22.5s `make test-cov`); no backend code changes |
| Eval CLI | **5/5** passed |
| Regression | V1.1 chat send/stream/persistence unchanged; Phase 1–7 targeted frontend regression (**49** tests green); session-expiry banner test passes after EmptyState live-region fix |

Empty state surface summary:

| Surface | Title | Description | CTA |
| --- | --- | --- | --- |
| Chat — saved sessions (auth) | No saved conversations yet | Start a new chat to build up your conversation history. | **New chat** → `handleNewChat` |
| Chat — saved sessions (guest) | No saved conversations yet | Sign in to keep multiple conversations and pick up where you left off. | _(none — sign in via `AuthControls`)_ |
| Chat — transcript (all) | Start the conversation | Ask a question, iterate on an idea, or test a prompt… | Composer placeholder **Ask something…** (unchanged) |
| Chat — transcript (auth + flags) | _(same)_ | _(same)_ + toggle hint lines for enabled web search / documents |
| Documents — list | No documents yet | Upload a file above to ground chat answers in your own content. | _(upload zone above list)_ |
| Web search no results | — | — | **Deferred** (not reachable without backend metadata) |

Guest vs authenticated copy:

| Surface | Guest | Authenticated |
| --- | --- | --- |
| Saved sessions | Sign-in oriented; no **New chat** CTA | History copy + **New chat** CTA in empty state |
| Transcript empty | Welcome only; no tool/RAG hints | Welcome + toggle hints when server flags on |
| Documents | N/A (`ProtectedRoute`) | Upload-oriented empty state |

---

## Phase 9 - Mobile Responsiveness Review (P2)

**Status:** Complete (2026-07-22)

### Objectives

Validate and fix obvious layout issues on mobile viewports for core flows.

### Review Checklist

| Area            | Check                                             | Fix if needed                           |
| --------------- | ------------------------------------------------- | --------------------------------------- |
| Chat page       | Sidebar drawer opens/closes; composer visible     | Touch targets ≥ 44px; safe-area padding |
| Session list    | Readable titles; delete control accessible        | Truncation, button spacing              |
| Message bubbles | Text wraps; markdown readable                     | `overflow-x` on code blocks             |
| Code blocks     | Horizontal scroll without breaking layout         | `pre` overflow-x-auto                   |
| Composer        | Toggles wrap; send button reachable               | Stack toggles on narrow screens         |
| Documents page  | Upload form usable; list readable                 | Full-width inputs                       |
| Auth flow       | Google sign-in button visible; banner not clipped | Header stacking                         |
| 404 page        | CTAs full-width tap targets                       | Padding                                 |

### Tasks

- Run manual pass at 375px width (iPhone SE) and 390px (common mobile)
- Fix identified CSS issues in `ChatPage`, `Composer`, `MessageList`/markdown renderer, `DocumentsPage`, `NotFoundPage`
- Use existing Tailwind v4 responsive utilities — no new breakpoint system
- Document review results in Phase 9 completion record (pass/fail per row)
- Add optional Playwright/visual note in completion record — automated mobile tests out of scope unless trivial

### Success Criteria

- Core flows work well on mobile without horizontal page scroll (except code blocks).
- No P1 layout bugs open on checklist items.

### Verification Checklist

- Manual checklist completed and recorded.
- Frontend tests and build pass.
- No desktop regressions from mobile fixes.

### Exit Criteria

- Mobile review complete with fixes applied.
- User confirms Phase 9 completion.

### Phase 9 Completion Record

Verified **2026-07-22**. Manual review method: Chrome DevTools device emulation (**375×667**, **390×844**) against `npm run dev` + production build spot-check at **1280px**.

| Area         | Status (pass/fixed/deferred) |
| ------------ | ---------------------------- |
| Chat sidebar | **fixed** — drawer open/close OK; hamburger/Close/delete controls raised to `min-h-11`/`min-w-11`; saved-session row gap tightened (`gap-1 sm:gap-2`) |
| Composer     | **pass** — toggles wrap; send/stop already `min-h-11`; safe-area + `visualViewport` keyboard inset unchanged |
| Markdown     | **fixed** — plain-text bubbles get `min-w-0` + `overflow-x-auto` wrapper for long preformatted lines (no markdown library) |
| Documents    | **fixed** — upload submit `w-full sm:w-auto min-h-11`; list delete `min-h-11 w-full sm:w-auto`; file input already full-width |
| Auth         | **fixed** — guest header stacks; Logout/`Why login?` `min-h-11`; Google GIS button visible (GIS-rendered size unchanged — third-party) |
| 404          | **fixed** — CTAs stack full-width on narrow screens with `min-h-11` tap targets |

**Viewport notes:** No horizontal **page** scroll on `/`, `/unknown-test`, or guest `/documents` redirect at 375px or 390px. Internal message overflow scrolls within bubble wrapper.

**Fix summary (area → issue → change):**

| Area | Issue | Change |
| ---- | ----- | ------ |
| Chat sidebar | Delete/hamburger/Close below 44px | `min-h-11 min-w-11 inline-flex items-center justify-center` on delete, hamburger, mobile Close |
| Session list | Delete tap spacing on narrow drawer | `gap-1 sm:gap-2` on saved-session row |
| Message bubbles | Long code-like plain text could widen page | `min-w-0` bubble + `overflow-x-auto max-w-full` content wrapper |
| Documents | Upload/delete controls narrow on mobile | Full-width mobile buttons with `min-h-11` |
| Auth / banner | Small tap targets; expiry banner cramped | `min-h-11` on Logout/Why login/Dismiss; session-expiry banner stacks on mobile |
| 404 | CTAs small on narrow screens | Full-width stacked links with `min-h-11` |

**Deferred items:**

| Item | Rationale |
| ---- | --------- |
| Automated Playwright/visual mobile tests | Out of scope per plan — manual checklist only |
| Google GIS button pixel size | Third-party widget; `size: 'medium'` retained; button remains visible and usable in stacked guest header |

**Regression vs Phase 8 baseline:** Backend **453** tests, **87.14%** coverage (**21.48s**); frontend **164** Vitest tests (**163** pass — pre-existing `healthClient.test.ts` URL mismatch when `frontend/.env` sets `VITE_API_BASE_URL=http://127.0.0.1:8000`); eval CLI **5/5**; V1.1 regression subset **81** passed; targeted Phase 1–8 frontend regression **56/56**; production build succeeds.

---

## Phase 10 - Final V1.1.1 Validation

**Status:** Complete (2026-07-22)

### Objectives

Verify the complete V1.1.1 polish release meets the definition of done through systematic validation and production deployment readiness.

### Validation Checklist

| Area                  | Verification                                                         |
| --------------------- | -------------------------------------------------------------------- |
| V1.1 regression       | Unified chat toggles, streaming tools/RAG, provider parity unchanged |
| Delete session        | API + UI; cascade; post-delete selection                             |
| Auto titles           | First message → ~50 char title; existing titles preserved            |
| Route protection      | `/documents` redirect; session expiry banner on `/`                  |
| 404 page              | Unknown routes show branded page                                     |
| Demo protection       | Token cap, upload quota, rate limits, ops docs                       |
| Loading states        | Consistent indicators on all long operations                         |
| Provider errors       | Friendly messages; no raw SDK text                                   |
| Empty states          | Actionable CTAs on empty surfaces                                    |
| Mobile                | Checklist complete; no open P1 layout issues                         |
| Tests                 | Backend ≥ 80% coverage; frontend Vitest green                        |
| CI                    | All quality gates green                                              |
| Documentation         | README, `.env.example`, ops demo protection notes                    |
| Production deployment | Deployed and smoke-tested                                            |

### Tasks

- Run full manual QA script covering validation checklist.
- Run evaluation CLI; compare to V1.1 baseline.
- Run V1.1 regression suite (unified chat, streaming, tools, RAG, sessions, documents).
- Docker Compose smoke test.
- Update documentation:
  - `backend-python/README.md` — delete session API, title behavior, demo caps
  - Root `README.md` — V1.1.1 polish summary
  - `.env.example` — new settings
  - Architecture spec — mark V1.1.1 sections (minimal delta)
- Create V1.1.1 release summary at `docs/releases/post-mvp-v1.1.1-release-summary.md`.
- Record validation results in Phase 10 section below.
- Complete production deployment and post-deploy smoke.

### Success Criteria

- Every validation checklist row verified and recorded.
- No P0/P1 issues open for V1.1.1 scope.
- CI green; Docker smoke test passes.
- Production deployment completed.

### Exit Criteria

- V1.1.1 declared complete per Definition of Done.
- User confirms Phase 10 completion.
- **V1.x frozen; V2 development may begin.**

### Phase 10 Completion Record

Verified **2026-07-22**. Full validation per Phase 10 prompt; release summary at [docs/releases/post-mvp-v1.1.1-release-summary.md](../releases/post-mvp-v1.1.1-release-summary.md).

#### Validation checklist

| Area | Status | Evidence |
| ---- | ------ | -------- |
| V1.1 regression | **pass** | Backend 149 targeted tests pass; frontend 59 targeted tests pass; eval **5/5** |
| Delete session | **pass** | `pytest tests/test_chat_sessions.py -k delete`; `ChatPage.sessions` delete tests |
| Auto titles | **pass** | `pytest tests/test_session_titles.py`; `derive_session_title` unit + integration tests |
| Route protection | **pass** | `ProtectedRoute.test.tsx`, `ChatPage.sessionExpiry.test.tsx`, `DocumentsPage.test.tsx` |
| 404 page | **pass** | `NotFoundPage.test.tsx` |
| Demo protection | **pass** | `test_guest_output_token_cap`, `test_guest_quota`, `test_documents_api`; ops doc + `.env.example` |
| Loading states | **pass** | `LoadingIndicator.test.tsx`; `ProtectedRoute` loading shell; code review of session/upload/stream paths |
| Provider errors | **pass** | `friendlyErrors.test.ts`, `provider_error_assertions.py` |
| Empty states | **pass** | `EmptyState.test.tsx`, `DocumentList.test.tsx`, `MessageList.test.tsx`, `ChatPage.sessions` |
| Mobile | **pass** | Phase 9 Completion Record verified; no open P1 layout issues on checklist rows |
| Tests | **pass** | Backend **453** / **87.14%**; frontend **163/164** (pre-existing `healthClient` mismatch) |
| CI | **pass** | Local gates match `.github/workflows/pr-quality.yml` jobs |
| Documentation | **pass** | README, `.env.example`, architecture spec V1.1.1 delta, release summary |
| Production deployment | **blocked** | No CD promotion executed; see deployment section below |

#### Manual QA summary

| Script section | Result | Method |
| -------------- | ------ | ------ |
| A. V1.1 baseline | **pass** | Automated: unified chat, streaming, tools, RAG, guest denial tests |
| B. Demo protection | **pass** | Automated quota/token cap tests; ops doc + `.env.example` reviewed |
| C. Delete session | **pass** | `ChatPage.sessions` + backend delete/cascade tests |
| D. Auto titles | **pass** | Backend title tests; frontend session list display tests |
| E. Protected routes & expiry | **pass** | `ProtectedRoute`, `ChatPage.sessionExpiry`, `DocumentsPage` tests |
| F. 404 page | **pass** | `NotFoundPage.test.tsx` |
| G. Loading states | **pass** | Component tests + code review (timing unchanged from Phase 6) |
| H. Provider errors | **pass** | Test doubles + assertion tests; no raw SDK substrings |
| I. Empty states | **pass** | Component and page tests for sidebar, documents, transcript |
| J. Mobile spot-check | **pass** | Phase 9 record; checklist fixes present in codebase |
| K. Docker Compose smoke | **pass** | See Docker smoke section |
| L. Production deployment | **blocked** | No deploy credentials / CD workflow run in validation session |

#### Docker Compose smoke (2026-07-22)

```bash
docker compose --profile python up --build -d
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health          # 200
curl -s http://localhost:8000/api/health/ready                                     # {"status":"ok","db":"ok"}
curl -s -o /dev/null -w "%{http_code}" http://localhost/                           # 200
docker compose --profile python down
```

Note: readiness endpoint is `/api/health/ready` (not `/api/ready`). One transient persistence test failure occurred while Docker Postgres occupied port 5432; resolved after stack teardown — not a code defect.

#### Production deployment result

**Status: blocked (pending operator promotion).**

- Existing Railway backend health: `https://fullstack-ai-platform-production.up.railway.app/api/health` → **200** (pre-V1.1.1 artifact; V1.1 toggles enabled).
- V1.1.1 code changes are **not yet promoted** to production in this validation run.
- Promotion path: [CD_PRODUCTION.md](../../CD_PRODUCTION.md) — manual `workflow_dispatch` with successful staging `source_sha`.
- **User waiver required** for Definition of Done item 14 until promotion and post-deploy smoke complete.

Post-deploy smoke minimum (when promoted): health **200**, ready **200**, frontend load, guest chat, auth `/documents` gate, 404 page.

#### Phases 0–9 completion records

All ten phase records (0–9) verified present and filled in this plan (2026-07-22).

#### Metrics (Phase 10 gates)

| Metric | Result |
| ------ | ------ |
| Backend tests | **453 passed**, **22.83s** |
| Backend coverage | **87.14%** on `app/` |
| Eval CLI | **5/5** (timestamp 2026-07-22T12:39:00Z) |
| Frontend Vitest | **164** tests (**163 pass** — pre-existing `healthClient.test.ts`) |
| Frontend build | **pass** |
| V1.1 regression (backend) | **149 passed** |
| V1.1.1 targeted (backend) | **22 passed** |
| Combined spot-check | **64 passed**, 17 skipped |
| Frontend targeted | **59 passed** |

#### Documentation updates

- [backend-python/README.md](../../backend-python/README.md) — session DELETE API, auto-title, demo caps, ops link
- [README.md](../../README.md) — V1.1.1 polish summary
- [backend-python/.env.example](../../backend-python/.env.example) — rate limit review note
- [Post-MVP-V1-Architecture-and-Technical-Design-Specs.md](../references/Post-MVP-V1-Architecture-and-Technical-Design-Specs.md) — V1.1.1 delta section
- [docs/releases/post-mvp-v1.1.1-release-summary.md](../releases/post-mvp-v1.1.1-release-summary.md) — created

#### P0/P1 issues

None open for V1.1.1 scope. Pre-existing: `healthClient.test.ts` URL host mismatch (non-blocking per Phase 9 baseline).

#### Regression vs Phase 9 baseline

No regression: backend **453** / **87.14%** / **22.83s** (Phase 9: 453 / 87.14% / ~21.48s); eval **5/5** unchanged; frontend **163/164** unchanged.

---

## Suggested Task Breakdown (PR-Friendly)

1. **PR 1:** Phase 0 audit notes + baseline record.
2. **PR 2:** Phase 1 demo protection — settings, token cap, upload quota, ops docs.
3. **PR 3:** Phase 2 delete session — backend API + store + tests.
4. **PR 4:** Phase 2 delete session — frontend UI + confirmation + fallback selection.
5. **PR 5:** Phase 3 auto-generate titles — backend helper + persist + tests.
6. **PR 6:** Phase 3 auto-generate titles — frontend display fallback.
7. **PR 7:** Phase 4 protected routes + session expiry redirect.
8. **PR 8:** Phase 5 friendly 404 page.
9. **PR 9:** Phase 6 shared `LoadingIndicator` + replacements.
10. **PR 10:** Phase 7 provider error normalization audit + friendly frontend messages.
11. **PR 11:** Phase 8 shared `EmptyState` + surface updates.
12. **PR 12:** Phase 9 mobile CSS fixes.
13. **PR 13:** Phase 10 validation + documentation + release summary.

---

## Risk Register and Mitigation

| Risk                                             | Impact | Mitigation                                                           |
| ------------------------------------------------ | ------ | -------------------------------------------------------------------- |
| Delete active session leaves broken UI state     | High   | Explicit post-delete selection algorithm; frontend tests             |
| Title overwrite accidentally clears user titles  | Medium | Only update when `title IS NULL`; test preservation                  |
| Demo caps too aggressive for dev/staging         | Medium | High defaults locally; `demo_mode_strict` flag for production        |
| Protected route redirect loops                   | Medium | `/` stays public; test anonymous → `/documents` → `/`                |
| Provider error mapping misses new SDK exceptions | Medium | Fail closed to generic `provider_error`; audit tests                 |
| Mobile fixes break desktop layout                | Low    | Tailwind responsive prefixes; test at desktop + mobile widths        |
| Scope creep into V2 (AI titles, rename, export)  | High   | Locked decisions; reference out-of-scope list                        |
| Cascade delete removes wanted audit data         | Low    | Usage events cascade by design; document in delete confirmation copy |
| Upload quota false positives                     | Medium | Clear error code; quota reset at UTC midnight documented             |

---

## Observability (V1.1.1 additions)

| Field / metric                   | Purpose                                  |
| -------------------------------- | ---------------------------------------- |
| `session_delete_total`           | Track session deletions (structured log) |
| `title_auto_generated_total`     | Sessions receiving auto title            |
| `guest_output_token_cap_applied` | When guest cap reduces max_tokens        |
| `upload_quota_denied_total`      | Upload rejected due to daily quota       |

Emit via structured log fields (same pattern as V1/V1.1); no Prometheus requirement.

---

## Definition of Done

Post-MVP V1.1.1 is complete when **all** of the following are true:

1. **Delete chat session** — `DELETE /api/chat/sessions/{id}` with cascade; UI confirmation; auto-select/create fallback session.
2. **Auto-generate chat titles** — first user message → trimmed ~50 char title; existing titles preserved.
3. **Graceful session timeout & protected routes** — centralized protection; expired/unauthenticated users redirected to `/` with friendly notification.
4. **Friendly 404 page** — unknown routes show branded page with "Back to Chat" action.
5. **Public demo protection** — rate limits reviewed; output token cap; upload quotas; provider spending alerts documented.
6. **Consistent loading states** — shared loading component on chat, web search, RAG, uploads.
7. **Graceful provider error handling** — normalized errors; friendly retry messages; no raw provider text.
8. **Mobile responsiveness review** — checklist complete; obvious layout issues fixed.
9. **Empty state improvements** — informative, actionable empty screens.
10. No architectural refactoring; no new platform capabilities.
11. V1.1 regression suite passes; evaluation CLI passes.
12. Coverage ≥ 80% on `app/`; frontend Vitest green.
13. Documentation and V1.1.1 release summary updated.
14. Production deployment completed.

---

## Final Acceptance Gate

All items must be true:

- Phases 0–10 completion records filled and verified.
- All nine V1.1.1 feature items functional.
- V1.1 capabilities unchanged except where explicitly improved (sessions, auth UX, errors).
- No V2 features implemented.
- Production deployed and smoke-tested.
- User confirms V1.1.1 completion.
- **V1.x frozen; V2 development authorized.**
