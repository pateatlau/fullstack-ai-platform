# 1. Current-State Assessment

Python/FastAPI application root is `backend-python/app/main.py`. Startup loads settings once via `backend-python/app/core/config.py`, validates the selected provider key only, wires CORS and routers, and normalizes exception responses into a typed error envelope.

Relevant runtime/request path is:

1. Frontend builds request type in `frontend/src/types/chat.ts`, currently with provider union `openai` or `gemini` only.
2. Frontend sends JSON to non-streaming and streaming endpoints via `frontend/src/api/chatClient.ts`.
3. Streaming frames are parsed in `frontend/src/api/sseParser.ts` as `start`, `delta`, `end`, `error`.
4. FastAPI routes in `backend-python/app/routers/chat.py` call service methods.
5. Service orchestration in `backend-python/app/services/chat_service.py` resolves provider, chooses default model, applies timeout, normalizes errors, and emits normalized SSE.
6. Provider abstraction is a protocol in `backend-python/app/providers/base.py` with `stream_chat` and `complete_chat` returning normalized `ProviderChunk`.
7. Provider instantiation is a simple branch factory in `backend-python/app/providers/factory.py`.
8. OpenAI adapter in `backend-python/app/providers/openai_provider.py` uses `AsyncOpenAI` chat completions and streams `delta.content` plus `finish_reason`.
9. Gemini adapter in `backend-python/app/providers/gemini_provider.py` uses `google-genai` sync calls wrapped with `asyncio.to_thread` and converts prompt-style input/output.
10. API request/response/frame schema lives in `backend-python/app/schemas/chat.py`, with provider constrained to `openai` or `gemini` via `Literal`.

Provider/model-specific knowledge currently appears in:

- `backend-python/app/core/config.py`: `llm_provider` default, `openai_model`, `gemini_model`, provider key validation.
- `backend-python/app/schemas/chat.py`: `ProviderName` `Literal`.
- `backend-python/app/services/chat_service.py`: default-model branching.
- `backend-python/app/providers/factory.py`: provider resolution branches.
- `frontend/src/types/chat.ts`: provider union in request type.
- `frontend/src/api/chatClient.ts`: provider union in response type.

Streaming contract is already provider-agnostic at app boundary:
backend emits SSE `start/delta/end/error` and frontend consumes that normalized shape without provider-specific parsing.

Testing baseline:

- Endpoint and validation behavior in `backend-python/tests/test_chat_endpoint.py`.
- Streaming flow and disconnect behavior in `backend-python/tests/test_chat_stream.py`.
- Gemini/provider factory settings tests in `backend-python/tests/providers/test_gemini_provider.py`.
- Frontend streaming/reducer tests in `frontend/src/components/Composer.test.tsx`, `frontend/src/state/chatReducer.test.ts`, `frontend/src/api/sseParser.test.ts`.

Dependency baseline from `backend-python/pyproject.toml` currently includes `openai` and `google-genai`, but no `groq` or `anthropic` packages.

# 2. Key Findings and Architectural Decisions

**Decision**
Keep current provider protocol and factory shape, extend with two new adapters.

**Rationale**
Existing abstraction already supports divergent provider internals while keeping shared service and SSE contract stable.

**Alternatives considered**
Full plugin registry or capability matrix; rejected as unnecessary for four providers and contrary to minimal-change goal.

**Decision**
Use provider-native SDKs for Groq and Anthropic.

**Rationale**
Current code uses native SDKs per provider (OpenAI, Gemini). Native SDKs provide correct auth, error classes, and streaming semantics with less custom HTTP code.

**Alternatives considered**
OpenAI-compatible Groq via OpenAI SDK base URL. Rejected as default path because it hides Groq-specific error/model behavior; may remain fallback option if SDK constraints arise.

**Decision**
Preserve startup behavior where only selected default provider key is required.

**Rationale**
`backend-python/app/core/config.py` currently validates only active provider key. Changing to require all keys would be a behavior regression.

