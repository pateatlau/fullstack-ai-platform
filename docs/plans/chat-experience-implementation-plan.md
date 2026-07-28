# Chat Experience — Implementation Plan (Persistence, Multiple Sessions, Summarization, Guest Quota UX)

> Planning document only. No code is written and no files are modified by this plan.
> Grounded in the **actual repository state** (verified July 2026 against `backend-python/`
> and `frontend/`). References to existing files were read and confirmed. Proposed work is
> labelled **[Proposed]**; items needing confirmation **[Verify]**; open decisions **[Open]**.
> Authentication mechanics are **consumed, not designed here** — see the companion
> `docs/plans/google-auth-implementation-plan.md` (the chat-experience prompt refers to this
> deliverable as `docs/prompts/google-auth-plan-prompt.md`; the actual artifact in this repo
> is the implementation plan document).

---

## 1) Objective, Scope, and Non-Goals

### 1.1 Objective

Complete the chat experience end-to-end (Python FastAPI backend + React frontend) so that the
product delivers the authoritative Target Chat-Experience Behavior:

1. The chatbot page is the default landing page for guests and authenticated users.
2. Guests may send up to a configurable number of messages per UTC day (default 20); when the
   limit is reached, sending is blocked and the user is prompted to log in.
3. Provider/model switching is available **only to authenticated users**; guests use a fixed
   default provider/model and do not see or use the switcher.
4. Summarization happens automatically when a conversation grows long, transparently, and is
   used for subsequent context assembly.
5. Only authenticated users may create new chats; guests are limited to a single default chat.
6. Authenticated users can create, list, switch between, and resume multiple chat sessions.
7. Guest history is preserved across login by **using** the already-linked identity so a
   guest's prior default chat appears under the authenticated account.

### 1.2 In scope

- Chat session lifecycle: **list**, **create** (authenticated-only), switch, resume.
- Message persistence semantics and ordering (reuse existing gap-free `seq`, idempotency).
- Automatic summarization behavior and its UX (reuse existing threshold + boundary model).
- Guest daily message-quota enforcement (backend-authoritative) and its UX.
- Provider/model gating between guest and authenticated users (backend-authoritative).
- Multi-session sidebar UX and how the client uses the consumed identity to drive features.

### 1.3 Out of scope (deferred to the auth layer — already delivered/planned)

Google credential acquisition, ID-token verification, app-JWT issuance/verification, client
token **storage/transport mechanism**, logout mechanics, and the mechanics of guest→user
**linking at login**. These are consumed from `docs/plans/google-auth-implementation-plan.md`.
The Node.js backend (`backend-nodejs/`) is **paused — out of scope**. No event sourcing, CQRS,
billing-grade accounting, DB partitioning, refresh tokens, or Kubernetes.

### 1.4 Constraints and consumed capabilities (assumptions)

Consumed from the auth layer (treated as existing inputs):

- Backend caller resolution already exists:
  [`get_current_caller`](../../backend-python/app/core/caller.py) returns a
  `CallerContext { kind: "user"|"guest", user_id, guest_id, issued_guest_token,
is_authenticated }`. An invalid/expired JWT falls back to the guest tier (not an error).
- Guest continuity via the `X-Guest-Token` request/response header; the server stores only a
  token **hash** and issues a new token when absent.
- Guest→user linking at login sets `guest_identities.linked_user_id` (**link-only, no session
  migration**) via [`AuthService`](../../backend-python/app/services/auth_service.py).
- **[Verify / Dependency]** Frontend auth is delivered by the companion plan: an auth/user
  context, `Authorization: Bearer <app JWT>` on requests, `X-Guest-Token` capture/replay from
  responses, `VITE_GOOGLE_CLIENT_ID`, and a backend CORS `expose_headers` fix so browser JS can
  read `X-Guest-Token`. **This chat-experience plan depends on that transport existing**; where
  it is not yet built, the sequencing in §11 assumes the auth slice lands first or in parallel.

### 1.5 Current-state assessment (observed vs. proposed)

Legend: ✅ implemented · ◻ partial · ❌ missing.

**Backend (`backend-python/`) — persistence, summarization, quota, usage:**

