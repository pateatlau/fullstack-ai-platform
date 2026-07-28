# Node.js Backend v1 — Implementation Plan

Status update (2026-07-15): this plan is deferred until after MVP. Python backend on Railway is the active production backend.

## 1. Objective

- Build a Node.js + Express + TypeScript backend that matches the current Python/FastAPI backend's behavior closely enough that the existing frontend does not need to change.
- Reorganize the backend layout so the current Python implementation lives under `backend-python` and the new Node implementation lives under `backend-nodejs`.
- Preserve the current HTTP contract, SSE streaming format, provider abstraction, validation rules, and error semantics.

## 2. Scope

**In scope**

- New Express + TypeScript backend with the same endpoints:
  - `GET /api/health`
  - `POST /api/chat`
  - `POST /api/chat/stream`
- OpenAI and Gemini provider support behind a shared adapter interface.
- Same request and response shapes used by the frontend today.
- Same SSE event sequence and error framing used by the streaming UI.
- Equivalent automated tests for health, non-streaming chat, streaming chat, validation, and provider behavior.
- Repo restructuring from the current single `backend/` project to:

```text
backend-python/
backend-nodejs/
```

**Out of scope**

- Frontend changes beyond config updates if local dev ports differ.
- Persistence, auth, rate limiting, multi-session chat, or UI redesign.
- Removing the Python backend. It remains the reference implementation during this phase.

## 3. Source Of Truth

- The current Python backend is the behavioral reference.
- The current frontend is the compatibility test.
- This plan is derived from `docs/plans/chatbot-v1.md`, the current backend README, and the live frontend API usage.

That means the Node implementation should copy the Python backend's externally visible behavior first, even when the internal implementation differs.

## 4. Parity Requirements

The Node backend must preserve the following contracts.

### API endpoints

| Method | Path               | Required behavior                                       |
| ------ | ------------------ | ------------------------------------------------------- |
| GET    | `/api/health`      | Return status, active default provider, and app version |
| POST   | `/api/chat`        | Return one complete assistant message as JSON           |
| POST   | `/api/chat/stream` | Return `text/event-stream` frames over a POST request   |

### Request shape

```json
{
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "What is FastAPI?" }
  ],
  "model": "gpt-4o-mini",
  "provider": "openai",
  "temperature": 0.7
}
```

### Non-streaming response shape

```json
{
  "id": "resp_01hz...",
  "role": "assistant",
  "content": "FastAPI is a Python web framework...",
  "model": "gpt-4o-mini",
  "provider": "openai",
  "created_at": "2026-07-09T12:00:00Z"
}
```

### Streaming SSE contract

The frontend already expects SSE over `fetch()` POST, not WebSocket and not `EventSource`.

The Node backend must emit frames in this form:

```text
event: start
data: {"type":"start","id":"resp_01hz","timestamp":"2026-07-09T12:00:00Z"}

event: delta
data: {"type":"delta","id":"resp_01hz","content":"Fast","timestamp":"2026-07-09T12:00:00.05Z"}

event: end
data: {"type":"end","id":"resp_01hz","finish_reason":"stop","timestamp":"2026-07-09T12:00:01Z"}
```

On mid-stream failure, the backend must emit:

```text
event: error
data: {"type":"error","id":"resp_01hz","code":"provider_error","message":"Upstream provider failed","timestamp":"..."}
```

### Error envelope

Non-streaming errors must remain:

```json
{
  "error": {
    "code": "validation_error",
    "message": "messages must not be empty"
  }
}
```

### Validation rules

Node should preserve the same effective validation behavior as Python:

- `messages` must be present and non-empty.
- `role` must be one of `system`, `user`, `assistant`.
- `content` should be trimmed and length-limited.
- `model` cannot be blank.
- `temperature` must stay within a safe bounded range.
- Oversized request bodies should be rejected early.

### Error taxonomy

Node should preserve the current backend error codes:

- `validation_error`
- `provider_timeout`
- `provider_rate_limited`
- `provider_error`
- `internal_error`

## 5. Target Repository Layout

```text
backend/
  python/
    Makefile
    pyproject.toml
    README.md
    app/
    tests/
  nodejs/
    package.json
    tsconfig.json
    README.md
    src/
      server.ts
      app.ts
      core/
        config.ts
        errors.ts
        logger.ts
      routers/
        health.ts
        chat.ts
      services/
        chatService.ts
        sse.ts
      providers/
        base.ts
        factory.ts
        openaiProvider.ts
        geminiProvider.ts
      schemas/
        chat.ts
      types/
        chat.ts
    tests/
      health.test.ts
      chatEndpoint.test.ts
      chatStream.test.ts
      providers/
        openaiProvider.test.ts
        geminiProvider.test.ts
      fakes/
        fakeProvider.ts
```

## 6. Proposed Node Stack

- Runtime: Node.js LTS
- Framework: Express
- Language: TypeScript
- Validation: `zod`
- Env loading and validation: `zod` plus `dotenv`
- Streaming transport: native Express response streaming with SSE headers
- Testing: Vitest
- HTTP test client: Supertest
- Linting/formatting: ESLint and Prettier
- Provider SDKs:
  - `openai`
  - `@google/genai` or the current supported Gemini SDK for Node at implementation time