**Alternatives considered**
Require all provider keys at startup. Rejected due to unnecessary friction and changed operator workflow.

**Decision**
Add minimal provider/model compatibility validation.

**Rationale**
Current API accepts any model string; adding more providers increases mismatch risk. Minimal map-based validation prevents invalid combinations without introducing dynamic model registry.

**Alternatives considered**
No compatibility validation. Rejected because it increases avoidable runtime provider errors.

**Decision**
Keep frontend streaming/parser unchanged; add provider/model selection with minimal UI additions.

**Rationale**
Frontend already consumes normalized frames and should remain provider-format agnostic.

**Alternatives considered**
Provider-specific frontend streaming paths. Rejected as unnecessary complexity.

**Decision**
Anthropic integration should explicitly map system messages to top-level `system` parameter.

**Rationale**
Anthropic Messages API uses top-level `system` (and supports richer structures); direct reuse of OpenAI message list semantics is not exact.

**Alternatives considered**
Pass all messages as user/assistant text only and drop system role. Rejected due semantic loss.

**Decision**
Groq model id `openai/gpt-oss-20b` and Anthropic model id `claude-haiku-4-5-20251001` remain unchanged.

**Rationale**
Both identifiers are present in current official docs snapshots.

**Alternatives considered**
Swapping to aliases automatically. Rejected by requirement; keep exact ids and flag runtime availability verification.

Required refactor:

- Small shared constants/mapping for provider-to-default-model and provider-to-allowed-models in backend service/schema path.

Optional improvement:

- Introduce lightweight provider metadata module to reduce duplicated literals across frontend files. Useful but not required for correctness.

# 3. Files Impacted

### Files to modify

- `backend-python/pyproject.toml`: add new SDK dependencies.
- `backend-python/app/core/config.py`: add keys/models for Groq and Anthropic, extend provider key validation.
- `backend-python/app/schemas/chat.py`: extend provider literal and add minimal provider/model compatibility validation hook.
- `backend-python/app/providers/factory.py`: register `GroqProvider` and `AnthropicProvider` branches.
- `backend-python/app/services/chat_service.py`: extend default model resolution; add/centralize compatibility check if kept in service.
- `backend-python/tests/conftest.py`: add default env vars for `GROQ_API_KEY` and `ANTHROPIC_API_KEY` for isolated tests.
- `backend-python/tests/test_chat_endpoint.py`: add provider/model mismatch and new-provider request validation tests.
- `backend-python/tests/test_chat_stream.py`: add streaming error/finish regression for new providers via fakes/mocks.
- `backend-python/.env.example`: add Groq/Anthropic key/model variables and provider options.
- `backend-python/.env.required`: document conditional key requirements for four providers.
- `backend-python/README.md`: document supported providers/models and setup changes.
- `README.md`: update project-level provider support list.
- `frontend/src/types/chat.ts`: extend provider union and optional model typing helpers.
- `frontend/src/api/chatClient.ts`: extend response provider union.
- `frontend/src/pages/ChatPage.tsx`: add provider/model selection state and include in request payload.
- `frontend/src/components/Composer.test.tsx`: update assertions if composer/page now exposes provider controls.
- `frontend/src/state/chatReducer.test.ts`: only if new action/state fields are introduced for selection persistence.
- `frontend/README.md`: reflect provider selection behavior in UI.

### Files to create

- `backend-python/app/providers/groq_provider.py`: Groq adapter implementing `LLMProvider` with async streaming and completion.
- `backend-python/app/providers/anthropic_provider.py`: Anthropic adapter implementing `LLMProvider` with message/system mapping and streaming event extraction.
- `backend-python/tests/providers/test_groq_provider.py`: unit tests for Groq mapping/stream extraction/error handling.
- `backend-python/tests/providers/test_anthropic_provider.py`: unit tests for Anthropic mapping/system handling/stream extraction/error handling.
- Optional only: `frontend/src/constants/providerModels.ts` for shared UI/provider-model metadata if duplication in `ChatPage` grows.