| Capability                                                                                                                                    | State                                         | Evidence                                                                                                                                                                         |
| --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Data model (`users`, `guest_identities`, `chat_sessions`, `chat_messages`, `session_summaries`, `usage_events`, `guest_quota_counters`)       | ✅                                            | [app/db/models.py](../../backend-python/app/db/models.py), Alembic baseline [0001_init_chat_persistence.py](../../backend-python/alembic/versions/0001_init_chat_persistence.py) |
| `owner_xor` (user XOR guest), gap-free per-session `seq` via `next_seq` + `SELECT FOR UPDATE`, `client_message_id` partial-unique idempotency | ✅                                            | [app/db/models.py](../../backend-python/app/db/models.py), [app/db/chat.py](../../backend-python/app/db/chat.py)                                                                 |
| Non-streaming chat `POST /api/chat`                                                                                                           | ✅                                            | [app/routers/chat.py](../../backend-python/app/routers/chat.py)                                                                                                                  |
| Streaming chat `POST /api/chat/stream` (SSE start/delta/end/error)                                                                            | ✅                                            | [app/routers/chat.py](../../backend-python/app/routers/chat.py), [app/schemas/chat.py](../../backend-python/app/schemas/chat.py)                                                 |
| Session **resume** `GET /api/chat/sessions/{session_id}` (ownership-checked)                                                                  | ✅                                            | [app/routers/chat.py](../../backend-python/app/routers/chat.py)                                                                                                                  |
| Implicit session **create** on first turn (no `session_id`) or append with `session_id`                                                       | ✅                                            | [app/services/chat_service.py](../../backend-python/app/services/chat_service.py)                                                                                                |
| Session **list** endpoint                                                                                                                     | ❌                                            | No `GET /api/chat/sessions` route exists                                                                                                                                         |
| Explicit session **create** endpoint                                                                                                          | ❌                                            | Creation is only implicit inside chat turns                                                                                                                                      |
| Session **rename/delete** endpoints                                                                                                           | ❌                                            | Not present                                                                                                                                                                      |
| Guest **single-default-chat** enforcement                                                                                                     | ❌                                            | Backend allows multiple `chat_sessions` per guest; not restricted                                                                                                                |
| **New-chat restriction** for guests (authenticated-only create)                                                                               | ❌                                            | Not enforced server-side                                                                                                                                                         |
| Guest **provider/model gating** (fixed default for guests)                                                                                    | ❌                                            | Any caller may pass any configured provider/model                                                                                                                                |
| Daily guest quota (UTC window, atomic upsert) enforced pre-flight                                                                             | ✅                                            | [app/services/quota_service.py](../../backend-python/app/services/quota_service.py), [app/db/identity.py](../../backend-python/app/db/identity.py)                               |
| Summarization: threshold trigger, `covers_through_seq` boundary, deterministic context assembly, best-effort/non-fatal                        | ✅                                            | [`_maybe_summarize` / `build_context_messages`](../../backend-python/app/services/chat_service.py)                                                                               |
| Usage accounting (`kind=chat                                                                                                                  | summary`, `token_source`, `request_id` dedup) | ✅                                                                                                                                                                               | [app/services/usage_service.py](../../backend-python/app/services/usage_service.py), [app/db/usage.py](../../backend-python/app/db/usage.py) |
| Google auth + JWT + guest linking                                                                                                             | ✅                                            | [app/routers/auth.py](../../backend-python/app/routers/auth.py), [app/services/auth_service.py](../../backend-python/app/services/auth_service.py)                               |

**Frontend (`frontend/`) — chat UI/state/streaming:**

| Capability                                                      | State                                  | Evidence                                                                                                                                                                      |
| --------------------------------------------------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Streaming chat UI (SSE parse, deltas, stop/retry, error banner) | ✅                                     | [useChatStream.ts](../../frontend/src/hooks/useChatStream.ts), [sseParser.ts](../../frontend/src/api/sseParser.ts), [chatReducer.ts](../../frontend/src/state/chatReducer.ts) |
| Provider/model switcher in composer                             | ✅ but **ungated**                     | [Composer.tsx](../../frontend/src/components/Composer.tsx) — available to all users                                                                                           |
| Multi-session sidebar                                           | ◻ **UI scaffold only, non-functional** | [ChatPage.tsx](../../frontend/src/pages/ChatPage.tsx) (hardcoded `current-session`, empty "Saved")                                                                            |
| Session list/switch/create wired to backend                     | ❌                                     | No API calls; `selectedSessionId` unused for loading                                                                                                                          |
| Session resume (load prior transcript)                          | ❌                                     | No fetch of `GET /api/chat/sessions/{id}`                                                                                                                                     |
| Messages persisted across reload                                | ❌                                     | In-memory reducer only                                                                                                                                                        |
| Identity headers on requests (`Authorization`, `X-Guest-Token`) | ❌                                     | [chatClient.ts](../../frontend/src/api/chatClient.ts) sends only `Content-Type`                                                                                               |
| `session_id` / `client_message_id` sent on requests             | ❌                                     | Not present in `ChatRequest` shape                                                                                                                                            |
| Quota-reached UX (block + login prompt)                         | ❌                                     | No handling of `429 quota_exceeded`                                                                                                                                           |
| Auth/user state consumer                                        | ❌ (delivered by auth plan)            | No auth context today                                                                                                                                                         |

**True remaining gaps (both sides are non-trivial):**

- **Backend:** session **list** endpoint; explicit **create** endpoint (authenticated-only);
  guest **single-default-chat** enforcement; guest **provider/model gating**; optional
  rename/delete; surfacing capability/quota signals for the client. All must be
  **server-authoritative**.
- **Frontend:** wire the sidebar to real session list/switch/create; session **resume**;
  send `session_id` + `client_message_id`; **gate** the provider/model switcher by identity;
  **quota-reached** UX; consume identity headers from the auth layer.

### 1.6 Non-goals (future evolution)

Cross-session semantic search, manual summarization triggers, per-user default-model
preferences, message editing, soft-delete/retention automation, and pagination beyond a simple
cursor. Recorded as evolution paths, not this phase.

---

## 2) Chat Sessions and Multi-Session Design

### 2.1 Ownership and access rules (reuse existing invariants)

- Every session is owned by exactly one principal: **user XOR guest** (`owner_xor` DB
  constraint). Reuse [`get_owned_session`](../../backend-python/app/db/chat.py), which filters
  by `user_id`/`guest_id` from the resolved `CallerContext`.
- Every read/list/switch/resume path **must** scope by the caller's owner id. A session that
  does not match the caller's owner id returns **404** (do not leak existence via 403).
- No cross-owner access: an authenticated user cannot read a guest session they were not linked
  to, and a guest cannot read a user session. (Linking surfacing is handled in §2.6.)

### 2.2 Required backend additions

**[Proposed] `GET /api/chat/sessions` — list caller's sessions.**

