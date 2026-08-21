# Frontend Application

Streaming chat and operations UI for the Fullstack AI Platform. MVP, Post-MVP V1.1, and V2 Epics 01–11 are complete; feature availability is driven by backend health flags.

## Capabilities

- Tailwind CSS v4 ChatGPT-like shell with responsive sidebar (drawer / collapse)
- SSE streaming with stop, retry, and connection-error banner
- Unified chat toggles on `/` — web search and document grounding for authenticated users (V1.1)
- SSE handling for `retrieval_complete`, `tool_start`, and `tool_end` lifecycle events (V1.1)
- Provider capability gating via `GET /api/health` → `capabilities.by_provider` (V1.1)
- Google OAuth sign-in; guest session continuity via `X-Guest-Token`
- Forwards `X-Request-ID` on retry for request traceability
- Provider/model selection (OpenAI, Gemini, Groq, Anthropic) in the composer
- Advanced RAG citations, agent/MCP tool lifecycle states, and WebSocket voice controls on `/`
- Memory settings, workflow operations, usage/cost observability, and plugin inventory pages
- Unified approval inbox with inline chat/workflow decisions and revision history
- Background Jobs dashboard with filters, schedules, dead-letter detail, and retry
- Security & Governance dashboard for role assignments, audit review, and policy summaries at `/security` (V2 Epic 11)
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
- `src/pages/MemorySettingsPage.tsx` - memory records, preferences, and summary controls
- `src/pages/WorkflowsPage.tsx` - workflow definitions, runs, approvals, cancel, and resume
- `src/pages/ObservabilityPage.tsx` - usage and cost dashboard
- `src/pages/PluginsPage.tsx` - loaded/failed plugin inventory
- `src/pages/ApprovalsPage.tsx` - pending/history approval inbox and revisions
- `src/pages/JobsPage.tsx` - jobs, schedules, dead letters, and retry
- `src/pages/SecurityPage.tsx` - flag-gated Roles, Audit Log, and Policies dashboard
- `src/api/` - typed chat, document, memory, workflow, observability, plugin, approval, jobs, and security clients
- `src/types/security.ts` - Security & Governance response and UI types
- `src/components/` - `MessageList`, `MessageBubble`, `Composer`, `StreamingIndicator`, shared `LoadingIndicator`, shared `EmptyState`
- `src/utils/friendlyErrors.ts` - maps provider error codes to user-facing retry copy (Phase 7)
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
- Operations: health-gated navigation to Documents, Workflows, Observability, Plugins, Approvals, Jobs, and Security

## V2 Program UI

All V2 epics are complete. Their master flags default to `false`; the frontend reads backend health and hides or disables feature entry points without requiring matching `VITE_*` flags.

| Epic | Capability                 | Frontend experience                                                      | Backend health gate           | Release                                                           |
| ---- | -------------------------- | ------------------------------------------------------------------------ | ----------------------------- | ----------------------------------------------------------------- |
| 01   | Agent Framework            | Agent-backed web-search turns and tool lifecycle events on `/`           | `agent_runtime_enabled`       | [Summary](../docs/releases/post-mvp-v2-epic1-release-summary.md)  |
| 02   | Advanced RAG               | Structured citations on grounded chat and `/documents`                   | `advanced_rag_enabled`        | [Summary](../docs/releases/post-mvp-v2-epic2-release-summary.md)  |
| 03   | MCP Integration            | Remote MCP tools appear through the existing agent/tool chat experience  | `mcp_enabled`                 | [Summary](../docs/releases/post-mvp-v2-epic3-release-summary.md)  |
| 04   | Voice Interfaces           | Authenticated voice controls, transcripts, playback, and barge-in on `/` | `voice_enabled`               | [Summary](../docs/releases/post-mvp-v2-epic4-release-summary.md)  |
| 05   | Memory System              | `/settings/memory` records, preferences, project memory, and summaries   | `memory_enabled`              | [Summary](../docs/releases/post-mvp-v2-epic5-release-summary.md)  |
| 06   | Workflow Engine            | `/workflows` definitions, runs, approvals, cancel, and resume            | `workflow_engine_enabled`     | [Summary](../docs/releases/post-mvp-v2-epic6-release-summary.md)  |
| 07   | Observability & Evaluation | `/observability` usage/cost dashboard                                    | `observability_enabled`       | [Summary](../docs/releases/post-mvp-v2-epic7-release-summary.md)  |
| 08   | Plugin Architecture        | `/plugins` loaded/failed inventory and contribution details              | `plugins_enabled`             | [Summary](../docs/releases/post-mvp-v2-epic8-release-summary.md)  |
| 09   | Human-in-the-Loop          | `/approvals`, inline chat decisions, workflow edits/reasons              | `hitl_enabled`                | [Summary](../docs/releases/post-mvp-v2-epic9-release-summary.md)  |
| 10   | Background Jobs            | `/jobs` queue/schedule tabs, filters, dead-letter detail, retry          | `background_jobs_enabled`     | [Summary](../docs/releases/post-mvp-v2-epic10-release-summary.md) |
| 11   | Security & Governance      | `/security` roles, audit log, and policy summary                         | `security_governance_enabled` | [Summary](../docs/releases/post-mvp-v2-epic11-release-summary.md) |

