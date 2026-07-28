---
epic: v2-04
title: Voice Interfaces
status: completed
version: 2
depends_on: [v2-03]
provides:
  [
    SttProvider,
    TtsProvider,
    VoiceSessionManager,
    VoiceSession,
    VoiceConfig,
    VoiceStreamBridge,
    InterruptController,
    OpenAiVoiceAdapter,
    VOICE_ENABLED,
    useVoiceSession,
    VoiceModeControls,
  ]
feature_flags: [VOICE_ENABLED]
packages:
  [app/ai/voice, frontend/src/hooks, frontend/src/api, frontend/src/components]
test_paths:
  [
    tests/ai/voice,
    tests/test_voice_router.py,
    frontend/src/hooks/useVoiceSession.test.ts,
    frontend/src/components/VoiceModeControls.test.tsx,
    frontend/src/pages/ChatPage.voice.test.tsx,
  ]
---

# Post-MVP V2 Epic 04 — Voice Interfaces

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "4. Voice Interfaces"

**Predecessor:** [Epic 03 — MCP Integration](./post-mvp-v2-epic-03-mcp-integration.md)

---

# Part I — Design

## Objective

Add speech-to-text (STT), text-to-speech (TTS), bidirectional streaming voice, interrupt (barge-in), and voice session lifecycle under `app/ai/voice/`. Voice is a **client modality** that feeds text into and consumes text from the existing chat/agent pipeline via `UnifiedChatService` — not a rewrite of agent internals.

Ships behind `VOICE_ENABLED=false` (default). When the flag is off, existing text chat SSE/MCP/agent/RAG paths are unchanged.

**Delivers:** Provider-agnostic STT/TTS Protocols, OpenAI concrete adapter (Whisper + TTS API), WebSocket voice transport, voice session manager (attach to chat session), streaming audio chunk framing, barge-in/interrupt semantics, flag-guarded router, and **end-to-end frontend voice mode** (mic input, spoken replies, transcript streaming parity with text mode).

**Does not ship:** WebRTC/SSE voice transport; cross-session voice memory (Epic 05); workflow voice nodes (Epic 06); voice OTel spans/eval harness (Epic 07); voice plugin SDK (Epic 08); spoken-action approval (Epic 09); async offline transcription jobs (Epic 10); RBAC/audit/rate limits for voice (Epic 11); Realtime API unified duplex (future); advanced voice UX polish (waveform visualizers, voice selection UI beyond config default).

## Principles

Platform-first · composition over coupling · provider-agnostic core (Protocols) · streaming-first (chunked audio + text deltas) · async-first · interface-driven · security by default (authenticated-only voice sessions) · incremental · no over-engineering · extend existing chat/streaming surfaces · text remains agent interchange format

## Architecture

```text
ChatPage (voice mode ON) ──► useVoiceSession ──WebSocket──► /api/voice/ws
      │                              │
      │                              ├─ mic capture → audio_in
      │                              ├─ audio_out → Web Audio playback
      │                              └─ text events → chatReducer (same as SSE)
      ▼
MessageList / Composer               (transcript visible while speaking & replying)

Client (WebSocket)
      │
      ▼
VoiceRouter (/api/voice/ws)          [VOICE_ENABLED gate]
      │
      ▼
VoiceSessionManager ── attach ──► chat session_id / CallerContext
      │
      ├─► SttProvider (streaming) ──► transcript text
      │         │
      │         ▼
      │   UnifiedChatService.stream_execute()  ← same path as POST /api/chat/stream
      │         │                              (RAG pre-handoff, agent, MCP tools unchanged)
      │         ▼
      │   assistant text stream (SSE frames internally)
      │         │
      └─► TtsProvider (streaming) ◄── chunked synthesis
      │
InterruptController ── cancel TTS + upstream LLM stream on barge-in
```

```text
app/ai/voice/                      # NEW — voice platform layer
├── __init__.py                    # public API exports
├── interfaces.py                  # SttProvider, TtsProvider, VoiceSession Protocols
├── stt.py                         # SttPipeline — streaming transcription orchestration
├── tts.py                         # TtsPipeline — streaming synthesis orchestration
├── session.py                     # VoiceSessionManager — lifecycle, chat attach, heartbeat
├── streaming.py                   # VoiceStreamBridge — audio chunk framing; WS message codec
├── interrupt.py                   # InterruptController — barge-in / cancel semantics
├── config.py                      # VoiceConfig model
├── exceptions.py                  # VoiceSessionError, SttError, TtsError
└── providers/
    └── openai_voice.py            # OpenAiVoiceAdapter (Whisper STT + OpenAI TTS)

app/routers/voice.py               # NEW — WebSocket endpoint (flag-guarded)
app/core/config.py                 # extend — VOICE_ENABLED, voice provider settings
app/providers/capabilities.py      # extend — supports_audio for openai when voice configured
app/schemas/voice.py               # NEW — WS frame schemas (JSON; no raw audio in logs)
app/ai/deps.py                     # extend — voice DI factories
tests/ai/voice/                    # NEW — unit + integration with fakes
tests/test_voice_router.py         # NEW — router + flag parity

frontend/src/
├── api/voiceClient.ts             # NEW — WebSocket client + message codec
├── hooks/useVoiceSession.ts       # NEW — session lifecycle, mic, playback, events
├── components/VoiceModeControls.tsx  # NEW — mic toggle, recording state, voice-mode switch
├── components/Composer.tsx        # modify — voice mode toggle + mic affordance
└── pages/ChatPage.tsx             # modify — wire voice session; shared transcript path
```

Epic 01 agent runtime, Epic 02 RAG pipeline, Epic 03 MCP tool path, and V1 text chat remain **unchanged** except for additive voice router branch when `VOICE_ENABLED=true` and optional voice-mode branch in `ChatPage` when flag on.

## Components

| Component             | Role                                                                                   | Key outputs                              |
| --------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------- |
| `SttProvider`         | Protocol for streaming speech-to-text                                                  | Partial/final transcript strings         |
| `TtsProvider`         | Protocol for streaming text-to-speech                                                  | Audio byte chunks                        |
| `OpenAiVoiceAdapter`  | Concrete STT (Whisper API) + TTS (OpenAI speech API) adapter                           | Implements both Protocols                |
| `SttPipeline`         | Buffer/chunk audio → invoke STT → emit transcript events                               | `TranscriptEvent` stream                 |
| `TtsPipeline`         | Consume assistant text → invoke TTS → emit audio chunks                                | `AudioOutEvent` stream                   |
| `VoiceSessionManager` | Create/attach/teardown voice sessions; heartbeat; timeout                              | `VoiceSession` handle                    |
| `VoiceStreamBridge`   | Encode/decode WebSocket JSON frames; bridge audio ↔ pipelines                          | WS send/receive                          |
| `InterruptController` | On barge-in: cancel TTS task + upstream `UnifiedChatService` stream                    | Clean partial state                      |
| `VoiceRouter`         | FastAPI WebSocket route; auth gate; session handshake                                  | Bidirectional voice channel              |
| `VoiceConfig`         | Sample rate, chunk size, timeouts, provider selection                                  | Validated settings                       |
| `UnifiedChatService`  | Existing orchestrator — voice calls `stream_execute()` with transcript as user message | SSE-equivalent text stream (internal)    |
| `useVoiceSession`     | Frontend hook — WS connect, mic capture, audio playback, event callbacks               | Voice WS events                          |
| `VoiceModeControls`   | Mic button, recording indicator, voice-mode toggle in Composer                         | User speak / interrupt affordances       |
| `ChatPage` voice path | When voice mode ON: route turns via WS; dispatch text events to `chatReducer`          | Same `MessageList` streaming as SSE path |

