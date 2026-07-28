---
epic: v2-02
title: Advanced RAG
status: completed
version: 2
depends_on: [v2-01]
provides:
  [
    AdvancedRetrievalPipeline,
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
    Citation,
    MetadataFilter,
    QueryRewriter,
    Reranker,
    ContextCompressor,
    HybridRetriever,
    ParentChildChunker,
    IndexingJob,
    ADVANCED_RAG_ENABLED,
  ]
feature_flags: [ADVANCED_RAG_ENABLED]
packages: [app/ai/rag, app/ai/documents, app/ai/interfaces, app/ai/vectorstores]
test_paths:
  [
    tests/ai/rag,
    tests/test_retriever.py,
    tests/test_rag_service.py,
    tests/test_document_chunker.py,
    tests/test_vector_store.py,
    tests/test_unified_chat.py,
  ]
---

# Post-MVP V2 Epic 02 — Advanced RAG

> **Agents:** Read [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md). Implement **Part II** phase-by-phase; consult **Part I** for behaviour and scope questions only.

**Strategy:** [V2 architecture](../references/fullstack-ai-platform-v2-architecture-implementation-strategy.md) § “2. Advanced RAG”

**Predecessor:** [Epic 01 — Agent Framework](./post-mvp-v2-epic-01-agent-framework.md)

---

# Part I — Design

## Objective

Production-quality retrieval by extending the existing V1 RAG stack under `app/ai/rag/`, `app/ai/documents/`, and `app/ai/vectorstores/` — hybrid search, metadata filters, query rewrite, parent-child retrieval, Cohere cross-encoder rerank, context compression, better chunking, structured citations, and a thin indexing job interface.

Ships behind `ADVANCED_RAG_ENABLED=false` (default). When the flag is off, the V1 dense-only `Retriever` → `ContextBuilder` path in `UnifiedChatService` / `RAGService` is unchanged.

**Does not ship:** RAG inside `app/ai/agent/` core; agent tools that replace pre-handoff retrieval; full background job queues/workers (Epic 9); RAG evaluation harness expansion beyond existing `make eval` (Epic 6); MCP/memory/workflows.

## Principles

Platform-first · composition over coupling · provider-agnostic core (Protocols) · streaming-first (preserve retrieval SSE frames) · async-first · interface-driven · security by default (owner-scoped retrieval) · incremental · no over-engineering · prefer extending existing modules

## Architecture

```text
Upload → IngestionPipeline (chunk/parent-child) → VectorStore (+ FTS) → ready
                                                      │
Question → [flag on] AdvancedRetrievalPipeline        │
             ├─ QueryRewriter (LLMProvider)           │
             ├─ HybridRetriever (dense + Postgres FTS)│
             ├─ MetadataFilter                        │
             ├─ Parent expansion                      │
             ├─ Reranker (Cohere rerank-v3.5)         │
             └─ ContextCompressor → Citations         │
         → UnifiedChatService / RAGService merge      │
         → Agent / Chat (unchanged handoff boundary)

[flag off] Retriever → ContextBuilder (V1 path)
```

```text
app/ai/rag/                    # extend — do not create parallel advanced_rag package
├── service.py                 # existing; branch advanced pipeline when flag on
├── retriever.py               # existing V1 dense path (flag off)
├── context_builder.py         # existing; used by V1 and as compressor fallback
├── prompt_builder.py
├── schemas.py                 # extend: Citation, RetrievalRequest/Result, RetrievedCandidate
├── pipeline.py                # NEW AdvancedRetrievalPipeline
├── hybrid/
│   ├── lexical.py             # Postgres FTS query
│   └── fusion.py              # Reciprocal Rank Fusion (RRF)
├── rewrite/
│   └── query_rewriter.py
├── rerank/
│   └── cohere.py              # CohereReranker adapter (SDK behind Protocol)
├── compress/
│   └── compressor.py
├── citations/
│   └── builder.py
└── indexing/
    ├── protocol.py            # IndexingJob Protocol
    └── sync_runner.py         # in-process runner; TODO(epic-9) queue

app/ai/interfaces/
├── vector_store.py            # extend: filtered + lexical search
├── reranker.py                # NEW Protocol
├── query_rewriter.py          # NEW Protocol
├── context_compressor.py      # NEW Protocol
└── embedding_provider.py      # unchanged

app/ai/documents/chunkers/
├── recursive.py               # keep for V1 / flag-off ingest default
└── parent_child.py            # NEW ParentChildChunker
```

## Components

| Component                            | Role                                                                                          | Key outputs                           |
| ------------------------------------ | --------------------------------------------------------------------------------------------- | ------------------------------------- |
| `AdvancedRetrievalPipeline`          | Flag-on orchestrator for rewrite → hybrid → filter → parent expand → rerank → compress → cite | `RetrievalResult`                     |
| `RetrievedCandidate`                 | Immutable intermediate carrier for all pipeline stages                                        | chunk, parent, scores, metadata       |
| `HybridRetriever`                    | Dense (`VectorStore.similarity_search`) + Postgres FTS; fuse with RRF                         | Ranked `list[RetrievedCandidate]`     |
| `MetadataFilter`                     | Apply structured filters on chunk/document metadata                                           | Filtered `RetrievedCandidate`s        |
| `QueryRewriter`                      | Optional LLM rewrite via `LLMProvider` + prompt; at most once per request                     | Rewritten query string                |
| `ParentChildChunker`                 | Small child chunks for retrieval; larger parent for context                                   | Child + parent `DocumentChunk`s       |
| `Reranker` / `CohereReranker`        | Cross-encoder rerank Protocol + Cohere `rerank-v3.5`                                          | Reordered candidates + `rerank_score` |
| `ContextCompressor`                  | Select/trim/remove only — never rewrite source text                                           | `BuiltContext` from original text     |
| `CitationBuilder`                    | Assign `[n]` after compression; map included blocks → citations                               | `list[Citation]`                      |
| `IndexingJob` / `SyncIndexingRunner` | Thin async-ingest interface; sync runner now                                                  | Job id + status                       |
| `UnifiedChatService` / `RAGService`  | Pre-agent handoff wiring; flag branch only                                                    | Chat/RAG responses + citations        |

## Scope

**In:**

- Hybrid retrieval (Postgres FTS + dense) with **RRF** fusion
- Metadata filtering on retrieval
- Query rewriting (LLMProvider-backed)
- Parent-child chunking + parent expansion at retrieve time
- Cross-encoder reranking via **Cohere `rerank-v3.5`** (pluggable Protocol)
- Context compression
- Better chunking (parent-child; keep `RecursiveChunker` for flag-off / legacy)
- Structured citations (API + prompt markers) and **minimal frontend** citation rendering
- Background indexing: `IndexingJob` Protocol + in-process `SyncIndexingRunner`
- Feature flag `ADVANCED_RAG_ENABLED` (default `false`); V1 path when off
- Tests, docs, release summary

**Out:**

- Epic 3 MCP · Epic 4 Memory · Epic 5 Workflows · Epic 6 Observability/RAG eval expansion · Epic 7 Plugins · Epic 8 HITL
- Epic 9 full queue/workers/schedulers (`TODO(epic-9):` only)
- RAG-in-agent-core or agent tool replacing pre-handoff retrieval
- Default flip of `ADVANCED_RAG_ENABLED` to `true`
- Rich citation UX (hover cards, PDF page jump, highlight overlays) beyond minimal list + `[n]` markers
- New vector DB vendors; Redis embedding cache (optional later comment only)

## Dependencies

| Requires                                                                                                                                   | Provides to downstream                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Epic 01 (`v2-01`) complete; V1 RAG (`Retriever`, `ContextBuilder`, `PgVectorStore`, `IngestionPipeline`, `UnifiedChatService` pre-handoff) | `AdvancedRetrievalPipeline`, `RetrievedCandidate`, `Citation`, Protocols (`Reranker`, `QueryRewriter`, `ContextCompressor`, `IndexingJob`), `ADVANCED_RAG_ENABLED`, hybrid/parent-child store capabilities |