`/settings/memory`, `/workflows`, `/observability`, `/plugins`, `/approvals`, `/jobs`, and `/security` are protected routes. Guests retain core chat access but cannot enter these authenticated operational surfaces.

## Security & Governance Dashboard (V2 Epic 11)

- **Route:** `/security` (authenticated users only); the nav link appears when `GET /api/health` reports `security_governance_enabled=true`.
- **Roles:** system-role definitions, paginated users, current assignments, and assign/revoke actions for callers with `rbac:manage`.
- **Audit Log:** filters for actor, action, resource type, outcome, and date range, plus paginated event details.
- **Policies:** read-only guardrail counts and active rate-limit/quota configuration; raw regex rules, bootstrap emails, and credentials are never displayed.
- **States:** `403` responses render permission-specific empty states; `503 feature_disabled` renders an unavailable state. Backend authorization remains authoritative.

The dashboard uses the existing `VITE_API_BASE_URL`; no frontend security flag is required. Backend setup and staged rollout are documented in [backend-python/README.md](../backend-python/README.md#security--governance-operations-v2-epic-11).

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
- `GET /api/voice/ws` (WebSocket) when `VOICE_ENABLED=true`
- `/api/memory/*` when `MEMORY_ENABLED=true`
- `/api/workflows*` and `/api/workflow-runs*` when `WORKFLOW_ENGINE_ENABLED=true`
- `/api/observability/usage` when `OBSERVABILITY_ENABLED=true`
- `/api/plugins*` when `PLUGINS_ENABLED=true`
- `/api/approvals*` when `HITL_ENABLED=true`
- `/api/jobs*` when `BACKGROUND_JOBS_ENABLED=true`
- `/api/security/*` role, audit, and policy endpoints when `SECURITY_GOVERNANCE_ENABLED=true`

Document and RAG endpoints (auth-only, Phase 11 backend):

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{id}`
- `DELETE /api/documents/{id}`
- `POST /api/rag/ask`

See [backend-python/README.md](../backend-python/README.md) → **Knowledge and RAG API (Phase 11)** for request/response shapes and env requirements (`RAG_ENABLED=true` for ask).

Release history: [V1](../docs/releases/post-mvp-v1-release-summary.md), [V1.1](../docs/releases/post-mvp-v1.1-release-summary.md), and the complete [V2 Epic 01–11 release set](../docs/releases/).

## Documents and RAG UI (Phase 12 + V1.1)

- **Route:** `/documents` (authenticated users only via `ProtectedRoute`; unauthenticated/expired sessions redirect to `/`) — upload, list, delete
- **Chat route:** `/` — primary ask surface; authenticated users enable web search / document grounding via composer toggles (V1.1)
- **404 catch-all:** unknown paths (e.g. `/unknown-path`) render `NotFoundPage` with **Back to Chat** → `/` (V1.1.1 Phase 5)
- **Required env:** `VITE_API_BASE_URL` (unchanged; no new `VITE_*` flags — feature availability is detected via `GET /api/health`)
- **Backend:** Set `RAG_ENABLED=true` and/or `TOOLS_ENABLED=true` for document / web search toggles; `WEB_SEARCH_API_KEY` required when tools are on
- **Guest policy:** Guests cannot use toggles; see sign-in prompt in composer. Documents nav link hidden until signed in
- **Standalone RAG panel:** `/documents` may still call `POST /api/rag/ask`; chat toggles are the primary UX in V1.1

If the backend is not running or CORS is misconfigured, streaming requests will fail.

The frontend expects the Python production backend to support:

- SSE `start`, `delta`, `end`, and `error` frames
- Additive SSE `retrieval_complete`, `tool_start`, `tool_end` when unified toggles are on (V1.1)
- Health fields: chat/RAG/tool/provider capabilities plus every V2 master flag used by the program UI table above
- Standard error envelope `{ error: { code, message, request_id } }`
- `X-Request-ID` on every response (forwarded on retry via `chatClient.ts`)
- `X-Guest-Token` and `X-Guest-Quota-Remaining` for anonymous callers
- CORS for the active frontend origin
- Provider selection values for OpenAI, Gemini, Groq, and Anthropic

## Tests

Current frontend tests: **326 passed** (54 files, Vitest, 2026-08-21).

Coverage includes:

- SSE frame parsing across arbitrary chunk boundaries (including V1.1 tool/RAG lifecycle frames)
- Reducer transitions for streaming, interruption, retry, and error cases
- Page-level composer behavior with streamed tokens, toggles, and Stop
- Composer provider/model selection wiring and payload coverage (`use_web_search`, `use_documents`)
- `X-Request-ID` retry forwarding in `chatClient.ts`
- Shell accessibility smoke coverage for core landmarks and controls
- Agent/RAG/MCP/voice chat integration and feature-gated states
- Memory, workflow, observability, plugin, approvals, jobs, and security clients/pages
- Role mutations, approval/job actions, audit/policy views, and disabled/forbidden states

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