## Use cases (voice mode ON)

When `VOICE_ENABLED=true` and the user enables **voice mode** in the chat UI:

| #   | Use case                   | Behaviour                                                                                                                                                                                                                                                    |
| --- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | **Verbal user input**      | User holds/clicks mic → browser captures audio → WS `audio_in` → STT → user message text streams into `MessageList` (`transcript_partial` → live update; `transcript_final` → committed user message via `ADD_USER_MESSAGE`)                                 |
| 2   | **Verbal assistant reply** | After user utterance, backend runs chat pipeline → WS emits `assistant_text_delta` + `audio_out` → UI **plays audio** and **streams assistant text** into the active assistant bubble (`START_MESSAGE` / `APPEND_DELTA` / `END_MESSAGE`)                     |
| 3   | **Transcript parity**      | User and assistant **text transcripts always stream in `MessageList`** using the same `chatReducer` actions and visual treatment as voice-mode-off SSE chat (`useChatStream` callbacks). Audio is additive playback; text remains the canonical chat record. |

Voice mode OFF (or `VOICE_ENABLED=false`): existing text Composer + SSE (`useChatStream`) path unchanged.

## Frontend event → chatReducer mapping

Voice mode must reuse the same reducer actions as `ChatPage` + `useChatStream` (no parallel message state):

| WS event / hook signal    | chatReducer action                                               | Notes                                                                    |
| ------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `transcript_partial`      | Update in-progress user draft ref (Composer) or ephemeral bubble | Optional live caption before commit                                      |
| `transcript_final`        | `ADD_USER_MESSAGE`                                               | Same shape as text send                                                  |
| (turn start)              | `START_MESSAGE`                                                  | Mirror SSE `start`                                                       |
| `assistant_text_delta`    | `APPEND_DELTA`                                                   | Mirror SSE `delta`                                                       |
| `tool_start` / `tool_end` | Reuse existing `ChatPage` tool indicator handlers                | Mirror SSE tool frames                                                   |
| `turn_complete`           | `END_MESSAGE`                                                    | Mirror SSE `end`; include `tools_used` / retrieval metadata when present |
| `interrupted`             | `INTERRUPT_MESSAGE` or `STOP_MESSAGE`                            | Match existing stop semantics                                            |
| WS / mic error            | `STREAM_ERROR` / `SET_ERROR`                                     | Mirror SSE error handling                                                |

## Scope

**In:**

- STT pipeline (audio → text for chat/agent)
- TTS pipeline (assistant text → streamed audio)
- Bidirectional streaming voice over **WebSocket** (JSON-framed messages; base64 audio payloads)
- Interrupt handling (barge-in): cancel in-flight TTS **and** upstream LLM stream
- Voice session management (create, attach to chat session/user, heartbeat, timeout, teardown)
- Provider abstraction (`SttProvider`, `TtsProvider` Protocols)
- OpenAI concrete adapter using existing `openai` dependency (Whisper + TTS API)
- Feature flag `VOICE_ENABLED` (default `false`); text chat unchanged when off
- Authenticated users only for voice sessions (guest denial)
- Tests with fakes (no live API in CI)
- **Frontend Voice Integration** (end-to-end when flag on):
  - Voice mode toggle in `Composer` (authenticated users only; hidden when `voice_enabled=false`)
  - Mic capture via `MediaRecorder` / `getUserMedia` → WS `audio_in`
  - Assistant audio playback via Web Audio API (PCM16 24 kHz mono from WS `audio_out`)
  - Transcript streaming in `MessageList` via existing `chatReducer` (parity with SSE)
  - Barge-in control (stop/interrupt while assistant speaking)
- Frontend tests (Vitest + mocked WS/mic)

**Out:**

- Epic 05 Memory (cross-session voice context) · Epic 06 Workflows (voice nodes) · Epic 07 Observability (voice trace spans, eval harness) · Epic 08 Plugins (voice plugin SDK) · Epic 09 HITL (approval before spoken actions) · Epic 10 Background Jobs (async/offline transcription) · Epic 11 Security & Governance (RBAC, audit logs, rate limits, secret vault)
- WebRTC transport · SSE extension for binary audio · OpenAI Realtime unified duplex API
- Advanced voice UX polish (waveform visualizers, per-message voice picker, offline TTS download)
- Default flip of `VOICE_ENABLED` to `true`
- Guest voice mode (authenticated-only; guests keep text-only chat)
- Moving RAG or MCP into voice layer
- Rewriting agent planner/executor for audio-native interchange

## Dependencies

| Requires                                                                                                                                                                           | Provides to downstream                                                                                                                             |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Epic 03 (`v2-03`) complete; Epic 02 RAG pre-handoff in `UnifiedChatService`; Epic 01 agent/streaming; frontend chat stack (`ChatPage`, `useChatStream`, `chatReducer`, `Composer`) | `SttProvider`, `TtsProvider`, `VoiceSessionManager`, `VoiceStreamBridge`, `InterruptController`, `VOICE_ENABLED`, `useVoiceSession`, voice mode UI |

**Future consumers:** Epic 05 (Memory — voice session summaries), Epic 06 (Workflows — voice I/O nodes), Epic 07 (Observability — voice latency/token metrics), Epic 08 (Plugins — third-party voice providers), Epic 09 (HITL — pause before spoken tool actions), Epic 11 (Security — voice RBAC, quotas)

## Locked decisions