- Auth: any caller (user or guest). Owner-scoped by resolved `CallerContext`.
- Ordering: `last_message_at DESC NULLS LAST, created_at DESC` (most recently active first).
- Response: a list of **session summaries** (metadata only, no messages) to keep the sidebar
  cheap. Reuse `ChatSessionOut` fields minus `messages`, or add a lean `ChatSessionListItem`
  schema: `{ id, title, last_message_at, created_at, message_count? }`. **[Open]** whether to
  include `message_count` (requires an aggregate; acceptable for MVP volumes) or omit it.
- Pagination: **cursor-based, optional for MVP.** Default page size ~50 ordered by the sort
  key with an opaque `?cursor=` (encode `last_message_at`+`id`). For MVP volumes a single page
  is acceptable; ship the cursor param but allow a simple capped list first. **[Open]**
- Guest behavior: returns the guest's **single** default session (0 or 1 item).

**[Proposed] `POST /api/chat/sessions` — explicit create (authenticated-only).**

- Auth: **authenticated users only.** A guest caller → **403** `code="new_chat_forbidden"`.
- Body: optional `{ title? }`; server may leave `title` null (derived later from first user
  message, matching current behavior).
- Response: `ChatSessionOut` for the new empty session (no messages).
- Rationale: gives the sidebar "+ New chat" a deterministic action instead of relying on
  implicit create; preserves the existing implicit-create path for backward compatibility.

**[Open] `PATCH /api/chat/sessions/{id}` (rename) and `DELETE /api/chat/sessions/{id}`.**

- Not required by the Target Behavior (which lists only create/list/switch/resume). Recommend
  **deferring** rename/delete to a follow-up; if included, both are authenticated-or-owner and
  ownership-checked. Delete must `CASCADE` messages/summaries/usage (FKs already `ondelete
CASCADE`). Marked out of the minimal slice.

### 2.3 Guest single-default-chat constraint (server-enforced)

- A guest has **at most one** `chat_sessions` row. Enforce in the create path and the
  implicit-create path inside [`_resolve_session`](../../backend-python/app/services/chat_service.py):
  - Guest chat turn **without** `session_id`: reuse the guest's existing session if present,
    else create the single default session.
  - Guest chat turn **with** a `session_id` that is not their existing default: **404**
    (ownership) — a guest can only ever address their one session.
  - `POST /api/chat/sessions` by a guest: **403** `new_chat_forbidden`.
- **[Open]** Enforcement mechanism: application-level check (query existing guest session
  before create) vs. a DB partial-unique index on `chat_sessions(guest_id) WHERE guest_id IS
NOT NULL`. Recommend the **application-level check first** (no migration, testable via fakes),
  with the DB index as a defense-in-depth follow-up. If the index is added, it requires a new
  Alembic revision and must tolerate the existing multi-guest-session possibility in dev data.

### 2.4 Request/response contracts (consistent with existing schemas)

- Reuse [`ChatRequestSchema`](../../backend-python/app/schemas/chat.py): `messages`, `model?`,
  `provider?`, `temperature`, **`session_id?`**, **`client_message_id?`** (all already present).
  No breaking changes.
- Reuse [`ChatResponseSchema`](../../backend-python/app/schemas/chat.py) (returns `session_id`
  when persistence is active) and the SSE `StartFrame.session_id`.
- Reuse [`ChatSessionOut` / `ChatMessageOut`](../../backend-python/app/schemas/chat.py) for
  resume; add the lean list-item schema for the list endpoint (§2.2).

### 2.5 Deterministic ordering and idempotency on append

- Ordering: unchanged — gap-free per-session `seq` allocated via `next_seq` +
  `SELECT FOR UPDATE`; reads ordered by `seq`.
- Idempotency: unchanged — `client_message_id` partial-unique index; a replayed
  `client_message_id` returns the prior assistant reply without a new provider call (already
  implemented in [`complete_chat` / `prepare_stream`](../../backend-python/app/services/chat_service.py)).
- The frontend **must** generate and send a stable `client_message_id` per user submission
  (see §6) so retries/reconnects are safe.

### 2.6 Guest→authenticated continuity surfacing

- Linking already sets `linked_user_id` at login (consumed capability). This phase must ensure
  the guest's prior default chat **appears under the authenticated account**. Two options:
  - **[Recommended] Ownership resolution at list/read time:** when the caller is authenticated,
    the list/read queries also include sessions owned by any `guest_identities` whose
    `linked_user_id == user_id`. This is a **read-time projection** — no data migration, fully
    reversible, and preserves the `owner_xor` invariant on existing rows.
  - **[Alternative] One-time re-owning migration** at login (set `chat_sessions.user_id`, clear
    `guest_id`). Rejected for MVP: mutates ownership, risks violating `owner_xor` handling and
    the guest single-chat assumptions, and is harder to reverse.
- **[Open]** Confirm the linked-session read projection satisfies "guest's prior default chat
  appears under the account" without also unlocking guest-tier limits for that session (it
  should: capabilities derive from the **current caller** identity, not the session's origin).

### 2.7 Backward compatibility

- `CHAT_PERSISTENCE_ENABLED=false` continues to behave statelessly: no session ids, no
  list/create semantics, no quota — existing tests
  ([test_chat_persistence.py](../../backend-python/tests/test_chat_persistence.py)) must keep
  passing. The list/create endpoints return an appropriate empty/disabled response when
  persistence is off. **[Open]** Exact behavior of `GET /api/chat/sessions` when persistence is
  disabled: return `[]` (recommended) vs. 404.

