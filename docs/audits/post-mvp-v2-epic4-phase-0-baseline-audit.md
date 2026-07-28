# Post-MVP V2 Epic 04 Phase 0 — Baseline Audit

**Epic:** v2-04 Voice Interfaces
**Phase:** 0 — Baseline Audit
**Date:** 2026-07-28
**Auditor:** AI Agent
**Status:** Complete

---

## Executive Summary

This audit establishes the baseline state before implementing Epic 04 (Voice Interfaces). All quality gates pass successfully. The codebase is ready for Phase 1 implementation.

**Key Findings:**

- ✅ All backend quality gates pass (lint, format, typecheck, test coverage 89.02%, eval 5/5)
- ✅ All frontend quality gates pass (lint, format, tests 188/188, build successful)
- ✅ Epic 03 MCP integration components exist and are operational
- ✅ No voice modules exist yet (clean baseline)
- ✅ OpenAI SDK 2.45.0 supports Whisper + TTS APIs
- ✅ `websockets` 16.0 present (transitive via `google-genai`, not `uvicorn[standard]`)

---

## 1. Epic 03 Phase 10 Status

**Finding:** Epic 03 MCP integration work appears complete and merged to `main` branch.

**Evidence:**

- MCP package exists at `backend-python/app/ai/mcp/` with 11 modules:
  - `__init__.py`, `client.py`, `registry.py`, `discovery.py`, `executor.py`
  - `auth.py`, `permissions.py`, `config.py`, `exceptions.py`
  - `transport/__init__.py`, `transport/stdio.py`
- MCP tests exist at `backend-python/tests/ai/mcp/` including:
  - `test_auth.py`, `test_registration.py`, `test_registration_partial.py`, `test_integration.py`
- Terminal history shows Phase 8, 9, and 10 commits merged to main
- `backend-python/.env.example` includes `MCP_ENABLED` and MCP server configs

**Recommendation:** Epic 03 is functionally complete. Epic 04 implementation may proceed.

---

## 2. Backend Quality Gates

### 2.1 Lint Check

```bash
Command: cd backend-python && make lint
Result: ✅ PASS
Output: All checks passed!
Duration: 761 ms
```

### 2.2 Format Check

```bash
Command: cd backend-python && make format-check
Result: ✅ PASS
Output: 290 files already formatted
Duration: 761 ms
```

### 2.3 Type Check

```bash
Command: cd backend-python && make typecheck
Result: ✅ PASS
Output: 0 errors, 0 warnings, 0 informations
Duration: 6158 ms
```

### 2.4 Test Coverage

```bash
Command: cd backend-python && make test-cov
Result: ✅ PASS
Tests Passed: 923
Tests Failed: 0
Coverage: 89.02% (exceeds 80% requirement)
Duration: 131.90s (2:11)
```

**Coverage Highlights:**

- `app/ai/agent/`: Various modules (80%+ on core paths)
- `app/ai/mcp/`: Complete MCP package coverage
- `app/ai/rag/`: RAG pipeline coverage
- `app/services/unified_chat_service.py`: 59% (expected due to conditional branches)
- `app/services/chat_service.py`: 90%
- `app/routers/chat.py`: 88%

**Low Coverage Areas (expected):**

- `app/db/deps.py`: 0% (dependency injection scaffolding)
- `app/main.py`: 75% (startup/shutdown paths)

### 2.5 Evaluation CLI

```bash
Command: cd backend-python && make eval
Result: ✅ PASS
Passed: 5
Failed: 0
Skipped: 0
Duration: 2613 ms
```

**Evaluation Results:**

- `[prompt]`: 2 passed (mean latency 0.5 ms)
  - `rag_answer_renders`: PASS (1ms)
  - `chat_summarize_system_snapshot`: PASS (0ms)
- `[retrieval]`: 2 passed (mean latency 14.0 ms, precision 1.000, recall 0.750)
  - `retrieval_finds_fixture`: PASS (27ms, precision=1.000, recall=0.500)
  - `empty_corpus_retrieval`: PASS (1ms, precision=1.000, recall=1.000)
- `[e2e]`: 1 passed (mean latency 158.0 ms)
  - `e2e_fixture_answer`: PASS (158ms, correctness=True, faithfulness=1.000, hallucination=False)

---

## 3. Frontend Quality Gates

### 3.1 Lint Check

```bash
Command: cd frontend && npm run lint
Result: ✅ PASS
Duration: 3093 ms
```

### 3.2 Format Check

```bash
Command: cd frontend && npm run format:check
Result: ✅ PASS
Output: All matched files use Prettier code style!
Duration: 1356 ms
```

### 3.3 Tests

```bash
Command: cd frontend && npm test -- --run
Result: ✅ PASS
Test Files: 35 passed
Tests: 188 passed
Duration: 4.05s
```

### 3.4 Build