### Files reviewed but not expected to change

- `backend-python/app/main.py`: global error envelope and middleware already suitable.
- `backend-python/app/providers/base.py`: protocol likely sufficient unchanged.
- `backend-python/app/routers/chat.py`: route layer remains stable.
- `backend-python/app/routers/health.py`: no provider-specific logic needed.
- `frontend/src/api/sseParser.ts`: already provider-agnostic.
- `frontend/src/hooks/useChatStream.ts`: streaming transport remains unchanged.
- `frontend/src/state/chatReducer.ts`: unless provider/model UI state is introduced here, no required change.

# 4. Fine-Grained Implementation Plan

## Step 0.1 — Baseline verification and regression guardrails

**Goal**
Document current expected behavior and establish regression baseline before any code change.

**Why this step is needed**
Prevents accidental regression while extending provider matrix.

**Files**

- inspect only: `backend-python/tests/test_chat_endpoint.py`
- inspect only: `backend-python/tests/test_chat_stream.py`
- inspect only: `backend-python/tests/providers/test_gemini_provider.py`
- inspect only: `frontend/src/components/Composer.test.tsx`

**Changes**
No code changes. Record current pass criteria and identify exact tests to rerun after each phase.

**Validation**
Run backend and frontend tests once to capture baseline status.

**Expected result**
A known-green baseline and explicit regression targets for OpenAI and Gemini.

## Step 1.1 — Add Groq and Anthropic dependencies

**Goal**
Add required Python dependencies only.

**Why this step is needed**
Provider adapters cannot be implemented without SDK clients and typed errors.

**Files**

- modify: `backend-python/pyproject.toml`

**Changes**
Add `groq` and `anthropic` dependencies with project-consistent version strategy.

**Provider-specific considerations**
Prefer versions that expose async client APIs and stable streaming support.

**Validation**
Install/sync dependencies and ensure import resolution in a lightweight check.

**Expected result**
Runtime can import `groq` and `anthropic` packages.

## Step 1.2 — Extend backend settings for new providers

**Goal**
Add Groq/Anthropic environment fields and preserve conditional key validation behavior.

**Why this step is needed**
Factory/service defaults and startup fail-fast behavior depend on `Settings`.

**Files**

- modify: `backend-python/app/core/config.py`

**Changes**
Add fields: `groq_api_key`, `groq_model` default `openai/gpt-oss-20b`, `anthropic_api_key`, `anthropic_model` default `claude-haiku-4-5-20251001`.
Extend `validate_provider_key` branches for `llm_provider` values `groq` and `anthropic`, keeping selected-provider-only requirement.

**Provider-specific considerations**
No API keys should be surfaced outside backend config.

**Validation**
Add/adjust settings tests for provider selection and missing-key errors.

**Expected result**
Startup validation behaves consistently for all four providers.

## Step 1.3 — Update environment documentation files

**Goal**
Reflect new configuration contract for operators/developers.

**Why this step is needed**
Prevents misconfiguration and confusion during local/prod setup.

**Files**

- modify: `backend-python/.env.example`
- modify: `backend-python/.env.required`
- modify: `backend-python/README.md`
- modify: `README.md`

**Changes**
Document `LLM_PROVIDER` values `openai`, `gemini`, `groq`, `anthropic` and corresponding keys/models.

**Validation**
Manual review for consistency with `Settings` names and defaults.

**Expected result**
Docs and env templates align with code configuration.

## Step 2.1 — Expand provider schema literal and compatibility validation

**Goal**
Allow new provider values and prevent invalid provider/model combinations.

**Why this step is needed**
Current `ProviderName` literal blocks new providers, and unchecked model strings can mismatch provider.

**Files**

- modify: `backend-python/app/schemas/chat.py`
- modify or inspect only: `backend-python/app/services/chat_service.py`

