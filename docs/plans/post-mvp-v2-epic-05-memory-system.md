---
epic: v2-05
title: Memory System
status: in_progress
version: 2
depends_on: [v2-04]
provides:
  [
    MemoryManager,
    MemoryProvider,
    MemoryContext,
    MemoryRecord,
    ConversationSummaryService,
    SemanticRetriever,
    MemoryContextBuilder,
    MemoryPromptInjector,
    MemoryPolicyEngine,
    LifecycleManager,
    PgVectorMemoryProvider,
    MEMORY_ENABLED,
    memory_router,
  ]
feature_flags: [MEMORY_ENABLED]
packages: [app/ai/memory]
test_paths:
  [
    tests/ai/memory,
    tests/test_memory_router.py,
    frontend/src/pages/MemorySettingsPage.test.tsx,
    frontend/src/api/memoryClient.test.ts,
  ]
---

# Post-MVP V2 Epic 05 — Memory System

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § "5. Memory System"

**Predecessor:** [Epic 04 — Voice Interfaces](./post-mvp-v2-epic-04-voice-interfaces.md)

---

# Part I — Design

## Objective

Introduce a provider-agnostic memory platform that gives the AI assistant durable context across conversations while preserving the existing architecture.

The Memory System is the platform's canonical context layer.

It provides durable context for conversations while remaining independent from RAG, provider implementations, and future platform capabilities such as workflows, evaluation, and multi-agent orchestration.

**Delivers:** Rolling conversation summaries (extending existing V1 `SessionSummary`), durable user/project memories with semantic retrieval, structured user preferences, lifecycle management, authenticated Memory management REST API, and frontend settings UI — all behind `MEMORY_ENABLED=false` (default).

**Does not ship:** Shared organization memory; standalone `projects` entity or cross-session project workspaces; workflow memory; background job queue (uses in-process async only); governance/RBAC; memory OTel spans/eval harness (Epic 07); memory plugin SDK (Epic 08); guest-user memory; inferred preferences from chat (preferences are explicit user settings only in v1).

Capabilities:

- Rolling Conversation Summaries
- Long-term memory
- User preferences
- Project memory (session-scoped in v1)
- Semantic retrieval
- Memory lifecycle management

Memory is additive. When disabled, existing chat, RAG, MCP, streaming and agent pipelines remain unchanged.

---

## Design Principles

- Platform-first
- Provider-agnostic
- Composition over coupling
- Explainable retrieval
- Deterministic orchestration
- Explicit lifecycle management
- Privacy by default
- Feature-flag rollout

---

## Scope

### In Scope

- Rolling Conversation Summary
- Long-term memory
- User preferences
- Project memory
- Semantic retrieval
- Lifecycle management
- Memory deletion

### Out of Scope

- Shared organization memory
- Workflow memory
- Background optimization
- Governance/RBAC

---

## High-Level Architecture

```text
User Request
      │
      ▼
ChatService / UnifiedChatService
      │
      ▼
 ┌──────────────────┼────────────┐
 ▼                  ▼            ▼
Conversation      Memory        RAG (Unified path)
Summary          Retrieval
 │                  │
 │         ┌────────┴────────┐
 │         ▼                 ▼
 │    User Memory      Project Memory
 │         │                 │
 │         ▼                 ▼
 │   Long-Term Memory   Session-Scoped Memory
 └──────────┬───────────────────────┘
            ▼
    MemoryContextBuilder
            ▼
   MemoryPromptInjector (+ RAG instructions)
            ▼
      Agent Runtime / LLM Provider
            ▼
    Assistant Response
            ▼
   MemoryPolicyEngine
            ▼
 MemoryManager / Lifecycle
```

---

## Locked Architectural Decisions

| Topic                | Decision                                                                                                                                         | Deferred to                                            |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------ |
| Memory domains       | Conversation, User, Project, System (reserved)                                                                                                   | System memory → future epic                            |
| Extraction timing    | Synchronous summaries; asynchronous durable memories                                                                                             | Background Jobs queue → Epic 10                        |
| Storage              | PostgreSQL + pgvector; separate tables from RAG document chunks                                                                                  | Alternate vector backends → future provider            |
| Chat orchestration   | `MemoryManager` invoked from **`ChatService` and `UnifiedChatService`** via shared wiring — never from routers or agent core directly            | —                                                      |
| Project scope (v1)   | **`project_id` = `chat_session_id`** — project memory is session-scoped until a standalone projects entity exists                                | Dedicated projects table → future epic                 |
| Conversation summary | **Extend** existing V1 `SessionSummary` / `ChatService._maybe_summarize` — do not duplicate storage or parallel summary tables                   | —                                                      |
| Prompt injection     | New `MemoryPromptInjector` augments chat messages via `PromptManager`; RAG `PromptBuilder` receives memory block as optional `instructions` only | —                                                      |
| RAG boundary         | RAG and Memory remain independent pre-handoff pipelines                                                                                          | RAG-as-memory → out of scope                           |
| Retrieval            | Semantic-first with ranking, deduplication, token budgeting                                                                                      | Cross-encoder rerank → Epic 02 patterns optional later |
| Preferences          | Structured rows in `user_preferences` — **no embeddings**                                                                                        | Inferred preferences → out of scope v1                 |
| Lifecycle            | Created → Active → Consolidated → Archived → Deleted                                                                                             | —                                                      |
| Auth                 | Memory read/write and management API **authenticated users only** (no guest memory)                                                              | Guest memory → out of scope                            |

---

## High-Level Flow

User Message
→ Conversation Summary
→ Semantic Retrieval
→ User Memory
→ Project Memory
→ RAG
→ MemoryContextBuilder
→ PromptBuilder
→ Agent Runtime
→ LLM

After response:

→ MemoryPolicyEngine
→ MemoryManager
→ LifecycleManager

---

## End-to-End Sequence

```text
User
 │
 │ Message
 ▼
ChatService / UnifiedChatService          ← both invoke MemoryManager when flag on
 │
 ├── Retrieve Conversation Summary        ← SessionSummary via ConversationSummaryService
 │
 ├── Retrieve Semantic Memories
 │
 ├── Retrieve User Preferences
 │
 ├── Retrieve Project Memory              ← scoped to chat_session_id (v1)
 │
 ├── Execute RAG (optional, Unified path)
 │
 ├── Build MemoryContext
 │
 ├── Inject memory into prompt/messages   ← MemoryPromptInjector (+ RAG instructions)
 │
 ├── Execute Agent Runtime / Provider
 │
 ▼
LLM
 │
 ▼
Assistant Response
 │
 ├── Evaluate Memory Candidates
 │
 ├── Apply Memory Policies
 │
 ├── Persist Durable Memories             ← async (asyncio.create_task)
 │
 ├── Update Conversation Summary          ← existing _maybe_summarize path
 │
 └── Publish Memory Events
 │
 ▼
Response Returned
```

**Conversation Summary Timing**

At the beginning of each request, the latest persisted Rolling Conversation Summary from the previous assistant turn is retrieved and used during semantic retrieval and prompt construction. After the assistant response completes successfully, the Rolling Conversation Summary is updated and persisted for use in the next request.

---

## Storage Architecture

```text
MemoryManager
      │
 ┌────┼──────────────┐──────────────┐
 ▼                   ▼              ▼
Conversation   User Memory   Project Memory
      │             │             │
      └───────┬─────┴─────────────┘
              ▼
      Memory Provider
              ▼
   PgVectorMemoryProvider
              ▼
     PostgreSQL + pgvector
```

---

## Memory Provider Contract

All storage providers must implement the same interface.

Responsibilities include:

- Store MemoryRecord
- Update MemoryRecord
- Delete MemoryRecord
- Retrieve by semantic similarity
- Retrieve by identifier
- Lifecycle updates

Initial implementation:

- PgVectorMemoryProvider

Future providers:

- Pinecone
- Weaviate
- Qdrant

The remainder of the platform depends only on the MemoryProvider interface.

---

## Retrieval Pipeline

```text
Current Conversation
        │
Embedding Provider
        │
SemanticRetriever
        │
 ├─ Conversation Store
 ├─ User Store
 ├─ Project Store
 └─ Future Stores
        │
 Ranked Memories
        │
 Deduplicate
        │
 Quality Filter
        │
 Token Budget Allocator
        │
 MemoryContextBuilder
        │
 PromptBuilder
```

---

## Canonical Memory Representation

Every durable memory is represented internally by one abstraction.

```text
MemoryRecord
------------
id
memory_type
scope
owner_id
project_id
title
content
summary
embedding
metadata
importance
confidence
quality_score
created_at
updated_at
last_accessed_at
expires_at
lifecycle_state
source
```

Specializations:

```text
MemoryRecord (semantic — memory_records table)
      ▲
      ├── UserMemory        (memory_type='user')
      └── ProjectMemory     (memory_type='project', session_id scoped)

UserPreference (structured — user_preferences table, no embedding)
PreferenceMemory API model maps to UserPreference rows, not MemoryRecord.
```

All retrieval, lifecycle, ranking, providers, metrics and APIs operate on MemoryRecord.

---

## MemoryContext

Everything retrieved is normalized into:

```text
MemoryContext
-------------
conversation_summary
conversation_memories[]
user_memories[]
project_memories[]
preferences
metadata
token_usage
```

PromptBuilder never communicates directly with storage.

---

## Package Structure

```text
app/
└── ai/
    └── memory/
        ├── __init__.py
        ├── interfaces/
        │   └── memory_provider.py
        ├── providers/
        │   └── pgvector.py
        ├── manager.py
        ├── summarizer.py
        ├── semantic_retriever.py
        ├── context_builder.py
        ├── prompt_injector.py
        ├── policy_engine.py
        ├── lifecycle.py
        ├── quality.py
        ├── extraction.py
        ├── events.py
        └── models.py

app/routers/memory.py                     # NEW — authenticated Memory REST API
app/schemas/memory.py                     # NEW — request/response schemas
app/ai/deps.py                            # extend — Memory DI factories
app/services/chat_service.py              # modify — memory retrieve/inject/post-process
app/services/unified_chat_service.py      # modify — same memory hooks as ChatService
app/db/models.py                          # modify — MemoryRecord, UserPreference ORM
alembic/versions/0006_memory_tables.py   # NEW — memory_records, user_preferences
```

---

## Core Components

- MemoryManager
- ConversationSummaryService
- SemanticRetriever
- MemoryContextBuilder
- MemoryPromptInjector
- MemoryPolicyEngine
- LifecycleManager
- Memory Providers
- Memory Event Hooks
- Token Budget Allocator
- MemoryQualityEvaluator

---

## Component Responsibilities