**Future consumers:** Epic 4 (Memory semantic retrieval patterns), Epic 6 (RAG evaluation), Epic 9 (queue-backed `IndexingJob`), Epic 11 (governance on document access)

## Locked decisions

| Topic            | Decision                                                                                                                                                         | Deferred to                 |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| Package          | Extend `app/ai/rag/` (+ documents/chunkers, interfaces, vectorstores); no `app/ai/advanced_rag/`                                                                 | —                           |
| RAG ↔ Agent      | Stay in `UnifiedChatService` / `RAGService` **before** agent handoff; compose pipeline; **no** agent-core RAG; **no** agent tool replacing pre-handoff retrieval | —                           |
| Feature flag     | Single master `ADVANCED_RAG_ENABLED` (default **false**); all advanced stages behind it; V1 dense path when off                                                  | Default flag flip           |
| Pipeline carrier | Immutable internal `RetrievedCandidate` flows through all advanced stages (not ad-hoc tuples/dicts)                                                              | —                           |
| Score semantics  | Only `final_score` is consumed downstream; intermediate scores are diagnostic only                                                                               | —                           |
| Hybrid           | Postgres full-text (BM25-ish `tsvector`/`ts_rank`) + dense pgvector; fuse with **RRF**                                                                           | —                           |
| RRF              | Reciprocal Rank Fusion; configurable `rrf_k` (default **60**)                                                                                                    | —                           |
| Reranker         | Protocol + pluggable adapter; ship **Cohere `rerank-v3.5`**; timeout is a fraction of request budget; fall back to pre-rerank order                              | Other rerank providers      |
| Query rewrite    | Optional; default **on** when advanced flag on; uses `LLMProvider` + Jinja prompt; **at most once** per request; never recursively rewrite                       | —                           |
| Parent-child     | Children embedded + FTS-indexed for retrieval; parents stored for expansion; **dedupe to one parent block** when multiple children share a parent                | —                           |
| Compression      | Select/trim/remove only — **never** rewrite/summarize/paraphrase source text; V1 `ContextBuilder` when flag off                                                  | —                           |
| Citations        | Additive `citations`; `[n]` assigned **after compression**, immediately before prompt construction; keep `retrieved_chunks`                                      | Rich UI                     |
| Frontend         | Minimal citation rendering on chat + RAG ask surfaces                                                                                                            | Epic polish / design system |
| Indexing         | `IndexingJob` Protocol + `SyncIndexingRunner` (in-process); no Celery/RQ                                                                                         | Epic 9                      |
| Observability    | Structured latency/count fields; no raw query/doc text in logs by default                                                                                        | Epic 6                      |
| Dependencies     | Adding `cohere` (or equivalent HTTP client usage in main deps) requires **user approval** at Phase 6                                                             | —                           |

## Retrieval pipeline (flag on)

Stages pass an immutable `RetrievedCandidate` list (built at hybrid retrieve; enriched afterward). No ad-hoc score tuples between stages.

| Step               | Behaviour                                                                                                                            | Skip / fail                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| 1. Rewrite         | `QueryRewriter.rewrite(...)` **once** → search query; never rewrite the rewritten query                                              | On failure: use original question; log `query_rewrite_failed`               |
| 2. Hybrid retrieve | Dense + FTS → **RRF** → `list[RetrievedCandidate]` with `dense_score` / `lexical_score` / `rrf_score`; set `final_score = rrf_score` | Empty either side OK; union via RRF                                         |
| 3. Metadata filter | Filter candidates in place                                                                                                           | Invalid filter → empty result (not 500)                                     |
| 4. Parent expand   | Attach parent text; **multiple children sharing one parent → a single parent context block**                                         | Orphan child → use child content as the block                               |
| 5. Rerank          | Cohere rerank within latency budget; set `rerank_score` and `final_score = rerank_score`                                             | Timeout/failure: keep pre-rerank order + `final_score`; log `rerank_failed` |
| 6. Compress        | Select/trim/remove candidates to fit budget; **preserve original source text**                                                       | Fallback: V1-style tail drop (still no paraphrase)                          |
| 7. Cite            | **After compression**, assign contiguous `[1..n]` then build `Citation` list                                                         | Always if any included block                                                |

Owner scope (`user_id`) applies at every store query — same as V1.

## RetrievedCandidate (internal)

Immutable intermediate model used throughout the advanced pipeline (not a public HTTP DTO).

| Field           | Type / notes                                                                           |
| --------------- | -------------------------------------------------------------------------------------- |
| `chunk`         | Retrieved child (or flat) chunk identity + content                                     |
| `parent`        | Expanded parent content when applicable; `None` for orphans / flat                     |
| `metadata`      | Source, page, tags, document_id, etc.                                                  |
| `dense_score`   | Diagnostic; from vector search                                                         |
| `lexical_score` | Diagnostic; from FTS                                                                   |
| `rrf_score`     | Diagnostic; from RRF fusion                                                            |
| `rerank_score`  | Diagnostic; from cross-encoder when rerank succeeds                                    |
| `final_score`   | **Only score consumed by downstream stages** (ordering after each stage that re-ranks) |

## Score semantics

Only `final_score` is consumed by downstream components (ordering into compress/cite and any top-n cuts after a stage). Intermediate scores (`dense_score`, `lexical_score`, `rrf_score`, `rerank_score`) are diagnostic only and must not influence downstream behaviour directly.

## Chunking rules (parent-child)

| Rule                  | Default                                                                                                         |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| Child size / overlap  | `child_chunk_size=400`, `child_chunk_overlap=80`                                                                |
| Parent size / overlap | `parent_chunk_size=2000`, `parent_chunk_overlap=200`                                                            |
| Mapping               | Each child metadata includes `parent_id` (UUID of parent row) and `chunk_kind=child`                            |
| Parents               | `chunk_kind=parent`; embedded **optional** (default: parents **not** embedded; children only)                   |
| Parent dedupe         | Multiple child hits referencing the same parent expand to **one** parent context block                          |
| Flag off ingest       | Keep `RecursiveChunker` + existing `chunk_size` / `chunk_overlap`                                               |
| Flag on ingest        | Use `ParentChildChunker` when `ADVANCED_RAG_ENABLED=true`                                                       |
| Re-ingest             | Existing documents keep V1 chunks until re-uploaded; no mandatory migration rewrite of all corpora in this epic |

## Hybrid + RRF defaults

| Knob                   | Default                                                 |
| ---------------------- | ------------------------------------------------------- |
| `rag_top_k`            | 5 (final after rerank; existing setting)                |
| `hybrid_dense_top_k`   | 20                                                      |
| `hybrid_lexical_top_k` | 20                                                      |
| `rrf_k`                | 60                                                      |
| FTS config             | `english` + `to_tsvector` on chunk `content`; GIN index |
| Dense                  | Existing HNSW cosine on `embedding`                     |

## Rerank defaults

| Knob                | Default                                                                                               |
| ------------------- | ----------------------------------------------------------------------------------------------------- |
| Provider            | `cohere`                                                                                              |
| Model               | `rerank-v3.5`                                                                                         |
| `rerank_top_n`      | `rag_top_k` (or max(rag_top_k, 5))                                                                    |
| `rerank_timeout_ms` | Default **1500**; must be only a **fraction** of overall request latency budget                       |
| Timeout behaviour   | On timeout/error: gracefully fall back to pre-rerank ordering (`final_score` unchanged from post-RRF) |
| API key             | `COHERE_API_KEY` (required only when advanced RAG + rerank path runs)                                 |

## Citation model

| Field                 | Meaning                                                                             |
| --------------------- | ----------------------------------------------------------------------------------- |
| `index`               | 1-based marker matching prompt `[n]`; assigned **after compression**, before prompt |
| `chunk_id`            | UUID                                                                                |
| `document_id`         | UUID                                                                                |
| `filename` / `source` | From document or chunk metadata                                                     |
| `page`                | From metadata when present                                                          |
| `snippet`             | Short excerpt of **original** source text (bounded; not full parent text in API)    |
| `score`               | `final_score` of the included candidate                                             |