**Changes**
Expand `ProviderName` to include `groq` and `anthropic`.
Introduce minimal static compatibility map for known defaults and validate provider+model when both are provided.
Keep `model` as string to avoid over-engineering full model registry.

**Provider-specific considerations**
Retain exact model ids required:

- openai -> `gpt-4o-mini`
- gemini -> `gemini-3.1-flash-lite`
- groq -> `openai/gpt-oss-20b`
- anthropic -> `claude-haiku-4-5-20251001`

**Validation**
Add API tests for invalid combinations like openai + claude model and groq + gemini model.

**Expected result**
API accepts valid combinations and rejects mismatches with clear `validation_error`.

## Step 2.2 — Extend service default model resolution cleanly

**Goal**
Return correct default model per selected provider.

**Why this step is needed**
Current fallback returns OpenAI model for non-Gemini providers.

**Files**

- modify: `backend-python/app/services/chat_service.py`

**Changes**
Replace two-branch default logic with explicit mapping for four providers.

**Provider-specific considerations**
No silent model substitution; use configured defaults exactly.

**Validation**
Unit tests on service resolution path using each provider.

**Expected result**
Provider default model selection is deterministic and correct.

## Step 3.1 — Add Groq provider adapter skeleton and factory registration

**Goal**
Introduce Groq as first-class provider in existing architecture.

**Why this step is needed**
Factory currently has only OpenAI and Gemini branches.

**Files**

- create: `backend-python/app/providers/groq_provider.py`
- modify: `backend-python/app/providers/factory.py`

**Changes**
Implement `GroqProvider` class satisfying `LLMProvider` protocol.
Register `groq` branch in factory using `settings.groq_api_key`.

**Provider-specific considerations**
Use `AsyncGroq` client and Chat Completions API; do not assume OpenAI SDK reuse by default.

**Validation**
Factory test asserting groq branch returns `GroqProvider` instance.

**Expected result**
`ProviderFactory` resolves groq provider successfully.

## Step 3.2 — Implement Groq non-streaming request/response mapping

**Goal**
Support `complete_chat` for Groq with normalized string output.

**Why this step is needed**
Non-streaming endpoint must support Groq parity with existing providers.

**Files**

- create: `backend-python/app/providers/groq_provider.py`
- create: `backend-python/tests/providers/test_groq_provider.py`

**Changes**
Map internal messages to Groq chat message format.
Call async `chat.completions.create(stream=False)`.
Extract message text robustly (string and structured variants).

**Provider-specific considerations**
Model `openai/gpt-oss-20b` is documented on Groq models page; still verify account-level availability at smoke-test time.

**Validation**
Unit tests for message mapping and text extraction behavior.

**Expected result**
Groq `complete_chat` returns normalized assistant text.

## Step 3.3 — Implement Groq streaming normalization

**Goal**
Support `stream_chat` for Groq producing `ProviderChunk` deltas.

**Why this step is needed**
Streaming endpoint is primary UX path.

**Files**

- create: `backend-python/app/providers/groq_provider.py`
- create: `backend-python/tests/providers/test_groq_provider.py`

**Changes**
Use async streaming call and iterate events.
Extract delta text and finish_reason; ignore empty metadata-only chunks.

**Provider-specific considerations**
Handle Groq SDK error classes:
`APIConnectionError`, `APIStatusError`, `RateLimitError`, timeout variants where exposed.

**Validation**
Provider unit tests for chunk extraction and finish_reason propagation.
Endpoint stream tests via factory monkeypatch with Groq-like fake stream.

**Expected result**
Groq streams map into existing SSE delta/end contract with no frontend changes.

## Step 4.1 — Add Anthropic provider adapter skeleton and factory registration

**Goal**
Introduce Anthropic provider in same extension pattern.

**Why this step is needed**
Keeps architecture coherent and incremental after Groq.

**Files**

- create: `backend-python/app/providers/anthropic_provider.py`
- modify: `backend-python/app/providers/factory.py`