| Component                  | Responsibility                                                                                                              | Inputs                                     | Outputs                 | Dependencies                      |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ | ----------------------- | --------------------------------- |
| ChatService                | Plain chat orchestration; invokes MemoryManager on retrieve/inject/post-process when flag on.                               | User message, session context              | Assistant response      | MemoryManager, LLM providers      |
| UnifiedChatService         | Unified toggles (RAG/web search/agent); same memory hooks as ChatService.                                                   | User message, session context              | Assistant response      | MemoryManager, RAG, Agent Runtime |
| MemoryManager              | Entry point for all memory operations. Coordinates retrieval, persistence, lifecycle management, and provider interactions. | Memory requests                            | Memory records          | MemoryProvider, LifecycleManager  |
| ConversationSummaryService | Wraps V1 `SessionSummary` storage; exposes summary via `MemoryContext`; wires `build_context_messages` when flag on.        | Session id, conversation history           | Conversation summary    | ChatStore, LLM Provider           |
| SemanticRetriever          | Retrieves relevant memories using semantic similarity search across supported domains.                                      | Query embedding                            | Ranked MemoryRecords    | EmbeddingProvider, MemoryProvider |
| MemoryContextBuilder       | Combines retrieved memories into a normalized MemoryContext for prompt construction.                                        | Retrieved memories, preferences, summaries | MemoryContext           | TokenBudgetAllocator              |
| MemoryPromptInjector       | Injects `MemoryContext` into chat messages via `PromptManager`; never touches storage.                                      | MemoryContext, message list                | Augmented messages      | PromptManager                     |
| RAG PromptBuilder          | Existing RAG template renderer; receives optional memory block as `instructions` only.                                      | question, context, instructions            | BuiltPrompt             | PromptManager                     |
| MemoryPolicyEngine         | Determines whether memories should be created, updated, merged, consolidated, archived, or deleted.                         | Memory candidates                          | Lifecycle decisions     | LifecycleManager                  |
| LifecycleManager           | Executes lifecycle transitions and retention policies for all memory types.                                                 | Lifecycle decisions                        | Updated MemoryRecords   | MemoryProvider                    |
| MemoryProvider             | Abstract persistence interface for memory storage and retrieval.                                                            | CRUD requests                              | Memory records          | PgVectorMemoryProvider (initial)  |
| EmbeddingProvider          | Generates embeddings for memory retrieval and storage (reuse existing abstraction).                                         | Text                                       | Embedding vectors       | Embedding model                   |
| TokenBudgetAllocator       | Allocates the available context window across conversation history, summaries, memories, RAG, and tool outputs.             | Retrieved context                          | Budgeted MemoryContext  | MemoryContextBuilder              |
| MemoryQualityEvaluator     | Calculates confidence, importance, recency, and quality scores for durable memories.                                        | MemoryRecord                               | Updated quality metrics | MemoryManager                     |
| EventPublisher             | Publishes memory lifecycle events for future platform integrations.                                                         | Memory events                              | Event notifications     | Event subscribers (future)        |

---

## Memory Domains

### Conversation Memory

Rolling Conversation Summary stored in existing **`session_summaries`** (`SessionSummary` ORM). `ConversationSummaryService` is the Memory subsystem façade over `ChatStore.get_latest_summary` / `add_summary` and `ChatService._maybe_summarize`.

When `MEMORY_ENABLED=true`, chat paths use `build_context_messages()` (summary + tail messages) instead of client-supplied full history for persisted sessions.

### Long-Term Memory

Durable cross-session **user-scoped** semantic memories in `memory_records` (`memory_type='user'`).

### User Preferences

Structured key/value settings in **`user_preferences`** (no embeddings, no vector search). Managed explicitly via Settings UI and REST API — not inferred from chat in v1.

### Project Memory

Session-scoped durable knowledge in `memory_records` (`memory_type='project'`). **`project_id` in API/models maps to `chat_session_id` in v1.** Cross-session project workspaces require a future projects entity.

---

## Existing V1 Assets (reuse, do not duplicate)

| Asset                                                | Location                                   | Epic 05 role                                                                         |
| ---------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------ |
| `SessionSummary` ORM + `session_summaries` table     | `app/db/models.py`                         | Canonical rolling summary storage                                                    |
| `ChatStore.get_latest_summary` / `add_summary`       | `app/db/chat.py`                           | Persistence for summaries                                                            |
| `ChatService._maybe_summarize`                       | `app/services/chat_service.py`             | Threshold-triggered summary generation (reuse; extend hooks)                         |
| `ChatService.build_context_messages`                 | `app/services/chat_service.py`             | Wire into live chat when flag on (currently tested only)                             |
| `chat/summarize_system/v1`, `chat/summarize_user/v1` | `app/ai/prompts/chat/`                     | Summary LLM prompts (reuse)                                                          |
| `chat/context_summary_prefix/v1`                     | `app/ai/prompts/chat/`                     | Summary injection into message list                                                  |
| `summary_trigger_message_count`                      | `app/core/config.py`                       | Existing threshold (default 20 pending messages)                                     |
| `EmbeddingProvider` + `create_embedding_provider`    | `app/ai/embeddings/`, `app/ai/interfaces/` | Memory embedding generation                                                          |
| `PgVectorStore` patterns                             | `app/ai/vectorstores/pgvector.py`          | Reference for HNSW/cosine/owner isolation — **separate tables** from document chunks |

---

## Chat Integration Strategy

Memory hooks run on **all persisted authenticated chat paths** when `MEMORY_ENABLED=true`:

| Path               | Service                                              | When                                     |
| ------------------ | ---------------------------------------------------- | ---------------------------------------- |
| Plain non-stream   | `ChatService.complete_chat`                          | Default chat (no RAG/web-search toggles) |
| Plain stream       | `ChatService.stream_chat`                            | Default SSE streaming                    |
| Unified non-stream | `UnifiedChatService.execute`                         | RAG and/or web-search toggles            |
| Unified stream     | `UnifiedChatService.stream_execute`                  | RAG/web-search/agent streaming           |
| Voice              | `UnifiedChatService.stream_execute` via voice bridge | Voice turns (Epic 04)                    |

**Pre-response (synchronous):**

1. `MemoryManager.retrieve_context(session_id, user_id, query)` → `MemoryContext`
2. `MemoryPromptInjector.inject(messages, memory_context)` → augmented messages (or RAG `instructions`)
3. For persisted sessions with flag on: prefer `build_context_messages(session_id)` over unbounded client history

**Post-response (async best-effort):**

1. Existing `_maybe_summarize` (unchanged trigger semantics)
2. `MemoryManager.extract_and_persist_async(...)` for durable memories (does not block response)

**Flag off:** No memory retrieve/inject/extract calls; `_maybe_summarize` continues to run as today (unchanged Epic 04 behaviour).

**Flag on:** Full memory pipeline; `_maybe_summarize` invoked via `ConversationSummaryService` as part of memory subsystem.

**Guests:** No memory retrieval, extraction, or management API access. Chat behaviour unchanged from pre-epic paths.

---

## Persistence Schema

Alembic migration **`0006_memory_tables`** (Phase 1). Separate from RAG `document_chunks.embedding`.

### `memory_records`

| Column                     | Type                              | Notes                                                              |
| -------------------------- | --------------------------------- | ------------------------------------------------------------------ |
| `id`                       | uuid PK                           |                                                                    |
| `owner_id`                 | uuid FK → `users.id`              | Required; owner isolation                                          |
| `session_id`               | uuid FK → `chat_sessions.id` NULL | Set for `memory_type='project'` (v1 project scope)                 |
| `memory_type`              | text CHECK                        | `user` \| `project`                                                |
| `title`                    | text NULL                         | Optional short label                                               |
| `content`                  | text                              | Canonical memory text                                              |
| `summary`                  | text NULL                         | Optional compressed form                                           |
| `embedding`                | vector(1536) NULL                 | Required for semantic types; null only during failed embed retry   |
| `metadata`                 | jsonb                             | Source turn, extraction model, etc.                                |
| `importance`               | float                             | Default 0.5                                                        |
| `confidence`               | float                             | Default 0.5                                                        |
| `quality_score`            | float                             | Default 0.5                                                        |
| `lifecycle_state`          | text CHECK                        | `created` \| `active` \| `consolidated` \| `archived` \| `deleted` |
| `source`                   | text                              | e.g. `extraction_v1`, `api`                                        |
| `created_at`, `updated_at` | timestamptz                       |                                                                    |
| `last_accessed_at`         | timestamptz NULL                  |                                                                    |
| `expires_at`               | timestamptz NULL                  | Optional TTL                                                       |

**Indexes:** HNSW on `embedding` (cosine); `(owner_id, memory_type, lifecycle_state)`; `(session_id)` where project-scoped.

### `user_preferences`

| Column                     | Type                 | Notes                                   |
| -------------------------- | -------------------- | --------------------------------------- |
| `id`                       | uuid PK              |                                         |
| `user_id`                  | uuid FK → `users.id` |                                         |
| `key`                      | text                 | e.g. `response_tone`, `preferred_units` |
| `value`                    | jsonb                | Structured value                        |
| `created_at`, `updated_at` | timestamptz          |                                         |

**Unique:** `(user_id, key)`.

### Unchanged

- **`session_summaries`** — rolling summaries remain here (no new summary table).

---

## Memory REST API

Authenticated-only (`Depends(get_current_caller)`). Router: `app/routers/memory.py`. Mounted when `MEMORY_ENABLED=true`; returns `503 feature_disabled` when flag off (same pattern as voice).