---

## 3) Guest Policy, Quota Enforcement, and Provider/Model Gating

### 3.1 Daily quota (backend-authoritative)

- Reuse [`QuotaService.check` / `.record`](../../backend-python/app/services/quota_service.py)
  and `guest_quota_counters` (UTC daily window, atomic upsert). `check` raises
  `QuotaExceededError` (**429**, `code="quota_exceeded"`) **before** any provider call — this
  pre-flight already runs for both `POST /api/chat` and `POST /api/chat/stream`.
- **UX when limit reached:** the composer blocks sending, shows a clear "daily guest limit
  reached" message, and prompts login (see §6). The client detects the **429**
  `quota_exceeded` from the non-stream pre-flight (stream endpoint also returns HTTP 429 before
  the SSE body starts).
- **[Open]** Surface **remaining quota** to the client proactively (e.g., a response header
  `X-Guest-Quota-Remaining` on chat responses, or a field on the session-list/me response) so
  the UI can warn before the hard block. Recommended: a response header on chat responses
  (cheap, no new endpoint); requires CORS `expose_headers` (already needed for `X-Guest-Token`).

### 3.2 Provider/model gating

- **Guest default:** guests use the system default provider/model — the existing
  `LLM_PROVIDER` + its configured model (e.g., `OPENAI_MODEL`). **[Open]** whether to introduce
  explicit `GUEST_DEFAULT_PROVIDER` / `GUEST_DEFAULT_MODEL` env vars or reuse `LLM_PROVIDER`
  and the provider's default model. Recommend reusing existing defaults to avoid new config,
  with optional explicit vars as a follow-up.
- **Server-side rejection:** when the caller is a guest and the request's `provider`/`model`
  differ from the guest default, reject with **403** `code="provider_not_allowed"` **before**
  any provider call, in the chat service's `_resolve_provider`/pre-flight path. Guests may omit
  `provider`/`model` entirely (server applies the default).
- **Frontend gating (UX only):** hide the switcher for guests; always send the default (or omit
  it). Backend remains authoritative.

### 3.3 New-chat restriction for guests

- `POST /api/chat/sessions` by a guest → **403** `code="new_chat_forbidden"`.
- Implicit second-session attempt (guest sends with a `session_id` that isn't their default) →
  **404** (ownership), consistent with §2.3.
- **UX:** the "+ New chat" control is hidden/disabled for guests with a login affordance.

### 3.4 Capability discovery on the client

- The client derives capabilities from the **consumed identity state** (authenticated vs.
  guest), not hardcoded UI assumptions:
  - authenticated ⇒ switcher visible, "+ New chat" enabled, multi-session list.
  - guest ⇒ switcher hidden, single chat, quota banner, login prompts.
- **[Open]** Optionally expose a small capabilities payload (e.g., on the auth `me`/login
  response or a `GET /api/chat/config`) carrying `{ is_authenticated, guest_daily_quota,
guest_quota_remaining, default_provider, default_model }`. Recommend deriving from auth state
  - a quota header first; add a config endpoint only if the UI needs richer signals.
- Backend rejection codes (`quota_exceeded`, `provider_not_allowed`, `new_chat_forbidden`) are
  the **authoritative** contract; the UI treats client-side gating as convenience only.

---

## 4) Summarization Integration

### 4.1 Confirmed existing behavior (reuse as-is)

- Trigger: after a completed turn, if pending messages (those with `seq >
covers_through_seq`) reach `SUMMARY_TRIGGER_MESSAGE_COUNT` (default **20**),
  [`_maybe_summarize`](../../backend-python/app/services/chat_service.py) generates a summary.
- Storage: a new `session_summaries` row with `version++` and `covers_through_seq =
last_message.seq`; unique on `(session_id, version)`.
- Context assembly: [`build_context_messages`](../../backend-python/app/services/chat_service.py)
  returns the latest summary as a system message **plus only messages after
  `covers_through_seq`**, in `seq` order — deterministic.
- Failure mode: **best-effort/non-fatal** — a summarization failure does not fail the chat turn.
- Observability: a `usage_events` row with `kind="summary"` is recorded per summarization.

### 4.2 Multi-session / guest correctness

- Summarization is **per-session** and already keyed by `session_id`; multi-session introduces
  no change to the boundary model. Each session summarizes independently.
- Guests: summarization applies to the guest's single default chat identically. No special
  casing required. After login, the linked session's summaries remain valid (read-time
  projection in §2.6 does not alter `covers_through_seq`).
- **No changes to summarization semantics are proposed.** Any change would require justification
  per the Critical Architecture Requirements.

### 4.3 Observability and validation

- Confirm summaries are produced/used via: `session_summaries` rows, `usage_events` with
  `kind="summary"` (and `token_source`), and structured logs around the trigger.
- Validation extends [test_summarization_and_linking.py](../../backend-python/tests/test_summarization_and_linking.py):
  threshold boundary, deterministic context assembly, and non-fatal failure (already covered);
  add a multi-session case asserting independent boundaries per session.

---

## 5) Chat Lifecycle Flows (Write + Read)

### 5.1 Guest sends in single default chat; approaches and hits quota

1. Client sends `POST /api/chat/stream` with `client_message_id`, no `session_id` (or the
   guest's known default), **no** provider/model (uses default), plus `X-Guest-Token` (or none
   on first ever request).