Citation indices (`[1]`, `[2]`, …) are assigned after context compression, immediately before prompt construction — guaranteeing contiguous numbering over what the model actually sees.

Additive API field: `citations: list[Citation] | null` on `ChatResponseSchema` / `RAGAskResponse`. Preserve existing `retrieved_chunks`.

SSE: extend `retrieval_complete` payload with `citation_count` (and keep `chunk_count`); do not stream raw snippets in SSE by default.

## Public APIs (stable after Phase 1)

| API                                                                                        | Kind                          |
| ------------------------------------------------------------------------------------------ | ----------------------------- |
| `AdvancedRetrievalPipeline`                                                                | Protocol / orchestrator entry |
| `RetrievalRequest`, `RetrievalResult`, `RetrievedCandidate`, `Citation`, `MetadataFilter`  | Model                         |
| `QueryRewriter`, `Reranker`, `ContextCompressor`, `IndexingJob`                            | Protocol                      |
| `HybridRetriever` (concrete OK to evolve internals)                                        | Component                     |
| `ParentChildChunker`                                                                       | Component                     |
| Extended `VectorStore`: `similarity_search` gains optional `filters`; add `lexical_search` | Protocol (additive)           |

`RetrievedCandidate` is part of the frozen **internal** pipeline contract (importable for tests); it is not exposed on HTTP schemas.

Internal (may evolve): Cohere client wrapper, FTS SQL, RRF helper, `SyncIndexingRunner`, DI wiring, frontend components.

**Exceptions:** Reuse / extend existing RAG/document errors; do not invent a parallel error hierarchy unless needed for indexing job status.

## Configuration defaults

| Setting                                      | Default                                                                                         |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `ADVANCED_RAG_ENABLED`                       | **`false`**                                                                                     |
| `rag_enabled`                                | unchanged (existing gate for document RAG)                                                      |
| `hybrid_dense_top_k`                         | 20                                                                                              |
| `hybrid_lexical_top_k`                       | 20                                                                                              |
| `rrf_k`                                      | 60                                                                                              |
| `query_rewrite_enabled`                      | `true` (honoured only when advanced flag on)                                                    |
| `rerank_provider`                            | `cohere`                                                                                        |
| `rerank_model`                               | `rerank-v3.5`                                                                                   |
| `rerank_timeout_ms`                          | 1500                                                                                            |
| `child_chunk_size` / `child_chunk_overlap`   | 400 / 80                                                                                        |
| `parent_chunk_size` / `parent_chunk_overlap` | 2000 / 200                                                                                      |
| `citation_snippet_max_chars`                 | 240                                                                                             |
| Existing                                     | `rag_top_k=5`, `rag_context_max_chars=8000`, embedding/chunk V1 settings unchanged for flag-off |

## Design acceptance

- Flag off: V1 retrieval/ingest/chat/RAG ask behaviour preserved (parity tests)
- Flag on: hybrid + filter + rewrite (once) + parent-child (deduped) + Cohere rerank + faithful compression + post-compression citations on document-grounded chat and `/api/rag/ask`
- Pipeline stages use `RetrievedCandidate`; only `final_score` drives downstream ordering
- Owner isolation unchanged
- Core RAG packages depend on Protocols — Cohere SDK only in `app/ai/rag/rerank/cohere.py` (adapter)
- No imports from `app/ai/agent/` into RAG core for “RAG-as-tool”
- Coverage ≥80% on `app/` and advanced RAG modules touched
- Minimal frontend shows citations without breaking existing chat UX
- `IndexingJob` exists; queue implementation left as `TODO(epic-9):`

## Architectural invariants

These rules must remain true throughout this epic. Violations require explicit user approval and Part I update.

- **Pre-handoff boundary** — Advanced RAG runs in `UnifiedChatService` / `RAGService` before agent handoff; `app/ai/agent/` core stays RAG-free.
- **Extend, don’t fork** — No parallel `advanced_rag` package; compose on existing retriever/store/chunker surfaces.
- **Flag-off parity** — `ADVANCED_RAG_ENABLED=false` leaves V1 dense `Retriever` + `RecursiveChunker` path behaviour unchanged.
- **Owner scope** — Every store query remains `user_id`-scoped; filters cannot bypass ownership.
- **Pipeline carrier** — Advanced stages exchange `RetrievedCandidate` (immutable); do not rebuild opaque tuples/dicts between stages.
- **Score ownership** — Only `final_score` drives downstream ordering/cuts; intermediate scores are diagnostic only.
- **Single rewrite** — Query rewriting is attempted at most once per request; rewritten queries must never be recursively rewritten.
- **Faithful compression** — `ContextCompressor` must never rewrite, summarize, paraphrase, or modify document content; it may only select, trim, or remove context while preserving original source text.
- **Parent dedupe** — Multiple child chunks referencing the same parent expand to a single parent context block.
- **Citation timing** — Citation indices are assigned after compression, immediately before prompt construction.
- **Rerank budget** — Reranker timeout consumes only a fraction of the request latency budget; timeout/error falls back to pre-rerank ordering.
- **Provider isolation** — Cohere SDK (and any rerank HTTP client) stays in the Cohere adapter module; pipeline depends on `Reranker` Protocol only.
- **Additive APIs** — Prefer additive fields (`citations`, optional filter args); do not break existing chat/RAG response contracts.
- **Content-safe logs** — No raw user questions, chunk text, or document bodies in structured logs by default (ids, counts, latencies only).
- **No Epic 9 queue** — Only `IndexingJob` + sync runner; real workers deferred with `TODO(epic-9):`.
- **No future-epic behaviour early** — MCP, memory, workflows, full OTel/RAG eval — `TODO(epic-N):` only.
- **Public APIs stable after Phase 1** — Changes to frozen Protocols/models require user approval.

---

# Part II — Execution

## Reuse existing components

**DO NOT REIMPLEMENT:**

| Component                                                 | Location                                                                                            |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `Retriever` (V1 dense)                                    | `app/ai/rag/retriever.py`                                                                           |
| `ContextBuilder`, `PromptBuilder`, `RAGService`           | `app/ai/rag/context_builder.py`, `prompt_builder.py`, `service.py`                                  |
| `ScoredChunk`, `VectorStore`, `PgVectorStore`             | `app/ai/interfaces/vector_store.py`, `app/ai/vectorstores/pgvector.py`                              |
| `EmbeddingProvider`, `OpenAIEmbeddingProvider`, factory   | `app/ai/interfaces/embedding_provider.py`, `app/ai/embeddings/`                                     |
| `IngestionPipeline`, parsers, `RecursiveChunker`          | `app/ai/documents/`                                                                                 |
| `KnowledgeService`, `DocumentService`, `SqlDocumentStore` | `app/services/knowledge_service.py`, `document_service.py`, `app/db/documents.py`                   |
| `Document` / `DocumentChunk` models                       | `app/db/models.py`                                                                                  |
| `UnifiedChatService` document merge + SSE `retrieval_*`   | `app/services/unified_chat_service.py`                                                              |
| Chat/RAG schemas (`RetrievedChunkMetaSchema`, etc.)       | `app/schemas/chat.py`, `app/schemas/rag.py`                                                         |
| DI factories                                              | `app/ai/deps.py`                                                                                    |
| `LLMProvider` / `ProviderFactory`                         | `app/providers/`                                                                                    |
| `PromptManager`                                           | `app/ai/prompts/`                                                                                   |
| `retry_async`                                             | `app/core/retry.py`                                                                                 |
| Frontend chat/RAG clients, `MessageBubble`, `RagAskPanel` | `frontend/src/api/chatClient.ts`, `ragClient.ts`, `components/MessageBubble.tsx`, `RagAskPanel.tsx` |
| Agent runtime / adapters                                  | `app/ai/agent/` — do not modify for RAG-in-agent                                                    |

## Not allowed