| Method   | Path                                        | Purpose                                                                                                    |
| -------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/memory/records`                       | List caller's memories. Query: `memory_type=user\|project`, optional `session_id` (required for `project`) |
| `GET`    | `/api/memory/records/{id}`                  | Get one memory (owner check)                                                                               |
| `DELETE` | `/api/memory/records/{id}`                  | Soft-delete → lifecycle `deleted`                                                                          |
| `GET`    | `/api/memory/preferences`                   | List caller preferences                                                                                    |
| `PUT`    | `/api/memory/preferences/{key}`             | Upsert preference (`value` json body)                                                                      |
| `DELETE` | `/api/memory/preferences/{key}`             | Remove preference                                                                                          |
| `DELETE` | `/api/memory/sessions/{session_id}/summary` | Clear rolling summary for owned session                                                                    |

**Health:** extend `GET /api/health` with `memory_enabled: bool` (frontend gate, same as `voice_enabled`).

**Response rules:** Never expose `embedding`, internal scores, or lifecycle state in public API responses — human-facing `title`, `content`, `memory_type`, `session_id` (when project), timestamps only.

---

## Extraction & Summary Prompts

| Operation                 | Prompt template                                                                                                                         | When                                 |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Rolling summary           | Existing `chat/summarize_system/v1`, `chat/summarize_user/v1`                                                                           | `_maybe_summarize` threshold (reuse) |
| Memory context injection  | **New** `chat/memory_context/v1`                                                                                                        | Pre-LLM message assembly             |
| Durable memory extraction | **New** `memory/extract/v1`                                                                                                             | Post-response async pipeline         |
| Quality gate              | Deterministic rules in `MemoryQualityEvaluator` (min confidence, dedupe by cosine similarity ≥ 0.92, reject session-ephemeral phrasing) | Before persist                       |

Extraction uses the same chat provider/model as the originating turn unless configured otherwise (`memory_extraction_model` optional override).

---

## Public APIs (stable after Phase 1)

| API                                                                                               | Kind                               |
| ------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `MemoryProvider`                                                                                  | Protocol                           |
| `MemoryManager`                                                                                   | Class (public orchestration entry) |
| `MemoryRecord`, `MemoryContext`, `MemoryType`, `LifecycleState`                                   | Model / enum                       |
| `ConversationSummaryService`, `SemanticRetriever`, `MemoryContextBuilder`, `MemoryPromptInjector` | Class                              |
| `MemoryPolicyEngine`, `LifecycleManager`, `MemoryQualityEvaluator`                                | Class                              |
| `MemoryEvent` (domain event base)                                                                 | Model                              |
| `MemoryError`, `MemoryNotFoundError`, `MemoryAccessDeniedError`                                   | Exception                          |
| Memory REST router export                                                                         | FastAPI router                     |

Internal (may evolve): `PgVectorMemoryProvider`, extraction pipeline, `TokenBudgetAllocator`, DI wiring, consolidation heuristics.

---

## Configuration defaults

| Setting                              | Default                                                                                         |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `MEMORY_ENABLED`                     | **`false`**                                                                                     |
| `memory_provider`                    | `"pgvector"`                                                                                    |
| `memory_retrieval_top_k`             | `8`                                                                                             |
| `memory_min_quality_score`           | `0.4`                                                                                           |
| `memory_min_confidence`              | `0.5`                                                                                           |
| `memory_dedupe_similarity_threshold` | `0.92`                                                                                          |
| `memory_token_budget`                | `1500` (chars budget for injected memory block)                                                 |
| `memory_extraction_enabled`          | `true` (when master flag on)                                                                    |
| `memory_extraction_model`            | `""` (empty → same model as chat turn)                                                          |
| `memory_archived_retention_days`     | `90`                                                                                            |
| Existing                             | `summary_trigger_message_count=20`, `embedding_provider`, `embedding_dimensions=1536` unchanged |

---

## Dependencies

| Requires                                          | Provides to downstream                             |
| ------------------------------------------------- | -------------------------------------------------- |
| Epic 04 Voice (stable chat/voice pipeline)        | `MemoryManager`, `MemoryContext`, `MEMORY_ENABLED` |
| V1 `SessionSummary` / `ChatService` summarization | Session-scoped continuity                          |
| `EmbeddingProvider`, pgvector Postgres            | Semantic memory storage                            |
| `PromptManager`                                   | Memory injection templates                         |

**Future consumers:** Epic 06 Workflows (workflow-scoped memory deferred); Epic 07 Observability (memory metrics/spans); Epic 10 Background Jobs (replace in-process async persistence).

---

## Design acceptance

- Flag off: no memory router; no memory UI; chat/RAG/voice/MCP/agent paths unchanged from Epic 04
- Flag on, authenticated: chat retrieves/injects memory; durable memories persist async; management API + Settings UI work
- Rolling summaries use existing `session_summaries`; `build_context_messages` wired for persisted sessions
- Project memory isolated per `chat_session_id`; no cross-session project retrieval
- User preferences are explicit settings only; stored without embeddings
- RAG `PromptBuilder` unchanged except optional memory `instructions`; document chunk retrieval untouched
- Coverage ≥80% on `app/` and `app/ai/memory/`
- No memory content, embeddings, or PII in structured logs by default
- CI uses fake embeddings — no live extraction required for unit/integration gates

---

## Architectural Invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Orchestration boundary** — Memory retrieve/inject/post-process invoked only from `ChatService` and `UnifiedChatService`; never from routers, agent core, or RAG modules directly.
- **RAG independence** — Memory and RAG are separate pre-handoff pipelines; no shared storage tables with document chunks.
- **MemoryRecord canonical** — All durable semantic memories use `memory_records`; preferences use `user_preferences`.
- **MemoryContext boundary** — Downstream prompt assembly receives `MemoryContext` via `MemoryPromptInjector` / RAG instructions only; no storage access from prompt layer.
- **Session summary reuse** — Rolling summaries persist in `session_summaries`; no parallel summary table.
- **Project scope v1** — `project_id` ≡ `chat_session_id`; cross-session project memory forbidden until projects entity ships.
- **Provider replaceability** — All storage through `MemoryProvider` Protocol; pgvector is one adapter.
- **Auth-only memory** — Guests receive no memory retrieval, extraction, or API access.
- **Flag-off parity** — `MEMORY_ENABLED=false` preserves Epic 04 behaviour on all hot paths.
- **Async persistence** — Durable memory writes never block chat/voice response delivery.
- **Explainable retrieval** — Ranking inputs logged as metrics only (no content).
- **Public APIs stable after Phase 1** — Protocol/model changes require user approval.
- **No Epic 06+ behaviour early** — Workflow memory, background job queue, OTel memory spans, RBAC — `TODO(epic-N):` only.

---

## Acceptance Criteria

- Memory improves continuity without changing architecture.
- Prompt growth remains bounded.
- Preferences persist.
- Project knowledge is reusable.
- Retrieval remains explainable.
- Existing pipeline is unaffected when disabled.

# Part II — Execution

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement Part II phase-by-phase. Part I is frozen and is the architectural source of truth. Do not redesign architecture during implementation.

## Phase integration rules

Early phases build **subsystems in isolation** (unit/integration tests with fakes). **Chat pipeline wiring is deferred to Phase 8** except Phase 2 summary wiring which only connects `build_context_messages` + existing `_maybe_summarize` gating.

| Phase | Builds                                                           | Chat wiring             |
| ----- | ---------------------------------------------------------------- | ----------------------- |
| 1     | Models, provider scaffold, migration                             | None                    |
| 2     | Summary façade + `build_context_messages` activation             | Partial (summary only)  |
| 3     | Async durable memory extraction                                  | None (manager API only) |
| 4–5   | Preferences + project-scoped records                             | None                    |
| 6     | Semantic retrieval + `MemoryContextBuilder`                      | None                    |
| 7     | Lifecycle + REST API                                             | None                    |
| 8     | Full retrieve/inject/extract in ChatService + UnifiedChatService | **Complete**            |
| 9–10  | Frontend + release                                               | —                       |

Phases 5 and 6: Phase 5 persists project-scoped records via provider; Phase 6 implements cross-domain **retrieval** ranking (Phase 5 step "Semantic Retrieval" means provider CRUD + isolation tests only, not `SemanticRetriever`).

## Reuse Existing Components

**DO NOT REIMPLEMENT**

| Component                                                   | Location                                                        |
| ----------------------------------------------------------- | --------------------------------------------------------------- |
| `ChatService`, `build_context_messages`, `_maybe_summarize` | `app/services/chat_service.py`                                  |
| `SessionSummary`, `ChatStore` summary methods               | `app/db/models.py`, `app/db/chat.py`                            |
| `UnifiedChatService`, `stream_execute()`, `execute()`       | `app/services/unified_chat_service.py`                          |
| RAG `PromptBuilder`                                         | `app/ai/rag/prompt_builder.py`                                  |
| `PromptManager`, chat prompt templates                      | `app/ai/prompts/`                                               |
| `EmbeddingProvider`, `create_embedding_provider`            | `app/ai/embeddings/`, `app/ai/interfaces/embedding_provider.py` |
| `PgVectorStore` (patterns only — separate tables)           | `app/ai/vectorstores/pgvector.py`                               |
| `ProviderFactory`, LLM providers                            | `app/providers/`                                                |
| RAG pipeline                                                | `app/ai/rag/`                                                   |
| Feature flag infrastructure                                 | `app/core/config.py`                                            |
| DI factories                                                | `app/ai/deps.py`                                                |
| Voice → chat bridge                                         | `app/ai/voice/chat_bridge.py`                                   |

Memory is additive. Existing chat, RAG, agent runtime, MCP and streaming paths must remain unchanged when `MEMORY_ENABLED=false`.

Disabling MEMORY_ENABLED prevents new Memory operations from starting. In-flight asynchronous persistence operations may complete normally.

---

## Not Allowed

- Bypass `ChatService` / `UnifiedChatService` for memory orchestration
- Duplicate `session_summaries` or reimplement rolling summary storage
- Couple Memory and RAG into one pipeline or share document chunk tables
- Allow storage providers to leak outside `MemoryProvider`
- Allow prompt/injection layer to access storage directly
- Implement future System Memory or standalone projects entity
- Implement workflow memory, organization memory, inferred preferences, or guest memory
- Break feature-flag parity

---

## Baseline

_Copied from Epic 04 Phase 11 completion record (update in Phase 0 audit)._

| Area                     | State                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------------ |
| Backend tests / coverage | **1076 passed**, **89.52%** `app/` (Epic 04)                                                     |
| Voice package coverage   | **93%** `app/ai/voice/`                                                                          |
| Eval CLI                 | **5/5 passed**                                                                                   |
| Frontend tests           | **233 passed** (39 files); build pass                                                            |
| Chat pipeline            | Stable — `ChatService` + `UnifiedChatService`                                                    |
| Voice                    | Completed (Epic 04)                                                                              |
| V1 session summaries     | **`SessionSummary` + `_maybe_summarize` exist**; `build_context_messages` not wired to live chat |
| V2 memory subsystem      | None                                                                                             |
| Durable semantic memory  | None                                                                                             |
| PgVector                 | Used for **document chunks** only; memory tables not yet created                                 |

---

## Phase Status

| Phase | Name                           | Effort | Status      |
| ----- | ------------------------------ | ------ | ----------- |
| 0     | Baseline Audit                 | XS     | Completed   |
| 1     | Models, Interfaces & Migration | L      | Completed   |
| 2     | Rolling Conversation Summary   | M      | Completed   |
| 3     | Long-Term Memory               | L      | Completed   |
| 4     | User Preferences               | M      | Completed   |
| 5     | Project Memory                 | M      | Completed   |
| 6     | Semantic Retrieval             | L      | Completed   |
| 7     | Lifecycle & REST API           | L      | Completed   |
| 8     | Chat Pipeline Integration      | XL     | Not Started |
| 9     | Frontend Controls              | S      | Not Started |
| 10    | Validation & Release           | M      | Not Started |

---

# Phase 0 — Baseline Audit

**Effort:** XS

**Objective**

Establish a verified implementation baseline before introducing the Memory subsystem. Confirm that the existing platform is stable, all architectural dependencies are understood, and the execution environment satisfies the assumptions defined in Part I. This phase produces the reference baseline against which all subsequent implementation and regression testing will be measured.

**Deliverables**

- `docs/audits/post-mvp-v2-epic5-phase-0-baseline-audit.md`
- Architecture inventory
- Dependency verification
- Feature flag verification
- Platform readiness assessment
- Baseline quality metrics
- Implementation readiness checklist

**Steps**

### Platform Verification

- [x] Confirm Epic 04 Phase 11 complete / authorized for Epic 05
- [x] Inventory V1 `SessionSummary`, `_maybe_summarize`, `build_context_messages` wiring status
- [x] Inventory chat routing split (`ChatService` vs `UnifiedChatService`)
- [x] Verify pgvector availability and document-chunk schema (separate from memory)
- [x] Verify Voice integration remains operational.
- [x] Verify RAG integration remains operational.
- [x] Verify MCP integration remains operational.
- [x] Verify Tool execution remains operational.
- [x] Verify streaming responses remain operational.

### Architecture Review

- [x] Review the frozen Part I architecture.
- [x] Verify all architectural invariants.
- [x] Identify all Memory integration points.
- [x] Identify existing extension points.
- [x] Confirm no Memory implementation already exists.
- [x] Record implementation assumptions.

### Dependency Verification

- [x] Verify pgvector availability.
- [x] Verify PostgreSQL configuration.
- [x] Verify embedding provider configuration.
- [x] Verify existing provider abstractions.
- [x] Verify dependency injection configuration.
- [x] Verify feature flag infrastructure.

### Codebase Inventory

- [x] Inventory existing chat services.
- [x] Inventory PromptBuilder implementation.
- [x] Inventory provider implementations.
- [x] Inventory embedding services.
- [x] Inventory RAG pipeline.
- [x] Inventory existing persistence services.
- [x] Record components to be reused.

### Baseline Quality Validation

- [x] Execute lint.
- [x] Execute type checking.
- [x] Execute unit tests.
- [x] Execute integration tests.
- [x] Execute evaluation suite.
- [x] Record baseline quality metrics.

### Implementation Readiness

- [x] Confirm all required dependencies are available.
- [x] Confirm implementation order matches Part II.
- [x] Confirm no architectural conflicts exist.
- [x] Publish baseline audit document.
- [x] Freeze implementation baseline.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`

Additional verification:

- [x] Chat functionality verified.
- [x] Voice functionality verified.
- [x] RAG functionality verified.
- [x] MCP functionality verified.
- [x] Streaming functionality verified.
- [x] All quality gates pass.

**Acceptance**

- Existing platform is fully operational.
- All architectural assumptions have been verified.
- Required dependencies are available.
- Existing extension points have been identified.
- No implementation blockers remain.
- Baseline metrics have been recorded.
- Repository is ready for Memory implementation.

**Exit Criteria**

- Baseline audit completed.
- Platform readiness confirmed.
- Quality gates passed.
- Architecture verified.
- Implementation baseline frozen.
- User confirmation pending to proceed to Phase 1 (see audit exit criteria).

**Rollback**

- [x] No rollback required.
- [x] This phase introduces no functional code changes.

**Completion Record**

| Metric                   | Result                                                       |
| ------------------------ | ------------------------------------------------------------ |
| Lint                     | ✅ PASS                                                      |
| Typecheck                | ✅ PASS                                                      |
| Unit Tests               | ✅ 1079 passed                                               |
| Integration Tests        | ✅ (included in suite)                                       |
| Evaluation Suite         | ✅ 5/5 passed                                                |
| Platform Readiness       | ✅ Confirmed                                                 |
| Baseline Audit Published | ✅ `docs/audits/post-mvp-v2-epic5-phase-0-baseline-audit.md` |

---

# Phase 1 — Models, Interfaces & Migration

**Effort:** L

**Objective**

Establish the complete Memory domain foundation by implementing the canonical data models, provider contracts, lifecycle definitions, orchestration interfaces, and **Alembic migration** defined in Part I. This phase freezes the public Memory API and provides the stable foundation for all subsequent phases.

**Deliverables**

- Canonical `MemoryRecord`
- Canonical `MemoryContext`
- `MemoryProvider` interface
- `PgVectorMemoryProvider` implementation scaffold
- `MemoryManager`
- **`memory_records` + `user_preferences` Alembic migration**
- ORM models (`MemoryRecord`, `UserPreference`)
- Lifecycle enums and state definitions
- Shared model validation
- Initial public API freeze
- Unit test suite

Memory events use provider-independent domain event abstractions. Event payload schemas are implementation details and are defined during lifecycle implementation.

**Steps**

### Package Structure

- [x] Create the `app/ai/memory/` package.
- [x] Create the package layout defined in Part I.
- [x] Add package exports through `__init__.py`.
- [x] Verify package imports are dependency-cycle free.

### Canonical Models

- [x] Implement the canonical `MemoryRecord` model.
- [x] Implement the canonical `MemoryContext` model.
- [x] Implement supporting enums and value objects.
- [x] Add schema validation.
- [x] Add serialization/deserialization support.
- [x] Add comprehensive model documentation.

### Lifecycle Definitions

- [x] Implement lifecycle state enums.
- [x] Define valid lifecycle transitions.
- [x] Validate illegal state transitions.
- [x] Keep lifecycle implementation independent of storage.

### Provider Contracts

- [x] Create the `MemoryProvider` abstraction.
- [x] Define provider CRUD operations.
- [x] Define semantic retrieval operations.
- [x] Define preference persistence operations.
- [x] Define lifecycle update operations.
- [x] Ensure interface remains provider-agnostic.

### PgVector Provider Scaffold

- [x] Create `PgVectorMemoryProvider`.
- [x] Implement constructor and dependency injection.
- [x] Define placeholder implementations for provider methods.
- [x] Do not implement retrieval logic yet (Phase 6).
- [x] Do not implement persistence logic yet (Phase 3).

### Memory Manager

- [x] Implement `MemoryManager`.
- [x] Inject `MemoryProvider`.
- [x] Define orchestration entry points.
- [x] Ensure business logic remains outside storage providers.
- [x] Expose only the APIs required by `UnifiedChatService`.

### Database Migration

- [x] Add `MemoryRecord` and `UserPreference` ORM models to `app/db/models.py`.
- [x] Create Alembic migration `0006_memory_tables` per Part I schema.
- [x] Add HNSW index on `memory_records.embedding` (cosine, same dimensions as RAG).
- [x] Verify migration is independent of `document_chunks` table.
- [x] Add migration rollback test in CI (upgrade/downgrade smoke).

### Configuration

- [x] Add `MEMORY_ENABLED` feature flag.
- [x] Add Memory configuration section.
- [x] Register provider configuration.
- [x] Preserve backward compatibility when disabled.

### Testing