**Changes**
Implement `AnthropicProvider` class satisfying `LLMProvider`.
Register `anthropic` branch using `settings.anthropic_api_key`.

**Provider-specific considerations**
Use `AsyncAnthropic` client for async parity with service layer.

**Validation**
Factory test for anthropic resolution.

**Expected result**
`ProviderFactory` resolves anthropic provider successfully.

## Step 4.2 — Implement Anthropic message/system mapping for non-streaming

**Goal**
Correctly convert internal messages into Anthropic Messages API request.

**Why this step is needed**
Anthropic semantics differ from OpenAI/Groq.
System role should map to top-level `system` field.

**Files**

- create: `backend-python/app/providers/anthropic_provider.py`
- create: `backend-python/tests/providers/test_anthropic_provider.py`

**Changes**
Split system messages from conversation turns.
Build Anthropic `messages` list with `user/assistant` roles only.
Set required `max_tokens` and selected model.
Map response content blocks to plain text output.

**Provider-specific considerations**
Temperature range in Anthropic docs is `0.0` to `1.0`. Current app allows up to `2.0`.
Plan: clamp or validate for anthropic path explicitly, and document behavior.

**Validation**
Unit tests for system extraction and message conversion across mixed histories.

**Expected result**
Anthropic `complete_chat` returns expected normalized text for common text-block responses.

## Step 4.3 — Implement Anthropic streaming event handling

**Goal**
Normalize Anthropic stream events into `ProviderChunk` text deltas.

**Why this step is needed**
Anthropic stream emits typed events like `content_block_delta` and `message_delta`.

**Files**

- create: `backend-python/app/providers/anthropic_provider.py`
- create: `backend-python/tests/providers/test_anthropic_provider.py`

**Changes**
Iterate SDK stream events.
Emit content only for `text_delta` events.
Capture `stop_reason` from `message_delta/message_stop` and map to `finish_reason`.
Ignore `ping` and non-text deltas.

**Provider-specific considerations**
Unknown future event types should be ignored safely per docs guidance.

**Validation**
Unit tests with mocked event sequence:
`message_start`, `content_block_delta text_delta`, `message_delta stop_reason`, `message_stop`.

**Expected result**
Anthropic streaming integrates with existing `ChatService` SSE framing unchanged.

## Step 4.4 — Integrate provider-specific error translation

**Goal**
Ensure Groq and Anthropic exceptions map to existing normalized error codes.

**Why this step is needed**
Current `normalize_chat_error` uses class-name heuristics and may miss SDK-specific categories.

**Files**

- modify: `backend-python/app/services/chat_service.py`
- create or modify: `backend-python/tests/providers/test_groq_provider.py`
- create or modify: `backend-python/tests/providers/test_anthropic_provider.py`
- modify: `backend-python/tests/test_chat_endpoint.py`

**Changes**
Extend normalization logic minimally for known SDK error class names and status codes (`429`, timeout, auth/invalid model as `provider_error` with safe message).
Keep external error schema unchanged.

**Provider-specific considerations**
Never leak raw upstream payload or sensitive headers into client-facing messages.

**Validation**
Targeted tests for timeout, rate-limited, and generic failure mapping.

**Expected result**
Error behavior remains consistent for all providers and endpoints.

## Step 5.1 — Extend frontend provider/model typing and API response types

**Goal**
Allow frontend request/response types to represent four providers.

**Why this step is needed**
Type unions currently block `groq` and `anthropic` values.

**Files**

- modify: `frontend/src/types/chat.ts`
- modify: `frontend/src/api/chatClient.ts`

**Changes**
Expand provider unions to include `groq` and `anthropic`.
Optionally add typed provider/model map helper to reduce string duplication.

**Validation**
Run frontend typecheck/build.

**Expected result**
Type system accepts all four providers without unsafe casts.

## Step 5.2 — Add minimal provider/model selector UI and payload wiring