- Refactor unrelated code beyond documented integration steps
- Rename packages or invent `app/ai/advanced_rag/`
- Add dependencies without user approval (especially `cohere` / promoting `httpx`)
- Break existing chat/RAG API contracts (additive only)
- Implement Epic 3+ behaviour (MCP, memory, workflows, full job queue, RAG eval platform)
- Move RAG into `app/ai/agent/` core or replace pre-handoff retrieval with an agent tool
- Paraphrase/summarize document text in `ContextCompressor` (select/trim/remove only)
- Recursively rewrite queries or consume intermediate scores instead of `final_score`
- Log raw document/query text by default
- Change `AGENT_RUNTIME_ENABLED` defaults or agent public APIs

## Baseline

_Copied from Epic 01 Phase 12 completion record._

| Area                     | State                                                                                           |
| ------------------------ | ----------------------------------------------------------------------------------------------- |
| Backend tests / coverage | Flag-off: **604 passed**, **88.19%** `app/`; flag-on (agent): **604 passed**, **87.61%** `app/` |
| `app/ai/agent/` coverage | **91.12%** (144 tests; gate ≥80%)                                                               |
| Eval CLI                 | **5 passed**, 0 failed (`2026-07-23T23:55:38Z`)                                                 |
| Flag-off regression      | **Pass** — `AGENT_RUNTIME_ENABLED=false make test-cov`                                          |
| Flag-on parity (agent)   | **Pass** — `AGENT_RUNTIME_ENABLED=true make test-cov`                                           |
| Orchestration            | RAG remains pre-handoff in `UnifiedChatService`; agent optional behind `AGENT_RUNTIME_ENABLED`  |
| Advanced RAG             | None (dense-only `Retriever`; `RecursiveChunker`; no hybrid/rerank/citations UI)                |

## Phase status

| Phase | Name                         | Effort | Status      |
| ----- | ---------------------------- | ------ | ----------- |
| 0     | Baseline Audit               | XS     | Completed   |
| 1     | Scaffold, Models, Interfaces | M      | Completed   |
| 2     | Parent-Child Chunking        | M      | Completed   |
| 3     | Metadata Filtering           | S      | Completed   |
| 4     | Hybrid Retrieval + RRF       | M      | Completed   |
| 5     | Query Rewriting              | S      | Completed   |
| 6     | Cross-Encoder Reranking      | M      | Completed   |
| 7     | Context Compression          | S      | Completed   |
| 8     | Citations (Backend)          | M      | Completed   |
| 9     | Indexing Job Interface       | S      | Completed   |
| 10    | Chat/RAG Integration         | M      | Completed   |
| 11    | Frontend Citations           | S      | Completed   |
| 12    | Validation & Release         | S      | Completed   |

---

## Phase 0 — Baseline Audit

**Effort:** XS

**Deliverables:** `docs/audits/post-mvp-v2-epic2-phase-0-baseline-audit.md`

**Steps:**

- [x] Confirm Epic 01 Phase 12 complete / authorized for Epic 02
- [x] Run backend gates: `make lint`, `make format-check`, `make typecheck`, `make test-cov`, `make eval`
- [x] Run frontend gates: `npm run lint`, `npm run format:check`, `npm test -- --run`, `npm run build`
- [x] Inventory paths: `app/ai/rag/**`, `app/ai/documents/**`, `app/ai/vectorstores/pgvector.py`, `app/ai/interfaces/vector_store.py`, `app/services/unified_chat_service.py`, `app/services/knowledge_service.py`, `app/db/models.py` (`Document`/`DocumentChunk`), `app/schemas/chat.py`, `app/schemas/rag.py`, `frontend` citation-related surfaces
- [x] Record current RAG behaviour (dense-only, sync ingest, `retrieved_chunks` debug meta, no citations UI)
- [x] Write audit doc; record metrics below
- [x] Phase 0 complete — user confirmed

**Verify:** `make lint && make typecheck && make test-cov && make eval`

**Acceptance:**

- All quality gates pass; no repository code changes
- Inventory documents real module paths only

**Exit criteria:**

- Audit published; baseline recorded; user confirmed Phase 0

**Completion record:**

| Metric                   | Result                                                    |
| ------------------------ | --------------------------------------------------------- |
| Backend tests / coverage | **604 passed**, **87.61%** `app/`                         |
| Frontend tests           | **177 passed** (35 files)                                 |
| Eval CLI                 | **5 passed**, 0 failed (`2026-07-25T05:11:59Z`)           |
| Git commit               | `feb1b91`                                                 |
| Audit doc                | `docs/audits/post-mvp-v2-epic2-phase-0-baseline-audit.md` |

---

## Phase 1 — Scaffold, Models, Interfaces

**Effort:** M

**Deliverables:** Advanced RAG models/Protocols; `ADVANCED_RAG_ENABLED=false`; package layout stubs; public API exports

**Steps:**

- [x] Add `ADVANCED_RAG_ENABLED` to `app/core/config.py` + `backend-python/.env.example` (default **false**)
- [x] Add models: `RetrievalRequest`, `RetrievalResult`, `RetrievedCandidate`, `Citation`, `MetadataFilter` in `app/ai/rag/schemas.py` (or adjacent models module)
- [x] Document score fields on `RetrievedCandidate` per Part I § Score semantics (`final_score` vs diagnostic scores)
- [x] Add Protocols: `QueryRewriter`, `Reranker`, `ContextCompressor`, `IndexingJob` under `app/ai/interfaces/`
- [x] Add `AdvancedRetrievalPipeline` Protocol + no-op/skeleton impl in `app/ai/rag/pipeline.py` that delegates to existing `Retriever` until later phases fill stages
- [x] Create package dirs: `hybrid/`, `rewrite/`, `rerank/`, `compress/`, `citations/`, `indexing/` with `__init__.py`
- [x] Export public API from `app/ai/rag/__init__.py` / interfaces `__init__.py` as needed
- [x] Add `tests/ai/rag/test_models.py`, `test_interfaces.py`
- [x] Phase 1 complete — user confirmed

**Verify:** `make typecheck && pytest tests/ai/rag/test_models.py tests/ai/rag/test_interfaces.py`

**Acceptance:**

- Imports clean; flag default false; chat hot path untouched
- Public APIs match Part I freeze list including immutable `RetrievedCandidate`

**Exit criteria:**

- Tests pass; public API finalized; user confirmed Phase 1

**Phase 1 notes (API freeze decisions):**

| Topic                | Decision                                                                                         |
| -------------------- | ------------------------------------------------------------------------------------------------ |
| `RetrievedCandidate` | `chunk: ScoredChunk`, `parent: str \| None`; ignore `chunk.score` downstream — use `final_score` |
| `RetrievalRequest`   | `question`, `user_id`, `top_k \| None`, `filters \| None`                                        |
| `RetrievalResult`    | `candidates`, `citations` (default `[]`), `context_text`, `truncated`, `retrieval_latency_ms`    |
| `MetadataFilter`     | optional `document_ids`/`tags` frozensets, `source`, `mime_type`                                 |
| Models               | frozen dataclasses in `app/ai/rag/schemas.py`                                                    |
| `ContextCompressor`  | returns existing `BuiltContext`                                                                  |
| `IndexingJob`        | `submit` / `get_status` + `IndexingJobState` / `IndexingJobStatus`                               |
| Pipeline             | Protocol + `DefaultAdvancedRetrievalPipeline` skeleton in `pipeline.py`                          |

---

## Phase 2 — Parent-Child Chunking

**Effort:** M

**Deliverables:** `app/ai/documents/chunkers/parent_child.py`; schema/metadata conventions; Alembic migration if new columns required; ingest wiring when advanced flag on

**Steps:**