| Topic                   | Decision                                                                                                                                                                                                                     | Deferred to                         |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Package                 | New `app/ai/voice/`; tests `tests/ai/voice/`; router `app/routers/voice.py`; schemas `app/schemas/voice.py`                                                                                                                  | —                                   |
| Primary transport       | **WebSocket** (`/api/voice/ws`); JSON message envelope with typed events; base64-encoded PCM audio payloads                                                                                                                  | WebRTC, SSE binary extension        |
| Primary STT/TTS vendor  | **OpenAI** via existing `openai` package — Whisper (`whisper-1`) STT; OpenAI TTS (`tts-1`, voice `alloy`)                                                                                                                    | Deepgram, ElevenLabs, Gemini audio  |
| Provider location       | Protocols in `app/ai/voice/interfaces.py`; concrete adapter in `app/ai/voice/providers/openai_voice.py` — **not** mixed into `app/providers/` LLM modules                                                                    | Relocate to shared providers layer  |
| Agent integration point | Voice router → **`UnifiedChatService.stream_execute()`** with transcript text; internal SSE frame consumption; no direct `DefaultAgent` bypass                                                                               | —                                   |
| RAG boundary            | RAG stays in `UnifiedChatService` / `RAGService` **before** agent handoff; voice does not bypass or relocate RAG                                                                                                             | —                                   |
| MCP boundary            | MCP tools flow through existing `ToolRegistry` → `ToolExecutor` path; voice does not embed MCP transport                                                                                                                     | —                                   |
| Interrupt semantics     | Barge-in cancels **both** in-flight TTS synthesis **and** upstream LLM/agent stream (`asyncio.Task.cancel` + stream close); partial assistant message discarded unless persisted by chat layer mid-stream rules              | Partial persist on interrupt        |
| Session model           | **1:1** voice session ↔ existing `chat session_id`; voice session id separate (`voice_session_id`); **authenticated users only**; guests receive `voice_auth_required` error                                                 | Guest voice                         |
| Audio format            | **PCM16**, **24 kHz**, **mono**; max **4096 bytes** raw audio per inbound chunk (base64 in JSON); outbound chunks same encoding                                                                                              | Opus, MP3 streaming                 |
| Feature flag            | Single master **`VOICE_ENABLED`** (default **false**); no finer STT/TTS sub-flags in v1                                                                                                                                      | Granular STT/TTS flags              |
| `supports_audio`        | Set `openai` → `supports_audio=True` in `capabilities.py` when OpenAI voice adapter registered; others remain `false`                                                                                                        | Multi-vendor audio capabilities     |
| Frontend voice mode     | **Ship end-to-end** when `VOICE_ENABLED=true`: toggle in `Composer`; `useVoiceSession` + `voiceClient.ts`; wire `ChatPage` to reuse `chatReducer` streaming actions; authenticated-only                                      | Guest voice, advanced UX polish     |
| Transcript parity       | Voice mode routes **text events** (`transcript_*`, `assistant_text_delta`, `tool_*`, `turn_complete`) to the **same** `chatReducer` actions as `useChatStream` SSE frames — single `MessageList`, no duplicate transcript UI | Separate voice-only transcript pane |
| Voice vs text transport | Voice mode ON → WebSocket turn loop; voice mode OFF → existing SSE `POST /api/chat/stream`; both persist/display identical message shapes                                                                                    | Replacing SSE when voice off        |
| Observability           | Structured log fields (session ids, latencies, event types); **no raw audio or full transcripts** in logs by default                                                                                                         | Epic 07 OTel spans                  |
| Dependencies            | Reuse existing **`openai`** package; **no new runtime dependencies** for v1; explicit `websockets` pin only if Phase 0 audit shows not reliably transitive — requires user approval at that phase                            | Deepgram/ElevenLabs SDKs            |
| Credentials             | Reuse `Settings.openai_api_key`; no separate voice vault                                                                                                                                                                     | Epic 11 secret vault                |

## Voice session flow

```text
1. Connect (when VOICE_ENABLED=true):
   Client ──WebSocket──► GET /api/voice/ws?session_id={chat_session_id}
     ↓
   Auth: require authenticated CallerContext (reject guest)
     ↓
   VoiceSessionManager.create(session_id, caller) → voice_session_id
     ↓
   Server → Client: { "type": "session_started", "voice_session_id", "audio_format": "pcm16_24k_mono" }

2. User speaks (STT path):
   Client → Server: { "type": "audio_in", "seq", "payload_b64", "final": false|true }
     ↓
   SttPipeline → SttProvider.transcribe_stream(chunks)
     ↓
   Server → Client: { "type": "transcript_partial" | "transcript_final", "text" }
     ↓
   On transcript_final (or end-of-utterance): enqueue chat turn

3. Assistant response (chat + TTS path):
   VoiceStreamBridge calls UnifiedChatService.stream_execute(messages + transcript, ...)
     ↓
   Consume internal SSE frames (start, delta, tool_*, end) — map delta text → TtsPipeline
     ↓
   TtsProvider.synthesize_stream(text_chunks) → audio_out frames
     ↓
   Server → Client: { "type": "assistant_text_delta", "text" }
   Server → Client: { "type": "audio_out", "seq", "payload_b64" }
     ↓
   On end frame: { "type": "turn_complete" }

4. Barge-in (interrupt):
   Client → Server: { "type": "interrupt" }  (or new audio_in while TTS/LLM active)
     ↓
   InterruptController.cancel_all(session):
     - cancel TtsPipeline task
     - cancel UnifiedChatService stream task
     ↓
   Server → Client: { "type": "interrupted" }

5. Teardown:
   Client → Server: { "type": "session_end" }  OR heartbeat timeout
     ↓
   VoiceSessionManager.teardown(voice_session_id)
     ↓
   Server → Client: { "type": "session_closed" }
```

Owner scope (`user_id`) enforced via chat session ownership checks in `VoiceSessionManager` (reuse chat session access patterns from `ChatService`).

## WebSocket message protocol

All messages JSON UTF-8. Binary audio **never** sent as raw WS binary frames in v1 — base64 in `payload_b64` for simplicity and log-safe debugging.

| Type                      | Direction       | Fields                                                | Purpose                                   |
| ------------------------- | --------------- | ----------------------------------------------------- | ----------------------------------------- |
| `session_started`         | Server → Client | `voice_session_id`, `audio_format`                    | Handshake complete                        |
| `audio_in`                | Client → Server | `seq`, `payload_b64`, `final`                         | Inbound audio chunk                       |
| `transcript_partial`      | Server → Client | `text`, `stability` (optional)                        | Interim STT                               |
| `transcript_final`        | Server → Client | `text`                                                | Final STT for chat turn                   |
| `assistant_text_delta`    | Server → Client | `text`                                                | Assistant text (mirrors SSE delta)        |
| `audio_out`               | Server → Client | `seq`, `payload_b64`                                  | Outbound synthesized audio                |
| `tool_start` / `tool_end` | Server → Client | `name`, `success` (end only)                          | Optional tool progress (from SSE)         |
| `interrupt`               | Client → Server | —                                                     | User barge-in                             |
| `interrupted`             | Server → Client | —                                                     | Cancel acknowledged                       |
| `turn_complete`           | Server → Client | `tools_used?`, `retrieved_chunk_count?`, `citations?` | Turn finished; metadata mirrors SSE `end` |
| `heartbeat`               | Both            | `ts`                                                  | Keep-alive                                |
| `session_end`             | Client → Server | —                                                     | Client-initiated teardown                 |
| `session_closed`          | Server → Client | `reason`                                              | Session ended                             |
| `error`                   | Server → Client | `code`, `message`                                     | Recoverable/fatal errors                  |

## STT rules

| Rule                   | Default / behaviour                                                                |
| ---------------------- | ---------------------------------------------------------------------------------- |
| Provider               | `OpenAiVoiceAdapter` when `voice_provider=openai`                                  |
| Model                  | `whisper-1`                                                                        |
| Language               | Auto-detect (no override in v1)                                                    |
| Utterance end          | Client sends `audio_in` with `final: true` OR 800 ms silence heuristic in pipeline |
| Min audio before STT   | 100 ms of audio                                                                    |
| Max utterance duration | 60 s (then force `transcript_final` + error if exceeded)                           |
| Retry                  | Timeout/429 → retry via `retry_async` (max 3); auth/validation → no retry          |
| Empty transcript       | Skip chat turn; send `error` code `empty_transcript`                               |

## TTS rules

| Rule                         | Default / behaviour                                                                           |
| ---------------------------- | --------------------------------------------------------------------------------------------- |
| Provider                     | `OpenAiVoiceAdapter`                                                                          |
| Model                        | `tts-1`                                                                                       |
| Voice                        | `alloy` (configurable via `voice_tts_voice`)                                                  |
| Input                        | Assistant text deltas buffered to sentence boundaries when possible; flush on `end` SSE frame |
| Output format                | PCM16 24 kHz mono (adapter converts if API returns MP3 — convert in adapter only)             |
| Max text per synthesis chunk | 4096 characters                                                                               |
| Retry                        | Same classification as STT                                                                    |
| Cancel                       | `InterruptController` cancels in-flight synthesis task                                        |