**Goal**
Expose OpenAI, Gemini, Groq, Anthropic options in chat UI with fixed target models.

**Why this step is needed**
Current UI sends messages only, with no provider/model controls.

**Files**

- modify: `frontend/src/pages/ChatPage.tsx`
- modify or inspect only: `frontend/src/components/Composer.tsx`

**Changes**
Add minimal selector controls (provider and model, or provider-only with fixed mapped model) with defaults preserving current behavior.
Pass selected provider/model into `startRequest` payload.

**Provider-specific considerations**
No provider secrets on frontend.
Keep model list static and explicit, no dynamic discovery.

**Validation**
Update UI tests for selection and payload assertions.

**Expected result**
User can choose each provider/model pair from UI and request payload reflects selection.

## Step 5.3 — Frontend regression and interaction tests for selection + streaming

**Goal**
Confirm UI behavior and streaming contract remain intact after selector changes.

**Why this step is needed**
Chat shell is complex and heavily event-driven.

**Files**

- modify: `frontend/src/components/Composer.test.tsx`
- modify: `frontend/src/state/chatReducer.test.ts`
- inspect only: `frontend/src/api/sseParser.test.ts`

**Changes**
Add/adjust tests for:
provider/model selection persistence per send,
streaming still renders `start/delta/end`,
error handling unchanged.

**Validation**
Run frontend test suite and build.

**Expected result**
Selection feature works without breaking streaming/retry/stop UX.

## Step 6.1 — Expand backend integration tests for four-provider matrix

**Goal**
Validate endpoint orchestration, provider resolution, and mismatch handling.

**Why this step is needed**
Most regressions occur at service+router boundary.

**Files**

- modify: `backend-python/tests/test_chat_endpoint.py`
- modify: `backend-python/tests/test_chat_stream.py`
- modify: `backend-python/tests/conftest.py`

**Changes**
Add parameterized tests covering provider value acceptance, default model resolution, mismatch rejection, stream frame integrity, and normalized errors.

**Validation**
Run backend test suite and lint.

**Expected result**
Automated tests cover core behavior for all providers without real API calls.

## Step 6.2 — Add dedicated provider unit tests for Groq and Anthropic adapters

**Goal**
Validate adapter-level mapping logic independent of FastAPI routes.

**Why this step is needed**
Provider SDK event/message mapping is the highest provider-specific risk area.

**Files**

- create: `backend-python/tests/providers/test_groq_provider.py`
- create: `backend-python/tests/providers/test_anthropic_provider.py`

**Changes**
Use fake/mocked SDK clients for:
request shape,
system mapping,
delta extraction,
finish reason extraction,
error scenarios.

**Validation**
Run provider test modules directly plus full suite.

**Expected result**
Provider adapters are independently verifiable and easier to debug.

## Step 7.1 — Documentation and smoke-test checklist finalization

**Goal**
Publish concise operator/dev guidance and manual validation matrix.

**Why this step is needed**
Feature is incomplete without reproducible setup and smoke steps.

**Files**

- modify: `backend-python/README.md`
- modify: `frontend/README.md`
- modify: `README.md`

**Changes**
Document supported providers/models, env vars, and smoke commands for all four combinations.
Explicitly note no database/session scope changes in this feature.

**Validation**
Human review of docs against implemented behavior.

**Expected result**
Docs are aligned, and manual verification path is clear.

# 5. Testing Matrix

