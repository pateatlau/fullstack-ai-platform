# Backend (FastAPI)

FastAPI backend for the chatbot project.

## Tech Stack

- FastAPI
- uvicorn
- pydantic-settings
- OpenAI SDK
- Google GenAI SDK

## Layout

- `app/main.py` - app creation, CORS, router wiring
- `app/core/config.py` - environment-driven settings
- `app/routers/health.py` - `GET /api/health`
- `app/routers/chat.py` - `POST /api/chat`, `POST /api/chat/stream`
- `app/services/chat_service.py` - orchestration, SSE framing
- `app/providers/base.py` - provider protocol
- `app/providers/openai_provider.py` - OpenAI adapter
- `app/providers/gemini_provider.py` - Gemini adapter
- `app/providers/factory.py` - provider selection
- `app/schemas/chat.py` - request/response/frame schemas
- `tests/` - test doubles and provider tests

## Setup

```bash
cp .env.example .env
uv sync
```

## Shortcut Commands

```bash
make run
make lint
make format
make format-check
make test
```

## Run

```bash
make run
```

## Environment Variables

From `.env.example`:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.1-flash-lite
CORS_ALLOWED_ORIGINS=http://localhost:5173
APP_ENV=development
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
```

## Provider Selection

- Default provider comes from `LLM_PROVIDER`.
- You can also pass `provider` and `model` in each chat request.

## Endpoints

- `GET /` - welcome message
- `GET /api/health` - status + active provider + app version
- `POST /api/chat` - non-streaming completion
- `POST /api/chat/stream` - SSE streaming completion

## Quick Checks

```bash
curl -s http://localhost:8000/api/health
```

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say hello"}]}'
```

## Tests

```bash
make test
```

Gemini provider-focused test:

```bash
uv run pytest -q tests/providers/test_gemini_provider.py
```
