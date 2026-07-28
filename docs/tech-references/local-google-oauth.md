# Google OAuth on Localhost

Local Vite proxies `/api` to **`http://127.0.0.1:8000`** (IPv4 loopback), not `http://localhost:8000`. On macOS, `localhost` often resolves to IPv6 (`::1`) first. Docker Compose publishes `*:8000` on that stack while `make backend` binds only `127.0.0.1:8000`, so proxying to `localhost` can hit a stale compose backend that has no `GOOGLE_CLIENT_ID` and return **503** `auth_not_configured` ("Login is temporarily unavailable.") even though the local API is fine.

## Checklist when Google sign-in fails locally

1. Prefer **one** backend: either `make backend` **or** `docker compose --profile python up`, not both on port 8000.
2. If compose was left running: `docker compose --profile python stop backend-python` (or `docker stop chatbot-backend-python`), then `make backend`.
3. Confirm `GOOGLE_CLIENT_ID` in `backend-python/.env` and matching `VITE_GOOGLE_CLIENT_ID` in `frontend/.env` (same OAuth Web client ID as production is fine for local).
4. Google Cloud Console → OAuth Web client → Authorized JavaScript origins must include `http://localhost:5173` (and `http://127.0.0.1:5173` if you open the app that way).
5. Restart Vite after changing `frontend/vite.config.ts` or `frontend/.env` (`npm run dev`).

A `gsi/button` **403** from `accounts.google.com` is often a Google Identity Services iframe quirk and is not the same as app auth failing — the decisive request is `POST /api/auth/google`.

See also: [google-auth-implementation-plan.md](../plans/google-auth-implementation-plan.md).
