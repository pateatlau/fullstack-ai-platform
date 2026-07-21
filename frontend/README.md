# Frontend Application

Streaming chat UI for the Fullstack AI Platform. **MVP complete (2026-07-19)**; **Post-MVP V1.1 complete (2026-07-22)** — unified chat toggles for web search and document grounding on `/`.

## Capabilities

- Tailwind CSS v4 ChatGPT-like shell with responsive sidebar (drawer / collapse)
- SSE streaming with stop, retry, and connection-error banner
- Unified chat toggles on `/` — web search and document grounding for authenticated users (V1.1)
- SSE handling for `retrieval_complete`, `tool_start`, and `tool_end` lifecycle events (V1.1)
- Provider capability gating via `GET /api/health` → `capabilities.by_provider` (V1.1)
- Google OAuth sign-in; guest session continuity via `X-Guest-Token`
- Forwards `X-Request-ID` on retry for request traceability
- Provider/model selection (OpenAI, Gemini, Groq, Anthropic) in the composer
- Vitest coverage for SSE parsing, reducer, composer, toggles, and accessibility smoke tests

## Features

- Tailwind CSS v4 styling with design-token-driven utility classes
- Left sidebar with current session state, saved-session placeholders, and responsive drawer/collapse behavior
- Message list with role-based bubbles and streaming states
- Composer with Send/Stop behavior, sticky bottom layout, provider/model selection, and unified-chat toggles (V1.1)
- Streaming token updates over SSE
- Local reducer/context chat state
- Retry after interrupted assistant streams
- Connection error banner for backend/network failures
- Unit tests for SSE parsing, reducer behavior, composer-driven streaming, and shell accessibility hooks

## Key Files

- `src/pages/ChatPage.tsx` - responsive chat shell, sidebar state, toggle orchestration, and page wiring
- `src/hooks/useChatStream.ts` - SSE streaming hook with tool/RAG lifecycle callbacks
- `src/hooks/useChatStreamingEnabled.ts` - reads health flags and provider capabilities
- `src/context/ChatContext.tsx` - provider + context hook
- `src/api/sseParser.ts` - buffered SSE frame parser
- `src/components/` - `MessageList`, `MessageBubble`, `Composer`, `StreamingIndicator`
- `src/index.css` - Tailwind CSS v4 import, theme tokens, and global base layer

## Setup

```bash
cp .env.example .env
npm install
```

`.env`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

## Run

```bash
npm run dev
```

App runs at `http://localhost:5173` by default.

## UI Overview

- Desktop: persistent left sidebar, conversation panel, sticky composer
- Tablet: collapsible sidebar controlled from the header
- Mobile: off-canvas sidebar drawer with overlay
- Thread UI: distinct user/assistant bubble styling, streaming placeholder, retry and stop states

## Scripts

```bash
npm run dev      # start dev server
npm run test     # run vitest
npm run lint     # run eslint
npm run format   # run prettier write
npm run format:check # run prettier check
npm run build    # type-check + production build
npm run preview  # preview production build
```

Recommended CI-style checks (matches PR Quality Gates):

```bash
npm run lint
npm run format:check
npm test -- --run
npm run build
```

## Streaming Flow

On mount, `ChatPage` reads `chat_streaming_enabled`, `tools_enabled`, `rag_enabled`, and `capabilities.by_provider` from `GET /api/health`.

When streaming is enabled:

1. User submits from `Composer` (optional `useWebSearch` / `useDocuments` when authenticated and flags allow).
2. `ChatPage` dispatches user message and calls `useChatStream.start(...)`.
3. `useChatStream` opens `POST /api/chat/stream` with toggle fields in the JSON body.
4. `SseParser` emits frames — core: `start`/`delta`/`end`/`error`; V1.1 additive: `retrieval_complete`, `tool_start`, `tool_end`.
5. Reducer updates assistant message incrementally per `delta`; status indicators show retrieval/web search phases.
6. If the connection drops mid-stream, partial content is preserved and the UI offers Retry.

When streaming is disabled (`CHAT_STREAMING_ENABLED=false`), the same UI uses `useChatCompletion` and non-streaming `POST /api/chat` instead (full response applied in one step; toggles still supported when flags allow).

## Accessibility Notes

