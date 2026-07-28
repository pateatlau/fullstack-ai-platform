# Public Demo Protection (V1.1.1 Phase 1)

Operational guide for cost controls on a public demo deployment of the fullstack AI platform.

## Purpose

Public demo cost control without punishing signed-in users beyond an optional demo-tier upload cap. Anonymous guests receive message quotas (existing), output token caps (new), and cannot use tools or document grounding (V1.1). Authenticated users keep full chat capabilities; optional daily upload count limits prevent corpus flooding.

## Env var profile

| Setting | Dev recommendation | Production public demo |
| --- | --- | --- |
| `GUEST_DAILY_MESSAGE_QUOTA` | `20` (default) | `20` |
| `GUEST_MAX_OUTPUT_TOKENS` | `4096` (high; default) | `512` |
| `AUTHENTICATED_DAILY_UPLOAD_QUOTA` | unset (unlimited) | `20` |
| `GUEST_DAILY_UPLOAD_QUOTA` | `5` (future-proof) | `5` (if guest upload enabled) |
| `DEMO_MODE_STRICT` | `false` | `true` on public deploy |
| `DOCUMENT_UPLOAD_MAX_BYTES` | `10485760` (10 MB) | `10485760` |
| `DEFAULT_MAX_TOKENS` | unset | unset unless global cap needed |

When `DEMO_MODE_STRICT=true`:

- Guest output token cap is lowered to **min(`GUEST_MAX_OUTPUT_TOKENS`, 512)**.
- Authenticated daily upload quota defaults to **20** when `AUTHENTICATED_DAILY_UPLOAD_QUOTA` is unset.
- Recommended: set explicit production values in env rather than relying on strict-mode defaults alone.

## Rate limits

| Variable | Default | Production demo note |
| --- | --- | --- |
| `RATE_LIMIT_ANONYMOUS_PER_MINUTE` | `30` | Keep or lower slightly (e.g. `20`) for anonymous abuse |
| `RATE_LIMIT_AUTHENTICATED_PER_MINUTE` | `120` | Keep default unless demo sees auth-tier abuse |

Review outcome (Phase 1): current defaults (**30** anon / **120** auth per minute) are acceptable for an initial public demo. Tighten via env if monitoring shows sustained abuse. Health endpoints remain exempt.

## Guest policies

| Control | Mechanism |
| --- | --- |
| Message quota | `QuotaService` + `guest_quota_counters` — **20**/UTC day (configurable) |
| Output token cap | `resolve_max_tokens()` — caps provider `max_tokens` for guest completions |
| Tools / documents | V1.1 denial — guests cannot enable `use_web_search` or `use_documents` |

## Upload quota

- **Scope:** authenticated users on `POST /api/documents/upload` (auth-only route).
- **Counter:** `upload_quota_counters` table (`user_id` + UTC `window_start` + `upload_count`).
- **Enforcement:** check before ingestion; increment after successful ingest.
- **Reset:** UTC midnight (same window as guest message quota).
- **Error:** HTTP **429**, `error.code` = `quota_exceeded`.

## Provider spending alerts

Configure billing/usage alerts in each provider dashboard before exposing a public demo.

### OpenAI

1. Sign in to [OpenAI Platform](https://platform.openai.com/).
2. Go to **Settings → Billing → Usage limits** (or organization limits).
3. Set monthly budget caps and email alerts at 50% / 75% / 100%.
4. Monitor **Usage** for chat and embedding models used by RAG.

### Anthropic

1. Sign in to [Anthropic Console](https://console.anthropic.com/).
2. Open **Billing** / **Usage** and configure spend notifications.
3. Set workspace or organization limits if available on your plan.

### Google Gemini / AI Studio

1. Sign in to [Google AI Studio](https://aistudio.google.com/) or Google Cloud console for API billing.
2. Enable budget alerts on the billing account linked to the Gemini API key.
3. Monitor quota and rate-limit dashboards for the enabled models.

### Groq

1. Sign in to [Groq Console](https://console.groq.com/).
2. Review **Usage** and configure notification thresholds where offered.
3. Track rate limits separately from spend (Groq may have generous free tiers).

### Tavily (web search)

1. Sign in to [Tavily](https://tavily.com/) dashboard.
2. Monitor API quota / credit usage for the search key (`WEB_SEARCH_API_KEY`).
3. Set external alerts (e.g. weekly usage review) — no in-app spending alert in this codebase.
4. Authenticated web search cost is bounded by tool gating + operator monitoring; daily search counter is **not** implemented in Phase 1.

## `demo_mode_strict`

Enable on public Railway/production demo deploy:

```bash
DEMO_MODE_STRICT=true
GUEST_MAX_OUTPUT_TOKENS=512
AUTHENTICATED_DAILY_UPLOAD_QUOTA=20
```

Keep `DEMO_MODE_STRICT=false` locally so engineers retain high guest token caps and unlimited uploads unless testing quota behavior explicitly.

## Observability

Structured log fields (no message or document content):

| Field | When |
| --- | --- |
| `guest_output_token_cap_applied=true` | Guest completion/stream applies output token ceiling |
| `capped_max_tokens` | Effective guest `max_tokens` passed to provider |
| `upload_quota_denied_total=true` | Authenticated upload rejected for daily quota |
| `user_id`, `count`, `quota` | Upload denial context |

Correlate with `X-Request-ID` from API responses for support triage.
