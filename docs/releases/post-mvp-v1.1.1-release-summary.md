# Post-MVP V1.1.1 Release Summary

**Release name:** Post-MVP V1.1.1 — Production Polish (UX, Auth Hardening, Demo Protection)
**Release date:** 2026-07-22
**Validation:** Phase 10 final acceptance (see [post-mvp-v1.1.1-implementation-plan.md](../plans/post-mvp-v1.1.1-implementation-plan.md))

---

## Summary vs V1.1

V1.1 consolidated web search and document grounding on the main chat (`/`). **V1.1.1 polishes production readiness** — session UX, auth/route hardening, demo cost controls, consistent feedback, and mobile layout — without new platform capabilities or orchestration changes.

| Area | V1.1 | V1.1.1 |
| ---- | ---- | ------ |
| Session delete | Not available | `DELETE /api/chat/sessions/{id}` + UI confirmation + post-delete fallback |
| Session titles | 80-char slice at create; many `null` | ~50 char title from first user message; **"New chat"** until first turn |
| Route protection | Ad-hoc auth on `/documents` | `ProtectedRoute`; expired JWT redirect to `/` with banner |
| Unknown URLs | Blank SPA page | Branded `NotFoundPage` with **Back to Chat** / **Go Home** |
| Public demo cost | Guest message quota + HTTP rate limits | Guest output token cap, upload quota, `demo_mode_strict`, ops spending-alert docs |
| Loading UX | Ad-hoc text and spinners | Shared `LoadingIndicator` (inline, skeleton, overlay) |
| Provider errors in UI | Some raw/generic messages | `friendlyErrors.ts` maps known codes; no SDK leakage |
| Empty screens | Minimal copy | Shared `EmptyState` with actionable CTAs |
| Mobile | Untested systematically | Manual checklist at 375px / 390px; touch-target fixes |

---

## API and UX Changes

### Session API (additive)

| Method | Path | Notes |
| ------ | ---- | ----- |
| DELETE | `/api/chat/sessions/{id}` | Auth-only; **204** on success; **403** guest; **404** not owned; cascade delete |

### Auto-title behavior

- Trigger: first persisted user message when `session.title IS NULL`
- Algorithm: `derive_session_title()` — first line, whitespace collapsed, ~50 characters
- Existing titles preserved on subsequent messages

### Frontend routing and UX

- `ProtectedRoute` on `/documents` — guest/expired JWT → redirect `/`
- `NotFoundPage` catch-all (`path="*"`)
- `ConfirmDialog` for session delete
- Shared `LoadingIndicator`, `EmptyState`, `friendlyErrors`

### Demo protection settings

| Variable | Purpose |
| -------- | ------- |
| `GUEST_MAX_OUTPUT_TOKENS` | Cap guest completion length |
| `AUTHENTICATED_DAILY_UPLOAD_QUOTA` | Daily upload count for signed-in users |
| `GUEST_DAILY_UPLOAD_QUOTA` | Future-proof guest upload cap |
| `DEMO_MODE_STRICT` | Tighten caps for public demo deploy |

**Ops guide:** [docs/ops/public-demo-protection.md](../ops/public-demo-protection.md)

---

## Breaking Changes

**None.** All changes are additive or UX-only. V1.1 unified chat toggles, streaming protocol, and guest tool/RAG denial unchanged.

---

## Migration / Upgrade Notes

### New or documented environment variables

See `backend-python/.env.example` V1.1.1 demo protection section. For public demo deploys, set:

```dotenv
DEMO_MODE_STRICT=true
GUEST_MAX_OUTPUT_TOKENS=512
AUTHENTICATED_DAILY_UPLOAD_QUOTA=20
```

Review `RATE_LIMIT_ANONYMOUS_PER_MINUTE` / `RATE_LIMIT_AUTHENTICATED_PER_MINUTE` per ops doc.

### Frontend

No new build-time variables. Existing `VITE_API_BASE_URL` and Google OAuth config unchanged.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| AI-generated titles, chat rename/search/folders/export | V2+ |
| Automated Playwright/visual mobile regression | Deferred — manual checklist only |
| Pre-existing `healthClient.test.ts` URL mismatch (`localhost` vs `127.0.0.1` when `frontend/.env` sets `VITE_API_BASE_URL`) | Pre-existing; 163/164 Vitest pass locally |
| Standalone streaming `/api/rag/ask`, MCP, hybrid search, citations UI | V2 (unchanged from V1.1) |
| Production deployment of V1.1.1 artifacts | **Blocked** — see Phase 10 Completion Record; requires CD promotion per [CD_PRODUCTION.md](../../CD_PRODUCTION.md) |

---

## Verification Metrics (Phase 10 — 2026-07-22)

| Gate | Result |
| ---- | ------ |
| Backend tests | **453 passed**, **87.14%** coverage on `app/`, **22.83s** |
| Backend quality | `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval` — all pass |
| Eval CLI | **5/5** passed (`backend-python/.eval/eval-report.json`, timestamp 2026-07-22T12:39:00Z) |
| Frontend tests | **164** Vitest tests (**163 pass** — pre-existing `healthClient` URL mismatch); lint, format, build pass |
| V1.1 regression (backend) | **149 passed** (targeted suite via `uv run python -m pytest`) |
| V1.1.1 targeted regression (backend) | **22 passed** |
| V1.1 + V1.1.1 combined spot-check | **64 passed**, 17 skipped (Postgres-dependent tests skip when DB unavailable) |
| Frontend targeted regression | **59 passed** (12 test files) |
| Docker Compose smoke | Health **200**, ready **200** (`/api/health/ready`, `db: ok`), frontend **200** |
| Production deployment | **Blocked** — no CD promotion in this validation run; existing Railway health **200** (pre-V1.1.1 artifact) |

---

## References

- Implementation plan: [docs/plans/post-mvp-v1.1.1-implementation-plan.md](../plans/post-mvp-v1.1.1-implementation-plan.md)
- V1.1 release: [docs/releases/post-mvp-v1.1-release-summary.md](./post-mvp-v1.1-release-summary.md)
- Architecture spec (V1.1.1 delta): [docs/references/Post-MVP-V1-Architecture-and-Technical-Design-Specs.md](../references/Post-MVP-V1-Architecture-and-Technical-Design-Specs.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