- [x] Add model validation tests.
- [x] Add serialization tests.
- [x] Add provider contract tests.
- [x] Add dependency injection tests.
- [x] Add lifecycle validation tests.
- [x] Add package import tests.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`

Additional verification:

- [x] All Memory models serialize correctly.
- [x] Provider interfaces compile successfully.
- [x] Dependency injection resolves successfully.
- [x] No circular imports detected.
- [x] Feature flag defaults to disabled.

**Acceptance**

- Canonical models exactly match the frozen Part I architecture.
- Public APIs are stable and provider-independent.
- Storage implementation details remain hidden behind `MemoryProvider`.
- `MemoryManager` becomes the single orchestration entry point for the Memory subsystem.
- No retrieval or persistence logic is implemented before their respective phases.
- Existing application behaviour remains unchanged with `MEMORY_ENABLED=false`.

**Exit Criteria**

- All model tests pass.
- All interface tests pass.
- All quality gates pass.
- Public APIs frozen.
- Ready to begin Phase 2 without further structural changes.

**Rollback**

- [ ] Remove `app/ai/memory/` package.
- [ ] Remove feature flag additions.
- [ ] Remove dependency registration.
- [ ] Verify application builds successfully without Memory components.

**Completion Record**

| Metric               | Result                                                                 |
| --------------------- | ---------------------------------------------------------------------- |
| Models implemented   | `MemoryRecord`, `MemoryContext`, `MemoryType`, `MemoryScope`, `LifecycleState` |
| Provider interfaces  | `MemoryProvider` (Protocol) + `PgVectorMemoryProvider` scaffold        |
| Lifecycle enums      | 5 states, transition table, `validate_transition()`                   |
| Unit tests           | ✅ 78 new/updated tests (1157 total passed; +78 vs Phase 0 audit baseline of 1079 — Epic 04 table shows 1076 pre-audit) |
| Coverage             | ✅ 89.77% `app/` (100% on `app/ai/memory/`)                            |
| API freeze completed | ✅ `app/ai/memory/__init__.py` exports frozen Phase 1 surface          |

---

# Phase 2 — Rolling Conversation Summary

**Effort:** M

**Objective**

Extend the existing V1 `SessionSummary` subsystem via `ConversationSummaryService`. Wire `build_context_messages()` into persisted chat paths when `MEMORY_ENABLED=true`. Do **not** create parallel summary storage or replace `_maybe_summarize` — wrap and gate existing behaviour.

**Deliverables**

- `ConversationSummaryService`
- Rolling Conversation Summary
- Summary persistence
- Summary retrieval
- ChatService summary wiring (`build_context_messages`)
- Prompt context optimization
- Integration test suite

**Steps**

### Summary Service

- [x] Implement `ConversationSummaryService` as façade over `ChatStore` summary methods.
- [x] Reuse existing `chat/summarize_*` and `chat/context_summary_prefix` templates.
- [x] Delegate generation to `ChatService._maybe_summarize` (do not duplicate LLM call logic).
- [x] Document flag-off vs flag-on `_maybe_summarize` behaviour in Phase 0 audit (flag off: unchanged; flag on: via ConversationSummaryService).

### Summary Persistence

- [x] Persist via existing `session_summaries` table only — no new summary table.
- [x] Associate summaries with chat sessions (existing FK).
- [x] Maintain version / `covers_through_seq` semantics unchanged.

### Summary Retrieval

- [x] Retrieve latest summary via `ChatStore.get_latest_summary`.
- [x] Expose through canonical `MemoryContext.conversation_summary`.
- [x] Handle missing summaries gracefully (empty context).

### Prompt Context Optimization

- [x] Wire `ChatService.build_context_messages` into `complete_chat` and `stream_chat` when flag on + persistence active.
- [x] Replace unbounded client `request.messages` with summary + tail for persisted sessions.
- [x] Preserve guest/non-persisted behaviour (client messages unchanged).

### Chat Integration (partial — summary only)

- [x] Invoke summary retrieval before LLM in `ChatService` (flag on only).
- [x] Do **not** wire durable memory retrieval until Phase 8.
- [x] Ensure UnifiedChatService benefits via shared `ChatService` paths where applicable.

### Error Handling

- [x] Handle summary generation failures gracefully.
- [x] Handle persistence failures gracefully.
- [x] Continue chat execution when summaries are unavailable.
- [x] Log operational failures without exposing conversation contents.

### Testing

- [x] Add summary generation tests.
- [x] Add summary persistence tests.
- [x] Add summary retrieval tests.
- [x] Add integration tests.
- [x] Add prompt growth regression tests.
- [x] Add failure recovery tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] Rolling Conversation Summaries generated successfully.
- [x] Rolling Conversation Summaries update correctly.
- [x] Summaries persist successfully.
- [x] Prompt growth remains bounded.
- [x] Existing chat behaviour remains unchanged.
- [x] Feature flag regression passes.

**Acceptance**

- Rolling Conversation Summaries are generated automatically after assistant responses.
- Summaries remain independent from durable memories.
- Prompt growth is reduced without losing important conversational context.
- Summaries integrate into the Memory subsystem through `MemoryContext`.
- Existing chat behaviour remains unchanged when `MEMORY_ENABLED=false`.
- Summary failures never interrupt chat execution.

**Exit Criteria**

- Conversation Summary subsystem operational (via existing `session_summaries`).
- `build_context_messages` wired for flag-on persisted chat.
- Ready for Long-Term Memory implementation (Phase 3).

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Disable ConversationSummaryService.
- [ ] Remove summary injection into `MemoryContext`.
- [ ] Verify existing prompt construction remains unchanged.

**Completion Record**

| Metric                  | Result                                                                 |
| ----------------------- | ---------------------------------------------------------------------- |
| Summary generation      | ✅ `ConversationSummaryService` delegates to `_maybe_summarize`       |
| Summary persistence     | ✅ Reuses existing `session_summaries` (no parallel table)             |
| Prompt optimization     | ✅ `build_context_messages` wired in `ChatService` when flag on         |
| Integration tests       | ✅ `tests/ai/memory/test_summarizer.py`                                |
| Feature flag regression | ✅ Guest/non-persisted paths unchanged when flag off                   |
| Coverage                | ✅ Verified at Phase 7 regression (1278 tests, 89.31% `app/`)          |

---

# Phase 3 — Long-Term Memory

**Effort:** L

**Objective**

Implement the durable memory creation pipeline that identifies meaningful information from conversations, evaluates memory quality, generates semantic embeddings, and persists approved memories asynchronously through the `MemoryProvider`. This phase establishes long-term memory while ensuring the existing chat experience remains unaffected.

Initial implementation executes asynchronous persistence using lightweight in-process background execution (e.g. asyncio.create_task or equivalent). Future Background Jobs infrastructure will replace the execution mechanism without changing MemoryManager or MemoryProvider interfaces.

**Deliverables**

- Durable memory extraction pipeline
- Candidate memory extraction
- MemoryQualityEvaluator
- Embedding generation
- Asynchronous persistence pipeline
- Memory lifecycle event publication
- Error handling and retry strategy
- Integration test suite

**Steps**

### Memory Extraction Pipeline

- [x] Implement the durable memory extraction pipeline.
- [x] Execute extraction after successful assistant responses.
- [x] Extract candidate memories from completed conversations.
- [x] Ensure extraction operates independently of prompt construction.
- [x] Keep extraction isolated from the synchronous chat response path.

### Candidate Memory Extraction

- [x] Identify durable facts from conversation history.
- [x] Extract user-specific knowledge.
- [x] Extract project-specific knowledge.
- [x] Ignore temporary conversational context.
- [x] Ignore short-lived or low-value information.
- [x] Produce normalized candidate memory objects.

### MemoryQualityEvaluator

- [x] Implement MemoryQualityEvaluator.
- [x] Score candidate memories against acceptance criteria.
- [x] Reject duplicate memories.
- [x] Reject low-confidence memories.
- [x] Reject transient or session-only information.
- [x] Produce only approved memories for persistence.

### Embedding Generation

- [x] Generate semantic embeddings for approved memories.
- [x] Reuse the existing `EmbeddingProvider`.
- [x] Avoid embedding generation for structured preferences.
- [x] Validate embedding generation failures.
- [x] Prevent embedding failures from interrupting chat execution.

### Persistence

- [x] Persist approved memories through `MemoryProvider`.
- [x] Ensure storage remains provider-independent.
- [x] Associate owner, project and lifecycle metadata.
- [x] Record creation timestamps.
- [x] Record embedding metadata.
- [x] Validate successful persistence.

### Asynchronous Processing

- [x] Execute persistence asynchronously.
- [x] Prevent persistence latency from delaying user responses.
- [x] Queue persistence operations when appropriate.
- [x] Isolate persistence failures from chat execution.
- [x] Ensure retryable failures are recoverable.

### Lifecycle Integration

- [x] Initialize newly created memories with the correct lifecycle state.
- [x] Publish memory creation events.
- [x] Register lifecycle metadata.
- [x] Preserve compatibility with future lifecycle management.

### Error Handling

- [x] Handle extraction failures gracefully.
- [x] Handle embedding failures gracefully.
- [x] Handle provider failures gracefully.
- [x] Log operational failures without exposing memory contents.
- [x] Preserve existing chat behaviour during all failure scenarios.

### Testing

- [x] Add candidate extraction tests.
- [x] Add quality evaluation tests.
- [x] Add embedding integration tests.
- [x] Add persistence tests.
- [x] Add asynchronous execution tests.
- [x] Add failure recovery tests.
- [x] Add provider integration tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] Candidate memories generated correctly.
- [x] Quality evaluation rejects low-value memories.
- [x] Approved memories generate embeddings successfully.
- [x] Memories persist through `MemoryProvider`.
- [x] Persistence executes asynchronously.
- [x] Chat latency remains unaffected.
- [x] Failure scenarios do not interrupt chat responses.

**Acceptance**

- Durable memories are created automatically after conversations.
- Only approved memories are persisted.
- Embeddings are generated through the existing embedding abstraction.
- Persistence remains completely provider-agnostic.
- Chat responses never wait for persistence completion.
- Extraction failures do not impact the user experience.
- Existing platform behaviour remains unchanged when `MEMORY_ENABLED=false`.

**Exit Criteria**

- Durable memory pipeline operational.
- Asynchronous persistence verified.
- Provider integration verified.
- Quality evaluation validated.
- Ready for User Preferences implementation (Phase 4).

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Disable durable memory extraction.
- [ ] Disable asynchronous persistence.
- [ ] Disable embedding generation.
- [ ] Verify existing chat pipeline remains unchanged.

**Completion Record**

| Metric                       | Result                                                                 |
| ---------------------------- | ---------------------------------------------------------------------- |
| Candidate memories extracted | ✅ `MemoryExtractor` + `memory/extract.v1` prompt                      |
| Approved memories persisted  | ✅ Async pipeline via `extract_and_persist_async` + `PgVectorMemoryProvider` |
| Embeddings generated         | ✅ `EmbeddingProvider` with retry; failures isolated from chat         |
| Persistence latency          | ✅ Non-blocking (`background_tasks.schedule_extraction_task`)          |
| Failure recovery validated   | ✅ Extraction/embed/persist failure tests pass                         |
| Integration tests            | ✅ Provider + manager + async execution suite                          |
| Coverage                     | ✅ 89.76% `app/` (Phase 3 verified at 1157+ tests)                   |

---

# Phase 4 — User Preferences

**Effort:** M

**Objective**

Implement the User Preference subsystem to persist stable, user-specific preferences that personalize future conversations. User Preferences represent structured long-term settings rather than semantic memories and are persisted independently before being incorporated into the canonical `MemoryContext`.

**Deliverables**

- `UserPreference` ORM model + API schemas
- User preference persistence
- Preference retrieval
- Preference normalization
- MemoryContext integration
- Preference update pipeline
- Integration test suite

**Steps**

### Preference Models

- [x] Implement `UserPreference` ORM model (see Part I schema).
- [x] Define API request/response schemas for preference keys/values.
- [x] Implement validation rules.
- [x] Support serialization and deserialization.
- [x] Keep preference models independent from semantic memories.

### Preference Persistence

- [x] Extend `MemoryProvider` to support structured preference storage.
- [x] Persist preferences independently of semantic memories.
- [x] Associate preferences with the owning user.
- [x] Support preference creation.
- [x] Support preference updates.
- [x] Support preference deletion.

### Preference Retrieval

- [x] Retrieve preferences for the active user.
- [x] Validate preference availability.
- [x] Handle missing preferences gracefully.
- [x] Retrieve only active preferences.
- [x] Keep retrieval provider-independent.

### Preference Normalization

- [x] Normalize retrieved preferences.
- [x] Resolve duplicate preference values.
- [x] Preserve deterministic ordering.
- [x] Produce canonical preference objects.
- [x] Prepare preferences for `MemoryContext`.

### MemoryContext Integration

- [x] Integrate normalized preferences into `MemoryContext`.
- [x] Keep preferences separate from semantic memories.
- [x] Preserve the canonical MemoryContext structure defined in Part I.
- [x] Ensure `PromptBuilder` receives preferences only through `MemoryContext`.
- [x] Prevent direct storage access from downstream components.

### Preference Management

- [x] Support preference replacement.
- [x] Support preference removal.
- [x] Preserve consistency across updates.
- [x] Prevent duplicate preference records.
- [x] Validate preference ownership.

### Error Handling

- [x] Handle persistence failures gracefully.
- [x] Handle retrieval failures gracefully.
- [x] Continue chat execution when preferences are unavailable.
- [x] Log operational failures without exposing user preference contents.

### Testing

- [x] Add preference model tests.
- [x] Add persistence tests.
- [x] Add retrieval tests.
- [x] Add normalization tests.
- [x] Add MemoryContext integration tests.
- [x] Add provider integration tests.
- [x] Add failure recovery tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] Preferences persist successfully.
- [x] Preferences retrieve correctly.
- [x] Preferences remain separate from semantic memories.
- [x] MemoryContext contains normalized preferences.
- [x] Existing chat behaviour remains unchanged.
- [x] Feature flag regression passes.

**Acceptance**

- User preferences persist across conversations.
- Preferences remain structured rather than semantic.
- Preference retrieval remains provider-independent.
- Preferences integrate into the canonical `MemoryContext`.
- `PromptBuilder` consumes preferences only through `MemoryContext`.
- Existing platform behaviour remains unchanged when `MEMORY_ENABLED=false`.

**Exit Criteria**

- User Preference subsystem operational.
- Preference persistence validated.
- MemoryContext integration verified.
- Ready for Project Memory implementation.

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Disable preference persistence.
- [ ] Disable preference retrieval.
- [ ] Remove preference integration from `MemoryContext`.
- [ ] Verify existing chat pipeline remains unchanged.

**Completion Record**

| Metric                    | Result                                                              |
| ------------------------- | ------------------------------------------------------------------- |
| Preferences persisted     | ✅ `PgVectorMemoryProvider` CRUD on `user_preferences`              |
| Preference retrieval      | ✅ `list_preferences` / `get_preference` + normalization            |
| MemoryContext integration | ✅ `MemoryContextBuilder.with_preferences`                          |
| Provider integration      | ✅ User isolation + independence from `memory_records`              |
| Feature flag regression   | ✅ No chat wiring; `MEMORY_ENABLED=false` behaviour unchanged       |
| Coverage                  | ✅ 1210 tests passed, 89.76% `app/`; lint + typecheck clean         |

---

# Phase 5 — Project Memory

**Effort:** M

**Objective**

Implement session-scoped project memory persistence and isolation. In v1, **`project_id` = `chat_session_id`**. Persist project-scoped records via `MemoryProvider`; full semantic retrieval ranking arrives in Phase 6.

**Deliverables**

- `ProjectMemory` model
- Project memory persistence
- Project-scoped semantic retrieval
- Project isolation enforcement
- MemoryContext integration
- Ownership validation
- Integration test suite

**Steps**

### Project Memory Models

- [x] Use `MemoryRecord` with `memory_type='project'` and `session_id` set.
- [x] Map API `project_id` field to `session_id` internally.
- [x] Validate session ownership on all project memory operations.

### Project Isolation

- [x] Enforce strict session ownership boundaries (no cross-session retrieval).
- [x] Validate session identity during persistence, retrieval, and deletion.

### Provider CRUD (retrieval ranking deferred to Phase 6)

- [x] Persist project memories through `MemoryProvider`.
- [x] List/filter by `session_id` + `owner_id` in provider layer.
- [x] Add isolation tests — **do not implement `SemanticRetriever` here**.

### MemoryContext Integration

- [x] Normalize retrieved project memories.
- [x] Merge project memories into the canonical `MemoryContext`.
- [x] Preserve ordering guarantees defined in Part I.
- [x] Keep project memories logically separate from user preferences.
- [x] Prevent downstream components from accessing storage directly.

### Memory Lifecycle Integration

- [x] Register project memories with the LifecycleManager.
- [x] Initialize lifecycle state correctly.
- [x] Publish lifecycle events.
- [x] Preserve compatibility with future lifecycle transitions.

### Error Handling

- [x] Handle persistence failures gracefully.
- [x] Handle retrieval failures gracefully.
- [x] Handle project validation failures gracefully.
- [x] Continue chat execution when project memories are unavailable.
- [x] Log operational failures without exposing project memory contents.

### Testing

- [x] Add project memory model tests.
- [x] Add persistence tests.
- [x] Add provider list/filter tests (no SemanticRetriever yet).
- [x] Add project isolation tests.
- [x] Add lifecycle integration tests.
- [x] Add failure recovery tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] Project memories persist successfully.
- [x] Project memories retrieve successfully.
- [x] Cross-project retrieval is prevented.
- [x] Cross-project updates are prevented.
- [x] Cross-project deletion is prevented.
- [x] MemoryContext contains normalized project memories.
- [x] Existing chat behaviour remains unchanged.
- [x] Feature flag regression passes.

**Acceptance**

- Project memories persist independently of user preferences and Rolling Conversation Summaries.
- Project isolation is enforced for all persistence and retrieval operations.
- Retrieval remains provider-independent through `MemoryProvider`.
- Project memories integrate into the canonical `MemoryContext`.
- Existing platform behaviour remains unchanged when `MEMORY_ENABLED=false`.
- Project memory failures never interrupt chat execution.

**Exit Criteria**

- Project Memory persistence and isolation validated.
- Ready for Semantic Retrieval (Phase 6).

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Disable project memory persistence.
- [ ] Disable project memory retrieval.
- [ ] Remove project memory integration from `MemoryContext`.
- [ ] Verify existing chat pipeline remains unchanged.

**Completion Record**

| Metric                      | Result                                                              |
| --------------------------- | ------------------------------------------------------------------- |
| Project memories persisted  | ✅ `MemoryType.PROJECT` records scoped via `session_id`             |
| Project memory retrieval    | ✅ `MemoryManager.list/search/get_project_*` + provider filters     |
| Project isolation validated | ✅ Session ownership + cross-session move rejected                |
| MemoryContext integration   | ✅ `MemoryContextBuilder.with_project_memories`                     |
| Provider integration        | ✅ `PgVectorMemoryProvider` session-scoped CRUD/search              |
| Feature flag regression     | ✅ No chat wiring; flag-off behaviour unchanged                     |
| Coverage                    | ✅ `test_project.py`, `test_project_integration.py`               |

---

# Phase 6 — Semantic Retrieval

**Effort:** L

**Objective**

Implement the semantic retrieval pipeline that transforms the current conversation into a semantic query, retrieves relevant memories across supported memory domains, ranks and filters results, enforces prompt token budgets, and produces the canonical `MemoryContext` consumed by `PromptBuilder`.

**Deliverables**

- `SemanticRetriever`
- Query embedding generation
- Multi-domain retrieval
- Semantic ranking
- Deduplication
- Quality filtering
- Token budget allocation
- `MemoryContextBuilder` integration
- Retrieval benchmark suite
- Integration test suite

**Steps**

### SemanticRetriever

- [x] Implement `semantic_retriever.py`.
- [x] Register `SemanticRetriever` within the Memory subsystem.
- [x] Inject `MemoryProvider`.
- [x] Inject the existing `EmbeddingProvider`.
- [x] Keep retrieval logic provider-independent.

### Query Preparation

- [x] Build the semantic retrieval query from the current conversation.
- [x] Incorporate conversation summary when available.
- [x] Normalize retrieval inputs.
- [x] Generate semantic query embeddings.
- [x] Handle embedding failures gracefully.

### Multi-Domain Retrieval

- [x] Retrieve Conversation Memory.
- [x] Retrieve User Memory.
- [x] Retrieve Project Memory.
- [x] Exclude inactive lifecycle states.
- [x] Exclude deleted memories.
- [x] Respect project ownership boundaries.
- [x] Respect user ownership boundaries.

### Semantic Ranking

- [x] Rank retrieved memories by semantic similarity.
- [x] Apply provider-independent ranking.
- [x] Prioritize higher-confidence memories.
- [x] Prioritize more relevant memories.
- [x] Preserve deterministic ordering for equivalent scores.

### Deduplication

- [x] Remove duplicate semantic results.
- [x] Merge overlapping memories where appropriate.
- [x] Eliminate redundant context.
- [x] Preserve the highest-quality memory instance.

### Quality Filtering

- [x] Apply minimum quality thresholds.
- [x] Remove obsolete memories.
- [x] Remove archived memories.
- [x] Remove low-confidence memories.
- [x] Remove retrieval noise.

### Token Budget Management

- [x] Allocate memory token budget.
- [x] Prioritize higher-ranked memories.
- [x] Prevent prompt overflow.
- [x] Preserve deterministic truncation.
- [x] Record token allocation metrics.

### MemoryContext Construction

- [x] Normalize retrieved memories.
- [x] Build canonical `MemoryContext`.
- [x] Preserve Part I ordering guarantees.
- [x] Return only `MemoryContext`.
- [x] Prevent downstream storage access.

### Performance Optimization

- [x] Minimize retrieval latency.
- [x] Avoid duplicate provider calls.
- [x] Cache intermediate computations where appropriate.
- [x] Keep retrieval independent from persistence.

### Testing

- [x] Add semantic retrieval tests.
- [x] Add ranking tests.
- [x] Add deduplication tests.
- [x] Add quality filter tests.
- [x] Add token budget tests.
- [x] Add ownership isolation tests.
- [x] Add provider integration tests.
- [x] Add retrieval benchmark tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] Semantic queries generate embeddings successfully.
- [x] Retrieval returns memories from all supported domains.
- [x] Deleted memories are never returned.
- [x] Archived memories are excluded.
- [x] Ranking remains deterministic.
- [x] Deduplication removes redundant memories.
- [x] Token budget limits are enforced.
- [x] `MemoryContext` is generated successfully.

**Acceptance**

- Retrieval remains completely provider-independent.
- Retrieval is semantic-first as defined in Part I.
- All supported memory domains participate in retrieval.
- Only active, high-quality memories are returned.
- Prompt size remains bounded through deterministic token budgeting.
- `PromptBuilder` receives only the canonical `MemoryContext`.
- No storage implementation details leak beyond `MemoryProvider`.

**Exit Criteria**

- Semantic retrieval fully operational.
- Multi-domain retrieval verified.
- Ranking and filtering validated.
- Token budgeting validated.
- Ready for Chat Pipeline Integration (Phase 8).

**Rollback**

- [ ] Disable semantic retrieval.
- [ ] Disable `MemoryContext` construction.
- [ ] Remove retrieval integration.
- [ ] Verify existing prompt pipeline remains unchanged.

**Completion Record**

| Metric                  | Result                                                              |
| ----------------------- | ------------------------------------------------------------------- |
| Retrieval latency       | ✅ Pre-response retrieval; embedding retry with graceful fallback   |
| Memories retrieved      | ✅ User + project domains via `SemanticRetriever.retrieve`          |
| Ranking validated       | ✅ Similarity + confidence/quality rank scoring                     |
| Deduplication validated | ✅ Cosine dedupe at configured threshold                            |
| Token budget validated  | ✅ `TokenBudgetAllocator` caps injected memory block                |
| Integration tests       | ✅ `tests/ai/memory/test_semantic_retriever.py`                     |
| Coverage                | ✅ `MemoryManager.retrieve_context` + context builder integration   |

---

# Phase 7 — Lifecycle & REST API

**Effort:** L

**Objective**

Implement lifecycle management (`LifecycleManager`, `MemoryPolicyEngine`) and the authenticated **Memory REST API** defined in Part I. Lifecycle processing operates independently of chat execution. REST API enables Phase 9 frontend controls.

Consolidation heuristics (similarity thresholds, duplicate detection, merge strategies and quality scoring formulas) are implementation policies resolved during Phase 7. They are intentionally not part of the frozen architecture.

**Deliverables**

- `LifecycleManager`
- `MemoryPolicyEngine`
- Lifecycle state machine
- Lifecycle transition processing
- Retention policy enforcement
- Memory archival
- Memory deletion
- Lifecycle event publication
- **Memory REST API** (`app/routers/memory.py`)
- **`memory_enabled` health field**
- Integration test suite

**Steps**

### Lifecycle State Machine

- [x] Implement the `LifecycleManager`.
- [x] Define the lifecycle state machine.
- [x] Support the canonical lifecycle states defined in Part I.
- [x] Validate legal lifecycle transitions.
- [x] Reject invalid state transitions.
- [x] Keep lifecycle management provider-independent.

### MemoryPolicyEngine

- [x] Implement the `MemoryPolicyEngine`.
- [x] Evaluate lifecycle policies.
- [x] Determine transition eligibility.
- [x] Apply retention rules.
- [x] Apply archival rules.
- [x] Keep policy evaluation deterministic.

### Lifecycle Transitions

- [x] Support Created → Active transition.
- [x] Support Active → Consolidated transition.
- [x] Support Consolidated → Archived transition.
- [x] Support Archived → Deleted transition.
- [x] Publish lifecycle transition events.
- [x] Record lifecycle metadata.

### Memory Consolidation

- [x] Consolidate related memories where appropriate.
- [x] Eliminate redundant memories.
- [x] Preserve the highest-quality memory representation.
- [x] Maintain semantic integrity after consolidation.
- [x] Update lifecycle state accordingly.

### Retention & Archiving

- [x] Apply retention policies.
- [x] Archive obsolete memories.
- [x] Preserve archived memories for administrative purposes.
- [x] Exclude archived memories from semantic retrieval.
- [x] Record archival timestamps.

### Memory Deletion

- [x] Support explicit memory deletion via REST API.
- [x] Support lifecycle-driven deletion.
- [x] Remove deleted memories from retrieval.
- [x] Preserve deletion audit metadata where appropriate.
- [x] Ensure deletion remains provider-independent.

### REST API

- [x] Create `app/schemas/memory.py` request/response models (no embeddings/scores exposed).
- [x] Create `app/routers/memory.py` with Part I endpoints.
- [x] Gate router on `MEMORY_ENABLED`; return `503 feature_disabled` when off.
- [x] Enforce authenticated caller on all routes; deny guests.
- [x] Add `DELETE /api/memory/sessions/{session_id}/summary` (clear rolling summary).
- [x] Extend `GET /api/health` with `memory_enabled`.
- [x] Register router in `app/main.py` when flag on.

### Lifecycle Integration

- [x] Integrate lifecycle management with `MemoryManager`.
- [x] Integrate lifecycle processing with asynchronous persistence.
- [x] Preserve compatibility with `SemanticRetriever`.
- [x] Preserve compatibility with `MemoryContextBuilder`.
- [x] Ensure lifecycle processing remains independent of prompt construction.

### Error Handling

- [x] Handle lifecycle processing failures gracefully.
- [x] Handle policy evaluation failures gracefully.
- [x] Handle archival failures gracefully.
- [x] Handle deletion failures gracefully.
- [x] Continue chat execution during lifecycle failures.
- [x] Log operational failures without exposing memory contents.

### Testing

- [x] Add lifecycle state transition tests.
- [x] Add MemoryQualityEvaluator tests.
- [x] Add consolidation tests.
- [x] Add archival tests.
- [x] Add deletion tests.
- [x] Add provider integration tests.
- [x] Add lifecycle event tests.
- [x] Add REST API router tests (`tests/test_memory_router.py`).
- [x] Add failure recovery tests.

**Verify**

- `make test-cov`

Additional verification:

- [x] All lifecycle transitions execute correctly.
- [x] Invalid transitions are rejected.
- [x] Consolidated memories remain retrievable.
- [x] Archived memories are excluded from retrieval.
- [x] Deleted memories are never returned.
- [x] MemoryQualityEvaluator behaves deterministically.
- [x] Existing chat behaviour remains unchanged.
- [x] Feature flag regression passes.

**Acceptance**

- Lifecycle implementation exactly matches the canonical lifecycle defined in Part I.
- Memory transitions are deterministic and provider-independent.
- Retention and archival policies are enforced consistently.
- Deleted memories are permanently excluded from semantic retrieval.
- Lifecycle processing remains independent of prompt construction and chat execution.
- Existing platform behaviour remains unchanged when `MEMORY_ENABLED=false`.

**Exit Criteria**

- LifecycleManager operational.
- MemoryPolicyEngine validated.
- Memory REST API operational.
- Ready for Chat Pipeline Integration (Phase 8).

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Disable lifecycle processing.
- [ ] Disable policy evaluation.
- [ ] Disable consolidation and archival.
- [ ] Verify existing chat pipeline remains unchanged.

**Completion Record**

| Metric                  | Result                                                              |
| ----------------------- | ------------------------------------------------------------------- |
| Lifecycle transitions   | ✅ `LifecycleManager` + canonical transition table                  |
| Policy evaluation       | ✅ `MemoryPolicyEngine` (consolidation, archival, retention)        |
| Memory consolidation    | ✅ Dedupe clusters; highest-quality winner retained                 |
| Archival validation     | ✅ Consolidated → archived; excluded from semantic search           |
| Deletion validation     | ✅ REST soft-delete + retention-driven permanent deletion          |
| Provider integration    | ✅ `update_lifecycle_state`, `delete_record`, `list_records`        |
| Feature flag regression | ✅ Router mounted when flag on; `503 feature_disabled` when off   |
| Coverage                | ✅ 1278 tests passed, 89.31% `app/`; lint + typecheck clean         |

---

# Phase 8 — Chat Pipeline Integration

**Effort:** XL

**Objective**

Integrate the complete Memory subsystem into **`ChatService` and `UnifiedChatService`** while preserving all architectural constraints defined in Part I. Memory must function as an additive capability that enriches prompt construction without modifying RAG, provider, streaming, tool, or voice execution internals.

**Deliverables**

- `ChatService` + `UnifiedChatService` memory hooks
- `MemoryPromptInjector` integration
- End-to-end Memory orchestration (retrieve → inject → async extract)
- RAG `instructions` memory block (optional)
- Asynchronous persistence integration
- Feature flag integration
- End-to-end integration tests
- Regression test suite

**Steps**

### Chat Pipeline Integration

- [ ] Integrate `MemoryManager` into `ChatService.complete_chat` and `ChatService.stream_chat`.
- [ ] Integrate same hooks into `UnifiedChatService.execute` and `stream_execute`.
- [ ] Extract shared helper (e.g. `_apply_memory_context`) to avoid duplication.
- [ ] Preserve existing chat execution pipeline structure.
- [ ] Preserve compatibility with Voice mode (via UnifiedChatService).
- [ ] Preserve compatibility with MCP integration.
- [ ] Preserve compatibility with Tool execution.
- [ ] Preserve compatibility with the RAG pipeline.

### Memory Retrieval (pre-response)

- [ ] Invoke `MemoryManager.retrieve_context` before LLM call (authenticated + flag on).
- [ ] Invoke `SemanticRetriever` for user + project domains.
- [ ] Load preferences from `user_preferences`.
- [ ] Include conversation summary from Phase 2.
- [ ] Build canonical `MemoryContext` via `MemoryContextBuilder`.
- [ ] Respect lifecycle filtering and ownership boundaries.

### Prompt Construction

- [ ] Inject via `MemoryPromptInjector` + new `chat/memory_context/v1` template.
- [ ] For RAG/unified document path: pass memory block as RAG `PromptBuilder` `instructions`.
- [ ] Ensure injection layer performs no storage operations.
- [ ] Verify MemoryContext ordering matches Part I.

### AI Execution

- [ ] Execute existing provider/agent/RAG/tool pipelines unchanged after injection.
- [ ] Preserve streaming first-delta latency (retrieval completes before first delta).

### Post-Response Processing

- [ ] Invoke durable memory extraction asynchronously (`asyncio.create_task`).
- [ ] Evaluate candidates via `MemoryQualityEvaluator`.
- [ ] Persist approved memories; publish lifecycle events.
- [ ] Trigger existing `_maybe_summarize` (Phase 2 gating).
- [ ] Ensure post-processing never delays the response to the user.

### Feature Flag Integration

- [ ] Respect `MEMORY_ENABLED=false` on all hooks.
- [ ] Skip retrieve/inject/extract when disabled.
- [ ] Verify runtime feature flag switching.

### Failure Isolation

- [ ] Isolate retrieval, persistence, and embedding failures.
- [ ] Continue chat execution during Memory failures.
- [ ] Log operational failures without exposing memory contents.

### Performance Validation

- [ ] Measure retrieval latency.
- [ ] Measure prompt construction overhead.
- [ ] Measure asynchronous persistence latency.
- [ ] Verify streaming startup latency.

### Testing

- [ ] Add ChatService integration tests (plain path).
- [ ] Add UnifiedChatService integration tests (RAG/web-search/agent path).
- [ ] Add feature flag regression tests.
- [ ] Add end-to-end chat tests.
- [ ] Add streaming regression tests.
- [ ] Add RAG integration tests.
- [ ] Add provider compatibility tests.
- [ ] Add MCP compatibility tests.
- [ ] Add Voice compatibility tests.
- [ ] Add failure recovery tests.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`

