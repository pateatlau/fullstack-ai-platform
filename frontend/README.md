# Frontend (React + TypeScript + Vite)

Streaming chat UI for the Basic Chatbot project.

## Features

- Message list with role-based bubbles
- Composer with Send/Stop behavior
- Streaming token updates over SSE
- Local reducer/context chat state
- Unit tests for SSE parsing and reducer behavior

## Key Files

- `src/pages/ChatPage.tsx` - page wiring (state + streaming hook + UI)
- `src/context/ChatContext.tsx` - provider + context hook
- `src/state/chatReducer.ts` - chat state/actions reducer
- `src/hooks/useChatStream.ts` - fetch + stream reader + parser integration
- `src/api/chatClient.ts` - `/api/chat` + `/api/chat/stream` client
- `src/api/sseParser.ts` - buffered SSE frame parser
- `src/components/` - `MessageList`, `MessageBubble`, `Composer`, `StreamingIndicator`

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

## Streaming Flow

1. User submits from `Composer`.
2. `ChatPage` dispatches user message and calls `useChatStream.start(...)`.
3. `useChatStream` opens `POST /api/chat/stream`.
4. `SseParser` emits `start`/`delta`/`end`/`error` frames.
5. Reducer updates assistant message incrementally per `delta`.

## Backend Requirement

Frontend expects backend endpoints:

- `GET /api/health`
- `POST /api/chat`
- `POST /api/chat/stream` (SSE)

If the backend is not running or CORS is misconfigured, streaming requests will fail.