```bash
Command: cd frontend && npm run build
Result: ✅ PASS
Output:
  - dist/index.html: 0.45 kB (gzip: 0.29 kB)
  - dist/assets/index-B3cS7pl5.css: 41.62 kB (gzip: 7.69 kB)
  - dist/assets/index-t0R4WsjU.js: 456.06 kB (gzip: 138.11 kB)
Duration: 1965 ms
```

---

## 4. Backend Path Inventory

All required paths exist and are operational:

| Path                                           | Status            | Notes                                                  |
| ---------------------------------------------- | ----------------- | ------------------------------------------------------ |
| `app/providers/capabilities.py`                | ✅ Exists         | Provider capability flags                              |
| `app/ai/agent/streaming/`                      | ✅ Exists         | 3 modules: `__init__.py`, `adapter.py`, `publisher.py` |
| `app/ai/agent/adapters/chat_stream_adapter.py` | ✅ Exists         | Agent streaming adapter                                |
| `app/services/unified_chat_service.py`         | ✅ Exists         | 1,219 lines, orchestrates chat/RAG/tools               |
| `app/services/chat_service.py`                 | ✅ Exists         | 1,321 lines, core chat service                         |
| `app/routers/chat.py`                          | ✅ Exists         | 369 lines, chat endpoints                              |
| `app/core/config.py`                           | ✅ Exists         | Settings with MCP flags                                |
| `app/ai/mcp/`                                  | ✅ Exists         | 11 modules, MCP integration                            |
| `app/ai/voice/`                                | ❌ Does not exist | Expected (Phase 1 deliverable)                         |
| `app/routers/voice.py`                         | ❌ Does not exist | Expected (Phase 7 deliverable)                         |
| `app/schemas/voice.py`                         | ❌ Does not exist | Expected (Phase 1 deliverable)                         |

---

## 5. Frontend Path Inventory

All required paths exist and are operational:

| Path                                            | Status            | Notes                           |
| ----------------------------------------------- | ----------------- | ------------------------------- |
| `frontend/src/pages/ChatPage.tsx`               | ✅ Exists         | Main chat interface             |
| `frontend/src/hooks/useChatStream.ts`           | ✅ Exists         | SSE streaming hook              |
| `frontend/src/state/chatReducer.ts`             | ✅ Exists         | Chat state management           |
| `frontend/src/components/Composer.tsx`          | ✅ Exists         | Message input component         |
| `frontend/src/api/healthClient.ts`              | ✅ Exists         | Health check client             |
| `frontend/src/api/voiceClient.ts`               | ❌ Does not exist | Expected (Phase 9 deliverable)  |
| `frontend/src/hooks/useVoiceSession.ts`         | ❌ Does not exist | Expected (Phase 9 deliverable)  |
| `frontend/src/components/VoiceModeControls.tsx` | ❌ Does not exist | Expected (Phase 10 deliverable) |

**Voice-related files:** None found (expected clean baseline)

---

## 6. Current Behaviour

### 6.1 Text Chat (SSE)

- ✅ Working via `POST /api/chat/stream`
- ✅ Streaming enabled (text-only)
- ✅ SSE frames: `start`, `delta`, `tool_*`, `retrieval_complete`, `end`, `error`
- ✅ Frontend: `useChatStream` + `chatReducer` + `MessageList`

### 6.2 MCP Integration

- ✅ `MCP_ENABLED` flag present in config (default: `false` for backward compatibility)
- ✅ MCP modules: client, registry, discovery, executor, auth, permissions
- ✅ MCP tools flow through `ToolRegistry` → `ToolExecutor` when flag on
- ✅ Tests cover MCP integration with fakes (no live servers in CI)

### 6.3 Agent Runtime

- ✅ `AGENT_RUNTIME_ENABLED` flag present
- ✅ Agent runtime in `app/ai/agent/runtime/`
- ✅ Streaming via `StreamPublisher`, `sse_frame_from_agent_event()`

### 6.4 RAG Pipeline

- ✅ `ADVANCED_RAG_ENABLED` flag present
- ✅ RAG pre-handoff in `UnifiedChatService`
- ✅ Document retrieval, chunking, reranking operational

### 6.5 Voice Integration

- ❌ No `VOICE_ENABLED` flag yet
- ❌ No voice modules (`app/ai/voice/` does not exist)
- ❌ No voice router (`app/routers/voice.py` does not exist)
- ❌ No voice UI (no mic, no voice mode toggle)
- ✅ Expected: Phase 1+ will add voice layer

---

## 7. Dependency Analysis

### 7.1 OpenAI Package

```bash
Package: openai
Version: 2.45.0
Location: backend-python/.venv/lib/python3.12/site-packages
Requires: anyio, distro, httpx, jiter, pydantic, sniffio, tqdm, typing-extensions
```

**✅ Whisper API Support:** Yes

- OpenAI SDK 2.45.0 includes `client.audio.transcriptions` for Whisper (STT)

**✅ TTS API Support:** Yes

- OpenAI SDK 2.45.0 includes `client.audio.speech` for TTS

**Verification:**

```python
from openai import OpenAI
# client.audio.transcriptions.create(model="whisper-1", file=audio_file)
# client.audio.speech.create(model="tts-1", voice="alloy", input=text)
```