| Area                   | OpenAI                                              | Gemini                                              | Groq                                            | Anthropic                                                 |
| ---------------------- | --------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------- |
| Config key validation  | OPENAI_API_KEY required only when default is openai | GEMINI_API_KEY required only when default is gemini | GROQ_API_KEY required only when default is groq | ANTHROPIC_API_KEY required only when default is anthropic |
| Provider resolution    | factory branch + service resolve                    | factory branch + service resolve                    | factory branch + service resolve                | factory branch + service resolve                          |
| Request mapping        | existing chat messages                              | prompt conversion path                              | chat completions message mapping                | messages API with system split + max_tokens               |
| Streaming mapping      | delta.content + finish_reason                       | extracted text from stream chunks                   | delta.content + finish_reason                   | content_block_delta text_delta + stop_reason mapping      |
| Completion success     | /api/chat returns assistant content                 | same                                                | same                                            | same                                                      |
| Representative failure | provider_error / timeout / rate-limited             | same                                                | SDK status/connection errors normalized         | SDK/API errors normalized                                 |
| Frontend selection     | selectable + payload reflects                       | selectable + payload reflects                       | selectable + payload reflects                   | selectable + payload reflects                             |
| Regression coverage    | existing tests still pass                           | existing tests still pass                           | new provider tests                              | new provider tests                                        |

Automated tests:

- Backend: pytest suite including endpoint, stream, provider unit tests.
- Frontend: vitest suite plus build/typecheck.

Manual smoke tests:

- For each provider/model pair, verify selection, successful stream, incremental rendering, end frame completion, and error surfacing.
- Re-run OpenAI and Gemini manual stream checks to confirm no regression.

Optional live-provider tests:

- Run real calls with each provider key in local env to verify account-level model availability and real SDK behavior.

# 6. Recommended Implementation Sequence

- [ ] Step 0.1 — Baseline verification and regression guardrails
- [ ] Step 1.1 — Add Groq and Anthropic dependencies
- [ ] Step 1.2 — Extend backend settings for new providers
- [ ] Step 1.3 — Update environment documentation files
- [ ] Step 2.1 — Expand provider schema literal and compatibility validation
- [ ] Step 2.2 — Extend service default model resolution cleanly
- [ ] Step 3.1 — Add Groq provider adapter skeleton and factory registration
- [ ] Step 3.2 — Implement Groq non-streaming request/response mapping
- [ ] Step 3.3 — Implement Groq streaming normalization
- [ ] Step 4.1 — Add Anthropic provider adapter skeleton and factory registration
- [ ] Step 4.2 — Implement Anthropic message/system mapping for non-streaming
- [ ] Step 4.3 — Implement Anthropic streaming event handling
- [ ] Step 4.4 — Integrate provider-specific error translation
- [ ] Step 5.1 — Extend frontend provider/model typing and API response types
- [ ] Step 5.2 — Add minimal provider/model selector UI and payload wiring
- [ ] Step 5.3 — Frontend regression and interaction tests for selection + streaming
- [ ] Step 6.1 — Expand backend integration tests for four-provider matrix
- [ ] Step 6.2 — Add dedicated provider unit tests for Groq and Anthropic adapters
- [ ] Step 7.1 — Documentation and smoke-test checklist finalization

# 7. Risks and Edge Cases

- Anthropic message and system semantics differ from OpenAI/Groq.
  - Impact: behavior drift and weaker instruction following if system messages are mis-mapped.
  - Mitigation: dedicated mapping function and unit tests.
  - Addressed in: Steps 4.2, 6.2.

- Anthropic requires `max_tokens` and has different temperature expectations.
  - Impact: request failures or inconsistent generation behavior.
  - Mitigation: explicit `max_tokens` strategy and anthropic-specific temperature handling policy.
  - Addressed in: Steps 4.2, 4.4.

- Anthropic streaming event shape differs significantly.
  - Impact: missing output chunks or incorrect finish handling.
  - Mitigation: event-type parser focused on `text_delta` and `stop_reason` extraction.
  - Addressed in: Step 4.3.

- Groq OpenAI-compat assumptions may hide SDK-specific behavior.
  - Impact: incorrect error mapping and difficult debugging.
  - Mitigation: native Groq SDK integration with targeted error translation tests.
  - Addressed in: Steps 3.1, 3.3, 4.4, 6.2.

- Provider/model mismatch currently under-validated.
  - Impact: runtime provider errors from invalid combinations.
  - Mitigation: minimal compatibility map validation.
  - Addressed in: Step 2.1.

