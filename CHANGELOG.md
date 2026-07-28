# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Platform releases use internal names (Post-MVP V1.1, V2 Epic 04). Git tags follow semver when cut — see [Releases](https://github.com/pateatlau/basic-chatbot-react-python/releases). Application package versions remain at `0.1.0` until a formal API freeze; the planned public-documentation tag is `v1.0.0-public`.

## [Unreleased]

## [V2 Epic 04 — Voice Interfaces] — 2026-07-29

Bidirectional voice chat for authenticated users, reusing the same RAG, agent, and MCP orchestration path as text SSE.

### Added

- Voice layer under `app/ai/voice/` with STT/TTS provider protocols and OpenAI Whisper + TTS adapter
- WebSocket transport at `GET /api/voice/ws?session_id={chat_session_id}` with JSON-framed PCM16 24 kHz mono audio and transcript events
- Voice session manager (auth, ownership, heartbeat, timeout), barge-in interrupt controller, and `UnifiedChatService` bridge
- Frontend voice mode: `voiceClient.ts`, `useVoiceSession` hook, `VoiceModeControls` integrated into Composer and ChatPage
- `VOICE_ENABLED` feature flag (default off); `voice_enabled` on health endpoint when configured

### Changed

- Voice transcripts feed the existing chat reducer — assistant replies stream as text deltas plus audio output events

## [V2 Epic 03 — MCP Integration] — 2026-07-28

Model Context Protocol (MCP) client for discovering and executing tools from remote MCP servers via the existing tool platform.

### Added

- MCP client package (`app/ai/mcp/`) with stdio transport, JSON-RPC subprocess communication, and server registry lifecycle
- Dynamic tool discovery (`tools/list` → `ToolDefinition`) and execution adapter wired into `ToolRegistry` → `ToolExecutor`
- Config-driven server registration from `MCP_SERVERS` JSON with env-backed credentials and `{server_name}.{tool_name}` naming convention
- `McpPermissionPolicy` composing with `ToolAuthorizer` for per-server/per-tool allowlists (authenticated users only)
- Startup/shutdown hooks to register MCP tools when `MCP_ENABLED=true` and disconnect gracefully on shutdown
- `MCP_ENABLED` feature flag (default off); targets MCP specification 2024-11-05

## [V2 Epic 02 — Advanced RAG] — 2026-07-25

Advanced document retrieval extending the V1 RAG stack with hybrid search, reranking, compression, and citations.

### Added

- Advanced RAG pipeline behind `ADVANCED_RAG_ENABLED` (hybrid dense + Postgres FTS with RRF, metadata filters, LLM query rewrite, parent-child retrieval, Cohere rerank, context compression)
- Structured citations in chat/RAG APIs and SSE, with minimal frontend citation rendering
- Parent-child chunking and sync indexing job hook for knowledge ingest
- Provider-agnostic RAG protocols (`QueryRewriter`, `Reranker`, `ContextCompressor`) and shared retrieval models
- FTS migration with GIN index on `document_chunks.content_tsv`

### Changed

- Chat and RAG hot paths optionally route through `AdvancedRetrievalPipeline` when the flag is on; V1 dense-only path remains the default when off
- Compact chat composer: single input + toolbar shell, collapsed provider/model picker, visible tool checkboxes, and hover tooltips for provider/model, web search, documents, and Manage

### Fixed

- Gemini provider compatibility issues
- Chat session isolation between authenticated users
- Clear chat state on logout

## [V2 Epic 01 — Agent Framework] — 2026-07-24

Reusable, provider-agnostic agent runtime for multi-step tool use in unified web-search chat.

### Added

- Agent runtime under `app/ai/agent/` (`DefaultAgent`, ReAct planner, executor, scratchpad, reflection, retry, streaming)
- Chat adapter wiring unified web-search chat through the agent when `AGENT_RUNTIME_ENABLED=true`
- `StreamPublisher` mapping agent events to existing SSE frame names
- `AGENT_RUNTIME_ENABLED` feature flag (default off); V1.1 tool loop unchanged when disabled

## [Post-MVP V1.1.1 — Production Polish] — 2026-07-22

UX, auth hardening, and public-demo cost controls without new platform capabilities.

### Added

- Delete chat session (`DELETE /api/chat/sessions/{id}`) with UI confirmation and post-delete navigation fallback
- Auto-generated session titles (~50 characters from first user message)
- Protected routes for `/documents`; expired JWT redirect to `/` with banner
- Branded 404 page with **Back to Chat** / **Go Home**
- Public demo protection: guest output token cap, upload quotas, `DEMO_MODE_STRICT`, and ops spending-alert documentation
- Shared `LoadingIndicator`, `EmptyState`, and friendly provider error mapping (`friendlyErrors.ts`)

### Changed

- Consistent loading states and empty-state CTAs across chat and documents flows
- Mobile responsiveness review at 375px / 390px with touch-target fixes

## [Post-MVP V1.1 — Unified Chat] — 2026-07-22

Web search and document grounding consolidated on the main chat surface across all four LLM providers.

### Added

- Unified chat orchestration via `UnifiedChatService` with `use_web_search` and `use_documents` request toggles
- Multi-provider tool calling (OpenAI, Gemini, Groq, Anthropic) in non-streaming and streaming modes
- Per-request RAG provider/model selection on chat and `/api/rag/ask`
- SSE extensions: `retrieval_complete`, `tool_start`, `tool_end`
- Provider capability model on `GET /api/health`; composer toggles on `/` for authenticated users

### Changed

- Main chat (`/`) is the primary ask surface; `/documents` retained for upload, list, and delete

## [Post-MVP V1 — AI Platform] — 2026-07-21

Reusable AI platform on the Python backend: tools, knowledge ingestion, vector search, and generic RAG.

### Added

- Centralized Jinja2 prompt management with versioning and regression tests
- Generic tool platform (registry → validation → authorization → execution) with web search as the first production tool
- Knowledge platform: upload → parse (PDF, DOCX, MD, TXT) → chunk → embed → pgvector storage
- Generic RAG framework (`app/ai/rag/`) with user-scoped similarity search
- Evaluation CLI for prompt, retrieval, and end-to-end quality measurement
- Document/RAG HTTP API (auth-only) and frontend `/documents` route
- Feature flags: `RAG_ENABLED`, `TOOLS_ENABLED` (default off — MVP chat unchanged when disabled)

## [MVP — Foundational Chat Platform] — 2026-07-19

Production-ready full-stack streaming chat with multi-provider LLM support and Python backend hardening.

### Added

- React + TypeScript + Vite chat UI with SSE streaming, stop/cancel, and retry
- Google OAuth login with app-issued JWT and anonymous guest token flow
- Chat persistence (sessions, messages, guest quota) with PostgreSQL
- Multi-provider LLM abstraction (OpenAI, Gemini, Groq, Anthropic) via env-driven switching
- Non-streaming (`POST /api/chat`) and streaming SSE (`POST /api/chat/stream`) endpoints
- Consolidated environment configuration with startup validation
- Structured JSON logging with redaction in production
- Request correlation IDs (`X-Request-ID`) on all responses
- Centralized error envelope for validation, provider, database, and quota failures
- HTTP rate limiting with `Retry-After` header
- CI quality gates: Ruff lint/format, Pyright standard mode, pytest with ≥80% coverage on `app/`

### Changed

- Python FastAPI backend established as the production reference; Node.js backend remains chat-only reference (hardening deferred)