2. Backend resolves guest (mints token + returns `X-Guest-Token` if new), pre-flight
   `QuotaService.check`:
   - Under limit ⇒ resolve/create the single default session, append user message, stream,
     persist assistant message, `QuotaService.record`, maybe summarize.
   - At/over limit ⇒ **429 `quota_exceeded`** before SSE starts; **no** provider call, **no**
     session/message writes.
3. Client on 429: block composer, show limit message + login prompt.

### 5.2 Guest logs in (linking already done); prior chat surfaces; capabilities unlock

1. Auth layer performs login and links (`linked_user_id`) — consumed.
2. Client now sends `Authorization: Bearer <JWT>`. `GET /api/chat/sessions` returns the user's
   sessions **including** linked guest sessions (read-time projection, §2.6) — the prior
   default chat appears.
3. UI unlocks: provider/model switcher visible, "+ New chat" enabled, multi-session list.
4. Resuming the surfaced session uses `GET /api/chat/sessions/{id}`; capabilities now derive
   from the authenticated caller (no guest quota/gating).

### 5.3 Authenticated user creates, lists, switches, resumes

1. `POST /api/chat/sessions` ⇒ new empty session (`ChatSessionOut`).
2. `GET /api/chat/sessions` ⇒ ordered list for the sidebar.
3. Switch = select a session id in the UI; **resume** via `GET /api/chat/sessions/{id}` to load
   the transcript into the reducer.
4. New turns send that `session_id`; appends continue the gap-free `seq`.

### 5.4 Append with idempotency (retry safety)

1. Client generates a stable `client_message_id` per submission.
2. On retry/reconnect it resends the **same** id; backend returns the prior assistant reply
   (no duplicate provider call, no duplicate `seq`), preserving quota/usage integrity.

### 5.5 Long conversation triggers summarization; next turn uses summary

1. After a completed turn crosses `SUMMARY_TRIGGER_MESSAGE_COUNT`, a summary is written
   (best-effort) and a `kind="summary"` usage event recorded.
2. The next turn's context = latest summary (system) + messages after `covers_through_seq`.
3. If summarization fails, the turn still succeeds; context falls back to available messages.

### 5.6 Streaming happy path and failure/interruption

- Happy path: `start` (with `session_id`) → `delta`\* → `end` frames; assistant message +
  usage persisted after stream completes.
- Provider error mid-stream: assistant message persisted `status="error"`, `error` frame
  emitted; client shows error + retry (reusing `client_message_id`).
- Client disconnect: backend detects via `is_disconnected()`, persists `status="interrupted"`;
  client shows interrupted + retry.

---

## 6) Frontend Architecture and UX

### 6.1 Identity-driven conditional UI

- Consume the auth/user state from the auth layer's context (delivered by the companion plan).
  Derive a single `capabilities` view-model: `{ isAuthenticated, canSwitchProvider,
canCreateChat, isMultiSession }`.
- All conditional UI keys off this view-model; backend remains authoritative on rejection.

### 6.2 Multi-session sidebar

- Replace the scaffolded, hardcoded sidebar in
  [ChatPage.tsx](../../frontend/src/pages/ChatPage.tsx) with real wiring:
  - On load (authenticated): fetch `GET /api/chat/sessions`; render list ordered by
    `last_message_at`.
  - Active-session state: `activeSessionId` drives which transcript is loaded.
  - "+ New chat" (authenticated only): `POST /api/chat/sessions`, then select it.
  - Guest presentation: a single, non-removable default chat; no create button; a login
    affordance.

### 6.3 Composer / provider-model switcher gating

- [Composer.tsx](../../frontend/src/components/Composer.tsx): show the provider/model switcher
  only when `canSwitchProvider`. For guests, omit `provider`/`model` from the request (server
  applies default). Keep existing switcher logic for authenticated users unchanged.

### 6.4 Quota-reached and session-resume UX

- Quota reached: on `429 quota_exceeded`, disable the send control, show a clear limit banner,
  and present a login prompt. Optionally show remaining count from `X-Guest-Quota-Remaining`
  (§3.1) as a soft warning beforehand.
- Session resume: selecting a session fetches its transcript and loads it into the reducer,
  showing a loading state while fetching.

### 6.5 State/reducer and API-client changes (proposed, no code)

- [chatClient.ts](../../frontend/src/api/chatClient.ts): attach identity headers
  (`Authorization: Bearer`, `X-Guest-Token`) from the auth layer; **read** `X-Guest-Token` and
  `session_id` from responses; add `session_id` + `client_message_id` to `ChatRequest`; add
  `listSessions()`, `createSession()`, and `getSession(id)` calls.
- [chatReducer.ts](../../frontend/src/state/chatReducer.ts) /
  [ChatContext.tsx](../../frontend/src/context/ChatContext.tsx): extend state with
  `activeSessionId`, `sessions` (list metadata), and a `LOAD_SESSION`/`SET_SESSIONS` action set;
  add a `quotaBlocked` flag. Keep existing streaming actions unchanged.
- [useChatStream.ts](../../frontend/src/hooks/useChatStream.ts): thread `session_id` +
  `client_message_id` through the stream request; capture `start.session_id` to persist the id
  for follow-up turns; surface `429` distinctly from other errors.

### 6.6 Error/edge UX

- Quota exceeded (429) → block + login prompt.
- Unauthorized/foreign session (404) → clear the active session, show "not found", refresh list.
- Expired identity → defer to the auth layer's fallback/re-auth (do not implement here).
- Network/stream failure → existing error banner + retry (idempotent via `client_message_id`).

---

## 7) Configuration, Secrets, and Environment