## Interrupt / barge-in semantics

| Trigger                                | Action                                                                |
| -------------------------------------- | --------------------------------------------------------------------- |
| Client sends `interrupt`               | Cancel TTS + LLM stream immediately                                   |
| Client sends `audio_in` during TTS/LLM | Treat as barge-in (same cancel)                                       |
| Cancel complete                        | Emit `interrupted`; session stays open for next utterance             |
| Partial assistant message              | Not persisted on interrupt (match `MessageStatus.interrupted` intent) |
| Tool in progress                       | Cancel waits for in-flight tool via stream close; no new tool starts  |

## Session lifecycle

| Setting                                | Default                                                    |
| -------------------------------------- | ---------------------------------------------------------- |
| `voice_session_timeout_seconds`        | 300 (5 min idle)                                           |
| Heartbeat interval                     | Client every 30 s; server closes if 90 s without heartbeat |
| Max concurrent voice sessions per user | 1 (second connect → `error` code `session_already_active`) |
| Attach                                 | Requires valid `session_id` owned by caller                |
| Teardown                               | Idempotent; release provider resources                     |

## Public APIs (stable after Phase 1)

| API                                                                 | Kind                   |
| ------------------------------------------------------------------- | ---------------------- |
| `SttProvider`, `TtsProvider`, `VoiceSession`                        | Protocol               |
| `VoiceSessionManager`, `VoiceStreamBridge`, `InterruptController`   | Class (public)         |
| `VoiceConfig`, `TranscriptEvent`, `AudioOutEvent`, `VoiceWsMessage` | Model                  |
| `VoiceSessionError`, `SttError`, `TtsError`, `VoiceAuthError`       | Exception              |
| `create_voice_router(settings)` or router module export             | FastAPI router factory |

Internal (may evolve): `SttPipeline`, `TtsPipeline`, `OpenAiVoiceAdapter`, WS codec helpers, DI wiring.

## Configuration defaults

| Setting                            | Default                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| `VOICE_ENABLED`                    | **`false`**                                                                                        |
| `voice_provider`                   | `"openai"`                                                                                         |
| `voice_stt_model`                  | `"whisper-1"`                                                                                      |
| `voice_tts_model`                  | `"tts-1"`                                                                                          |
| `voice_tts_voice`                  | `"alloy"`                                                                                          |
| `voice_sample_rate_hz`             | `24000`                                                                                            |
| `voice_audio_encoding`             | `"pcm16"`                                                                                          |
| `voice_max_chunk_bytes`            | `4096`                                                                                             |
| `voice_session_timeout_seconds`    | `300`                                                                                              |
| `voice_heartbeat_interval_seconds` | `30`                                                                                               |
| `voice_max_utterance_seconds`      | `60`                                                                                               |
| Existing                           | `agent_runtime_enabled`, `advanced_rag_enabled`, `mcp_enabled`, `chat_streaming_enabled` unchanged |

## Design acceptance

- Flag off: no voice router mounted; no voice UI affordances; text chat SSE/MCP/agent/RAG unchanged
- Flag on: authenticated user enables voice mode → speaks → hears assistant reply; **text transcripts stream in `MessageList` identical to SSE mode**
- Voice feeds/consumes text through `UnifiedChatService` — RAG pre-handoff preserved
- MCP tools invokable during voice turns when flags on (via existing agent path)
- Barge-in cancels TTS + LLM stream; session recoverable; UI reflects interrupt in message list
- Core `app/ai/voice/` depends on Protocols only — no OpenAI SDK in pipeline modules (adapter only)
- Coverage ≥80% on `app/` and `app/ai/voice/`
- Frontend: `npm test -- --run` and `npm run build` pass; voice mode covered by Vitest with mocked WS/mic
- No raw audio/transcripts in structured logs by default
- CI uses fakes — no live OpenAI voice API calls

## Architectural invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Chat pipeline boundary** — Voice calls `UnifiedChatService` (or `ChatService` for non-stream complete if added later); no direct `DefaultAgent` bypass from voice core.
- **Pre-handoff RAG boundary** — RAG remains in `UnifiedChatService` / `RAGService` before agent handoff; voice does not bypass or relocate RAG.
- **MCP boundary** — MCP tools flow through `ToolRegistry` → `ToolExecutor`; voice does not embed MCP transport or discovery.
- **Text interchange** — Agent/planner/executor operate on text; audio ↔ text conversion stays in `app/ai/voice/` adapters/pipelines only.
- **Extend, don't fork** — New `app/ai/voice/` package; reuse `StreamPublisher`/SSE mapping concepts via internal bridge; do not duplicate chat event models blindly.
- **Flag-off parity** — `VOICE_ENABLED=false` leaves text chat, MCP, agent, RAG paths untouched.
- **Transport abstraction** — WS codec in `streaming.py`; core pipelines transport-agnostic.
- **Provider isolation** — OpenAI SDK imports only in `app/ai/voice/providers/openai_voice.py`.
- **Authenticated-only** — Voice sessions require authenticated caller; guests denied (consistent with tool auth posture).
- **Content-safe logs** — No raw audio payloads or full transcripts in structured logs by default (ids, durations, event types, error codes only).
- **Transcript parity** — Voice mode updates `MessageList` via the same `chatReducer` actions as SSE (`useChatStream`); no forked transcript state or duplicate message list.
- **Voice mode is additive** — Voice mode OFF uses existing SSE path unchanged; voice UI hidden when `voice_enabled=false`.
- **Single active session** — One voice session per user in v1.
- **No Epic 05+ behaviour early** — Memory, workflows, OTel voice spans, plugins, HITL, background jobs, RBAC — `TODO(epic-N):` only.
- **Public APIs stable after Phase 1** — Changes to frozen Protocols/models require user approval.

---

# Part II — Execution

## Reuse existing components

**DO NOT REIMPLEMENT:**