- [x] Implement `ParentChildChunker` per Part I § Chunking rules
- [x] Persist parent/child relationship (`chunk_kind`, `parent_id` via `metadata_json` and/or columns — prefer metadata if sufficient; migration only if querying parents requires columns/indexes)
- [x] Wire `IngestionPipeline` / `KnowledgeService` to select chunker by `ADVANCED_RAG_ENABLED`
- [x] Add retrieval helper to expand child → parent content with **parent dedupe** (one parent block per parent id)
- [x] Add `tests/ai/rag/test_parent_child_chunker.py` (+ update `tests/test_document_chunker.py` / pipeline tests as needed)
- [x] Phase 2 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_parent_child_chunker.py tests/test_document_chunker.py tests/test_document_pipeline.py`

**Acceptance:**

- Flag off: still `RecursiveChunker`
- Flag on: children retrievable; parents expand into context without embedding parents by default
- Multiple children → same parent yields a single parent context block

**Exit criteria:**

- Tests pass; user confirmed Phase 2

**Phase 2 notes (implementation decisions):**

| Topic                | Decision                                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `parent_id`          | Chunker mints parent UUIDs; `DocumentChunk.id` optional pre-assign; store persists as `document_chunks.id`               |
| Relationship storage | `chunk_kind` + `parent_id` in `metadata_json` only — no Alembic migration                                                |
| Config               | `child_chunk_*` / `parent_chunk_*` settings + `.env.example`; validated when advanced flag on                            |
| Chunker selection    | `IngestionPipeline` picks `ParentChildChunker` vs `RecursiveChunker` from flag (covers Knowledge + Document services)    |
| Embed                | KnowledgeService embeds non-parent chunks only; parents stored with `embedding=NULL`                                     |
| Expand helper        | `app/ai/rag/parent_expand.py` + `SqlDocumentStore.get_chunk_contents_by_ids`; **not** wired into pipeline until Phase 10 |

---

## Phase 3 — Metadata Filtering

**Effort:** S

**Deliverables:** Additive filter support on `VectorStore` + `PgVectorStore`; `MetadataFilter` application in pipeline

**Steps:**

- [x] Extend `VectorStore.similarity_search` with optional `filters: MetadataFilter | None = None` (additive)
- [x] Implement filter predicates in `PgVectorStore` (document_id set, tags, mime/source keys present in metadata/document row)
- [x] Unit tests for filter match / no-match / owner isolation
- [x] Phase 3 complete — user confirmed

**Verify:** `pytest tests/test_vector_store.py tests/ai/rag/test_metadata_filter.py`

**Acceptance:**

- Filters never return other users’ chunks
- Unfiltered calls preserve V1 behaviour

**Exit criteria:**

- Tests pass; user confirmed Phase 3

**Phase 3 notes (implementation decisions):**

| Topic                | Decision                                                                                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Architecture         | Store push-down on `similarity_search` **and** candidate-stage `apply_metadata_filter` (Part I stage 3)                                                       |
| Retriever            | Additive optional `filters` passthrough (default `None`); unfiltered V1 calls unchanged                                                                       |
| Field map            | `document_ids` → `document_chunks.document_id`; `tags` → `metadata_json.tags`; `source` → `metadata_json.source` (exact); `mime_type` → `documents.mime_type` |
| Tags                 | AND / containment (`tags` array must include every requested tag)                                                                                             |
| Multi-field          | AND across all set `MetadataFilter` fields                                                                                                                    |
| Unsatisfiable        | Empty `document_ids` or empty `tags` frozenset → empty result (not 500); remove Phase-1 reject `ValueError`                                                   |
| ScoredChunk metadata | `mime_type` surfaced via `setdefault` from document row for candidate-stage mime filters                                                                      |
| Helper module        | `app/ai/rag/metadata_filter.py` (internal; not added to Phase-1 public `__all__`)                                                                             |

---

## Phase 4 — Hybrid Retrieval + Reciprocal Rank Fusion (RRF)

**Effort:** M

**Deliverables:** FTS column/index migration; `lexical_search`; `hybrid/fusion.py` (RRF); `HybridRetriever`

**Steps:**

- [x] Alembic: `tsvector` (generated/stored) + GIN index on `document_chunks` content (or equivalent Postgres FTS setup)
- [x] Add `VectorStore.lexical_search(...)` + `PgVectorStore` implementation (owner-scoped)
- [x] Implement RRF in `app/ai/rag/hybrid/fusion.py` with `rrf_k=60`
- [x] Implement `HybridRetriever` combining dense + lexical → RRF → `list[RetrievedCandidate]` (`dense_score` / `lexical_score` / `rrf_score`; `final_score = rrf_score`)
- [x] Config knobs: `hybrid_dense_top_k`, `hybrid_lexical_top_k`, `rrf_k`
- [x] Tests: dense-only side empty, lexical-only, both, ordering by `final_score` only
- [x] Phase 4 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_hybrid_retriever.py tests/ai/rag/test_rrf.py tests/test_vector_store.py`

**Acceptance:**

- Hybrid returns fused `RetrievedCandidate` ranking; owner scope enforced on both channels
- Downstream stages consume `final_score` only
- Flag-off path does not call hybrid

**Exit criteria:**

- Tests pass; user confirmed Phase 4

**Phase 4 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| FTS column | Generated stored `content_tsv` via `to_tsvector('english', content)` + GIN `ix_document_chunks_content_tsv` (Alembic `0005`) |
| `lexical_search` | Mirrors dense signature (`query`, `top_k`, `user_id`, optional `filters`); owner-scoped; same filter push-down |
| Parents | `lexical_search` excludes `chunk_kind=parent`; V1 chunks without `chunk_kind` remain searchable |
| Post-RRF size | Full RRF union sorted by `final_score` (no hybrid-level truncate; later stages cut) |
| Wiring | `HybridRetriever` not wired into pipeline/chat (Phase 10); flag-off stays dense `Retriever` |
| Layout | `hybrid/fusion.py`, `hybrid/retriever.py`, `hybrid/lexical.py` helpers; SQL in `PgVectorStore` |

---

## Phase 5 — Query Rewriting

**Effort:** S

**Deliverables:** `app/ai/rag/rewrite/query_rewriter.py`; prompt `app/ai/prompts/rag/query_rewrite.v1.j2`

**Steps:**

- [x] Implement `LLMQueryRewriter` using `LLMProvider` + `PromptManager`
- [x] Wire into `AdvancedRetrievalPipeline` when `query_rewrite_enabled` and advanced flag on — **at most once** per request
- [x] Ensure rewritten output is never fed back into the rewriter
- [x] Failure → original query; structured log without raw text (counts/latency only)
- [x] Tests with fakes (`tests/fakes.py` patterns), including single-call guarantee
- [x] Phase 5 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_query_rewriter.py`

**Acceptance:**

- Rewrite success and fallback paths covered
- At most one rewrite attempt per request; no recursive rewrite
- No provider SDK imports in rewriter module beyond `LLMProvider`

**Exit criteria:**

- Tests pass; user confirmed Phase 5

**Phase 5 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| Prompt | `rag/query_rewrite.v1.j2`; variable `question` only; return rewritten query only |
| LLM params | Default model for `llm_provider`; temperature `0.0`; `max_tokens=128` |
| Config | `query_rewrite_enabled` default `true`; honoured only with `advanced_rag_enabled` |
| Pipeline gating | Rewrite only when advanced flag + `query_rewrite_enabled` + rewriter present |
| Failure | Exception or empty/whitespace → original query; log latency + `query_rewrite_failed` (no raw text) |
| Normalize | First non-empty line; strip wrapping quotes |
| DI | Not wired into chat/deps (Phase 10); skeleton accepts optional rewriter + settings |

---

## Phase 6 — Cross-Encoder Reranking

**Effort:** M

**Deliverables:** `Reranker` Protocol usage; `app/ai/rag/rerank/cohere.py`; config for Cohere

**Steps:**

- [x] Obtain **user approval** to add `cohere` dependency (or approved HTTP approach) before coding
- [x] Implement `CohereReranker` for model `rerank-v3.5`; set `rerank_score` and `final_score` on `RetrievedCandidate`
- [x] Add `COHERE_API_KEY`, `rerank_provider`, `rerank_model`, `rerank_timeout_ms` to settings + `.env.example`
- [x] Enforce timeout as a fraction of request budget; on timeout/failure keep pre-rerank order/`final_score`
- [x] Fake reranker for unit tests; optional mocked HTTP for adapter
- [x] Phase 6 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_reranker.py tests/ai/rag/test_cohere_reranker.py`

