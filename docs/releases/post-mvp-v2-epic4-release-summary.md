# Post-MVP V2 Epic 04 Release Summary

**Release name:** Post-MVP V2 Epic 04 — Voice Interfaces
**Release date:** 2026-07-29
**Validation:** Phase 11 final acceptance (see [post-mvp-v2-epic-04-voice-interfaces.md](../plans/post-mvp-v2-epic-04-voice-interfaces.md))
**Git commit (validation base):** `d6de621` — Epic 04 Phase 10 frontend integration

---

## Summary vs Epic 03

Epic 03 shipped MCP tool integration behind `MCP_ENABLED`. **V2 Epic 04 adds bidirectional voice** under `app/ai/voice/` (STT/TTS Protocols, OpenAI Whisper + TTS adapter, WebSocket transport, session manager, barge-in, end-to-end frontend voice mode) behind `VOICE_ENABLED` (default **off**).

| Area | Epic 03 / text chat | V2 Epic 04 |
| ---- | ------------------- | ---------- |
| User input | Text Composer → SSE | Voice mode: mic → WS `audio_in` → STT → same chat pipeline |
| Assistant output | SSE text deltas | Voice mode: WS `assistant_text_delta` + `audio_out` (PCM16 24 kHz mono) |
| Agent / RAG / MCP path | `UnifiedChatService.stream_execute()` | Unchanged — voice feeds transcript text into same orchestrator |
| Transport | `POST /api/chat/stream` (SSE) | Additive `GET /api/voice/ws?session_id=…` when flag on |
| Frontend transcript | `chatReducer` via `useChatStream` | Voice mode reuses same reducer actions (transcript parity) |
| Guest access | Text chat | Voice denied (authenticated-only; toggle hidden) |

---

## Delivered (Phases 0–11)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Models/Protocols, `VOICE_ENABLED`, package scaffold |
| 2 | `OpenAiVoiceAdapter` STT + `SttPipeline` |
| 3 | `OpenAiVoiceAdapter` TTS + `TtsPipeline` |
| 4 | `VoiceStreamBridge` WS JSON codec |
| 5 | `VoiceSessionManager` (auth, ownership, heartbeat, timeout) |
| 6 | `InterruptController` barge-in |
| 7 | `VoiceRouter` WebSocket endpoint + health `voice_enabled` |
| 8 | DI wiring + `UnifiedChatService` bridge |
| 9 | `voiceClient.ts`, `useVoiceSession` hook |
| 10 | `VoiceModeControls`, `Composer` / `ChatPage` integration |
| 11 | Validation gates + release summary |

**Stable public APIs** (Phase 1 freeze): `SttProvider`, `TtsProvider`, `VoiceSession`; `VoiceSessionManager`, `VoiceStreamBridge`, `InterruptController`; `VoiceConfig`, `TranscriptEvent`, `AudioOutEvent`, `VoiceWsMessage`; voice exceptions; flag-guarded router.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `VOICE_ENABLED` | `false` | Off: no voice router mounted; no voice UI; text SSE unchanged. On: authenticated users can enable voice mode → WS at `/api/voice/ws?session_id={chat_session_id}`. |

Requires `OPENAI_API_KEY` for live STT/TTS when using the default OpenAI adapter. CI uses fakes — no live OpenAI voice API calls.

**Rollback:** set `VOICE_ENABLED=false`; voice router and UI affordances inert; SSE path unaffected.

---

## Breaking Changes

**None.** Voice is additive behind a master flag. Chat HTTP/SSE contracts unchanged.

---

## Migration / Upgrade Notes

1. Pull release; ensure `backend-python/.env.example` includes `VOICE_*` settings (`VOICE_ENABLED=false` by default).
2. To exercise locally: set `VOICE_ENABLED=true`, ensure `OPENAI_API_KEY` is set, sign in (guests cannot use voice), open a chat session, toggle voice mode in Composer, grant mic permission, speak and hold/release mic for utterance end.
3. WebSocket auth: Bearer JWT (cookie or `access_token` query param per router tests).
4. Audio format: PCM16, 24 kHz, mono; max 4096 bytes raw per inbound chunk (base64 in JSON).
5. Docker Compose: Python backend exposes the same `/api/voice/ws` when `VOICE_ENABLED=true` in compose env; frontend must proxy WS to the backend origin (see [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)).

---

## Manual E2E Smoke (documented procedure)

Run with `VOICE_ENABLED=true`, backend on `:8000`, frontend dev server, authenticated user:

| Step | Expected |
| ---- | -------- |
| 1. Health | `GET /api/health` returns `voice_enabled: true` |
| 2. Voice toggle | Visible in Composer when signed in; hidden for guests and when flag off |
| 3. Voice mode ON | WS connects; `session_started` received |
| 4. Speak (mic) | `transcript_partial` live caption; `transcript_final` → user bubble in `MessageList` |
| 5. Assistant reply | `assistant_text_delta` streams in message bubble; `audio_out` plays via Web Audio |
| 6. Barge-in | Interrupt during playback cancels TTS/LLM; `interrupted` reflected in UI |
| 7. Voice mode OFF | Toggle off; text send via SSE unchanged from pre-epic behaviour |

Automated CI covers WS/router/hook/reducer paths with fakes; live mic/OpenAI smoke is manual.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Guest voice | Out of scope (authenticated-only) |
| WebRTC / Realtime API duplex | Future |
| Waveform UX, per-message voice picker | Out of scope |
| Voice OTel spans, eval harness | Epic 07 |
| Voice RBAC, quotas, audit | Epic 11 Security |
| Cross-session voice memory | Epic 05 |
| Workflow voice nodes | Epic 06 |
| Spoken-action approval (HITL) | Epic 09 |
| Async offline transcription | Epic 10 |

---

## Verification Metrics (Phase 11 — 2026-07-29)

| Gate | Result |
| ---- | ------ |
| Flag-off `VOICE_ENABLED=false make test-cov` | **1076 passed**, **89.52%** coverage on `app/` |
| Flag-on voice suite `tests/ai/voice` + `tests/test_voice_router.py` | **147 passed** |
| Voice package `app/ai/voice/` | **93%** (gate ≥80%) |
| Agent / RAG / MCP regression (`test_chat_stream`, `test_unified_chat`, `test_integration`) | **34 passed** |
| `make eval` | **5/5** passed (`.eval/eval-report.json`, timestamp `2026-07-28T19:37:53Z`) |
| Frontend | **219** Vitest / build — all pass |
| Voice frontend tests (4 files) | **31 passed** |
| Docker Compose smoke | Not run in this validation pass; WS endpoint documented above |

**CI note:** All voice tests use fake STT/TTS providers and mocked OpenAI clients — no live Whisper/TTS API in CI.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-04-voice-interfaces.md](../plans/post-mvp-v2-epic-04-voice-interfaces.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic4-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic4-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic3-release-summary.md](./post-mvp-v2-epic3-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
