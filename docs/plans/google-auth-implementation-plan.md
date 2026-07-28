# Full Google Authentication Flow — Implementation Plan

> Planning document only. No code is written and no files are modified by this plan.
> Grounded in the actual repository state (verified July 2026). Where this plan
> references existing files, those files were read and confirmed. Where it proposes
> changes, they are labelled **[Proposed]**. Items needing confirmation are labelled
> **[Verify]** and open decisions **[Open]**.

Scope boundary: **authentication only**. Chat persistence, multi-session, summarization,
and guest daily-quota **UX** belong to a separate chat-experience plan and are explicitly
deferred here. This plan only ensures authentication does not break those contracts and
that guest→user linking happens losslessly at login for that later phase to build on.

---

## 1) Objective, Scope, and Non-Goals

### 1.1 Objective

Deliver the complete Google authentication flow end-to-end against the **active Python
FastAPI backend** and the **React frontend**: Google login, app-JWT lifecycle,
authenticated vs. guest request identity, logout, and the auth-side of guest→user
continuity — working across local, staging, and production origins.

The backend authentication core already exists and is well-tested. The remaining work is
**predominantly frontend**, plus a **small number of targeted backend additions**.

### 1.2 In scope

- Google credential acquisition in the browser and exchange via existing
  [`POST /api/auth/google`](backend-python/app/routers/auth.py#L58-L73).
- App JWT (HS256, stateless) issuance/verification — reuse existing
  [`create_access_token` / `decode_access_token`](backend-python/app/core/security.py#L57-L92).
- Client token storage/transport and attachment as `Authorization: Bearer`.
- Guest identity continuity via existing `X-Guest-Token` + `guest_identities.linked_user_id`.
- Logout returning the client to the guest tier.
- Expiry/invalid-token graceful fallback to guest + re-auth path.
- Auth configuration, secrets, and CORS across local/staging/production (Railway backend,
  Vercel frontend).

### 1.3 Out of scope (defer to chat-experience plan)

- Creating/listing/switching chat sessions; message persistence semantics; summarization;
  guest daily message-quota enforcement UX. These are referenced only where authentication
  must not break them.

### 1.4 Constraints and assumptions

- Single active backend is `backend-python/` (FastAPI). `backend-nodejs/` is **paused —
  out of scope**.
- The stateless **HS256 app JWT** model is an established decision; do **not** introduce
  refresh tokens, opaque tokens, server-side session tables, revocation lists, or Redis
  sessions unless a correctness/security defect forces it. Any such idea is recorded as a
  non-goal / future evolution.
- Server-side verification is authoritative; the frontend never self-asserts identity.
- No plaintext secrets in the repo. `GOOGLE_CLIENT_ID` / `VITE_GOOGLE_CLIENT_ID` are not
  secrets (client-side/public); `JWT_SECRET` is a secret.
- Deployment model is Docker Compose / Railway / Vercel (no Kubernetes).

### 1.5 Current-state assessment

**Backend (`backend-python/`) — implemented and tested:**

| Capability                                                                                   | State       | Location                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/auth/google` endpoint                                                             | Implemented | [app/routers/auth.py](backend-python/app/routers/auth.py)                                                                                                                                                             |
| Google ID-token verification + user create/update                                            | Implemented | [app/services/auth_service.py](backend-python/app/services/auth_service.py)                                                                                                                                           |
| App JWT issuance/verification (HS256, stateless)                                             | Implemented | [app/core/security.py](backend-python/app/core/security.py)                                                                                                                                                           |
| Auth error contracts (`invalid_google_token`, `invalid_access_token`, `auth_not_configured`) | Implemented | [app/core/security.py](backend-python/app/core/security.py#L28-L56)                                                                                                                                                   |
| Caller resolution (Bearer JWT → user, else guest via `X-Guest-Token`)                        | Implemented | [app/core/caller.py](backend-python/app/core/caller.py#L122-L143)                                                                                                                                                     |
| Guest token mint/hash, IP hash                                                               | Implemented | [app/core/security.py](backend-python/app/core/security.py#L95-L118)                                                                                                                                                  |
| Guest→user linking at login (`linked_user_id`, link-only)                                    | Implemented | [app/services/auth_service.py](backend-python/app/services/auth_service.py#L173-L188), [app/db/identity.py](backend-python/app/db/identity.py#L127-L134)                                                              |
| Auth schemas (`GoogleLoginRequest`, `TokenResponse`, `AuthenticatedUser`)                    | Implemented | [app/schemas/auth.py](backend-python/app/schemas/auth.py)                                                                                                                                                             |
| Auth config/env + non-dev `JWT_SECRET` validation                                            | Implemented | [app/core/config.py](backend-python/app/core/config.py#L37-L72)                                                                                                                                                       |
| Chat endpoints echo minted guest token via `X-Guest-Token` response header                   | Implemented | [app/routers/chat.py](backend-python/app/routers/chat.py#L78-L81)                                                                                                                                                     |
| Auth tests + guest-linking tests + fake Google verifier                                      | Implemented | [tests/test_auth.py](backend-python/tests/test_auth.py), [tests/test_summarization_and_linking.py](backend-python/tests/test_summarization_and_linking.py#L186-L230), [tests/fakes.py](backend-python/tests/fakes.py) |

**Backend — gaps (true remaining work):**

- **Logout endpoint**: does **not** exist. `auth.py` exposes only `POST /api/auth/google`.
  Decision required on whether logout needs any server call at all (see Section 3.4 / 13).
- **CORS `expose_headers`**: [app/main.py](backend-python/app/main.py#L39-L45) configures
  `CORSMiddleware` **without** `expose_headers`. Cross-origin browser JS therefore
  **cannot read** the `X-Guest-Token` response header the chat router sets. This blocks the
  client from ever capturing a server-minted guest token in staging/production. **Targeted
  backend fix required.** **[Proposed]**
- **CORS methods**: currently `allow_methods=["GET", "POST"]`. Adequate for auth today;
  revisit only if logout uses a different method.

**Frontend (`frontend/`) — confirmed missing:**

- No login UI, no auth/user state, no “Why login?” affordance. [App.tsx](frontend/src/App.tsx)
  renders only [ChatPage](frontend/src/pages/ChatPage.tsx).
- [chatClient.ts](frontend/src/api/chatClient.ts) sends only `Content-Type` — it does **not**
  attach `Authorization` or `X-Guest-Token`, and does **not** read the `X-Guest-Token`
  response header. So even **guest continuity is not wired on the client today**.
- [ChatContext.tsx](frontend/src/context/ChatContext.tsx) holds only the chat reducer; no auth state.
- Env exposes only `VITE_API_BASE_URL` ([.env.example](frontend/.env.example),
  [.env.required](frontend/.env.required)); there is **no** `VITE_GOOGLE_CLIENT_ID`.

**True remaining gaps summary:** (1) all frontend auth UX + state + token/guest-token
transport; (2) backend `expose_headers` for `X-Guest-Token`; (3) a logout decision
(likely client-only, no endpoint); (4) `VITE_GOOGLE_CLIENT_ID` wiring + Google OAuth client
origins; (5) CORS origin alignment for real Vercel origins per environment.

### 1.6 Non-goals (future evolution only)

Refresh-token rotation, distributed/stateful session stores, token revocation lists,
multi-provider (multi-IdP) auth, account management, password auth, RBAC / fine-grained
permissions, and OAuth server-side session management. None are required by the Target
Authentication Behavior; record as future evolution, not this phase.

---

## 2) Google Login Flow (End-to-End)

### 2.1 Frontend credential acquisition **[Proposed]**

Use **Google Identity Services (GIS)** to obtain a **Google ID token (JWT)** in the browser.
Chosen surface: the **GIS rendered Sign-In button** (optionally One Tap later). The client
obtains only the ID token via the GIS `credential` callback and sends it to the backend —
it never inspects or trusts the token for identity.

- Load the GIS library (`https://accounts.google.com/gsi/client`) and initialize with
  `VITE_GOOGLE_CLIENT_ID`.
- On the `credential` callback, POST the credential to the backend.
- One Tap is deferred as an enhancement (Section 11 open question) to keep the first slice small.

**Client → backend request** (matches existing schema
[`GoogleLoginRequest`](backend-python/app/schemas/auth.py#L11-L12)):

```
POST /api/auth/google
Authorization: (none)
X-Guest-Token: <raw guest token, if the client currently holds one>
Content-Type: application/json
{ "id_token": "<google-id-token>" }
```

### 2.2 Backend verification (existing — reuse, do not rebuild)

[`login_with_google`](backend-python/app/routers/auth.py#L58-L73) reads the optional
`X-Guest-Token` header and calls
[`AuthService.login_with_google`](backend-python/app/services/auth_service.py#L138-L171):
verify ID token (signature + audience = `GOOGLE_CLIENT_ID`) →
resolve by `auth_provider='google'` + `external_auth_id=sub` → create or refresh profile →
link presenting guest (Section 4) → issue app JWT.

### 2.3 Response contract consumed by the client (existing)

[`TokenResponse`](backend-python/app/schemas/auth.py#L23-L27):

```jsonc
{
  "access_token": "<app jwt>",
  "token_type": "bearer",
  "expires_in": 3600, // jwt_access_token_expires_minutes * 60
  "user": {
    "id": "<uuid>",
    "email": "...",
    "display_name": "...",
    "picture_url": "...",
  },
}
```

**Client transition guest → authenticated:** store `access_token` (Section 3), set auth
state from `user`, and attach `Authorization: Bearer <access_token>` on subsequent requests.
Guest-token client handling on login is specified in Section 4.3.

### 2.4 Backend gaps to close for a complete flow

- **CORS `expose_headers` = `["X-Guest-Token"]`** so the client can read the minted guest
  token cross-origin (Section 6.4). **[Proposed]**
- No change needed to the endpoint, service, schemas, or verification path — they already
  satisfy the requirement. Do not duplicate them.

---

## 3) App Token Lifecycle and Session Handling

### 3.1 Issuance parameters (existing — reuse)

Reuse [config](backend-python/app/core/config.py#L39-L44): `JWT_ALGORITHM=HS256`,
`JWT_ACCESS_TOKEN_EXPIRES_MINUTES=60`, signed with `JWT_SECRET`. Payload is
`sub=user_id, iat, exp` (see
[`create_access_token`](backend-python/app/core/security.py#L57-L70)). No changes proposed.

### 3.2 Client token storage and transport decision **[Open → recommended]**

**Recommendation for the MVP: store the app JWT in `localStorage`, transported as
`Authorization: Bearer`.** Rationale and trade-offs:

| Option                                     | XSS exposure                      | CSRF exposure                      | Survives reload            | Cross-origin fit (Vercel↔Railway)                                       | Verdict                                                                   |
| ------------------------------------------ | --------------------------------- | ---------------------------------- | -------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| In-memory only                             | Lowest (token gone on XSS reload) | None (no cookie)                   | No (re-login every reload) | Simple                                                                  | Safe but poor UX                                                          |
| **localStorage + `Authorization: Bearer`** | Readable by XSS                   | None (not a cookie; not auto-sent) | Yes                        | Simple; no cookie/CORS-credentials complexity                           | **Chosen**                                                                |
| `HttpOnly` cookie                          | Not readable by JS                | Needs CSRF defense                 | Yes                        | Requires `allow_credentials=True` + exact-origin CORS + SameSite tuning | Rejected for MVP (adds stateful-cookie complexity the prompt discourages) |

The dominant residual risk is **XSS** (mitigations in Section 9). CSRF is not a concern
because the token is sent via an explicit header, not an ambient cookie, and the backend
uses `allow_credentials=False`. This choice keeps CORS simple and avoids server-side session
machinery. The final storage choice is recorded as a decision in Section 13.

Client guest token (`X-Guest-Token`) is stored the same way (`localStorage`) — see Section 4.

### 3.3 Expiry handling and re-authentication path

The backend already treats an expired/invalid app JWT as **anonymous**, not an error:
[`get_current_caller`](backend-python/app/core/caller.py#L122-L143) falls through to guest
resolution on `InvalidAccessTokenError`. Client behavior **[Proposed]**:

- The client cannot cheaply detect expiry locally without decoding; the robust trigger is
  server response. When an authenticated request returns an auth error contract
  (`invalid_access_token`) **or** the caller silently degrades to guest, the client:
  1. Clears the stored app JWT and auth state (returns to guest tier — a valid tier).
  2. Surfaces a non-blocking “Your session expired — sign in again” affordance.
  3. Continues functioning as guest (chat still works).
- Optional lightweight improvement: decode `exp` client-side to pre-emptively clear state
  and show the re-login prompt. **[Open]** — not required for correctness.
- No silent refresh (no refresh tokens by decision). Re-auth = re-run the Google login flow.

### 3.4 Logout semantics **[Decision — Section 13]**

Because the app JWT is stateless with no revocation infrastructure, **logout is a
client-only token discard** for the MVP. Exact client teardown steps:

1. Remove the app JWT from storage.
2. Clear in-memory auth/user state (return UI to guest affordances).
3. **Retain** the guest token (Section 4.3) so the user resumes a coherent guest identity.
4. No backend call. **No `/api/auth/logout` endpoint is introduced** unless a later
   requirement (e.g., server-side revocation) forces it — explicitly a non-goal now.

Justification: a stateless JWT cannot be server-invalidated without a revocation store,
which the prompt forbids adding absent a forcing requirement; the token simply expires at
`exp`. This is standard and acceptable for a portfolio MVP.

---

## 4) Guest Identity and Guest→User Linking (Auth Side Only)

### 4.1 Guest identity lifecycle (existing backend, new client wiring)

- Server owns guest identity: [`resolve_guest_caller`](backend-python/app/core/caller.py#L101-L120)
  mints a high-entropy token, stores only its **SHA-256 hash** (`guest_identities.token_hash`),
  and returns the **raw** token once via the `X-Guest-Token` response header (set by
  [`_set_guest_token`](backend-python/app/routers/chat.py#L78-L81)).
- **Client gap [Proposed]:** the client must (a) **read** `X-Guest-Token` from responses and
  persist the raw token, and (b) **send** it as the `X-Guest-Token` request header on
  subsequent requests. This is not implemented today and must be added in
  [chatClient.ts](frontend/src/api/chatClient.ts).

### 4.2 Linking behavior at login (existing — reuse)

[`_maybe_link_guest`](backend-python/app/services/auth_service.py#L173-L188) sets
`guest_identities.linked_user_id` — **link only, no ownership migration** (migration belongs
to the chat-experience phase). Edge cases and their current behavior:

| Edge case                                      | Current backend behavior                              | Assessment                                                                                                                                                         |
| ---------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No guest token presented                       | Returns `None`, no link (covered by test)             | Correct                                                                                                                                                            |
| Unknown/expired guest token (no matching hash) | `get_by_token_hash` → `None` → no link, no error      | Correct (idempotent, fail-soft)                                                                                                                                    |
| Already-linked guest                           | `link_to_user` re-sets `linked_user_id` to same/again | Idempotent for same user; **[Verify]** behavior if a guest token is re-linked to a _different_ user (last-writer-wins) — acceptable for MVP; note as open question |
| Repeated logins (same guest+user)              | Re-links to same user id                              | Idempotent, harmless                                                                                                                                               |
| Multiple guest tokens over time                | Only the _currently presented_ token is linked        | Acceptable; older guest identities remain unlinked — hand-off note for chat-experience phase                                                                       |

No backend redesign proposed. Optionally add explicit test coverage for the
already-linked/different-user case (Section 8).

### 4.3 Client guest-token lifecycle across login/logout **[Proposed]**

| Event                     | `X-Guest-Token` client behavior                                                                                                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| First guest request       | Send none; capture minted token from response header; persist it                                                                                                                                             |
| Subsequent guest requests | Send stored token                                                                                                                                                                                            |
| At login                  | Send stored guest token on `POST /api/auth/google` so the backend links it                                                                                                                                   |
| After successful login    | **Retain** the guest token in storage (do not delete) — enables later history attachment by the chat-experience phase and preserves continuity if the user logs out                                          |
| Authenticated requests    | Send `Authorization: Bearer` (primary identity); may still send `X-Guest-Token` harmlessly, but it is ignored when a valid JWT is present ([caller precedence](backend-python/app/core/caller.py#L122-L143)) |
| At logout                 | Keep the guest token; drop only the app JWT (Section 3.4)                                                                                                                                                    |

Hand-off note (do **not** design here): actual migration/attachment of prior guest chat
history to the user is owned by the chat-experience plan. This plan guarantees the link
exists and the guest token is preserved losslessly.

---

## 5) Frontend Auth Architecture and UX

### 5.1 Auth/user state management **[Proposed]**

Introduce a dedicated **`AuthContext`** (new, e.g. `frontend/src/context/AuthContext.tsx`)
separate from [ChatContext](frontend/src/context/ChatContext.tsx) to avoid entangling auth
with the chat reducer and to minimize chat-experience-phase churn. Responsibilities:

- Hold `{ status: 'guest' | 'authenticated', user: AuthenticatedUser | null, accessToken }`.
- Expose `login(idToken)`, `logout()`, and expiry handling.
- Persist/rehydrate app JWT and guest token from storage on load (Section 3.2, 4.3).
- Be the single source of truth driving conditional UI (Login vs. Logout, and future
  capability gating).

`AuthProvider` wraps the app in [App.tsx](frontend/src/App.tsx) above `ChatPage`/`ChatProvider`.

### 5.2 Landing/chat page affordances **[Proposed]**

Chat remains the default landing surface (no auth wall). Add to the chat page header/top bar:

- **Login** action (renders the GIS button / opens the login surface) — shown when guest.
- **“Why login?”** informational affordance (tooltip/popover/modal) explaining benefits
  (e.g., saved history later) — UX-only copy; no backend dependency.
- **Logout** action + minimal user indicator (`display_name`/`picture_url`) — shown when
  authenticated.

New components (illustrative): `LoginButton`, `WhyLoginInfo`, `UserMenu`/`AuthControls`
under `frontend/src/components/`.

### 5.3 State transitions

Guest → (GIS credential → `POST /api/auth/google` → 200) → Authenticated.
Authenticated → (logout, or expiry fallback) → Guest. All transitions flow through
`AuthContext`; components subscribe rather than manage tokens directly.

### 5.4 Client changes required **[Proposed]**

- [chatClient.ts](frontend/src/api/chatClient.ts): centralize header construction so every
  request attaches `Authorization: Bearer <jwt>` when authenticated and `X-Guest-Token`
  when a guest token is held; read `X-Guest-Token` from responses and persist it. This
  affects `sendChat`, `streamChat`, and any session-transcript fetch. Add a new auth client
  module (e.g. `frontend/src/api/authClient.ts`) for `POST /api/auth/google`.
- [useChatStream.ts](frontend/src/hooks/useChatStream.ts): unchanged in logic, but inherits
  the header wiring via `streamChat`. Must surface auth errors so `ChatPage` can trigger the
  expiry/re-auth path (Section 3.3).
- [ChatContext.tsx](frontend/src/context/ChatContext.tsx): unchanged; auth lives in
  `AuthContext`. Chat components read auth via a hook where needed (e.g., header controls).

### 5.5 Error / edge UX (use the **actual** backend error codes)

Confirmed codes from [security.py](backend-python/app/core/security.py#L28-L56):

| Code                           | HTTP | UX                                                                                                                   |
| ------------------------------ | ---- | -------------------------------------------------------------------------------------------------------------------- |
| `auth_not_configured`          | 503  | “Login is temporarily unavailable.” Hide/disable Login button; log; do not crash chat.                               |
| `invalid_google_token`         | 401  | “Google sign-in failed. Please try again.” Stay guest.                                                               |
| `invalid_access_token`         | 401  | Treat as expiry: clear JWT, drop to guest, prompt re-login (Section 3.3).                                            |
| Network failure (fetch reject) | —    | Reuse existing connection-error message pattern in [ChatPage](frontend/src/pages/ChatPage.tsx#L16-L22); allow retry. |

Error envelope is the standard `{ "error": { "code", "message" } }` already parsed by
[`toChatApiError`](frontend/src/api/chatClient.ts#L35-L55).

---

## 6) Configuration, Secrets, and Environment

### 6.1 Backend auth env vars (existing — reuse)

From [config.py](backend-python/app/core/config.py) and
[.env.example](backend-python/.env.example):

| Var                                | Default                 | Required in non-dev                                                                                   | Notes                                                                                             |
| ---------------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `GOOGLE_CLIENT_ID`                 | empty                   | Yes (endpoint fails closed `503` if unset)                                                            | Public client ID; enforced by [`get_google_verifier`](backend-python/app/routers/auth.py#L28-L33) |
| `JWT_SECRET`                       | dev-insecure default    | **Yes** — non-dev boot fails if left default ([validator](backend-python/app/core/config.py#L64-L72)) | Secret; from Railway env/secret store                                                             |
| `JWT_ALGORITHM`                    | `HS256`                 | —                                                                                                     | Keep HS256                                                                                        |
| `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` | `60`                    | —                                                                                                     | Tune if needed                                                                                    |
| `CORS_ALLOWED_ORIGINS`             | `http://localhost:5173` | Yes (exact frontend origins)                                                                          | Comma-split ([list prop](backend-python/app/core/config.py#L74-L80))                              |
| `APP_ENV`                          | `development`           | Set `staging`/`production`                                                                            | Gates `JWT_SECRET` validation                                                                     |

**New backend config [Proposed]:** none required as env vars. The only backend change is
`expose_headers=["X-Guest-Token"]` on the CORS middleware (code, not env).

### 6.2 New frontend env var **[Proposed]**

`VITE_GOOGLE_CLIENT_ID` — the Google OAuth **client ID** (public, not a secret). Add to
[.env.example](frontend/.env.example) and [.env.required](frontend/.env.required).
Injected at **build time** by Vite (`import.meta.env.VITE_GOOGLE_CLIENT_ID`), matching the
existing `VITE_API_BASE_URL` pattern — including Docker `--build-arg` (see
[.env.required](frontend/.env.required) note) and Vercel build-time environment variables.

### 6.3 Google Cloud OAuth client configuration

Create/confirm one **OAuth 2.0 Web client** in Google Cloud Console:

- **Authorized JavaScript origins** = the exact **frontend** origins (scheme+host, no path,
  no trailing slash) for each environment: local (`http://localhost:5173`), staging Vercel
  origin, production Vercel origin. (Deploy docs currently use placeholder hosts such as
  `<staging-frontend-host>` / `<prod-frontend-host>` — substitute the real Vercel origins;
  do not invent them here.) **[Verify]**
- **Authorized redirect URIs**: GIS token/One-Tap flows for ID tokens generally do not
  require classic redirect URIs; configure only if a redirect-based flow is later adopted.
  **[Verify]** for the exact GIS mode chosen.
- **Only the client ID is needed** — never the client secret. ID-token verification is
  signature+audience validation against Google’s public keys (done by `google-auth` in
  [`GoogleIDTokenVerifier`](backend-python/app/services/auth_service.py#L79-L114)); no
  authorization-code exchange occurs, so no client secret is used anywhere.

### 6.4 CORS and environment parity

- Frontend on **Vercel**, backend on **Railway**. Set backend `CORS_ALLOWED_ORIGINS` to the
  **exact** Vercel frontend origins per environment (staging vs production) — scheme+host,
  no trailing slash — matching the Google JavaScript origins one-to-one.
- Add `expose_headers=["X-Guest-Token"]` so the browser can read the minted guest token
  ([main.py CORS](backend-python/app/main.py#L39-L45)). Keep `allow_credentials=False`
  (Bearer header transport, not cookies). `allow_headers=["*"]` already permits the
  `Authorization` and `X-Guest-Token` **request** headers. **[Proposed]**
- Local dev: keep `http://localhost:5173`. Ensure any `http://localhost` variant used in
  Docker Compose is also listed (see [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md)).

---

## 7) Key Authentication Flows (Happy Path + Failure Paths)

### 7.1 Guest logs in (happy path)

1. Guest loads chat (default landing). Client sends stored `X-Guest-Token` if held; captures
   any minted token from the response header.
2. Guest clicks **Login**; GIS returns a Google ID token.
3. Client `POST /api/auth/google` with `{ id_token }` and current `X-Guest-Token`.
4. Backend verifies token, creates/refreshes user, links guest, issues app JWT
   ([AuthService](backend-python/app/services/auth_service.py#L138-L171)).
5. Client stores JWT + user, switches to authenticated UI, attaches `Authorization: Bearer`
   thereafter. Failure at step 3/4 → Section 7.5.

### 7.2 Guest with existing `X-Guest-Token` logs in (lossless link)

As 7.1, but the presented guest token matches a stored hash →
[`link_to_user`](backend-python/app/db/identity.py#L127-L134) sets `linked_user_id`. No
migration here; guest token retained client-side (Section 4.3). Unknown/expired token →
no link, no error (fail-soft).

### 7.3 Authenticated request — valid vs. expired JWT

- **Valid:** `Authorization: Bearer` → [`get_current_caller`](backend-python/app/core/caller.py#L122-L143)
  resolves an authenticated caller.
- **Expired/invalid:** same code path silently degrades to **guest** (no hard error). Client
  detects the degradation / `invalid_access_token` contract, clears the JWT, drops to guest,
  and prompts re-login (Section 3.3). Chat keeps working as guest.

### 7.4 Logout

Client discards the app JWT + auth state; retains the guest token; no backend call
(Section 3.4). UI returns to guest affordances.

### 7.5 Google verification failure / misconfiguration

- `GOOGLE_CLIENT_ID` unset → [`get_google_verifier`](backend-python/app/routers/auth.py#L28-L33)
  raises `AuthConfigError` → `503 auth_not_configured`. Client hides/disables Login and shows
  “Login temporarily unavailable”; chat unaffected.
- Bad/forged/expired Google token → `InvalidGoogleTokenError` → `401 invalid_google_token`.
  Client shows “Google sign-in failed, try again”; stays guest.

---

## 8) Testing and Validation Strategy

### 8.1 Backend (extend existing suites)

- Extend [tests/test_auth.py](backend-python/tests/test_auth.py) with the existing fakes
  ([FakeGoogleVerifier/FakeUserStore/FakeGuestStore](backend-python/tests/fakes.py)):
  - Login attaches `Authorization` transport is a client concern; assert token round-trip
    already covered ([`test_access_token_roundtrip`](backend-python/tests/test_auth.py#L191-L195)).
  - Add: guest already linked to a **different** user (last-writer-wins) — document/lock the
    intended behavior (Section 4.2). **[Proposed]**
  - Add: CORS `X-Guest-Token` is exposed (integration assertion on response headers /
    middleware config). **[Proposed]**
- Guest-linking coverage already exists in
  [tests/test_summarization_and_linking.py](backend-python/tests/test_summarization_and_linking.py#L186-L230)
  (links presenting guest; no-link without token; endpoint links from header) — keep and
  extend for edge cases only.
- If (and only if) a logout endpoint were introduced, add endpoint tests. Current decision:
  **no endpoint**, so no new endpoint tests.

### 8.2 Frontend (new)

- `AuthContext`: guest↔authenticated transitions, storage rehydrate, expiry clear.
- Login/Logout UI: renders Login when guest, Logout+user when authenticated; “Why login?”
  affordance present.
- Token attachment: requests carry `Authorization: Bearer` when authenticated and
  `X-Guest-Token` when held; response `X-Guest-Token` is captured and persisted.
- Guest-token lifecycle: retained across login and logout.
- Expired-token fallback: `invalid_access_token` clears JWT, drops to guest, prompts re-login,
  chat still functions.

### 8.3 Manual staging smoke checklist

Extend [scripts/smoke-tests.sh](scripts/smoke-tests.sh) scope / manual run:

- Google login end-to-end from the real Vercel origin.
- Authenticated chat request succeeds with `Authorization: Bearer`.
- Logout returns to guest; chat still works.
- Guest→user link verified (DB: `guest_identities.linked_user_id` set for the presented
  token’s hash).
- CORS: browser can read `X-Guest-Token`; preflight/actual requests succeed from the exact
  Vercel origin; no wildcard/trailing-slash drift.
- Misconfig path: with `GOOGLE_CLIENT_ID` unset, endpoint returns `503 auth_not_configured`.

### 8.4 Acceptance criteria (per capability)

- **Login:** valid Google credential yields a stored app JWT and authenticated UI; user
  profile shown.
- **Authenticated request:** backend resolves `user` caller for valid JWT.
- **Expiry fallback:** invalid/expired JWT → guest tier without hard failure + re-login prompt.
- **Logout:** JWT cleared, guest token retained, UI reverts to guest.
- **Linking:** presenting guest token at login sets `linked_user_id` idempotently.
- **Config/CORS:** works from local, staging, production origins; `X-Guest-Token` readable.

---

## 9) Security and Compliance Considerations

- **Token storage/transport:** `localStorage` + `Authorization: Bearer` (Section 3.2).
  Dominant risk is **XSS**. Mitigations: React’s default output escaping, no `dangerouslySet
InnerHTML` with untrusted content, dependency hygiene, and a tight CSP where feasible.
  CSRF is not applicable (no ambient cookie; `allow_credentials=False`).
- **Server-authoritative verification:** Google ID token verified server-side (signature +
  audience) and app JWT validated server-side; the client never self-asserts identity
  ([caller.py](backend-python/app/core/caller.py), [auth_service.py](backend-python/app/services/auth_service.py)).
- **PII minimization:** persist only `email`, `display_name`, `picture_url`, and the Google
  `sub` ([User model](backend-python/app/db/models.py#L50-L84)). No tokens stored server-side.
  Guest tokens and IPs are stored **hashed** (SHA-256) via
  [`hash_token`/`hash_ip`](backend-python/app/core/security.py#L95-L118).
- **Secrets hygiene:** `JWT_SECRET` from env/secret store; non-dev boot fails on the insecure
  default. `GOOGLE_CLIENT_ID`/`VITE_GOOGLE_CLIENT_ID` are public, not secrets. No client
  secret anywhere (Section 6.3).
- **CORS correctness:** exact per-environment frontend origins; no trailing slashes; expose
  only `X-Guest-Token`; keep methods minimal.
- **Linking robustness/idempotency:** link-only, fail-soft on unknown tokens, idempotent on
  repeats (Section 4.2) — prevents duplicate/inconsistent links.

---

## 10) Incremental Delivery Plan

Sequenced so a minimal, verifiable **login → authenticated → logout** slice lands first,
then guest-linking robustness, then full error/edge UX and environment hardening.

### Phase 0 — Backend CORS + config prerequisites

- **Objective:** unblock cross-origin guest-token capture and frontend config.
- **Tasks:** add `expose_headers=["X-Guest-Token"]` to CORS; add `VITE_GOOGLE_CLIENT_ID` to
  frontend env examples; document Google OAuth client origins.
- **Deliverables:** updated [main.py](backend-python/app/main.py) CORS; updated
  [frontend/.env.example](frontend/.env.example) / [.env.required](frontend/.env.required).
- **Repo impact:** change `backend-python/app/main.py`; `frontend/.env.example`,
  `frontend/.env.required`; docs note. No new backend files.
- **Acceptance:** browser can read `X-Guest-Token`; frontend build picks up client ID.
- **Validation:** integration check on exposed header; local build with the new var.
- **Risk:** Low. **Mitigation:** header-only change; no contract change.

### Phase 1 — Minimal login → authenticated → logout slice (frontend)

- **Objective:** end-to-end Google login, JWT storage, `Authorization` attachment, logout.
- **Tasks:** add GIS loader + `LoginButton`; add `AuthContext`/`AuthProvider`; add
  `authClient` for `POST /api/auth/google`; wire `Authorization` in `chatClient`; add Logout.
- **Deliverables:** `frontend/src/context/AuthContext.tsx`, `frontend/src/api/authClient.ts`,
  `LoginButton`/`AuthControls` components; edits to
  [App.tsx](frontend/src/App.tsx), [chatClient.ts](frontend/src/api/chatClient.ts),
  [ChatPage.tsx](frontend/src/pages/ChatPage.tsx).
- **Repo impact:** new frontend files above; edits to App/ChatPage/chatClient.
- **Acceptance:** login yields authenticated UI; authenticated request carries Bearer; logout
  reverts to guest.
- **Validation:** frontend unit/component tests (Section 8.2); manual local run.
- **Risk:** Medium (GIS integration). **Mitigation:** start with the rendered button; defer
  One Tap.

### Phase 2 — Guest-token client wiring + linking robustness

- **Objective:** capture/persist/send `X-Guest-Token`; verify lossless linking.
- **Tasks:** read+store minted guest token; send on requests and on login; retain across
  login/logout; add backend edge-case test for already-linked/different-user.
- **Deliverables:** `chatClient` guest-token handling; extended
  [test_auth.py](backend-python/tests/test_auth.py) / linking tests.
- **Repo impact:** edit `chatClient.ts`; extend backend auth/linking tests.
- **Acceptance:** `linked_user_id` set for presented token; guest token retained post-login.
- **Validation:** backend tests + manual DB check.
- **Risk:** Low–Medium. **Mitigation:** reuse existing server mechanism; no schema change.

### Phase 3 — Error/edge UX + expiry/re-auth

- **Objective:** graceful handling of `auth_not_configured` / `invalid_google_token` /
  `invalid_access_token` / network; expiry fallback + re-login prompt; “Why login?”.
- **Tasks:** map error codes to UX; implement expiry clear-and-prompt; add info affordance.
- **Deliverables:** UX handling in `AuthContext`/`ChatPage`; `WhyLoginInfo` component.
- **Repo impact:** edits to ChatPage/AuthContext; new info component.
- **Acceptance:** each error path shows correct UX; expired JWT degrades to guest + prompt.
- **Validation:** component tests for each error path.
- **Risk:** Low.

### Phase 4 — Environment hardening (staging/production)

- **Objective:** correct Google OAuth origins, CORS alignment, Vercel/Railway parity.
- **Tasks:** set exact Vercel origins in Google client + `CORS_ALLOWED_ORIGINS`; set
  `VITE_GOOGLE_CLIENT_ID` in Vercel build; confirm `APP_ENV`/`JWT_SECRET` on Railway.
- **Deliverables:** environment config (no code); staging smoke pass.
- **Repo impact:** deploy/config only.
- **Acceptance:** login works from real Vercel origins; CORS clean; misconfig returns 503.
- **Validation:** Section 8.3 staging checklist.
- **Risk:** Medium (origin drift). **Mitigation:** one-to-one origin mapping; no trailing slashes.

---

## 11) Risks, Trade-offs, and Open Questions

**Top risks & mitigations**

- **Client token storage (XSS):** `localStorage` is XSS-readable → mitigate via output
  escaping, no unsafe HTML injection, dependency hygiene, CSP. Reconsider cookie model only
  if XSS risk profile changes.
- **Guest→user linking edge cases:** already-linked / different-user / unknown token →
  fail-soft + idempotent; add explicit test to lock behavior.
- **CORS/origin drift across environments:** exact origins, no trailing slashes,
  `expose_headers=["X-Guest-Token"]`; verify from real Vercel origins.
- **Google client misconfiguration:** wrong/missing origins or client ID → login fails; the
  `503 auth_not_configured` path degrades gracefully. Verify origins one-to-one.
- **GIS integration variability:** button vs One Tap behavior; start with rendered button.

**Trade-offs (chosen direction)**

- Stateless client-discard logout (simple, no revocation) over server-tracked sessions.
- `localStorage` (persistent UX, simple CORS) over in-memory (safer but re-login every reload)
  or cookies (stateful CORS/CSRF complexity).

**Open questions**

- **[Open]** Final client token storage: `localStorage` (recommended) vs in-memory — confirm.
- **[Open]** Adopt One Top / One Tap in this phase or defer.
- **[Verify]** Exact staging/production Vercel + Railway origins (docs use placeholders).
- **[Verify]** Whether to link a re-presented guest token to a different user (last-writer-wins)
  or ignore once linked.
- **[Deferred to chat-experience phase]** Actual migration/attachment of guest chat history to
  the linked user; multi-session and quota UX.

---

## 12) Ready-to-Start Backlog (first 8–10 tasks)

| #   | Task                                                                     | Priority | Owner           | Est | Definition of Done                                                        | Dependencies |
| --- | ------------------------------------------------------------------------ | -------- | --------------- | --- | ------------------------------------------------------------------------- | ------------ |
| 1   | Add `expose_headers=["X-Guest-Token"]` to CORS middleware                | P0       | backend         | S   | Browser reads `X-Guest-Token` cross-origin; test asserts exposure         | —            |
| 2   | Add `VITE_GOOGLE_CLIENT_ID` to frontend env examples + build wiring      | P0       | devops/frontend | S   | Var present in `.env.example`/`.env.required`; read via `import.meta.env` | —            |
| 3   | Configure Google OAuth Web client (JS origins for local)                 | P0       | devops          | S   | Local origin authorized; client ID available                              | 2            |
| 4   | Add `AuthContext`/`AuthProvider` with storage rehydrate                  | P0       | frontend        | M   | Guest/auth state exposed; JWT+guest token persisted/rehydrated            | 2            |
| 5   | Add GIS loader + `LoginButton`; `authClient` for `POST /api/auth/google` | P0       | frontend        | M   | Google credential exchanged; JWT+user stored                              | 3,4          |
| 6   | Attach `Authorization: Bearer` in `chatClient` for all requests          | P0       | frontend        | S   | Authenticated requests carry Bearer; backend resolves user                | 4            |
| 7   | Add Logout action (client discard) + user indicator                      | P0       | frontend        | S   | Logout clears JWT, retains guest token, reverts UI                        | 4,5          |
| 8   | Wire `X-Guest-Token` capture/persist/send in `chatClient`                | P1       | frontend        | M   | Minted token captured; sent on requests + at login; retained on logout    | 1,6          |
| 9   | Expiry fallback + re-login prompt; error-code UX mapping                 | P1       | frontend        | M   | `invalid_access_token`→guest+prompt; 503/401 mapped; chat unaffected      | 5,6          |
| 10  | “Why login?” affordance + backend linking edge-case test                 | P2       | fullstack       | S   | Info UI present; test locks already-linked/different-user behavior        | 4,8          |

---

## 13) Key Architecture Decisions

**D1 — Client token storage model**

- **Decision:** Store the app JWT in `localStorage`, transported as `Authorization: Bearer`.
- **Rationale:** Persistent UX across reloads; simplest CORS (no credentials/cookies); no CSRF
  surface; matches stateless Bearer backend.
- **Rejected alternatives:** In-memory (re-login every reload); `HttpOnly` cookie (adds
  stateful-cookie CORS/CSRF/SameSite complexity the MVP avoids).
- **Reconsider when:** XSS risk becomes unacceptable, or a requirement needs cookie-based
  transport / server-side revocation.

**D2 — Logout semantics**

- **Decision:** Client-only token discard; no logout endpoint, no revocation store.
- **Rationale:** Stateless HS256 JWT cannot be server-invalidated without a revocation store,
  which is forbidden absent a forcing requirement; token expires at `exp`.
- **Rejected alternatives:** Server-side session/revocation table; blacklist.
- **Reconsider when:** A requirement demands immediate server-side invalidation.

**D3 — Guest→user linking strategy**

- **Decision:** Reuse existing link-only mechanism (`linked_user_id`), fail-soft + idempotent;
  no ownership migration here.
- **Rationale:** Minimal, correct, backward-compatible; migration belongs to chat-experience.
- **Rejected alternatives:** Migrating guest sessions/messages at login (out of scope).
- **Reconsider when:** The chat-experience phase defines history attachment semantics.

**D4 — Google credential acquisition method**

- **Decision:** Google Identity Services rendered Sign-In button returning an ID token;
  backend verifies (client ID only, no secret).
- **Rationale:** Simplest secure ID-token flow; no server-side code exchange; smallest slice.
- **Rejected alternatives:** OAuth authorization-code flow (needs client secret + redirect
  handling); One Tap first (deferred as enhancement).
- **Reconsider when:** One Tap/auto-select or additional providers are prioritized.

**D5 — JWT expiry / re-auth handling**

- **Decision:** Expired/invalid JWT degrades to guest (valid tier) + non-blocking re-login
  prompt; no silent refresh.
- **Rationale:** Backend already fails soft to guest; no refresh tokens by decision; preserves
  usability.
- **Rejected alternatives:** Refresh-token rotation; hard 401 that blocks chat.
- **Reconsider when:** Longer-lived sessions without re-login become a product requirement.