- Regression in default model resolution due to expanded provider set.
  - Impact: requests may use wrong model silently.
  - Mitigation: explicit provider-to-default mapping tests.
  - Addressed in: Step 2.2.

- Startup behavior regression if all keys become required.
  - Impact: local/prod friction and behavior change.
  - Mitigation: preserve selected-provider-only key validation.
  - Addressed in: Step 1.2.

- Frontend currently has no provider selector.
  - Impact: new providers inaccessible despite backend support.
  - Mitigation: minimal selector wiring with tests; no UI redesign.
  - Addressed in: Steps 5.2, 5.3.

- Model identifier availability can be account/region constrained despite docs.
  - Impact: runtime not-found/permission errors in live environments.
  - Mitigation: keep exact ids, add live smoke verification and clear error messaging.
  - Addressed in: Steps 4.4, 7.1.

# 8. Open Questions / Verification Items

1. Verify Groq model availability for target account/project:
   `openai/gpt-oss-20b` is listed publicly, but account permissions/rate tier may vary.
   - Why it matters: avoids false-negative implementation debugging.
   - Blocking: no for coding; yes for live smoke completion.

2. Confirm Anthropic model availability and lifecycle in target environment:
   `claude-haiku-4-5-20251001` is listed in current docs.
   - Why it matters: ensures exact pinned model id works in deployed account.
   - Blocking: no for coding; yes for live smoke completion.

3. Decide Anthropic `max_tokens` default policy for this app:
   fixed conservative default versus configurable setting addition.
   - Why it matters: required parameter and output length behavior.
   - Blocking: yes for Anthropic adapter implementation detail.

4. Decide Anthropic temperature handling when client sends greater than `1.0`:
   clamp, reject with `validation_error`, or pass through and rely on provider error.
   - Why it matters: current schema allows up to `2.0` globally.
   - Blocking: yes for clean cross-provider contract behavior.

# 9. Definition of Done

- Groq is available as a provider using `openai/gpt-oss-20b`.
- Anthropic is available as a provider using `claude-haiku-4-5-20251001`.
- Both providers work through existing Python/FastAPI request flow.
- Streaming for both providers is normalized into existing SSE `start/delta/end/error` contract.
- Frontend allows selecting all four provider/model options:
  - OpenAI `gpt-4o-mini`
  - Gemini `gemini-3.1-flash-lite`
  - Groq `openai/gpt-oss-20b`
  - Anthropic `claude-haiku-4-5-20251001`
- Invalid provider/model combinations are handled deterministically (validation or normalized error path per selected design).
- API keys remain backend-only; no frontend key exposure.
- Existing OpenAI behavior remains working.
- Existing Gemini behavior remains working.
- Backend linting and tests pass with project tooling from `backend-python/Makefile`.
- Frontend tests and build/typecheck pass.
- Manual smoke tests succeed for all four providers.
- Documentation updates completed in backend and root/frontend READMEs and env templates.
- No database persistence scope added.
- No Node.js backend parity changes added.
- No unrelated architecture redesign introduced.

# Final Smoke-Test Checklist

Use this checklist to verify the implementation after code changes or env updates:

1. Set `LLM_PROVIDER` to `openai`, configure only `OPENAI_API_KEY`, and confirm `/api/health` reports `openai`.
2. Repeat the same check for `gemini`, `groq`, and `anthropic`, each with only its matching API key set.
3. For each provider, submit `POST /api/chat` with no `provider` field and confirm the response uses that provider's configured default model.
4. For each provider, submit `POST /api/chat/stream` with no `provider` field and confirm the SSE stream starts, emits deltas, and ends successfully.
5. Confirm the frontend composer can select each provider and that the request payload includes the selected provider and model.
6. Confirm mismatched provider/model pairs are rejected with a validation error before any upstream provider call is made.
7. Run the focused backend and frontend checks documented in the respective READMEs before merging.