| Component                                                       | Location                                                                            |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `UnifiedChatService`, `stream_execute()`                        | `app/services/unified_chat_service.py`                                              |
| `ChatService`, `format_sse()`, `normalize_chat_error()`         | `app/services/chat_service.py`                                                      |
| `stream_agent_chat()`, `ChatAgentAdapter`                       | `app/ai/agent/adapters/chat_stream_adapter.py`, `chat_adapter.py`                   |
| `StreamPublisher`, `sse_frame_from_agent_event()`               | `app/ai/agent/streaming/`, `app/ai/agent/interfaces/streaming.py`                   |
| SSE frame models (`StartFrame`, `DeltaFrame`, `EndFrame`, etc.) | `app/schemas/chat.py`                                                               |
| `DefaultAgent`, agent runtime                                   | `app/ai/agent/runtime/default_agent.py`                                             |
| `LLMProvider`, `ProviderFactory`, `get_capabilities`            | `app/providers/`                                                                    |
| `supports_audio` capability scaffold                            | `app/providers/capabilities.py`                                                     |
| `CallerContext`, auth patterns                                  | `app/core/caller.py`, `app/routers/auth.py`                                         |
| Chat session CRUD, ownership                                    | `app/routers/chat.py`, `app/db/chat.py`                                             |
| `retry_async`, `is_retryable_exception`                         | `app/core/retry.py`                                                                 |
| `Settings`, config validation                                   | `app/core/config.py`                                                                |
| DI factories                                                    | `app/ai/deps.py`                                                                    |
| MCP tool path (unchanged)                                       | `app/ai/mcp/`, `app/ai/tools/`                                                      |
| RAG pipeline (unchanged)                                        | `app/ai/rag/`, `app/services/unified_chat_service.py`                               |
| `FakeProvider`, test fakes                                      | `tests/fakes.py`                                                                    |
| Existing chat stream tests (regression)                         | `tests/test_chat_stream.py`, `tests/test_unified_chat.py`                           |
| `useChatStream`, SSE parsing                                    | `frontend/src/hooks/useChatStream.ts`, `frontend/src/api/sseParser.ts`              |
| `chatReducer`, message streaming actions                        | `frontend/src/state/chatReducer.ts`                                                 |
| `ChatPage` send/stream orchestration                            | `frontend/src/pages/ChatPage.tsx`                                                   |
| `Composer` text input + tool toggles                            | `frontend/src/components/Composer.tsx`                                              |
| `MessageList`, `MessageBubble`                                  | `frontend/src/components/MessageList.tsx`, `MessageBubble.tsx`                      |
| Health flags hook                                               | `frontend/src/hooks/useChatStreamingEnabled.ts`, `frontend/src/api/healthClient.ts` |
| Chat types                                                      | `frontend/src/types/chat.ts`                                                        |

## Not allowed

- Refactor unrelated code beyond documented integration steps
- Rename packages or move `app/ai/agent/`, `app/ai/mcp/`, or `app/services/unified_chat_service.py`
- Add dependencies without user approval (especially Deepgram, ElevenLabs, pydub, new audio codecs)
- Change existing chat/MCP/agent API contracts (additive voice routes only)
- Implement Epic 05+ behaviour (Memory, Workflows, Observability spans, Plugins, HITL, Background Jobs, Security RBAC)
- Bypass `UnifiedChatService` for voice → agent path
- Bypass RAG pre-handoff for voice turns
- Embed MCP client/transport in voice modules
- Import OpenAI SDK outside `app/ai/voice/providers/`
- Log raw audio or full transcripts by default
- Change `AGENT_RUNTIME_ENABLED`, `ADVANCED_RAG_ENABLED`, `MCP_ENABLED` defaults or semantics
- Fork a separate message list or transcript store for voice mode
- Replace SSE chat path when voice mode is OFF
- Mount voice router when `VOICE_ENABLED=false`

## Baseline

_Copied from Epic 03 Phase 10 completion record._

| Area                                      | State                                                                                                                                             |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend tests / coverage                  |                                                                                                                                                   |
| MCP package coverage                      |                                                                                                                                                   |
| Eval CLI                                  |                                                                                                                                                   |
| Flag-off regression (`MCP_ENABLED=false`) |                                                                                                                                                   |
| Flag-on MCP parity                        |                                                                                                                                                   |
| Agent / RAG flag regressions              |                                                                                                                                                   |
| Voice integration                         | None (`VOICE_ENABLED` absent; no `app/ai/voice/`; no frontend voice UI)                                                                           |
| Frontend voice UI                         | None (`Composer` text-only; no mic/voice mode)                                                                                                    |
| Orchestration                             | RAG pre-handoff in `UnifiedChatService`; agent optional; MCP tools via `ToolRegistry` → `ToolExecutor`; text chat SSE via `POST /api/chat/stream` |

## Phase status

| Phase | Name                              | Effort | Status    |
| ----- | --------------------------------- | ------ | --------- |
| 0     | Baseline Audit                    | XS     | Completed |
| 1     | Scaffold, Models, Interfaces      | M      | Completed |
| 2     | STT Provider & Pipeline           | M      | Completed |
| 3     | TTS Provider & Pipeline           | M      | Completed |
| 4     | Streaming & WebSocket Framing     | M      | Completed |
| 5     | Voice Session Management          | M      | Completed |
| 6     | Interrupt / Barge-in              | S      | Completed |
| 7     | Voice Router & WebSocket Endpoint | M      | Completed |
| 8     | Backend Integration & DI Wiring   | M      | Completed |
| 9     | Frontend Voice Client             | M      | Completed |
| 10    | Frontend Voice Integration        | L      | Completed |
| 11    | Validation & Release              | S      | Completed |

---

## Phase 0 — Baseline Audit

**Effort:** XS

**Deliverables:** `docs/audits/post-mvp-v2-epic4-phase-0-baseline-audit.md`

**Steps:**

- [x] Confirm Epic 03 Phase 10 complete / authorized for Epic 04
- [x] Run backend gates: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
- [x] Run frontend gates: `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build`
- [x] Inventory backend paths: `app/providers/capabilities.py`, `app/ai/agent/streaming/`, `app/ai/agent/adapters/chat_stream_adapter.py`, `app/services/unified_chat_service.py`, `app/services/chat_service.py`, `app/routers/chat.py`, `app/core/config.py`, `app/ai/mcp/`
- [x] Inventory frontend paths: `frontend/src/pages/ChatPage.tsx`, `frontend/src/hooks/useChatStream.ts`, `frontend/src/state/chatReducer.ts`, `frontend/src/components/Composer.tsx`, `frontend/src/api/healthClient.ts` (confirm no voice UI yet)
- [x] Record current behaviour (text SSE only; no voice modules; MCP/agent/RAG flags present)
- [x] Confirm `openai` package version supports Whisper + TTS APIs; note whether `websockets` is transitive via `uvicorn[standard]`
- [x] Write audit doc; record metrics below
- [x] Phase 0 complete — user confirmed

**Verify:** `make lint && make typecheck && make test-cov && make eval` · `npm run lint && npm test -- --run && npm run build`

**Acceptance:**

- All quality gates pass; no repository code changes
- Inventory documents real module paths only (no `app/ai/voice/` or frontend voice modules yet)

**Exit criteria:**

- Audit published; baseline recorded; user confirmed Phase 0

**Completion record:**

| Metric                   | Result |
| ------------------------ | ------ |
| Backend tests / coverage |        |
| Eval CLI                 |        |
| Git commit               |        |
| Audit doc                |        |

---

## Phase 1 — Scaffold, Models, Interfaces

**Effort:** M

**Deliverables:** Voice models/Protocols; `VOICE_ENABLED=false`; package layout stubs; public API exports

**Steps:**

- [x] Add `VOICE_ENABLED` and voice settings to `app/core/config.py` + `backend-python/.env.example` (default **false**)
- [x] Create `app/ai/voice/` package tree per Part I Architecture (`__init__.py`, `interfaces.py`, `config.py`, `exceptions.py`, `providers/__init__.py`)
- [x] Add Protocols: `SttProvider` (`transcribe_stream`), `TtsProvider` (`synthesize_stream`), `VoiceSession` in `interfaces.py`
- [x] Add models: `VoiceConfig`, `TranscriptEvent`, `AudioOutEvent`, `VoiceWsMessage` in `config.py` / new `app/schemas/voice.py`
- [x] Add exceptions: `VoiceSessionError`, `SttError`, `TtsError`, `VoiceAuthError` in `exceptions.py`
- [x] Export public API from `app/ai/voice/__init__.py` per Part I § Public APIs
- [x] Add stub modules: `stt.py`, `tts.py`, `session.py`, `streaming.py`, `interrupt.py` (raise `NotImplementedError` or pass-through stubs)
- [x] Add `tests/ai/voice/test_models.py`, `test_interfaces.py`
- [x] Phase 1 complete — user confirmed