Additional verification:

- [ ] Chat execution succeeds with Memory enabled.
- [ ] Chat execution succeeds with Memory disabled.
- [ ] PromptBuilder consumes memory only via `MemoryPromptInjector` / RAG `instructions`.
- [ ] `ChatService` and `UnifiedChatService` both orchestrate memory when flag on.
- [ ] Streaming behaviour remains unchanged.
- [ ] Tool execution remains unchanged.
- [ ] Voice mode remains unchanged.
- [ ] MCP integration remains unchanged.
- [ ] Existing regression suite passes.

**Acceptance**

- `ChatService` and `UnifiedChatService` remain the sole orchestration boundaries.
- Memory integrates without introducing alternative execution paths.
- PromptBuilder never accesses storage directly; memory injected via `MemoryPromptInjector`.
- RAG and Memory remain independent pipelines.
- Memory persistence executes asynchronously.
- Memory failures never interrupt chat execution.
- Existing platform behaviour remains unchanged when `MEMORY_ENABLED=false`.
- All architectural invariants defined in Part I are preserved.

**Exit Criteria**

- Memory fully integrated into the production chat pipeline.
- End-to-end integration validated.
- Feature flag regression validated.
- Performance validation completed.
- Ready for frontend controls and production validation.

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Remove `MemoryManager` integration.
- [ ] Disable Memory retrieval.
- [ ] Disable asynchronous persistence.
- [ ] Verify existing chat pipeline remains unchanged.
- [ ] Verify Voice, MCP, RAG, Tools and Streaming continue to function normally.