**Acceptance:**

- Pipeline depends on Protocol; Cohere SDK confined to adapter module
- `rerank_top_n` respected; timeout falls back to pre-rerank ordering
- Downstream still orders by `final_score` only

**Exit criteria:**

- Dependency approved + tests pass; user confirmed Phase 6

**Phase 6 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| HTTP client | Existing `httpx` (no `cohere` SDK); user-approved |
| Endpoint | `POST https://api.cohere.com/v2/rerank`; model `rerank-v3.5` |
| Document text | Prefer `parent` when set; else `chunk.content` |
| Config | `COHERE_API_KEY`, `rerank_provider=cohere`, `rerank_model`, `rerank_timeout_ms=1500` |
| `rerank_top_n` | `request.top_k` if set, else `rag_top_k` |
| Failure | Timeout/HTTP/parse/missing key → pre-rerank order + `final_score`; log `rerank_failed` (no raw text) |
| Pipeline gating | Rerank only when `advanced_rag_enabled` + reranker present |
| DI | Not wired into chat/deps (Phase 10); skeleton accepts optional `reranker` + settings |

---

## Phase 7 — Context Compression

**Effort:** S

**Deliverables:** `app/ai/rag/compress/compressor.py`

**Steps:**

- [x] Implement `ContextCompressor` that selects/trims/removes by `final_score` within `rag_context_max_chars`
- [x] **Must not** rewrite, summarize, paraphrase, or otherwise alter document content — original source text only
- [x] Fallback to existing `ContextBuilder` behaviour when compression yields empty/over-budget edge cases (still no paraphrase)
- [x] Tests for budget fit, empty input, single chunk, and “text unchanged” assertions
- [x] Phase 7 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_compressor.py tests/test_rag_service.py`

**Acceptance:**

- Flag-on path uses compressor; flag-off still uses `ContextBuilder` only
- Compressor preserves original source text (select/trim/remove only)
- No raw chunk text in logs

**Exit criteria:**

- Tests pass; user confirmed Phase 7

**Phase 7 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| Class | `FaithfulContextCompressor` in `compress/compressor.py` |
| Ordering | Sort by `final_score` descending before packing |
| Block text | Prefer non-empty `parent`; else `chunk.content` (same as Cohere) |
| Trim | Prefix-truncate original block text to fill remaining budget; then stop |
| Formatting | V1-compatible `[n]` / optional `(source: …)` headers |
| Fallback | Empty pack with non-empty input → `ContextBuilder.build` (V1 tail-drop) |
| Pipeline gating | Compress only when `advanced_rag_enabled` + compressor present |
| DI | Not wired into chat/deps (Phase 10); skeleton accepts optional compressor + settings |
| Logs | `candidate_count` / `included_count` / `max_chars` / `compression_truncated` / `compression_fallback` — no raw text |

---

## Phase 8 — Citations (Backend)

**Effort:** M

**Deliverables:** `app/ai/rag/citations/builder.py`; schema/API additive `citations`; prompt marker updates

**Steps:**

- [x] Implement `CitationBuilder` per Part I § Citation model
- [x] Assign contiguous `[n]` **after** compression, immediately before prompt construction
- [x] Update context formatting so included blocks use `[n]` consistent with citations
- [x] Add `citations` to `ChatResponseSchema`, `RAGAskResponse`, internal RAG dataclasses (additive)
- [x] Extend SSE `retrieval_complete` with `citation_count` (keep `chunk_count`)
- [x] Update `RAGService` + unified chat mapping to populate `citations` when advanced flag on
- [x] Backend tests for schema + builder + API serialization + post-compression numbering
- [x] Phase 8 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_citations.py tests/test_rag_api.py tests/test_rag_service.py`

**Acceptance:**

- `retrieved_chunks` still present; `citations` populated when flag on and chunks included
- Citation indices are contiguous and assigned only after compression
- Prompt markers align with citation indexes; `score` reflects `final_score`

**Exit criteria:**

- Tests pass; user confirmed Phase 8

**Phase 8 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| Hot-path wiring | Option A: `CitationBuilder` wired in pipeline after compress; additive schemas/SSE + mappers; live V1 retrieve→`ContextBuilder` unchanged until Phase 10 |
| `citations` shape | HTTP/`RAGResponse`: `null` on V1/absent; list when advanced path produced citations (may be empty) |
| Snippet config | `citation_snippet_max_chars=240` in Settings + `.env.example`; prefix of original block text (parent preferred) |
| HTTP DTO | Single `CitationSchema` in `chat.py`; imported by `rag.py` |
| Score | Citation `score` = candidate `final_score` (not `chunk.score`) |
| Prompt markers | Compressor `[n]` order = citation indices; answer + document_context prompts mention `[n]` cites |
| Mapping | `to_citation_schemas` in `citations/mapping.py`; router + chat ready; Phase 10 fills live values |

---

## Phase 9 — Indexing Job Interface

**Effort:** S

**Deliverables:** `IndexingJob` Protocol; `SyncIndexingRunner`; thin hook from knowledge ingest; `TODO(epic-9):` markers

**Steps:**

- [x] Define job states: `queued` | `running` | `succeeded` | `failed` (in-memory / DB fields as minimal as needed without a queue product)
- [x] `SyncIndexingRunner` runs existing `IngestionPipeline` / `KnowledgeService` work behind the Protocol
- [x] Leave `TODO(epic-9): QueueIndexingRunner / workers / retries` — do not implement Celery/RQ
- [x] Tests for success/failure status reporting
- [x] Phase 9 complete — user confirmed

**Verify:** `pytest tests/ai/rag/test_indexing_job.py`

**Acceptance:**

- Callers can submit + read status via Protocol
- No external broker/worker introduced

**Exit criteria:**

- Tests pass; user confirmed Phase 9

**Phase 9 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| File bytes | `register_pending_work(PendingIndexingWork)` stages bytes in-memory keyed by `document_id`; frozen Protocol `submit(document_id, user_id)` unchanged |
| Status store | In-memory dict on `SyncIndexingRunner` only (no DB job table) |
| Unknown job | `IndexingJobNotFoundError` from `get_status` |
| Knowledge hook | Always: create pending doc → register work → `submit` → log `indexing_job_id`/`indexing_job_status`; HTTP still calls `ingest_document` |
| Processor | `_run_indexing_work` owns parse→chunk→embed→persist; runner records status and re-raises on failure |
| Failure message | `error_message` = exception type name only (no raw text/bytes) |
| Queue | `TODO(epic-9): QueueIndexingRunner / workers / retries / durable job store` |

---

## Phase 10 — Chat/RAG Integration

**Effort:** M

**Deliverables:** Flag branches in `UnifiedChatService` + `RAGService` + `deps.py`; end-to-end advanced pipeline

**Steps:**

- [x] When `rag_enabled` and `ADVANCED_RAG_ENABLED`: use `AdvancedRetrievalPipeline` instead of V1 `Retriever` alone
- [x] When advanced flag off: exact V1 retrieve → context → merge path
- [x] Preserve guest denial, persistence, `tools_used`, `retrieved_chunks`, agent handoff order
- [x] Wire DI in `app/ai/deps.py`
- [x] Parity tests: flag off regression; flag on advanced happy path (fakes for Cohere/LLM rewrite)
- [x] Update README + `.env.example` for advanced RAG
- [x] Phase 10 complete — user confirmed

**Verify:** `pytest tests/test_unified_chat.py tests/test_rag_service.py tests/ai/rag/test_pipeline_integration.py`

**Acceptance:**

- Flag off: legacy unchanged
- Flag on: rewrite→hybrid→filter→parent→rerank→compress→cite observable in unit/integration tests

**Exit criteria:**

- Parity tests pass; user confirmed Phase 10

**Rollback:**

