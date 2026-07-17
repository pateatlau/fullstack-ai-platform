# Frontend Application

Streaming chat UI for the Fullstack AI Platform project.

Current frontend scope includes the completed chatbot redesign and validation work:

- Tailwind CSS v4-based ChatGPT-like app shell
- Responsive sidebar foundation for future multi-chat sessions
- Polished conversation thread and sticky composer
- Inline Retry after interrupted streams
- Top-of-page banner for total connection failures
- Stop/cancel support during streaming
- Reducer, composer, and accessibility smoke coverage
- Provider/model selection in the composer for OpenAI, Gemini, Groq, and Anthropic
- Deployment env wiring for a hosted backend

## Features

- Tailwind CSS v4 styling with design-token-driven utility classes
- Left sidebar with current session state, saved-session placeholders, and responsive drawer/collapse behavior
- Message list with role-based bubbles and streaming states
- Composer with Send/Stop behavior, sticky bottom layout, and provider/model selection
- Streaming token updates over SSE
- Local reducer/context chat state
- Retry after interrupted assistant streams
- Connection error banner for backend/network failures
- Unit tests for SSE parsing, reducer behavior, composer-driven streaming, and shell accessibility hooks

## Key Files

- `src/pages/ChatPage.tsx` - responsive chat shell, sidebar state, and page wiring
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

Recommended CI-style checks:

```bash
npm test -- --run
npm run build
npm run lint
```

## Streaming Flow

1. User submits from `Composer`.
2. `ChatPage` dispatches user message and calls `useChatStream.start(...)`.
3. `useChatStream` opens `POST /api/chat/stream`.
4. `SseParser` emits `start`/`delta`/`end`/`error` frames.
5. Reducer updates assistant message incrementally per `delta`.
6. If the connection drops mid-stream, partial content is preserved and the UI offers Retry.

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

If the backend is not running or CORS is misconfigured, streaming requests will fail.

The frontend expects the backend to support:

- SSE `start`, `delta`, `end`, and `error` frames
- Standard non-streaming error envelopes
- CORS for the active frontend origin
- Provider selection values for OpenAI, Gemini, Groq, and Anthropic

## Tests

Current frontend tests cover:

- SSE frame parsing across arbitrary chunk boundaries
- Reducer transitions for streaming, interruption, retry, and error cases
- Page-level composer behavior with streamed tokens and Stop
- Composer provider/model selection wiring and payload coverage
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