**Verify:** `make typecheck && pytest tests/ai/voice/test_models.py tests/ai/voice/test_interfaces.py`

**Acceptance:**

- Imports clean; flag default false; chat/MCP hot paths untouched
- Public APIs match Part I freeze list

**Exit criteria:**

- Tests pass; public API finalized; user confirmed Phase 1

---

## Phase 2 — STT Provider & Pipeline

**Effort:** M

**Deliverables:** `app/ai/voice/providers/openai_voice.py` (STT half); `app/ai/voice/stt.py`

**Steps:**

- [x] Implement `OpenAiVoiceAdapter.transcribe_stream()` using OpenAI Whisper API (existing `openai` dep; **user approval** if API surface requires dep bump)
- [x] Implement `SttPipeline`: accept audio chunks, buffer, invoke STT, emit `TranscriptEvent` (partial/final)
- [x] Apply STT rules from Part I (min audio, max utterance, retry classification)
- [x] Add fake STT provider in tests (no live API)
- [x] Add `tests/ai/voice/test_stt_pipeline.py`, `test_openai_voice_stt.py` (mock OpenAI client)
- [x] Phase 2 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_stt_pipeline.py tests/ai/voice/test_openai_voice_stt.py`

**Acceptance:**

- Fake provider tests pass; OpenAI adapter mocked
- Pipeline emits partial + final transcripts
- No imports from voice pipelines into `app/ai/agent/` core

**Exit criteria:**

- Tests pass; user confirmed Phase 2

---

## Phase 3 — TTS Provider & Pipeline

**Effort:** M

**Deliverables:** `OpenAiVoiceAdapter` (TTS half); `app/ai/voice/tts.py`

**Steps:**

- [x] Implement `OpenAiVoiceAdapter.synthesize_stream()` using OpenAI TTS API
- [x] Implement `TtsPipeline`: accept text chunks/deltas, buffer to sentence boundaries, emit `AudioOutEvent`
- [x] Output PCM16 24 kHz mono per Part I (convert in adapter if API returns other format)
- [x] Apply TTS rules from Part I (max chunk chars, retry, cancel hook placeholder for Phase 6)
- [x] Add fake TTS provider in tests
- [x] Add `tests/ai/voice/test_tts_pipeline.py`, `test_openai_voice_tts.py`
- [x] Phase 3 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_tts_pipeline.py tests/ai/voice/test_openai_voice_tts.py`

**Acceptance:**

- Streaming audio chunks emitted for incremental text input
- Cancel hook present (wired in Phase 6)

**Exit criteria:**

- Tests pass; user confirmed Phase 3

---

## Phase 4 — Streaming & WebSocket Framing

**Effort:** M

**Deliverables:** `app/ai/voice/streaming.py`; `app/schemas/voice.py` finalized

**Steps:**

- [x] Implement `VoiceStreamBridge`: encode/decode JSON WS messages per Part I protocol table
- [x] Base64 codec for `payload_b64`; enforce `voice_max_chunk_bytes`
- [x] Validate inbound message types and schema (pydantic models)
- [x] Heartbeat message handling (receive only; session timeout deferred to Phase 5)
- [x] Add `tests/ai/voice/test_streaming.py` (round-trip codec, chunk limits, invalid message rejection)
- [x] Phase 4 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_streaming.py`

**Acceptance:**

- All protocol message types serialize/deserialize correctly
- Oversized chunks rejected with structured error

**Exit criteria:**

- Tests pass; user confirmed Phase 4

---

## Phase 5 — Voice Session Management

**Effort:** M

**Deliverables:** `app/ai/voice/session.py` (`VoiceSessionManager`)

**Steps:**

- [x] Implement `VoiceSessionManager`: create, attach to `session_id`, lookup by `voice_session_id`
- [x] Enforce authenticated-only (reject guest via `CallerContext`)
- [x] Enforce chat session ownership (reuse patterns from `ChatService` / session store)
- [x] Enforce one active voice session per user
- [x] Heartbeat tracking + idle timeout (`voice_session_timeout_seconds`)
- [x] Idempotent teardown; resource cleanup hooks for STT/TTS tasks
- [x] Add `tests/ai/voice/test_session.py`
- [x] Phase 5 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_session.py`

**Acceptance:**

- Guest denied; duplicate session rejected; timeout closes session
- Session attach requires valid chat `session_id`

**Exit criteria:**

- Tests pass; user confirmed Phase 5

---

## Phase 6 — Interrupt / Barge-in

**Effort:** S

**Deliverables:** `app/ai/voice/interrupt.py` (`InterruptController`)

**Steps:**