### 7.1 Backend env vars

Existing (reused): `CHAT_PERSISTENCE_ENABLED` (default `true`),
`SUMMARY_TRIGGER_MESSAGE_COUNT` (default `20`), `GUEST_DAILY_MESSAGE_QUOTA` (default `20`),
`LLM_PROVIDER` + per-provider keys/models (`OPENAI_*`, `GEMINI_*`, `GROQ_*`, `ANTHROPIC_*`),
`CORS_ALLOWED_ORIGINS`, `APP_ENV`, `MAX_MESSAGE_LENGTH`, `REQUEST_TIMEOUT_SECONDS`,
`DATABASE_URL`, `JWT_*`, `GOOGLE_CLIENT_ID`.

**[Proposed / Open] new (optional):** `GUEST_DEFAULT_PROVIDER`, `GUEST_DEFAULT_MODEL` (else
reuse `LLM_PROVIDER` + default model). No new required vars are strictly necessary.

**[Dependency]** CORS `expose_headers` must include `X-Guest-Token` (and
`X-Guest-Quota-Remaining` if adopted) — owned by the auth layer's CORS fix, consumed here.

### 7.2 Frontend env

- Reuse `VITE_API_BASE_URL`. Identity/config (`VITE_GOOGLE_CLIENT_ID`, token transport) come
  from the auth layer; not redefined here.

### 7.3 Staging vs production parity

- Railway (backend) + Vercel (frontend); separate databases per environment; `APP_ENV` set per
  environment; `CORS_ALLOWED_ORIGINS` lists the exact Vercel origins; `GUEST_DAILY_MESSAGE_QUOTA`
  and `SUMMARY_TRIGGER_MESSAGE_COUNT` may differ per environment for testing but default equal.

---

## 8) Health Checks, Observability, and Data Considerations

- **DB readiness:** persistence, quota, and session-list flows require the DB; reuse the
  existing readiness probe. Chat with `CHAT_PERSISTENCE_ENABLED=false` degrades to stateless.
- **Key metrics/logs:** quota denials (`429 quota_exceeded`), summary outcomes
  (success/failure, `kind="summary"` usage), token usage/estimated-cost trends, and
  session create/list/switch errors (`403 new_chat_forbidden`, `403 provider_not_allowed`,
  `404` ownership).
- **Data retention/privacy:** persisted `chat_messages`, `session_summaries`, and
  `usage_events` are for product/observability, **not billing**. Guest identities store only
  **hashed** tokens/IPs. Minimize PII in logs; do not log message content at info level.
- **Idempotency/integrity safeguards:** `client_message_id` (messages), `request_id`
  (usage dedup), atomic quota upsert, gap-free `seq`, and `covers_through_seq` boundary — all
  reused to prevent corruption on retries.

---

## 9) Testing and Validation Strategy

### 9.1 Backend (extend existing pytest suites + fakes)

- `test_chat_persistence.py`: session list returns only owned sessions and correct ordering;
  linked guest session surfaces for the authenticated user (read-time projection).
- New `test_chat_sessions.py` (or extend above): `GET /api/chat/sessions` shape/ordering;
  `POST /api/chat/sessions` authenticated-only (**403** for guest); guest single-default-chat
  enforcement (second-session attempt → 404/403).
- `test_guest_quota.py`: unchanged 429 pre-flight behavior + any new remaining-quota header.
- Provider gating: guest non-default provider/model → **403 `provider_not_allowed`** before any
  provider call; guest omitting provider/model uses the default.
- `test_summarization_and_linking.py`: add multi-session independence of `covers_through_seq`.
- `test_usage.py` / `test_chat_stream.py` / `test_chat_endpoint.py`: unchanged; ensure new
  gating does not regress streaming frames or usage recording. Reuse `tests/fakes.py`.

### 9.2 Frontend (extend reducer/hook/component tests)

- Sidebar: list render/order, switch, "+ New chat" (authenticated) — hidden for guest.
- Composer gating: switcher hidden for guest; visible + functional for authenticated
  (extend [Composer.test.tsx](../../frontend/src/components/Composer.test.tsx)).
- Quota-block UX: `429 quota_exceeded` blocks send + shows login prompt.
- Session resume: fetch + load transcript into reducer; loading state.
- Reducer: new `activeSessionId`/`sessions`/`quotaBlocked` actions
  (extend [chatReducer.test.ts](../../frontend/src/state/chatReducer.test.ts)).

### 9.3 E2E / manual smoke (staging)

Cover: guest quota block + login prompt; login continuity surfacing the prior chat;
authenticated create/list/switch/resume; summarization crossing the threshold. Extend
`scripts/smoke-tests.sh` where scriptable.

### 9.4 Acceptance criteria (per capability)

- Guest quota: 20th message succeeds, 21st returns 429 + UI block. Server writes nothing on 429.
- Provider gating: guest non-default provider/model → 403; switcher hidden in UI.
- New-chat restriction: guest `POST /api/chat/sessions` → 403; button hidden.
- Multi-session: authenticated user can create/list/switch/resume; ownership enforced (404 on
  foreign session).
- Continuity: after login, the guest's prior default chat appears in the list and resumes.
- Summarization: crossing threshold writes a summary + `kind="summary"` usage; next turn uses
  it; failure is non-fatal.

---

## 10) Security and Compliance Considerations

- **Server-authoritative gating** for quota, provider/model, and new-chat — never trust
  client-only checks. Rejection codes: `quota_exceeded` (429), `provider_not_allowed` (403),
  `new_chat_forbidden` (403).