- Set `ADVANCED_RAG_ENABLED=false`; remove advanced DI branches from hot path; re-run `pytest tests/test_unified_chat.py tests/test_rag_service.py`
- Revert PR if needed

**Phase 10 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| Hot-path branch | `UnifiedChatService` / `RAGService` call pipeline only when `advanced_rag_enabled` + pipeline injected |
| Pipeline retrieve | Prefer `HybridRetriever`; dense `Retriever` kept as test/fallback when hybrid absent |
| Parent expand | `SqlDocumentStore.get_chunk_contents_by_ids` as `ParentContentFetcher` via DI |
| DI | `get_advanced_retrieval_pipeline` wires rewriter, hybrid, parent fetch, Cohere reranker, compressor, citations |
| `retrieved_chunks` | Advanced path: included blocks derived from post-compression citations (score = `final_score`) |
| Citations | List (may be empty) on advanced path; `null` on V1 / flag-off |
| SSE | `retrieval_complete.citation_count` from advanced citations; 0 on V1 |
| Missing Cohere key | Adapter falls back to pre-rerank order (no startup hard-fail) |

---

## Phase 11 — Frontend Citations

**Effort:** S

**Deliverables:** Minimal citation rendering on chat + RAG ask UI

**Steps:**

- [x] Extend TS types for additive `citations` (`frontend/src/types/`, `chatClient.ts`, `ragClient.ts`)
- [x] Render minimal citation list under assistant messages in `MessageBubble` (and `RagAskPanel` as appropriate): index, filename/source, optional page, short snippet
- [x] Preserve existing “Grounded in N document chunks” summary behaviour (enhance, don’t remove without replacement)
- [x] Tests: `MessageBubble.test.tsx`, `RagAskPanel.test.tsx`, client parsing tests
- [x] Phase 11 complete — user confirmed

**Verify:** `cd frontend && npm test -- --run src/components/MessageBubble.test.tsx src/components/RagAskPanel.test.tsx src/api/chatClient.test.ts`

**Acceptance:**

- Citations render when present; no crash when absent/`null`
- No rich overlay/PDF viewer scope creep

**Exit criteria:**

- Frontend tests pass; user confirmed Phase 11

**Phase 11 notes (implementation decisions):**

| Topic | Decision |
| ----- | -------- |
| Citation list | Shared `CitationList` under assistant/`RagAskPanel`; index + filename/source + optional page + snippet |
| Non-streaming chat | `ChatResponse.citations` → `Message.citations` via `ChatPage` / `END_MESSAGE` |
| Streaming chat | No citation payloads in SSE; wire `chunk_count` into “Grounded in N…” only |
| Absent citations | `null` / omitted / `[]` → no list, no crash; grounded summary unchanged |
| Scope | No hover cards, PDF jump, highlight overlays, or SSE snippet streaming |

---

## Phase 12 — Validation & Release

**Effort:** S

**Steps:**

- [x] Full suite: `ADVANCED_RAG_ENABLED=false` then `true` (with fakes/keys as documented)
- [x] Also confirm `AGENT_RUNTIME_ENABLED` flag-off/on still green (no regressions from Epic 01)
- [x] Docker smoke; `make eval`
- [x] Frontend lint/test/build
- [x] Write `docs/releases/post-mvp-v2-epic2-release-summary.md`
- [x] Set Phase status rows to **Completed**; tick DoD
- [ ] Phase 12 complete — user confirmed; Epic 3 authorized

**Verify:** `make test-cov && make eval`

**Acceptance:**

- Part I design acceptance met; coverage ≥80% on `app/` and advanced RAG packages
- Flag-off parity pass

**Exit criteria:**

- Release summary published; user confirmed Phase 12; next epic authorized

**Completion record:**

| Metric                                             | Result                                                                                                      |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Backend tests / coverage                           | Flag-off: **723 passed**, **88.78%** `app/`; flag-on: **723 passed**, **88.79%** `app/`                     |
| Advanced RAG package coverage                      | **95.62%** epic packages (`ai/rag` **96.95%**); gate ≥80%                                                   |
| Eval CLI                                           | **5 passed**, 0 failed (`2026-07-25T15:26:10Z`)                                                             |
| Flag-off regression (`ADVANCED_RAG_ENABLED=false`) | **Pass** — `ADVANCED_RAG_ENABLED=false make test-cov`                                                       |
| Flag-on advanced parity                            | **Pass** — `ADVANCED_RAG_ENABLED=true make test-cov` (V1 tests env-isolated; advanced covered by fakes)     |
| Agent flag regression                              | **Pass** — `AGENT_RUNTIME_ENABLED=false/true make test-cov` (**89.30%** / **88.79%**)                       |
| Frontend tests                                     | lint / format / **186** Vitest / build — all pass                                                           |
| Docker Compose smoke                               | Health **200**, `/api/health/ready` **200** (`db: ok`), frontend **200** (`--profile python`, rebuilt)      |

**Phase 12 notes (validation decisions):**

| Topic | Decision |
| ----- | -------- |
| Flag-on full suite | V1 document/knowledge/eval-runner tests force `advanced_rag_enabled=False` so process `ADVANCED_RAG_ENABLED=true` does not hijack V1 paths |
| Advanced coverage | Phase 10 parity + `tests/ai/rag/**` with fakes (no live Cohere/rewrite required for CI) |
| Docker smoke | Epic-01 style health / ready / frontend; compose stack rebuilt for current code |

---

## Files index

| Path                                                             | Action        | Owner    | Phase       |
| ---------------------------------------------------------------- | ------------- | -------- | ----------- |
| `docs/audits/post-mvp-v2-epic2-phase-0-baseline-audit.md`        | create        | Docs     | 0           |
| `app/core/config.py`                                             | modify        | Core     | 1, 2, 4–6, 8 |
| `backend-python/.env.example`                                    | modify        | Docs     | 1, 2, 6, 8, 10 |
| `app/ai/rag/schemas.py`                                          | modify        | Core     | 1, 8        |
| `app/ai/rag/pipeline.py`                                         | create        | Core     | 1, 5–8, 10  |
| `app/ai/rag/hybrid/**`                                           | create        | Core     | 4           |
| `app/ai/rag/rewrite/**`                                          | create        | Core     | 5           |
| `app/ai/rag/rerank/**`                                           | create        | Adapter  | 6           |
| `app/ai/rag/compress/**`                                         | create        | Core     | 7           |
| `app/ai/rag/citations/**`                                        | create        | Core     | 8           |
| `app/ai/rag/indexing/**`                                         | create        | Core     | 9           |
| `app/ai/interfaces/reranker.py`                                  | create        | Core     | 1, 6        |
| `app/ai/interfaces/query_rewriter.py`                            | create        | Core     | 1, 5        |
| `app/ai/interfaces/context_compressor.py`                        | create        | Core     | 1, 7        |
| `app/ai/interfaces/vector_store.py`                              | modify        | Core     | 3, 4        |
| `app/ai/vectorstores/pgvector.py`                                | modify        | Core     | 3, 4        |
| `app/ai/documents/chunkers/parent_child.py`                      | create        | Core     | 2           |
| `app/ai/rag/parent_expand.py`                                    | create        | Core     | 2           |
| `app/ai/documents/pipeline.py`                                   | modify        | Core     | 2           |
| `app/ai/documents/schemas.py`                                    | modify        | Core     | 2           |
| `app/db/documents.py`                                            | modify        | Core     | 2           |
| `app/services/knowledge_service.py`                              | modify        | Core     | 2, 9        |
| `app/services/document_service.py`                               | modify        | Core     | 2           |
| `app/ai/rag/service.py`                                          | modify        | Core     | 10          |
| `app/services/unified_chat_service.py`                           | modify        | Adapter  | 10          |
| `app/ai/deps.py`                                                 | modify        | Adapter  | 10          |
| `app/schemas/chat.py`                                            | modify        | Core     | 8           |
| `app/schemas/rag.py`                                             | modify        | Core     | 8           |
| `app/ai/prompts/rag/*.j2`                                        | create/modify | Core     | 5, 8        |
| `alembic/versions/*`                                             | create        | Core     | 2?, 4       |
| `tests/ai/rag/**`                                                | create        | Tests    | 1–10        |
| `frontend/src/types/**`, `api/chatClient.ts`, `api/ragClient.ts` | modify        | Frontend | 11          |
| `frontend/src/components/MessageBubble.tsx`, `RagAskPanel.tsx`   | modify        | Frontend | 11          |
| `backend-python/README.md`, root `README.md`                     | modify        | Docs     | 10, 12      |
| `docs/releases/post-mvp-v2-epic2-release-summary.md`             | create        | Docs     | 12          |