**Completion Record**

| Metric                  | Result |
| ----------------------- | ------ |
| End-to-end integration  |        |
| Feature flag regression |        |
| Chat latency            |        |
| Retrieval latency       |        |
| Streaming regression    |        |
| RAG regression          |        |
| Voice regression        |        |
| MCP regression          |        |
| Integration tests       |        |
| Coverage                |        |

---

# Phase 9 — Frontend Controls

**Effort:** S

**Objective**

Implement the frontend user interface for managing the Memory subsystem. Users should be able to view, manage, and control their long-term memory settings while preserving the existing chat experience. The frontend communicates exclusively through the Memory API and must never access storage or internal Memory components directly.

Users should not see semantic scores, confidence scores, lifecycle state, embeddings, etc.

Only human-facing memory content.

**Deliverables**

- Memory Settings UI
- User Preference management
- Memory management interface
- Conversation summary controls
- Memory deletion workflow
- Frontend API integration
- Integration test suite

**Steps**

### Settings UI

- [ ] Add a dedicated Memory section to the authenticated user settings.
- [ ] Display Memory feature availability.
- [ ] Display current Memory status.
- [ ] Organize Memory settings for usability.
- [ ] Preserve consistency with the existing application UI.

### User Preferences

- [ ] Display stored user preferences.
- [ ] Allow users to update preferences.
- [ ] Allow users to remove preferences.
- [ ] Validate user input.
- [ ] Refresh displayed preferences after updates.

### Memory Management

- [ ] Display stored durable memories.
- [ ] Display project memories where applicable.
- [ ] Allow users to review stored memories.
- [ ] Support individual memory deletion.
- [ ] Support bulk memory management where appropriate.

### Conversation Summary Management

- [ ] Display conversation summary status.
- [ ] Allow users to clear Rolling Conversation Summaries.
- [ ] Confirm summary deletion before execution.
- [ ] Refresh UI after successful deletion.

### API Integration

- [ ] Create `frontend/src/api/memoryClient.ts`.
- [ ] Create `frontend/src/types/memory.ts`.
- [ ] Create `frontend/src/pages/MemorySettingsPage.tsx` (authenticated settings route).
- [ ] Extend `frontend/src/api/healthClient.ts` with `memory_enabled`.
- [ ] Wire navigation link in authenticated app shell (Settings → Memory).

### User Experience

- [ ] Display loading indicators during Memory operations.
- [ ] Display success notifications.
- [ ] Display validation messages.
- [ ] Display friendly error messages.
- [ ] Preserve responsiveness during asynchronous operations.

### Feature Flag Integration

- [ ] Hide Memory controls when `MEMORY_ENABLED=false`.
- [ ] Preserve existing authenticated user experience.
- [ ] Preserve guest user experience.
- [ ] Verify runtime feature flag behaviour.

### Error Handling

- [ ] Handle API failures gracefully.
- [ ] Handle network failures gracefully.
- [ ] Handle validation failures gracefully.
- [ ] Preserve existing application behaviour during frontend failures.

### Testing

- [ ] Add component tests.
- [ ] Add API integration tests.
- [ ] Add preference management tests.
- [ ] Add memory deletion tests.
- [ ] Add feature flag tests.
- [ ] Add responsive UI tests.
- [ ] Add accessibility tests.

**Verify**

- Frontend lint
- Frontend tests
- Production build

Additional verification:

- [ ] Memory Settings page renders successfully.
- [ ] User preferences load correctly.
- [ ] Memory retrieval succeeds.
- [ ] Memory deletion succeeds.
- [ ] Conversation summary deletion succeeds.
- [ ] Guest users cannot access Memory controls.
- [ ] Existing chat experience remains unchanged.
- [ ] Feature flag regression passes.

**Acceptance**

- Authenticated users can manage their stored Memory information.
- Memory management operates exclusively through the public Memory API.
- User preferences, project memories, and Rolling Conversation Summaries can be managed independently.
- Frontend remains fully functional when Memory is disabled.
- Guest users continue to experience the existing application unchanged.
- Frontend failures never interrupt chat functionality.

**Exit Criteria**

- Memory Settings UI operational.
- API integration validated.
- Memory management verified.
- Feature flag behaviour validated.
- Ready for production validation.

**Rollback**

- [ ] Hide Memory Settings UI.
- [ ] Disable frontend Memory API integration.
- [ ] Preserve existing application behaviour.
- [ ] Verify chat functionality remains unchanged.

**Completion Record**

| Metric                   | Result |
| ------------------------ | ------ |
| Memory Settings UI       |        |
| Preference management    |        |
| Memory management        |        |
| API integration          |        |
| Accessibility validation |        |
| Feature flag regression  |        |
| Frontend tests           |        |
| Coverage                 |        |

---

# Phase 10 — Validation & Release

**Effort:** M

**Objective**

Perform comprehensive validation of the completed Memory subsystem, ensuring that all architectural invariants defined in Part I have been preserved, all implementation phases have been successfully integrated, and the platform remains fully functional with Memory both enabled and disabled. This phase certifies the Memory subsystem as production-ready.

**Deliverables**

- End-to-end validation report
- Regression test report
- Performance validation report
- Production readiness assessment
- Release summary
- Completion metrics
- Epic completion sign-off

**Steps**

### Functional Validation

- [ ] Verify all implementation phases have been completed.
- [ ] Verify all Part I architectural invariants.
- [ ] Verify Conversation Summary functionality.
- [ ] Verify Long-Term Memory functionality.
- [ ] Verify User Preference functionality.
- [ ] Verify Project Memory functionality.
- [ ] Verify Semantic Retrieval functionality.
- [ ] Verify Lifecycle Management functionality.
- [ ] Verify MemoryContext generation.

### Integration Validation

- [ ] Verify `UnifiedChatService` integration.
- [ ] Verify `PromptBuilder` integration.
- [ ] Verify `MemoryManager` orchestration.
- [ ] Verify `MemoryProvider` abstraction.
- [ ] Verify provider compatibility.
- [ ] Verify asynchronous persistence.
- [ ] Verify Memory API functionality.

### Regression Testing

