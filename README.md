# Basic Chatbot (React + Python/Node Backends)

A full-stack streaming chatbot project with:

- Frontend: React + TypeScript + Vite
- Backends: FastAPI (Python) and Express + TypeScript (Node.js)
- LLM providers: OpenAI and Gemini (switchable via environment variables)

Current status:

- Stream interruption handling with inline Retry UI
- Typed backend error envelopes and SSE error frames
- Request-size and schema validation on both backends
- Backend and frontend automated test coverage for the main chat flow
- OpenAI and Gemini provider support behind the same frontend contract
- Python backend remains the behavior reference while the Node backend reaches parity
- Deployment runbook and prerequisite checklist documented for Render + Vercel

## Repository Structure

- `backend/python/` - FastAPI reference backend
- `backend/nodejs/` - Express + TypeScript parity backend
- `frontend/` - React client with streaming UI
- `docs/` - planning notes (ignored by git in this repo setup)

## Features

- Non-streaming endpoint: `POST /api/chat`
- Streaming SSE endpoint: `POST /api/chat/stream`
- Health endpoint: `GET /api/health`
- Provider abstraction: switch between OpenAI/Gemini without frontend changes
- Stop/cancel while streaming
- Retry after interrupted streams
- Standardized error handling for validation, timeout, and provider failures

## System Design Diagram

```mermaid
flowchart LR
  subgraph Client["React Client (Vite + TS)"]
    UI["Chat UI\nMessageList / Composer"]
    Hook["useChatStream hook\nfetch + SSE parser + AbortController"]
  end

  subgraph API["Backend Implementations"]
    Py["Python FastAPI backend"]
    Node["Node Express backend"]
    Router["chat routes\nGET /api/health\nPOST /api/chat\nPOST /api/chat/stream"]
    Service["ChatService\n(validates, orchestrates)"]
    Factory["ProviderFactory\n(reads LLM_PROVIDER env)"]
  end

  subgraph Providers["LLM Provider Adapters"]
    Base[["LLMProvider interface"]]
    OpenAIAdapter["OpenAIProvider"]
    GeminiAdapter["GeminiProvider"]
  end

  subgraph External["External APIs"]
    OpenAI[("OpenAI API")]
    Gemini[("Gemini API")]
  end

  subgraph Future["Future (post-V1)"]
    DB[("Persistence:\nsessions / messages")]
  end

  UI --> Hook --> Router
  Py --> Router
  Node --> Router
  Router --> Service --> Factory
  Factory --> Base
  Base --> OpenAIAdapter --> OpenAI
  Base --> GeminiAdapter --> Gemini
  Service -. future .-> DB
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (24 works)
- npm

## Quick Start

### 1) Python Backend

```bash
cd backend/python
cp .env.example .env
# Fill in API keys in .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 2) Node Backend

```bash
cd backend/nodejs
npm install
# Create backend/nodejs/.env and fill in provider keys
PORT=8001 npm run dev
```

### 3) Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Frontend default URL: `http://localhost:5173`

Python backend default URL: `http://localhost:8000`

Node backend recommended local URL: `http://localhost:8001`

Before running locally, make sure the selected backend provider has a real API key in the backend `.env` file you are using.

## Backend Selection

The frontend talks to whichever backend is configured in `frontend/.env`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

Use `8000` for Python and `8001` for Node during side-by-side development.

## Provider Switching

In either backend env file:

- `LLM_PROVIDER=openai` or `LLM_PROVIDER=gemini`
- set provider-specific key/model values

Examples:

```dotenv
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini
```

```dotenv
LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.1-flash-lite
```

Then restart backend.

For the Node backend, the equivalent env file is `backend/nodejs/.env`.

## API Overview

### Health

```http
GET /api/health
```

Example response:

```json
{
  "status": "ok",
  "provider": "gemini",
  "version": "0.1.0"
}
```

### Non-streaming chat

```http
POST /api/chat
Content-Type: application/json
```

Body:

```json
{
  "messages": [{ "role": "user", "content": "What is FastAPI?" }]
}
```

### Streaming chat (SSE)

```http
POST /api/chat/stream
Content-Type: application/json
```

Returns `text/event-stream` with frames: `start`, `delta`, `end`, and `error`.

## Development Commands

### Python Backend

```bash
cd backend/python
make run
make lint
make format
make format-check
make test
uv run pytest
```

### Node Backend

```bash
cd backend/nodejs
npm run dev
npm test
npm run lint
npm run format:check
npm run build
```

### Frontend

```bash
cd frontend
npm run test
npm run lint
npm run format
npm run format:check
npm run build
npm test -- --run
```

## Reliability Notes

- Non-streaming failures return a standard JSON error envelope with codes such as `validation_error`, `provider_timeout`, `provider_rate_limited`, `provider_error`, and `internal_error`.
- Streaming failures surface as SSE `error` frames with the same error codes.
- The frontend preserves partial assistant output on interruption, marks the message as interrupted, and offers Retry.
- Oversized or malformed requests are rejected before hitting the provider.

## Tests

Backend coverage includes:

- health endpoint
- non-streaming chat success and error normalization
- streaming SSE frame sequencing and cancellation behavior
- provider adapter coverage for OpenAI and Gemini
- env-driven provider selection and request-level provider overrides

Frontend coverage includes:

- SSE parser behavior across chunk boundaries
- reducer state transitions
- composer-driven streaming and Stop behavior

Recommended validation commands:

```bash
cd backend/python && uv run pytest
cd backend/nodejs && npm test
cd frontend && npm test -- --run
cd frontend && npm run build
```

## Side-By-Side Workflow

Recommended local ports:

- Python backend: `8000`
- Node backend: `8001`
- Frontend: `5173`

Typical parity workflow:

1. Run the Python backend on `8000` as the reference implementation.
2. Run the Node backend on `8001`.
3. Point `VITE_API_BASE_URL` to `http://localhost:8001` when validating Node behavior.
4. Switch `VITE_API_BASE_URL` back to `http://localhost:8000` when comparing against Python.

## Deployment Status

Deployment prerequisites and the operator runbook are documented in [docs/plans/chatbot-v1.md](docs/plans/chatbot-v1.md). Actual Render/Vercel deployment is still a manual step because it requires:

- a connected Git remote
- Render and Vercel accounts
- production env vars and provider secrets
- manual CORS and public-URL validation

## Notes

- Keep API keys in local `.env` files only.
- Rotate keys immediately if exposed.