- [x] Implement `InterruptController.cancel_all(session)`: cancel TTS task + upstream stream task
- [x] Wire cancel hooks into `SttPipeline`, `TtsPipeline`, and stream bridge from Phase 4
- [x] Treat inbound `audio_in` during active TTS/LLM as barge-in trigger
- [x] Emit `interrupted` WS message; session remains open
- [x] Document non-persistence of partial assistant message on interrupt
- [x] Add `tests/ai/voice/test_interrupt.py` (race: interrupt during TTS and during mock LLM stream)
- [x] Phase 6 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_interrupt.py`

**Acceptance:**

- Both TTS and LLM streams cancelled; no leaked tasks
- Session usable after interrupt

**Exit criteria:**

- Tests pass; user confirmed Phase 6

---

## Phase 7 — Voice Router & WebSocket Endpoint

**Effort:** M

**Deliverables:** `app/routers/voice.py`; register in `app/main.py` when flag on

**Steps:**

- [x] Implement `WebSocket /api/voice/ws` with query param `session_id`
- [x] Gate route: only mount/include when `VOICE_ENABLED=true` (or handler checks flag → close with error)
- [x] Wire handshake → `VoiceSessionManager` → STT/TTS pipelines → `VoiceStreamBridge`
- [x] On `transcript_final`: invoke chat turn (stub/mock in this phase; full `UnifiedChatService` in Phase 8)
- [x] Expose `voice_enabled` in `app/routers/health.py` when settings extended
- [x] Update `supports_audio` for `openai` in `capabilities.py` when voice enabled (document flip rules)
- [x] Add `tests/test_voice_router.py` (TestClient WebSocket with fakes; flag-off → 404 or policy close)
- [x] Phase 7 complete — user confirmed

**Verify:** `pytest tests/test_voice_router.py tests/ai/voice/`

**Acceptance:**

- Flag off: voice endpoint unavailable; text chat unchanged
- Flag on: WS handshake + echo/stub turn works with fakes

**Exit criteria:**

- Tests pass; user confirmed Phase 7

---

## Phase 8 — Backend Integration & DI Wiring

**Effort:** M

**Deliverables:** DI in `app/ai/deps.py`; `UnifiedChatService` bridge; parity tests

**Steps:**

- [x] Add `get_voice_session_manager()`, `get_stt_provider()`, `get_tts_provider()` to `app/ai/deps.py`
- [x] Wire `transcript_final` → build `ChatRequestSchema` → `UnifiedChatService.stream_execute()`
- [x] Consume SSE frames internally (`start`, `delta`, `tool_*`, `end`, `retrieval_complete`) — map `delta` → TtsPipeline + WS `assistant_text_delta`; forward tool/retrieval/end metadata on WS for frontend `END_MESSAGE` parity
- [x] Pass `CallerContext`, session, provider settings through voice → chat boundary
- [x] Preserve RAG (`use_documents`), agent (`AGENT_RUNTIME_ENABLED`), MCP tool path when respective flags on
- [x] Register voice router in `app/main.py` lifespan/router include (flag-guarded)
- [x] Update `README.md` + `backend-python/.env.example` with voice WS protocol summary
- [x] Add `tests/ai/voice/test_integration.py` (fake STT/TTS + fake chat stream; end-to-end turn)
- [x] Phase 8 complete — user confirmed

**Verify:** `pytest tests/ai/voice/test_integration.py tests/test_voice_router.py tests/test_unified_chat.py tests/test_chat_stream.py`

**Acceptance:**

- Flag off: text chat + MCP + agent + RAG unchanged
- Flag on: voice turn produces audio out from assistant text via real `UnifiedChatService` wiring (mocked provider APIs)

**Exit criteria:**

- Parity tests pass; user confirmed Phase 8

**Rollback:**

- Set `VOICE_ENABLED=false`; remove voice router registration and DI branches
- Re-run: `pytest tests/test_chat_stream.py tests/test_unified_chat.py tests/ai/mcp/test_integration.py`
- Revert PR if needed

---

## Phase 9 — Frontend Voice Client

**Effort:** M

**Deliverables:** `frontend/src/api/voiceClient.ts`, `frontend/src/types/voice.ts`, `frontend/src/hooks/useVoiceSession.ts`

**Steps:**

- [x] Extend `HealthResponse` + `useChatStreamingEnabled` with `voice_enabled` from `GET /api/health`
- [x] Add `VoiceWsMessage` types mirroring Part I protocol table
- [x] Implement `voiceClient.ts`: WebSocket connect to `/api/voice/ws?session_id=…` with auth headers/cookies; JSON encode/decode; heartbeat send
- [x] Implement `useVoiceSession`: connect/disconnect lifecycle; send `audio_in` / `interrupt` / `session_end`; parse inbound events; expose callbacks matching `useChatStream` shape where possible (`onDelta`, `onEnd`, `onError`, etc.)
- [x] Add PCM16 playback helper (Web Audio API) for `audio_out` chunks
- [x] Add mic capture helper (`getUserMedia` + chunking to `voice_max_chunk_bytes`) — permission-denied handling
- [x] Add `tests`: `frontend/src/hooks/useVoiceSession.test.ts`, `frontend/src/api/voiceClient.test.ts` (mock WebSocket + AudioContext)
- [x] Phase 9 complete — user confirmed

**Verify:** `npm test -- --run frontend/src/hooks/useVoiceSession.test.ts frontend/src/api/voiceClient.test.ts`

**Acceptance:**

- Hook connects, sends/receives mocked WS frames; audio codec round-trips in tests
- No `ChatPage` wiring yet; no change to SSE path

**Exit criteria:**

- Frontend unit tests pass; user confirmed Phase 9

---

## Phase 10 — Frontend Voice Integration

**Effort:** L

**Deliverables:** `VoiceModeControls.tsx`; updates to `Composer.tsx`, `ChatPage.tsx`

**Steps:**

- [x] Add `VoiceModeControls` — voice mode toggle (shown when `voice_enabled && isAuthenticated`); mic press/hold; recording indicator; interrupt/stop button during assistant playback
- [x] Extend `Composer` to render voice controls alongside text input; voice mode ON disables text send (mic drives turns) unless user toggles off
- [x] Wire `ChatPage`:
  - When voice mode ON: open `useVoiceSession` for `activeSessionId`; map WS text events → `chatReducer` per Part I mapping table (reuse same handlers as `useChatStream` callbacks)
  - When voice mode OFF: existing `useChatStream` / `useChatCompletion` path unchanged
  - `transcript_partial` → live caption in Composer or ephemeral user bubble
  - `transcript_final` → `ADD_USER_MESSAGE`; start assistant stream on `START_MESSAGE` equivalent
  - `assistant_text_delta` → `APPEND_DELTA`; play `audio_out` in parallel
  - Barge-in → send WS `interrupt`; dispatch `STOP_MESSAGE` / `INTERRUPT_MESSAGE`
- [x] Preserve session persistence, tool toggles (`useWebSearch`, `useDocuments`), provider selection for voice turns (pass through WS/backend chat request build)
- [x] Guest users: hide voice mode toggle; text-only unchanged
- [x] Add tests: `VoiceModeControls.test.tsx`, `ChatPage.voice.test.tsx` (mock `useVoiceSession`, assert reducer dispatches match SSE path)
- [x] Phase 10 complete — user confirmed

**Verify:** `npm test -- --run frontend/src/components/VoiceModeControls.test.tsx frontend/src/pages/ChatPage.voice.test.tsx && npm run build`

**Acceptance:**

- Voice mode ON: user mic input streams user transcript + assistant transcript in `MessageList` same as text mode
- Voice mode OFF: identical behaviour to pre-epic chat
- Assistant audio plays while text deltas render
- Flag off (`voice_enabled=false`): no voice toggle visible

**Exit criteria:**

- Frontend tests pass; manual smoke documented in audit/release notes; user confirmed Phase 10

**Rollback:**

- Hide voice toggle via `voice_enabled=false`; `ChatPage` voice branch inert; SSE path unaffected

---

## Phase 11 — Validation & Release

**Effort:** S

**Steps:**

- [x] Full backend suite: `VOICE_ENABLED=false` then `true` (fake STT/TTS; no live OpenAI voice API in CI)
- [x] Full frontend suite: `npm test -- --run`, `npm run build` with voice tests included
- [x] Manual E2E smoke (document in release summary): voice mode ON → speak → see streaming transcripts + hear reply; voice mode OFF → text SSE unchanged
- [x] Confirm `AGENT_RUNTIME_ENABLED`, `ADVANCED_RAG_ENABLED`, `MCP_ENABLED` flag-off/on still green (no regressions from Epic 01/02/03)
- [x] Docker smoke (optional: document WS endpoint in compose README)
- [x] `make eval`
- [x] Write `docs/releases/post-mvp-v2-epic4-release-summary.md`
- [x] Set Phase status rows to **Completed**; tick DoD
- [ ] Phase 11 complete — user confirmed; Epic 05 authorized

**Verify:** `make test-cov && make eval` · `npm test -- --run && npm run build`

**Acceptance:**

- Part I design acceptance met; coverage ≥80% on `app/` and `app/ai/voice/`
- Flag-off parity pass (backend + frontend: no voice UI, SSE unchanged)
- Voice mode ON: end-to-end verbal input + verbal reply + transcript streaming verified

**Exit criteria:**

- Release summary published; user confirmed Phase 11; next epic authorized

**Completion record:**

| Metric                                      | Result                                    |
| ------------------------------------------- | ----------------------------------------- |
| Backend tests / coverage                    | 1076 passed, 89.52% `app/`                |
| Voice package coverage                      | 93% `app/ai/voice/`                       |
| Frontend tests                              | 219 Vitest; 31 voice-specific; build pass |
| Eval CLI                                    | 5/5 passed                                |
| Flag-off regression (`VOICE_ENABLED=false`) | 1076 passed                               |
| Flag-on voice parity (backend + frontend)   | 147 voice tests; 31 frontend voice tests  |
| Agent / RAG / MCP flag regressions          | 34 passed                                 |

---

## Files index

| Path                                                      | Action | Owner    | Phase |
| --------------------------------------------------------- | ------ | -------- | ----- |
| `docs/audits/post-mvp-v2-epic4-phase-0-baseline-audit.md` | create | Docs     | 0     |
| `app/core/config.py`                                      | modify | Core     | 1, 7  |
| `backend-python/.env.example`                             | modify | Docs     | 1, 8  |
| `app/ai/voice/__init__.py`                                | create | Core     | 1     |
| `app/ai/voice/interfaces.py`                              | create | Core     | 1     |
| `app/ai/voice/config.py`                                  | create | Core     | 1     |
| `app/ai/voice/exceptions.py`                              | create | Core     | 1     |
| `app/ai/voice/stt.py`                                     | create | Core     | 2     |
| `app/ai/voice/tts.py`                                     | create | Core     | 3     |
| `app/ai/voice/streaming.py`                               | create | Core     | 4     |
| `app/ai/voice/session.py`                                 | create | Core     | 5     |
| `app/ai/voice/interrupt.py`                               | create | Core     | 6     |
| `app/ai/voice/providers/openai_voice.py`                  | create | Adapter  | 2, 3  |
| `app/schemas/voice.py`                                    | create | Core     | 1, 4  |
| `app/routers/voice.py`                                    | create | Adapter  | 7, 8  |
| `app/routers/health.py`                                   | modify | Adapter  | 7     |
| `app/providers/capabilities.py`                           | modify | Adapter  | 7     |
| `app/ai/deps.py`                                          | modify | Adapter  | 8     |
| `app/main.py`                                             | modify | Adapter  | 7, 8  |
| `tests/ai/voice/**`                                       | create | Tests    | 1–8   |
| `tests/test_voice_router.py`                              | create | Tests    | 7, 8  |
| `frontend/src/api/voiceClient.ts`                         | create | Frontend | 9     |
| `frontend/src/types/voice.ts`                             | create | Frontend | 9     |
| `frontend/src/hooks/useVoiceSession.ts`                   | create | Frontend | 9     |
| `frontend/src/hooks/useChatStreamingEnabled.ts`           | modify | Frontend | 9     |
| `frontend/src/api/healthClient.ts`                        | modify | Frontend | 9     |
| `frontend/src/components/VoiceModeControls.tsx`           | create | Frontend | 10    |
| `frontend/src/components/Composer.tsx`                    | modify | Frontend | 10    |
| `frontend/src/pages/ChatPage.tsx`                         | modify | Frontend | 10    |
| `frontend/src/hooks/useVoiceSession.test.ts`              | create | Tests    | 9     |
| `frontend/src/components/VoiceModeControls.test.tsx`      | create | Tests    | 10    |
| `frontend/src/pages/ChatPage.voice.test.tsx`              | create | Tests    | 10    |
| `backend-python/README.md`, root `README.md`              | modify | Docs     | 8, 11 |
| `docs/releases/post-mvp-v2-epic4-release-summary.md`      | create | Docs     | 11    |

## PR map

One PR per phase; branch `v2/epic-04/phase-{pp}-{slug}`.

## Risks

| Risk                                   | Mitigation                                                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Breaks text chat SSE path              | Flag default off; Phase 8 rollback; flag-off regression tests                                               |
| Latency (STT + LLM + TTS serial)       | Stream partial transcripts; stream TTS per sentence; document soft targets in release summary               |
| Session leaks / orphaned WS            | Idle timeout; idempotent teardown; test disconnect + timeout in `test_session.py`                           |
| Interrupt races                        | Single `InterruptController` per session; asyncio.Task tracking; Phase 6 race tests                         |
| Provider quota / rate limits           | Retry classification; graceful `error` WS frame; no retry on auth failures                                  |
| OpenAI SDK coupling                    | Protocol boundary; SDK only in `providers/openai_voice.py`                                                  |
| Audio logging privacy                  | Content-safe log invariant; base64 never logged                                                             |
| Scope creep into Epic 05–11            | Explicit Out list; `TODO(epic-N):` markers                                                                  |
| UnifiedChatService coupling complexity | Thin bridge in `streaming.py`; no changes to RAG/agent internals                                            |
| Guest access to voice                  | Authenticated-only invariant; test guest denial in router + session tests; hide voice toggle in UI          |
| Browser mic permission denied          | Graceful error in `VoiceModeControls`; fallback message; do not block text mode                             |
| Transcript/audio desync                | Text driven by WS deltas (canonical); audio playback best-effort; test reducer updates independent of audio |
| Frontend SSE regression                | Voice mode OFF must use unchanged `useChatStream` path; dedicated `ChatPage.voice.test.tsx` + flag-off test |

## Observability

Structured log fields (no raw audio/transcripts by default):

| Field                         | Purpose                      |
| ----------------------------- | ---------------------------- |
| `voice_enabled`               | Flag state                   |
| `voice_session_id`            | Voice session identifier     |
| `chat_session_id`             | Attached chat session        |
| `voice_event_type`            | WS message type              |
| `stt_latency_ms`              | STT round-trip               |
| `tts_latency_ms`              | TTS first-chunk latency      |
| `turn_latency_ms`             | End-to-end turn              |
| `voice_interrupted`           | Barge-in occurred            |
| `voice_session_closed_reason` | timeout / client_end / error |
| `voice_error_code`            | Structured error             |
| `audio_chunk_bytes`           | Chunk size (not payload)     |

## Definition of done

- [x] Part I components delivered; Part I design acceptance met
- [x] Public APIs stable per Phase 1
- [x] Voice path behind `VOICE_ENABLED`; text chat unchanged when off; parity when on
- [x] End-to-end voice mode UI: verbal input, verbal reply, transcript streaming in `MessageList` (parity with SSE)
- [x] Voice feeds/consumes text via `UnifiedChatService`; RAG/MCP/agent boundaries preserved
- [x] `tests/ai/voice/` complete; coverage ≥80% on `app/ai/voice/` and `app/`
- [x] Frontend voice tests pass; `npm run build` succeeds
- [x] `make eval` passes; release summary published
- [x] All phases **Completed**; user confirmed each (Phases 0–10 confirmed; Phase 11 awaiting user confirm)
- [x] Program DoD: [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md)
- [ ] User authorizes Epic 05

## Changelog

| Date       | Change                                                                                                                                                                                                                                                     |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-07-28 | Initial plan                                                                                                                                                                                                                                               |
| 2026-07-28 | v2 — Add end-to-end frontend voice mode; transcript parity with SSE; Phases 9–10                                                                                                                                                                           |
| 2026-07-29 | Phase 11 validation complete: flag-off/on test matrices, eval, frontend gates, voice package 93% coverage, release summary published, Docker WS documented. Phase 11 status → Completed (pending user confirmation / Epic 05 authorization). Part II only. |