## PR map

One PR per phase; branch `v2/epic-02/phase-{pp}-{slug}`.

## Risks

| Risk                             | Mitigation                                                                             |
| -------------------------------- | -------------------------------------------------------------------------------------- |
| Breaks V1 document chat          | Flag default off; Phase 10 rollback; flag-off parity tests                             |
| Cohere outage / missing key      | Rerank failure → keep RRF order; validate key only when advanced path needs it         |
| FTS migration cost / index bloat | GIN on content; measure in Phase 4; document ops notes in release summary              |
| Parent-child re-ingest gap       | Document that existing corpora need re-upload; no silent partial hybrid quality claims |
| Citation/API contract drift      | Additive `citations`; keep `retrieved_chunks`; contract tests                          |
| Provider coupling                | Cohere only in adapter; Protocols in `app/ai/interfaces/`                              |
| Scope creep into Epic 9 queue    | `IndexingJob` + sync runner only; `TODO(epic-9):`                                      |
| Agent boundary erosion           | Invariant: no RAG-in-agent; reuse table forbids agent tool replacement                 |
| New dependency surprises         | Explicit user approval gate in Phase 6                                                 |

## Observability

Structured log fields (no raw query/doc/chunk text by default):

| Field                                         | Purpose                      |
| --------------------------------------------- | ---------------------------- |
| `advanced_rag_enabled`                        | Flag state for the request   |
| `retrieval_latency_ms`                        | End-to-end retrieval         |
| `query_rewrite_latency_ms`                    | Rewrite stage                |
| `query_rewrite_failed`                        | Bool                         |
| `hybrid_dense_count` / `hybrid_lexical_count` | Candidate sizes              |
| `rrf_result_count`                            | Post-fusion size             |
| `rerank_latency_ms` / `rerank_failed`         | Rerank stage                 |
| `compression_truncated`                       | Bool                         |
| `citation_count`                              | Included citations           |
| `indexing_job_id` / `indexing_job_status`     | Ingest job (ids/status only) |

## Definition of done

- [x] Part I components delivered; Part I design acceptance met
- [x] Public APIs stable per Phase 1
- [x] Advanced path behind `ADVANCED_RAG_ENABLED`; V1 unchanged when off; parity when on
- [x] Citations on API + minimal frontend
- [x] `IndexingJob` + sync runner only; Epic 9 queue not implemented
- [x] Tests under `tests/ai/rag/` (+ updated existing); coverage ≥80% on `app/` and epic packages
- [x] `make eval` passes; release summary published
- [ ] All phases **Completed**; user confirmed each (Phases 0–11 confirmed; Phase 12 awaiting user confirm)
- [x] Program DoD: [\_program-v2-execution-guide.md](./_program-v2-execution-guide.md)
- [ ] User authorizes Epic 3

## Changelog

| Date       | Change                                                                                                                                                                                                                                                                                                                                       |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-07-24 | Initial plan (Part I + Part II). Locked: pre-handoff RAG boundary; Postgres FTS + dense + RRF; Cohere `rerank-v3.5`; additive citations + minimal frontend; master `ADVANCED_RAG_ENABLED`; thin `IndexingJob` (Epic 9 queue deferred); extend `app/ai/rag/`.                                                                                 |
| 2026-07-25 | Part I refinements (v2): `RetrievedCandidate` carrier; freeze score semantics (`final_score` only downstream); faithful compressor (no paraphrase); single rewrite; post-compression citation numbering; parent dedupe; rerank latency budget (`rerank_timeout_ms`). Phase acceptance criteria updated. No architecture/phase-order changes. |
| 2026-07-25 | Phase 0 baseline audit published; completion record filled; Phase 0 status → In Progress (pending user confirmation). Note: Epic 01 Phase 12 user-confirm / Epic 02 authorize checkbox still open. Part II only.                                                                                                                             |
| 2026-07-25 | Phase 0 marked Completed (user confirmed). Epic 01 Phase 12 / Epic 02 authorization accepted. No further phases started. Part II only.                                                                                                                                                                                                       |
| 2026-07-25 | Phase 1 scaffold implemented (flag, models, Protocols, pipeline skeleton, package stubs, tests). Status → In Progress pending user confirmation. Part II only. API freeze notes recorded under Phase 1.                                                                                                                                      |
| 2026-07-25 | Phase 1 marked Completed (user confirmed). Phase 2 parent-child chunking implemented: `ParentChildChunker`, metadata `chunk_kind`/`parent_id` (no migration), ingest flag wiring, embed-children-only, standalone `expand_parents` helper + tests. Status → In Progress pending user confirmation. Part II only.                             |
| 2026-07-25 | Phase 3 metadata filtering implemented: additive `filters` on `VectorStore`/`PgVectorStore`/`Retriever`; candidate-stage `apply_metadata_filter`; AND tags/fields; empty sets → empty result; tests in `test_vector_store.py` + `test_metadata_filter.py`. Status → In Progress pending user confirmation. Part II only.                     |
| 2026-07-25 | Phase 4 hybrid retrieval + RRF implemented: `content_tsv` FTS migration, `VectorStore.lexical_search`, `HybridRetriever` + RRF fusion, config knobs, tests. Not wired to chat/pipeline (Phase 10). Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 5 query rewriting implemented: `LLMQueryRewriter`, `query_rewrite.v1.j2`, `query_rewrite_enabled` config, pipeline single-call wiring + failure fallback, tests. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 6 cross-encoder reranking implemented: `CohereReranker` via existing `httpx` (no `cohere` SDK; user-approved), rerank config + `.env.example`, pipeline Protocol wiring with timeout/failure fallback, fake + mocked-HTTP tests. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 7 context compression implemented: `FaithfulContextCompressor` (select/prefix-trim/remove by `final_score`, original text only, `ContextBuilder` empty-pack fallback), pipeline Protocol wiring behind advanced flag, tests. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 8 citations (backend) implemented: `CitationBuilder` post-compress contiguous `[n]`, additive `citations`/`citation_count` schemas + mappers, `citation_snippet_max_chars`, prompt `[n]` guidance; live chat/RAG still V1 until Phase 10. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 9 indexing job interface implemented: `SyncIndexingRunner` (in-memory status + pending work), KnowledgeService thin hook always via Protocol `submit`/`get_status`, `TODO(epic-9)` queue markers, tests. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 10 chat/RAG integration implemented: DI wires full `AdvancedRetrievalPipeline` (hybrid + parent expand + rewrite/rerank/compress/cite); `UnifiedChatService`/`RAGService` flag branches; parity + pipeline integration tests; README/`.env.example` updated. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phase 11 frontend citations implemented: additive TS `Citation` + client fields; `CitationList` in `MessageBubble`/`RagAskPanel`; non-streaming chat wiring; SSE grounded count via `chunk_count` only; tests. Status → In Progress pending user confirmation. Part II only. |
| 2026-07-25 | Phases 2–11 marked Completed (user confirmed deliverables already shipped). Phase 12 status → In Progress. Part II only. |
| 2026-07-25 | Phase 12 validation complete: ADVANCED_RAG / AGENT_RUNTIME flag matrices `make test-cov`, `make eval`, Docker smoke, frontend gates, V1 test isolation for env=`true`, release summary published. Phase 12 status → Completed (pending user confirmation / Epic 3 authorization). Part II only. |