- Semantic landmarks for sidebar navigation, conversation main region, message thread, and composer
- Focus-visible treatments across shell controls and action buttons
- Accessible labels for message input, session navigation, and streaming state

## Error and Retry Behavior

- Total connection/setup failures show a banner at the top of the page
- Mid-stream interruptions mark the assistant message as interrupted
- Provider-originated stream failures render inline on the affected assistant message
- Retry replays the original request from scratch; there is no partial resume

## Backend Requirement

Frontend expects backend endpoints:

- `GET /api/health`
- `POST /api/chat`
- `POST /api/chat/stream` (SSE)

Document and RAG endpoints (auth-only, Phase 11 backend):

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{id}`
- `DELETE /api/documents/{id}`
- `POST /api/rag/ask`

See [backend-python/README.md](../backend-python/README.md) → **Knowledge and RAG API (Phase 11)** for request/response shapes and env requirements (`RAG_ENABLED=true` for ask).

Release summaries: [docs/releases/post-mvp-v1-release-summary.md](../docs/releases/post-mvp-v1-release-summary.md) (V1), [docs/releases/post-mvp-v1.1-release-summary.md](../docs/releases/post-mvp-v1.1-release-summary.md) (V1.1).

## Documents and RAG UI (Phase 12 + V1.1)

- **Route:** `/documents` (authenticated users only) — upload, list, delete
- **Chat route:** `/` — primary ask surface; authenticated users enable web search / document grounding via composer toggles (V1.1)
- **Required env:** `VITE_API_BASE_URL` (unchanged; no new `VITE_*` flags — feature availability is detected via `GET /api/health`)
- **Backend:** Set `RAG_ENABLED=true` and/or `TOOLS_ENABLED=true` for document / web search toggles; `WEB_SEARCH_API_KEY` required when tools are on
- **Guest policy:** Guests cannot use toggles; see sign-in prompt in composer. Documents nav link hidden until signed in
- **Standalone RAG panel:** `/documents` may still call `POST /api/rag/ask`; chat toggles are the primary UX in V1.1

If the backend is not running or CORS is misconfigured, streaming requests will fail.

The frontend expects the Python production backend to support:

- SSE `start`, `delta`, `end`, and `error` frames
- Additive SSE `retrieval_complete`, `tool_start`, `tool_end` when unified toggles are on (V1.1)
- Health fields: `chat_streaming_enabled`, `tools_enabled`, `rag_enabled`, `capabilities.by_provider`
- Standard error envelope `{ error: { code, message, request_id } }`
- `X-Request-ID` on every response (forwarded on retry via `chatClient.ts`)
- `X-Guest-Token` and `X-Guest-Quota-Remaining` for anonymous callers
- CORS for the active frontend origin
- Provider selection values for OpenAI, Gemini, Groq, and Anthropic

## Tests

Current frontend tests: **122 passed** (Vitest, 2026-07-22).

Coverage includes:

- SSE frame parsing across arbitrary chunk boundaries (including V1.1 tool/RAG lifecycle frames)
- Reducer transitions for streaming, interruption, retry, and error cases
- Page-level composer behavior with streamed tokens, toggles, and Stop
- Composer provider/model selection wiring and payload coverage (`use_web_search`, `use_documents`)
- `X-Request-ID` retry forwarding in `chatClient.ts`
- Shell accessibility smoke coverage for core landmarks and controls

## Deployment Notes

For hosted deployment, set `VITE_API_BASE_URL` to the public backend URL.

Current MVP production backend:

- `VITE_API_BASE_URL=https://fullstack-ai-platform-production.up.railway.app`

The frontend is intended for Vercel static deployment, but successful production use also depends on the backend allowing the exact frontend origin via `CORS_ALLOWED_ORIGINS`.

Use exact origins (no trailing slash) in `CORS_ALLOWED_ORIGINS`.

Google login requires `VITE_GOOGLE_CLIENT_ID` (public, not a secret) set at build time,
the same way as `VITE_API_BASE_URL`. The frontend's own origin must be registered as an
authorized JavaScript origin on the Google Cloud OAuth Web client — see
[backend-python/README.md](../backend-python/README.md) Deployment Notes for the
local/staging/production origin list.

The full deployment prerequisite checklist and manual runbook live in [../docs/plans/chatbot-v1.md](../docs/plans/chatbot-v1.md).