- **Ownership enforcement** on every session read/list/switch/resume (owner-scoped queries;
  404 for foreign/unknown sessions to avoid existence leaks).
- **Guest privacy:** hashed tokens/IPs only; PII minimization in persisted records and logs.
- **CORS correctness:** exact frontend origins per environment; `expose_headers` for
  `X-Guest-Token` (auth-layer dependency).
- **Abuse/rate-limit posture:** daily quota + idempotency (`client_message_id`, `request_id`,
  atomic quota upsert) prevent quota/usage corruption under retries.

---

## 11) Incremental Delivery Plan

> The auth layer is a dependency, not a phase here. Each phase is an independently shippable
> full-stack slice. Recommended sequence: guest policy/quota UX + single-default-chat first,
> then multi-session, then summarization UX/validation.

### Phase 1 — Guest policy, quota UX, and provider gating (backend + frontend)

- **Objective:** Make guest limits real and visible; gate provider/model server-side.
- **Tasks:**
  - [ ] Enforce guest single-default-chat in `_resolve_session` and reject non-default
        `session_id` (404). _(backend)_
  - [ ] Add guest provider/model gating (403 `provider_not_allowed`) in the pre-flight path.
        _(backend)_
  - [ ] (Optional) Add `X-Guest-Quota-Remaining` response header. _(backend)_
  - [ ] Wire identity headers + `client_message_id` in `chatClient.ts`. _(frontend)_
  - [ ] Hide provider/model switcher for guests; handle `429 quota_exceeded` (block + login
        prompt). _(frontend)_
- **Deliverables:** backend gating + frontend quota/gating UX.
- **Repository impact:** change
  [app/services/chat_service.py](../../backend-python/app/services/chat_service.py),
  [app/routers/chat.py](../../backend-python/app/routers/chat.py) (error mapping),
  [app/services/quota_service.py](../../backend-python/app/services/quota_service.py) (header);
  [chatClient.ts](../../frontend/src/api/chatClient.ts),
  [Composer.tsx](../../frontend/src/components/Composer.tsx),
  [chatReducer.ts](../../frontend/src/state/chatReducer.ts). New tests as in §9.
- **Acceptance:** §9.4 quota + provider-gating criteria pass.
- **Validation:** pytest gating tests; frontend quota-block test; manual guest run to 21st msg.
- **Risk:** Low–Medium. Mitigation: keep changes behind existing persistence flag; additive
  request fields only.

### Phase 2 — Multi-session (list/create + sidebar wiring)

- **Objective:** Real multi-session for authenticated users; guest single-chat presentation.
- **Tasks:**
  - [ ] `GET /api/chat/sessions` (owner-scoped, ordered, optional cursor). _(backend)_
  - [ ] `POST /api/chat/sessions` (authenticated-only; 403 for guest). _(backend)_
  - [ ] Read-time projection of linked guest sessions for authenticated callers. _(backend)_
  - [ ] Wire sidebar list/switch/create; session resume via existing GET. _(frontend)_
  - [ ] Reducer/context: `activeSessionId`, `sessions`, load-session actions. _(frontend)_
- **Deliverables:** working create/list/switch/resume end-to-end.
- **Repository impact:** add list/create routes to
  [app/routers/chat.py](../../backend-python/app/routers/chat.py); add list-item schema to
  [app/schemas/chat.py](../../backend-python/app/schemas/chat.py); extend
  [app/db/chat.py](../../backend-python/app/db/chat.py) (list query incl. linked guests);
  [ChatPage.tsx](../../frontend/src/pages/ChatPage.tsx),
  [ChatContext.tsx](../../frontend/src/context/ChatContext.tsx),
  [chatReducer.ts](../../frontend/src/state/chatReducer.ts),
  [chatClient.ts](../../frontend/src/api/chatClient.ts). New tests as in §9.
- **Acceptance:** §9.4 multi-session + continuity criteria pass.
- **Validation:** pytest session tests; frontend sidebar/resume tests; staging smoke.
- **Risk:** Medium. Mitigation: read-time projection (no migration); ownership tests.

### Phase 3 — Summarization UX/validation and observability

- **Objective:** Confirm summarization across multi-session/guest and make it observable.
- **Tasks:**
  - [ ] Multi-session summarization test (independent boundaries). _(backend)_
  - [ ] Add logs/metrics for summary outcomes and quota denials. _(backend)_
  - [ ] (Optional) subtle UI signal that context is summarized (non-blocking). _(frontend)_
- **Deliverables:** validated, observable summarization; no semantic changes.
- **Repository impact:** extend
  [test_summarization_and_linking.py](../../backend-python/tests/test_summarization_and_linking.py);
  minor logging in
  [app/services/chat_service.py](../../backend-python/app/services/chat_service.py).
- **Acceptance:** §9.4 summarization criteria pass; observability visible in logs/usage.
- **Validation:** pytest; manual long-conversation run.
- **Risk:** Low.

---

## 12) Risks, Trade-offs, and Open Questions

**Risks & mitigations**

- **Gating enforcement points:** must reject before provider calls to protect quota/usage —
  centralize in the chat-service pre-flight; cover with tests.
- **Continuity surfacing:** read-time linked-session projection must not accidentally re-apply
  guest limits or leak across users — key capabilities off the current caller, scope queries by
  `user_id` + linked `guest_id`s only.
- **Summarization correctness across sessions:** ensure boundaries stay per-session — add
  multi-session tests.
- **Session-list growth:** unbounded lists — ship a simple cap now, cursor pagination when
  needed.

