# Nodejs API Service

Express + TypeScript backend that mirrors the Python reference API.

## Stack

- Node.js 20+
- Express
- TypeScript
- Zod
- Vitest + Supertest
- OpenAI SDK
- Google Gen AI SDK

## Environment

Create `backend-nodejs/.env` with the values you want to run locally.

```dotenv
PORT=8001
APP_ENV=development
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
CORS_ALLOWED_ORIGINS=http://localhost:5173
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
REQUEST_BODY_LIMIT_BYTES=16384
```

Notes:

- Startup fails fast if the active provider key is missing.
- `LLM_PROVIDER` selects the default backend provider.
- Request-level `provider` and `model` overrides are still supported by the chat APIs.

## Install

```bash
cd backend-nodejs
npm install
```

## Run

Default dev server:

```bash
cd backend-nodejs
npm run dev
```

Recommended side-by-side local ports:

- Python backend: `8000`
- Node backend: `8001`
- Frontend: `5173`

Run Node on `8001` without editing the file:

```bash
cd backend-nodejs
PORT=8001 npm run dev
```

Build and run compiled output:

```bash
cd backend-nodejs
npm run build
npm start
```

## Test And Checks

```bash
cd backend-nodejs
npm test
npm run lint
npm run format:check
npm run build
```

## Endpoints

### Health

```http
GET /api/health
```

Example response:

```json
{
  "status": "ok",
  "provider": "openai",
  "version": "0.1.0"
}
```

### Non-streaming chat

```http
POST /api/chat
Content-Type: application/json
```

Example body:

```json
{
  "messages": [{ "role": "user", "content": "Say hello." }],
  "provider": "openai",
  "model": "gpt-4o-mini"
}
```

Example response:

```json
{
  "id": "resp_123456789abc",
  "role": "assistant",
  "content": "Hello!",
  "model": "gpt-4o-mini",
  "provider": "openai",
  "created_at": "2026-07-10T12:00:00.000Z"
}
```

### Streaming chat

```http
POST /api/chat/stream
Content-Type: application/json
```

Returns `text/event-stream` frames in this order:

- `start`
- `delta` repeated
- `end`
- or `error` on mid-stream failure

Example frame sequence:

```text
event: start
data: {"type":"start","id":"resp_123","timestamp":"2026-07-10T12:00:00.000Z"}

event: delta
data: {"type":"delta","id":"resp_123","content":"Hello","timestamp":"2026-07-10T12:00:00.100Z"}

event: end
data: {"type":"end","id":"resp_123","finish_reason":"stop","timestamp":"2026-07-10T12:00:00.500Z"}
```

## Backend Switching Workflow

Use this when comparing Node behavior against the Python reference.

### Run Python reference backend

```bash
cd backend-python
make run
```

This serves the reference backend on `http://localhost:8000`.

### Run Node backend on a separate port

```bash
cd backend-nodejs
PORT=8001 npm run dev
```

### Point the frontend at the backend you want to test

In `frontend/.env`:

```dotenv
VITE_API_BASE_URL=http://localhost:8001
```

Switch back to Python by restoring:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

## Useful Curl Checks

Health:

```bash
curl -s http://127.0.0.1:8001/api/health
```

Non-streaming chat:

```bash
curl -s -X POST http://127.0.0.1:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hello."}]}'
```

Streaming chat:

```bash
curl -sN -X POST http://127.0.0.1:8001/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hello."}]}'
```