- [ ] Execute full backend regression suite.
- [ ] Execute full frontend regression suite.
- [ ] Verify chat functionality.
- [ ] Verify Voice functionality.
- [ ] Verify RAG functionality.
- [ ] Verify MCP functionality.
- [ ] Verify Tool execution.
- [ ] Verify streaming responses.
- [ ] Verify authentication flow.
- [ ] Verify document upload and RAG integration.

### Feature Flag Validation

- [ ] Validate `MEMORY_ENABLED=true`.
- [ ] Validate `MEMORY_ENABLED=false`.
- [ ] Verify identical platform behaviour when disabled.
- [ ] Verify runtime feature flag behaviour.
- [ ] Verify graceful feature enablement.

### Performance Validation

- [ ] Measure memory retrieval latency.
- [ ] Measure prompt construction overhead.
- [ ] Measure asynchronous persistence latency.
- [ ] Measure streaming startup latency.
- [ ] Validate token budget behaviour.
- [ ] Verify acceptable production performance.

### Quality Validation

- [ ] Validate semantic retrieval quality.
- [ ] Validate memory ranking.
- [ ] Validate deduplication.
- [ ] Validate lifecycle transitions.
- [ ] Validate retention policies.
- [ ] Validate archival behaviour.
- [ ] Validate deletion workflow.

### Production Readiness

- [ ] Review observability metrics.
- [ ] Review structured logging.
- [ ] Verify error handling.
- [ ] Verify failure recovery.
- [ ] Verify deployment configuration.
- [ ] Publish production readiness report.

### Documentation

- [ ] Update implementation documentation.
- [ ] Update architecture documentation where required.
- [ ] Publish release summary.
- [ ] Record implementation metrics.
- [ ] Update Epic status.
- [ ] Archive implementation notes.

### Testing

- [ ] Execute complete backend test suite.
- [ ] Execute complete frontend test suite.
- [ ] Execute integration tests.
- [ ] Execute end-to-end tests.
- [ ] Execute evaluation suite.
- [ ] Execute performance validation.
- [ ] Execute production smoke tests.

**Verify**

- `make lint`
- `make typecheck`
- `make test-cov`
- `make eval`
- Frontend lint
- Frontend tests
- Frontend production build

Additional verification:

- [ ] All architectural invariants preserved.
- [ ] All implementation phases validated.
- [ ] Memory retrieval operational.
- [ ] Memory persistence operational.
- [ ] Lifecycle processing operational.
- [ ] Frontend Memory management operational.
- [ ] Existing platform functionality unchanged.
- [ ] Production deployment ready.

**Acceptance**

- All Part I architectural constraints have been preserved.
- All implementation phases have been successfully completed.
- Memory integrates seamlessly into the existing platform architecture.
- Existing chat, Voice, RAG, MCP, Tool execution, and streaming behaviour remain unchanged when `MEMORY_ENABLED=false`.
- Performance remains within acceptable production limits.
- All quality gates pass.
- The Memory subsystem is approved for production deployment.

**Exit Criteria**

- All validation activities completed.
- Regression suite passed.
- Performance validation approved.
- Production readiness confirmed.
- Epic formally completed.

**Rollback**

- [ ] Disable `MEMORY_ENABLED`.
- [ ] Redeploy the previous stable release if required.
- [ ] Verify platform functionality without Memory.
- [ ] Confirm rollback validation passes.
- [ ] Record rollback outcome if executed.

**Completion Record**

| Metric                    | Result    |
| ------------------------- | --------- |
| Backend Tests             |           |
| Frontend Tests            |           |
| Integration Tests         |           |
| End-to-End Tests          |           |
| Retrieval Validation      |           |
| Lifecycle Validation      |           |
| Performance Validation    |           |
| Feature Flag Regression   |           |
| Production Readiness      |           |
| Release Summary Published |           |
| Epic Status               | Completed |

---

# PR Map

One PR per phase.

- v2/epic-05/phase-00-baseline
- v2/epic-05/phase-01-models-migration
- v2/epic-05/phase-02-summaries
- v2/epic-05/phase-03-long-term-memory
- v2/epic-05/phase-04-user-preferences
- v2/epic-05/phase-05-project-memory
- v2/epic-05/phase-06-semantic-retrieval
- v2/epic-05/phase-07-lifecycle-api
- v2/epic-05/phase-08-chat-integration
- v2/epic-05/phase-09-frontend
- v2/epic-05/phase-10-release

---

# Risks

| Risk                      | Mitigation                                                                                                                          |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Prompt growth             | Rolling Conversation Summaries and token budgeting                                                                                  |
| Retrieval quality         | Ranking, quality scoring, deterministic retrieval                                                                                   |
| Memory pollution          | MemoryQualityEvaluator                                                                                                              |
| Cross-user leakage        | Owner and project isolation                                                                                                         |
| Provider coupling         | MemoryProvider abstraction                                                                                                          |
| Lifecycle regressions     | Dedicated lifecycle tests                                                                                                           |
| Feature regression        | MEMORY_ENABLED flag-off parity                                                                                                      |
| Storage migration         | Provider abstraction and replaceable backend                                                                                        |
| Performance               | Async persistence and bounded retrieval                                                                                             |
| Excessive memory growth   | Lifecycle retention, consolidation and deletion                                                                                     |
| Long-lived token exposure | Future authentication and data-protection enhancements (token revocation, session management, encryption at rest where appropriate) |

---

# Observability

Structured metrics only.

| Field                       | Purpose              |
| --------------------------- | -------------------- |
| memory_enabled              | Feature flag state   |
| memory_records_retrieved    | Retrieval volume     |
| memory_records_persisted    | Persistence volume   |
| memory_retrieval_latency_ms | Retrieval latency    |
| memory_persist_latency_ms   | Persistence latency  |
| memory_quality_score        | Quality distribution |
| lifecycle_transition        | Lifecycle tracking   |
| token_budget_used           | Prompt allocation    |
| memory_provider             | Active provider      |

No memory content, embeddings, or personally identifiable information should be logged by default.

---

# Definition of Done

- [ ] All Part I architectural invariants preserved.
- [x] Public APIs frozen after Phase 1.
- [ ] Memory fully orchestrated through `ChatService` and `UnifiedChatService`.
- [ ] Memory injected via `MemoryPromptInjector` (not direct storage access).
- [ ] RAG and Memory remain independent.
- [ ] `MEMORY_ENABLED=false` preserves Epic 04 behaviour.
- [ ] Lifecycle management and REST API operational.
- [ ] Retrieval deterministic.
- [ ] Frontend memory management complete.
- [ ] Backend and frontend tests pass; coverage ≥80% on `app/ai/memory/`.
- [ ] Release summary published.
- [ ] User authorizes Epic 06.

---

## Files index

| Path                                                      | Action | Owner    | Phase |
| --------------------------------------------------------- | ------ | -------- | ----- |
| `docs/audits/post-mvp-v2-epic5-phase-0-baseline-audit.md` | create | Docs     | 0     |
| `app/ai/memory/**`                                        | create | Core     | 1–8   |
| `app/ai/prompts/chat/memory_context.v1.j2`                | create | Core     | 8     |
| `app/ai/prompts/memory/extract.v1.j2`                     | create | Core     | 3     |
| `app/db/models.py`                                        | modify | Core     | 1     |
| `alembic/versions/0006_memory_tables.py`                  | create | Core     | 1     |
| `app/core/config.py`                                      | modify | Core     | 1     |
| `backend-python/.env.example`                             | modify | Docs     | 1     |
| `app/schemas/memory.py`                                   | create | Core     | 7     |
| `app/routers/memory.py`                                   | create | Adapter  | 7     |
| `app/routers/health.py`                                   | modify | Adapter  | 7     |
| `app/main.py`                                             | modify | Adapter  | 7, 8  |
| `app/ai/deps.py`                                          | modify | Adapter  | 1, 8  |
| `app/services/chat_service.py`                            | modify | Adapter  | 2, 8  |
| `app/services/unified_chat_service.py`                    | modify | Adapter  | 8     |
| `app/ai/rag/prompt_builder.py`                            | modify | Adapter  | 8     |
| `tests/ai/memory/**`                                      | create | Tests    | 1–8   |
| `tests/test_memory_router.py`                             | create | Tests    | 7     |
| `frontend/src/api/memoryClient.ts`                        | create | Frontend | 9     |
| `frontend/src/types/memory.ts`                            | create | Frontend | 9     |
| `frontend/src/pages/MemorySettingsPage.tsx`               | create | Frontend | 9     |
| `frontend/src/api/healthClient.ts`                        | modify | Frontend | 9     |
| `docs/releases/post-mvp-v2-epic5-release-summary.md`      | create | Docs     | 10    |

---

## Changelog

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                     |
| ------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1       | —          | Initial epic draft                                                                                                                                                                                                                                                                                                                                                          |
| 2       | 2026-07-31 | Implementation readiness: frontmatter (`provides`, `packages`, `test_paths`); Public APIs; config defaults; persistence schema; REST API; chat integration strategy; V1 `SessionSummary` reuse; project memory v1 = session-scoped; phase integration rules; Phase 1 migration; Phase 7 REST API; Phase 8 ChatService + UnifiedChatService; files index; baseline corrected |
| 2.1     | 2026-08-01 | Phase 0 baseline audit complete: 1079 tests, 89.57% coverage, eval 5/5, frontend 233 tests; audit published. Part II only.                                                                                                                                                                                                                                                  |
| 2.2     | 2026-08-01 | Phase 1 complete: canonical models/enums, `MemoryProvider` protocol, `PgVectorMemoryProvider` scaffold, `MemoryManager`, `memory_records`/`user_preferences` migration (0006), `MEMORY_ENABLED` + memory config, DI wiring, CI migration rollback smoke test. 1157 tests, 89.77% coverage. Public API frozen.                                                            |
| 2.3     | 2026-08-04 | Phase 3 complete: durable memory extraction pipeline (`MemoryExtractor`, `MemoryQualityEvaluator`), async persistence via `extract_and_persist_async`, embedding generation with retry, lifecycle event publication, `PgVectorMemoryProvider` record CRUD + semantic search for dedupe.                                                                                      |
| 2.4     | 2026-08-04 | Phase 4 complete: user preference persistence/retrieval in `PgVectorMemoryProvider`, validation + normalization (`preferences.py`), `MemoryContextBuilder`, domain API models (`UserPreferenceUpsert`/`UserPreferenceItem`), `MemoryManager.retrieve_preferences_context`. 1210 tests, 89.76% coverage.                                                                  |
| 2.5     | 2026-08-04 | Phase 2 complete: `ConversationSummaryService` over V1 `SessionSummary`, `build_context_messages` wired in `ChatService` when `MEMORY_ENABLED=true`, summary retrieval into `MemoryContext`.                                                                                                                                                                                |
| 2.6     | 2026-08-04 | Phase 5 complete: session-scoped project memory (`project.py`), provider CRUD/search isolation, `MemoryManager` project APIs, `MemoryContextBuilder.with_project_memories`.                                                                                                                                                                                               |
| 2.7     | 2026-08-04 | Phase 6 complete: `SemanticRetriever` multi-domain retrieval, ranking/dedupe/quality filtering, token budgeting, `MemoryManager.retrieve_context`.                                                                                                                                                                                                                        |
| 2.8     | 2026-08-04 | Phase 7 complete: `LifecycleManager`, `MemoryPolicyEngine`, lifecycle integration in `MemoryManager`, Memory REST API (`app/routers/memory.py`), `memory_enabled` health field. 1278 tests, 89.31% coverage.                                                                                                                                                             |

---