This stack matches the existing frontend ecosystem and keeps the Node backend small and idiomatic.

## 7. Node Architecture

### App layer

- `src/app.ts` creates the Express app, registers middleware, routers, and error handlers.
- `src/server.ts` loads config and starts the HTTP server.

### Config layer

- `src/core/config.ts` reads and validates environment variables.
- Startup should fail fast if the selected provider's API key is missing.

Expected environment variables:

```dotenv
PORT=8000
APP_ENV=development
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.1-flash-lite
CORS_ALLOWED_ORIGINS=http://localhost:5173
MAX_MESSAGE_LENGTH=4000
REQUEST_TIMEOUT_SECONDS=30
```

### Router layer

- `health.ts` handles `GET /api/health`.
- `chat.ts` handles `POST /api/chat` and `POST /api/chat/stream`.
- Routers should stay thin and delegate to `ChatService`.

### Service layer

`ChatService` should mirror the Python service responsibilities:

- validate and normalize request input
- resolve the provider through `ProviderFactory`
- handle non-streaming completions
- handle streaming completions
- map provider failures to the shared error taxonomy
- convert provider chunks into SSE frames

### Provider layer

The provider interface should normalize differences between OpenAI and Gemini so everything above it is provider-agnostic.

Suggested interface:

```ts
export interface ProviderChunk {
  content: string;
  finishReason: string | null;
}

export interface LLMProvider {
  streamChat(input: {
    messages: ChatMessage[];
    model: string;
    temperature: number;
    signal?: AbortSignal;
  }): AsyncIterable<ProviderChunk>;

  completeChat(input: {
    messages: ChatMessage[];
    model: string;
    temperature: number;
    signal?: AbortSignal;
  }): Promise<string>;
}
```

### Schema layer

`schemas/chat.ts` should define:

- request schema
- response schema
- error envelope schema
- SSE frame schemas for `start`, `delta`, `end`, and `error`

Using runtime schemas here matters because the Node backend does not get FastAPI/Pydantic behavior for free.

## 8. Streaming Design In Express

Express can support the same streaming model as FastAPI, but it needs explicit response handling.

### Required response behavior

For `POST /api/chat/stream`:

- set `Content-Type: text/event-stream`
- set `Cache-Control: no-cache`
- set `Connection: keep-alive`
- flush headers immediately
- write SSE frames incrementally with blank lines between frames
- close the stream cleanly after `end` or `error`

### Suggested implementation shape

1. Validate request body.
2. Resolve provider and request options.
3. Generate a response id and `start` frame.
4. Write the `start` frame immediately.
5. Iterate provider chunks with `for await (...)`.
6. Write one `delta` frame per chunk.
7. On completion, write `end`.
8. On upstream failure after streaming begins, write `error` then end the response.

### Cancellation handling

The frontend already uses `AbortController`, so the Node backend must stop upstream work when the client disconnects.

Implementation requirement:

- listen for `req.aborted`, `req.on('close')`, or equivalent connection termination signals
- propagate cancellation into the provider call with an `AbortController`
- stop producing further SSE frames once the client disconnects

This is the Node equivalent of the Python backend's disconnect-aware streaming loop.

## 9. Middleware And Operational Concerns

### Middleware

- `express.json({ limit: ... })` for request size enforcement
- CORS middleware configured from `CORS_ALLOWED_ORIGINS`
- request id middleware for basic observability
- centralized error middleware that maps known errors to the shared JSON envelope

### Logging

- log request lifecycle and provider failures
- avoid logging full user message contents at info level
- log client disconnects during streams for debugging parity with Python

### Timeouts

- enforce `REQUEST_TIMEOUT_SECONDS` around provider calls
- map timeout failures to `provider_timeout`

## 10. Testing Strategy

The Node backend should have parity tests, not just implementation tests.

### Endpoint tests

- `health.test.ts`
  - `GET /api/health` returns expected shape and default provider
- `chatEndpoint.test.ts`
  - success path returns assistant payload shape
  - invalid request returns `validation_error`
  - provider failure returns shared error envelope
- `chatStream.test.ts`
  - stream emits `start -> delta* -> end`
  - provider failure emits `start -> error`
  - client disconnect stops iteration and aborts provider work

### Provider tests

- `openaiProvider.test.ts`
  - non-streaming maps provider output into normalized content
  - streaming maps token deltas into normalized chunks
- `geminiProvider.test.ts`
  - same normalization guarantees for Gemini

### Test doubles

- `fakeProvider.ts` should mirror the Python `FakeProvider`
- streaming tests should avoid real network calls

### Compatibility checks

At least one manual or automated comparison should confirm that Python and Node return matching shapes for:

- health response
- non-stream chat response
- SSE frame ordering and payload keys
- error codes for common failure modes

## 11. Migration Plan

### Phase 0 — Restructure backend folders

1. Create `backend-python`.
2. Move the current Python backend contents into `backend-python`.
3. Update docs, local commands, and any root references that assume `backend/` is the Python app.
4. Verify the Python backend still runs unchanged from its new location.