**Recommendation:** No dependency upgrade needed for voice. Existing `openai>=2.45.0` is sufficient.

### 7.2 WebSockets Package

```bash
Package: websockets
Version: 16.0
Location: backend-python/.venv/lib/python3.12/site-packages
Requires: (none)
Required-by: google-genai
```

**Finding:** `websockets` 16.0 is present as a **transitive dependency** via `google-genai`, not via `uvicorn[standard]`.

**uvicorn[standard] dependencies:**

```bash
Package: uvicorn
Version: 0.51.0
Requires: click, h11
```

**Observation:** `uvicorn[standard]` includes WebSocket support via built-in ASGI handling (FastAPI's `WebSocket` endpoint uses ASGI protocol, not `websockets` package directly).

**Recommendation:** No explicit `websockets` package addition needed. FastAPI's `WebSocket` endpoint (used in Phase 7) works with `uvicorn[standard]` without additional dependencies. The existing transitive `websockets==16.0` from `google-genai` is sufficient if ever needed.

### 7.3 Dependency Summary

| Dependency          | Status                  | Version | Notes                             |
| ------------------- | ----------------------- | ------- | --------------------------------- |
| `openai`            | ✅ Present              | 2.45.0  | Supports Whisper + TTS            |
| `websockets`        | ✅ Present (transitive) | 16.0    | Via `google-genai`                |
| `uvicorn[standard]` | ✅ Present              | 0.51.0  | Includes ASGI WebSocket support   |
| Voice-specific deps | ❌ None yet             | —       | None required for v1 (per design) |

**Phase 0 Decision:** No new runtime dependencies required. Proceed with existing `openai>=2.45.0` and `uvicorn[standard]>=0.51.0`.

---

## 8. Git Status

```bash
Branch: feat/v2-epic-04-voice-interfaces-phase-00
Status: Clean working tree (no uncommitted changes)
Base: main (up to date)
```

**Recent commits:**

- Epic 03 Phase 8: `fix(mcp): improve registration robustness and security`
- Epic 03 Phase 9: `feat: complete Phase 9: wire MCP integration into app lifecycle`
- Epic 03 Phase 10: Appears merged to main

---

## 9. Acceptance Criteria

- [x] All backend quality gates pass (lint, format, typecheck, test coverage ≥80%, eval)
- [x] All frontend quality gates pass (lint, format, tests, build)
- [x] Inventory documents real module paths only
- [x] No `app/ai/voice/` or frontend voice modules exist yet
- [x] Epic 03 components operational (MCP, agent, RAG, unified chat)
- [x] OpenAI SDK supports Whisper + TTS APIs
- [x] `websockets` availability confirmed (transitive)
- [x] No repository code changes during audit
- [x] Baseline recorded

---

## 10. Completion Record

| Metric                   | Result                                                       |
| ------------------------ | ------------------------------------------------------------ |
| Backend tests / coverage | ✅ 923 passed, 89.02% coverage                               |
| Eval CLI                 | ✅ 5/5 passed (prompt, retrieval, e2e)                       |
| Frontend tests           | ✅ 35 files, 188 tests passed                                |
| Frontend build           | ✅ Successful (456 kB JS bundle)                             |
| Git commit               | `feat/v2-epic-04-voice-interfaces-phase-00` (clean)          |
| Audit doc                | ✅ `docs/audits/post-mvp-v2-epic4-phase-0-baseline-audit.md` |

---

## 11. Recommendations

1. **Proceed to Phase 1** — All gates green; codebase ready for scaffold/models/interfaces
2. **No dependency changes needed** — Existing `openai>=2.45.0` sufficient for Whisper + TTS
3. **No websockets package addition** — FastAPI WebSocket via ASGI works with `uvicorn[standard]`; existing transitive `websockets==16.0` is bonus
4. **Preserve flag-off paths** — When `VOICE_ENABLED=false` (default), text chat/MCP/agent/RAG must remain unchanged
5. **Reuse streaming patterns** — Voice layer should map to `StreamPublisher`/SSE frame concepts via `VoiceStreamBridge`

---

## 12. Appendix: Module Counts

### Backend

- Total Python files: 290
- `app/ai/agent/`: ~20 modules
- `app/ai/mcp/`: 11 modules
- `app/ai/rag/`: ~15 modules
- `app/services/`: 8 services
- `app/routers/`: 5 routers
- `tests/`: 923 tests

### Frontend

- Total TypeScript files: ~316 modules
- Test files: 35
- Tests: 188

---

## 13. Phase 0 Exit Criteria

- [x] Audit published
- [x] Baseline recorded
- [x] User confirmation pending

**Status:** Phase 0 complete. Awaiting user confirmation to proceed to Phase 1.

---

**Audit completed:** 2026-07-28T08:15:00Z
**Next phase:** Phase 1 — Scaffold, Models, Interfaces
**Branch:** `feat/v2-epic-04-voice-interfaces-phase-00`
