# Frontend Application

Streaming chat UI for the Fullstack AI Platform project.

Current frontend scope includes the completed chatbot redesign and validation work:

- Tailwind CSS v4-based ChatGPT-like app shell
- Responsive sidebar foundation for future multi-chat sessions
- Polished conversation thread and sticky composer
- inline Retry after interrupted streams
- top-of-page banner for total connection failures
- Stop/cancel support during streaming
- reducer, composer, and accessibility smoke coverage
- deployment env wiring for a hosted backend

## Features

- Tailwind CSS v4 styling with design-token-driven utility classes
- Left sidebar with current session state, saved-session placeholders, and responsive drawer/collapse behavior
- Message list with role-based bubbles and streaming states
- Composer with Send/Stop behavior and sticky bottom layout
- Streaming token updates over SSE
- Local reducer/context chat state
- Retry after interrupted assistant streams
- Connection error banner for backend/network failures
- Unit tests for SSE parsing, reducer behavior, composer-driven streaming, and shell accessibility hooks

## Key Files

- `src/pages/ChatPage.tsx` - responsive chat shell, sidebar state, and page wiring
- `src/context/ChatContext.tsx` - provider + context hook
- `src/state/chatReducer.ts` - chat state/actions reducer
- `src/hooks/useChatStream.ts` - fetch + stream reader + parser integration
- `src/api/chatClient.ts` - `/api/chat` + `/api/chat/stream` client
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

- total connection/setup failures show a banner at the top of the page
- mid-stream interruptions mark the assistant message as interrupted
- provider-originated stream failures render inline on the affected assistant message
- Retry replays the original request from scratch; there is no partial resume

## Backend Requirement

Frontend expects backend endpoints:

- `GET /api/health`
- `POST /api/chat`
- `POST /api/chat/stream` (SSE)

If the backend is not running or CORS is misconfigured, streaming requests will fail.

The frontend expects the backend to support:

- SSE `start`, `delta`, `end`, and `error` frames
- standard non-streaming error envelopes
- CORS for the active frontend origin

## Tests

Current frontend tests cover:

- SSE frame parsing across arbitrary chunk boundaries
- reducer transitions for streaming, interruption, retry, and error cases
- page-level composer behavior with streamed tokens and Stop
- shell accessibility smoke coverage for core landmarks and controls

## Deployment Notes

For hosted deployment, set `VITE_API_BASE_URL` to the public backend URL.

The frontend is intended for Vercel static deployment, but successful production use also depends on the backend allowing the exact frontend origin via `CORS_ALLOWED_ORIGINS`.

The full deployment prerequisite checklist and manual runbook live in [../docs/plans/chatbot-v1.md](../docs/plans/chatbot-v1.md).