Acceptance:

- existing Python tests still pass from `backend-python`
- frontend can still talk to the Python backend after path updates

### Phase 1 — Bootstrap Node backend

1. Create `backend-nodejs/package.json`, `tsconfig.json`, and base scripts.
2. Install and configure ESLint and Prettier for TypeScript (`eslint`, `@typescript-eslint/*`, `prettier`, and integration config), then add scripts such as `lint`, `lint:fix`, `format`, and `format:check`.
3. Add Express app bootstrap, TypeScript config, and test setup.
4. Add a minimal `GET /api/health` endpoint.

Acceptance:

- `npm run dev` starts the Node server
- `npm run lint` and `npm run format:check` run successfully
- `GET /api/health` returns the expected contract

### Phase 2 — Config, schemas, and error model

1. Add environment validation.
2. Add Zod schemas for requests and responses.
3. Add shared application error types and centralized error middleware.
4. Add body-size limits and CORS.

Acceptance:

- invalid payloads return the shared JSON error envelope
- missing provider keys fail fast at startup

### Phase 3 — Provider abstraction and OpenAI support

1. Implement `LLMProvider` interface.
2. Implement `openaiProvider.ts`.
3. Implement `factory.ts`.
4. Add a fake provider for tests.

Acceptance:

- `POST /api/chat` works end-to-end with OpenAI
- tests can replace the real provider with a fake deterministically

### Phase 4 — Non-streaming chat parity

1. Implement `ChatService.completeChat`.
2. Add `POST /api/chat`.
3. Normalize ids, timestamps, model, provider, and role fields.

Acceptance:

- frontend `sendChat()` works unchanged against Node
- response shape matches the current backend contract

### Phase 5 — Streaming chat parity

1. Implement SSE writer utilities.
2. Implement `ChatService.streamChat`.
3. Add `POST /api/chat/stream`.
4. Add disconnect-aware cancellation.
5. Normalize stream errors into SSE `error` frames.

Acceptance:

- frontend `streamChat()` works unchanged against Node
- the UI renders streamed tokens incrementally without parser changes
- aborting from the frontend stops upstream work

### Phase 6 — Gemini parity

1. Implement `geminiProvider.ts`.
2. Add factory branch and tests.
3. Confirm provider switching works only through config or request overrides.

Acceptance:

- changing `LLM_PROVIDER=gemini` preserves the same API behavior
- no router or frontend changes are required

### Phase 7 — Test parity and documentation

1. Port or recreate the Python backend's endpoint and provider tests in Node.
2. Add `backend-nodejs/README.md` with run, test, env, and endpoint usage.
3. Document how to run Python and Node backends side-by-side for comparison.

Acceptance:

- Node test suite is green
- maintainers can switch between Python and Node implementations predictably

## 12. Local Development Plan

Recommended dev ports:

- Python backend: `8000`
- Node backend: `8001` during implementation
- Frontend: `5173`

This avoids port contention while both backends exist.

Suggested workflow:

1. Keep Python running as the reference backend.
2. Run Node on a different port during development.
3. Point `VITE_API_BASE_URL` at the Node backend when testing parity.
4. Switch back to Python quickly when behavior diverges.

Once the Node backend is verified, either backend can become the active local target by changing only `VITE_API_BASE_URL`.

## 13. Risks And Decisions

### Risk: streaming behavior differs subtly from FastAPI

Express SSE support is straightforward, but buffering, header flushing, and disconnect handling are easier to get wrong than in FastAPI.

Mitigation:

- keep SSE framing in a small dedicated utility
- add explicit stream sequence tests
- validate in a real browser using the existing frontend hook

### Risk: provider SDK behavior is not symmetric across Python and Node

OpenAI and Gemini streaming APIs may expose different event shapes in Node than in Python.

Mitigation:

- normalize aggressively in the provider adapters
- keep provider-specific logic fully isolated under `providers/`

### Risk: schema and error drift breaks the frontend

Even small changes to response keys or SSE event payloads could break the current frontend parser and reducer flow.

Mitigation:

- treat the current frontend client and tests as compatibility fixtures
- do not change endpoint paths, top-level keys, or event names

## 14. Recommended Acceptance Checklist

- `backend-python` still runs and tests pass after the directory move
- `backend-nodejs` starts and passes its own test suite
- `GET /api/health` matches the current contract
- `POST /api/chat` works with the current frontend client unchanged
- `POST /api/chat/stream` works with the current frontend stream parser unchanged
- stream abort from the frontend stops server-side streaming work
- OpenAI and Gemini both work behind the same API
- docs explain how to run both backends and switch the frontend target

## 15. Implementation Order Recommendation

The fastest path is:

1. Move Python into `backend-python` without changing behavior.
2. Scaffold `backend-nodejs` with health, config, and tests.
3. Implement provider abstraction and non-streaming chat first.
4. Implement streaming SSE second.
5. Add Gemini only after OpenAI parity is stable.

That sequence minimizes unknowns and keeps the current Python backend available as a reference at every step.