**Trade-offs**

- Read-time projection vs. re-owning migration: chosen read-time for reversibility/safety at a
  small query-complexity cost.
- Reuse system default for guest provider vs. explicit guest env vars: chosen reuse for
  minimal config surface.

**Open questions**

1. Include `message_count` and/or cursor pagination in the list endpoint for MVP? (§2.2)
2. Enforce guest single-chat at app level only, or add a DB partial-unique index? (§2.3)
3. Surface remaining quota via response header vs. config endpoint? (§3.1/§3.4)
4. Introduce `GUEST_DEFAULT_PROVIDER/MODEL` or reuse `LLM_PROVIDER`? (§3.2/§7.1)
5. Behavior of `GET /api/chat/sessions` when `CHAT_PERSISTENCE_ENABLED=false`? (§2.7)
6. Ship rename/delete now or defer? (§2.2)

---

## 13) Ready-to-Start Backlog (first 8–10 tasks)

> Dependency for all: the auth layer (Bearer/`X-Guest-Token` transport, auth state) is
> available/landing in parallel per `docs/plans/google-auth-implementation-plan.md`.

1. **Guest single-default-chat enforcement** — P0 · backend · **M** · DoD: guest cannot create
   or address a second session (tests for 404/reuse); no regression to existing persistence
   tests. Deps: none.
2. **Guest provider/model gating (403)** — P0 · backend · **M** · DoD: guest non-default
   provider/model rejected pre-provider-call with `provider_not_allowed`; guest default path
   works. Deps: none.
3. **Frontend identity headers + `client_message_id`** — P0 · frontend · **M** · DoD:
   `chatClient.ts` sends `Authorization`/`X-Guest-Token`, captures `X-Guest-Token`, sends
   `session_id`/`client_message_id`. Deps: auth transport.
4. **Quota-reached UX** — P0 · frontend · **S** · DoD: `429 quota_exceeded` blocks send + shows
   login prompt (component test). Deps: task 3.
5. **Composer gating for guests** — P0 · frontend · **S** · DoD: switcher hidden for guests,
   default provider used; visible for authenticated. Deps: auth state.
6. **`GET /api/chat/sessions` (list)** — P1 · backend · **M** · DoD: owner-scoped, ordered,
   lean schema; includes linked guest sessions for authenticated caller. Deps: none.
7. **`POST /api/chat/sessions` (create, authenticated-only)** — P1 · backend · **S** · DoD:
   creates empty session; guest → 403 `new_chat_forbidden`. Deps: none.
8. **Sidebar wiring (list/switch/create)** — P1 · frontend · **L** · DoD: real list from API,
   switch loads transcript, "+ New chat" creates + selects; guest single-chat presentation.
   Deps: tasks 6–7.
9. **Session resume wiring** — P1 · frontend · **M** · DoD: selecting a session fetches
   `GET /api/chat/sessions/{id}` and loads transcript with loading state. Deps: task 8.
10. **Continuity read-time projection + tests** — P1 · fullstack · **M** · DoD: after login the
    prior guest chat appears in the list and resumes; ownership tests pass. Deps: tasks 6, 8.

---

## 14) Key Architecture Decisions

**D1 — Session-list shape & pagination**

- **Decision:** Lean owner-scoped list ordered by `last_message_at DESC`; ship a simple cap with
  an optional cursor param (single page acceptable for MVP).
- **Rationale:** Cheap sidebar; avoids premature pagination complexity.
- **Rejected:** Full message payloads in the list; mandatory pagination.
- **Reconsider when:** A user's session count regularly exceeds one page.

**D2 — Guest-gating enforcement point**

- **Decision:** Enforce quota/provider/new-chat in the **backend chat-service pre-flight**,
  before any provider call; UI gating is convenience only.
- **Rationale:** Server-authoritative; protects quota/usage integrity.
- **Rejected:** UI-only gating; enforcement after provider calls.
- **Reconsider when:** Introducing per-tier plans requiring richer policy.

**D3 — Guest provider/model default**

- **Decision:** Reuse system default (`LLM_PROVIDER` + its model); optional explicit guest env
  vars later.
- **Rationale:** Minimal config surface; matches current behavior.
- **Rejected:** New required `GUEST_DEFAULT_*` vars now.
- **Reconsider when:** Product wants a distinct guest model from the system default.

**D4 — Summarization trigger/boundary reuse**

- **Decision:** Reuse `SUMMARY_TRIGGER_MESSAGE_COUNT` + `covers_through_seq` unchanged;
  per-session boundaries.
- **Rationale:** Proven, deterministic, tested; multi-session needs no change.
- **Rejected:** New summarization strategy for multi-session.
- **Reconsider when:** Context-window pressure demands adaptive summarization.

**D5 — Idempotency strategy**

- **Decision:** Reuse `client_message_id` (messages), `request_id` (usage), atomic quota upsert,
  gap-free `seq`.
- **Rationale:** Prevents duplicate writes/quota corruption on retries.
- **Rejected:** New idempotency layer.
- **Reconsider when:** Introducing multi-node write paths needing distributed coordination.

**D6 — Guest→authenticated continuity surfacing**

- **Decision:** Read-time projection of sessions from `guest_identities.linked_user_id` for
  authenticated callers; no ownership migration.
- **Rationale:** Reversible, preserves `owner_xor`, no migration risk.
- **Rejected:** One-time re-owning migration at login.
- **Reconsider when:** Query complexity or performance of the projection becomes a bottleneck.
