# Post-MVP V2 Epic 02 — Phase 0 Baseline Audit

**Epic:** v2-02 Advanced RAG
**Audit date:** 2026-07-25
**Auditor:** Cursor agent (Phase 0 execution)
**Git commit:** `feb1b91` — `fix: route Vite /api proxy via 127.0.0.1 so localhost Google login skips unconfigured Docker backends (#83)`
**Depends on:** Epic 01 (v2-01 Agent Framework) — Phase 0 sign-off accepted Epic 02 authorization (2026-07-25)

---

## Executive summary

Phase 0 baseline audit for Epic 02 (Advanced RAG). **All backend and frontend quality gates pass.** No advanced RAG package, flag, hybrid/FTS, rerank, parent-child chunker, or citations API/UI exists yet — safe to scaffold in Phase 1 under existing `app/ai/rag/`.

| Gate area | Result |
| --------- | ------ |
| Epic 01 Phase 12 validation | ✅ Release summary + completion metrics present |
| Epic 01 Phase 12 user confirm / Epic 02 authorize | ✅ Confirmed by user with Phase 0 sign-off (2026-07-25) |
| Backend lint / format / typecheck | ✅ Pass |
| Backend test-cov (≥80%) | ✅ Pass — 604 tests, **87.61%** `app/` |
| Backend eval CLI | ✅ Pass — 5/5 |
| Frontend lint / format / build | ✅ Pass |
| Frontend Vitest | ✅ Pass — **177/177** |
| Advanced RAG conflicts | ✅ None (`ADVANCED_RAG_ENABLED` absent; no hybrid/rerank/citations modules) |

**Recommendation:** Phase 0 complete. Await explicit user instruction before Phase 1.

---

## Epic 01 completion confirmation

| Evidence | Location |
| -------- | -------- |
| Release summary | `docs/releases/post-mvp-v2-epic1-release-summary.md` (2026-07-24) |
| Phase 12 validation metrics | `docs/plans/post-mvp-v2-epic-01-agent-framework.md` — Phase 12 Completion record |
| Merged validation commit | `de1035c` — `chore: complete V2 Epic 01 Phase 12 validation…` (#81) |
| Epic 02 baseline (copied) | Epic 02 Part II § Baseline matches Epic 01 Phase 12 metrics |

| Open item | Status |
| --------- | ------ |
| Epic 01 / Epic 02 authorization for continuing Epic 02 | ✅ Accepted with Epic 02 Phase 0 sign-off (2026-07-25) |

Epic 02 Phase 0 is complete. Further phases await explicit user instruction.

---

## Quality gate results

### Backend (`backend-python/`)

Commands run from `backend-python/` on 2026-07-25.

| Command | Result | Notes |
| ------- | ------ | ----- |
| `make lint` | ✅ Pass | Ruff — all checks passed |
| `make format-check` | ✅ Pass | 226 files already formatted |
| `make typecheck` | ✅ Pass | Pyright — 0 errors |
| `make test-cov` | ✅ Pass | **604 passed**; **87.61%** coverage on `app/` (gate ≥80%) |
| `make eval` | ✅ Pass | 5 passed, 0 failed, 0 skipped |

**Eval detail** (`2026-07-25T05:11:59.979130+00:00`):

| Level | Passed | Failed | Skipped |
| ----- | ------ | ------ | ------- |
| prompt | 2 | 0 | 0 |
| retrieval | 2 | 0 | 0 |
| e2e | 1 | 0 | 0 |

Report: `backend-python/.eval/eval-report.json`

**Verify command** (`make lint && make typecheck && make test-cov && make eval`): ✅ Pass (individual gates above).

### Frontend (`frontend/`)

| Command | Result | Notes |
| ------- | ------ | ----- |
| `npm run lint` | ✅ Pass | ESLint clean |
| `npm run format:check` | ✅ Pass | Prettier clean |
| `npm test -- --run` | ✅ Pass | **177 passed** (35 files) |
| `npm run build` | ✅ Pass | `tsc -b && vite build` succeeded |

---

## Current RAG behaviour (baseline)

Documented from live module paths — V1 dense-only stack; no advanced pipeline.

```text
Upload → KnowledgeService.ingest_document
           → IngestionPipeline (RecursiveChunker → embed → PgVectorStore)
           → Document / DocumentChunk rows (sync, in-request)

Question → Retriever (embed + VectorStore.similarity_search)
         → ContextBuilder (char budget, numbered [n] blocks in prompt only)
         → PromptBuilder → LLM
         → retrieved_chunks debug meta on Chat / RAG ask responses

[Unified chat] UnifiedChatService pre-handoff retrieve+merge when use_documents
[Agent] Optional AGENT_RUNTIME_ENABLED path after RAG merge (Epic 01); no RAG-in-agent
```

| Behaviour | Current state |
| --------- | ------------- |
| Retrieval | Dense cosine only via `PgVectorStore.similarity_search` (owner-scoped) |
| Hybrid / FTS | **Absent** — no `lexical_search`, no `tsvector`/GIN |
| Query rewrite | **Absent** |
| Rerank | **Absent** — no Cohere / `Reranker` Protocol |
| Chunking | `RecursiveChunker` only; no `ParentChildChunker` |
| Compression | V1 `ContextBuilder` tail-drop by `rag_context_max_chars` |
| Citations API | **Absent** — no `citations` field; prompt uses `[n]` blocks without structured `Citation` DTOs |
| Frontend citations | **Absent** — count summary only (“Grounded in N…”, “Retrieved N chunks…”) |
| Indexing jobs | Sync in-process ingest in `KnowledgeService`; no `IndexingJob` Protocol |
| Feature flag | `ADVANCED_RAG_ENABLED` **not present**; `rag_enabled` remains V1 gate |

---

## Path inventory

Real module paths only (as of `feb1b91`).

### `backend-python/app/ai/rag/` (extend — do not fork)

| Path | Lines | Role |
| ---- | ----- | ---- |
| `app/ai/rag/__init__.py` | 19 | Public exports: `Retriever`, `ContextBuilder`, `PromptBuilder`, `RAGService`, schemas |
| `app/ai/rag/retriever.py` | 62 | V1 dense retrieve |
| `app/ai/rag/context_builder.py` | 66 | Char-budget context; prompt `[n]` formatting |
| `app/ai/rag/prompt_builder.py` | 69 | Jinja RAG prompt assembly |
| `app/ai/rag/schemas.py` | 29 | `RetrievedChunkMeta`, `RAGResponse` only |
| `app/ai/rag/service.py` | 247 | Non-streaming `RAGService.ask` |

**Not present (expected for Phase 1+):** `pipeline.py`, `hybrid/`, `rewrite/`, `rerank/`, `compress/`, `citations/`, `indexing/`.

### `backend-python/app/ai/documents/`

| Path | Role |
| ---- | ---- |
| `app/ai/documents/pipeline.py` | `IngestionPipeline` — parse → `RecursiveChunker` → embed → persist |
| `app/ai/documents/chunkers/recursive.py` | Default / only chunker |
| `app/ai/documents/chunkers/base.py` | Chunker Protocol |
| `app/ai/documents/parsers/*` | PDF / DOCX / text + router |
| `app/ai/documents/schemas.py` | `DocumentChunk`, `ParsedDocument` |

**Not present:** `chunkers/parent_child.py`.

### Vector store / interfaces

| Path | Role |
| ---- | ---- |
| `app/ai/interfaces/vector_store.py` | `ScoredChunk`, `VectorStore` Protocol (`upsert`, `similarity_search`, `delete_by_document`) — **no filters / lexical** |
| `app/ai/interfaces/embedding_provider.py` | Embedding Protocol (unchanged for Epic 02) |
| `app/ai/vectorstores/pgvector.py` | Dense HNSW/cosine search; owner join on `Document.user_id` |

**Not present under `app/ai/interfaces/`:** `reranker.py`, `query_rewriter.py`, `context_compressor.py`.

### Services / DB / schemas

| Path | Role |
| ---- | ---- |
| `app/services/unified_chat_service.py` (1089 lines) | Pre-handoff `Retriever` + `ContextBuilder`; SSE `retrieval_*`; agent branch after RAG |
| `app/services/knowledge_service.py` (157 lines) | Sync ingest lifecycle; no job queue |
| `app/db/models.py` — `Document`, `DocumentChunk` | Chunks: `content`, `metadata_json`, `embedding`; **no FTS column / parent_id column** |
| `app/schemas/chat.py` | `RetrievedChunkMetaSchema`, optional `retrieved_chunks` on `ChatResponseSchema`; SSE `RetrievalCompleteFrame` has `chunk_count` only |
| `app/schemas/rag.py` | `RAGAskRequest` / `RAGAskResponse` with `retrieved_chunks`; **no `citations`** |
| `app/ai/deps.py` | DI for RAG / embeddings / vector store (Phase 10 wiring target) |
| `app/core/config.py` | `rag_enabled`, `rag_top_k`, `rag_context_max_chars`, chunk/embed settings; **no advanced RAG knobs** |

### Frontend citation-related surfaces

| Path | Current behaviour |
| ---- | ----------------- |
| `frontend/src/types/rag.ts` | `RetrievedChunkMeta` + `RAGAskResponse.retrieved_chunks` only |
| `frontend/src/api/ragClient.ts` | RAG ask client |
| `frontend/src/api/chatClient.ts` | Optional `retrieved_chunks` on chat response |
| `frontend/src/components/MessageBubble.tsx` | “Grounded in N document chunk(s).” — no citation list |
| `frontend/src/components/RagAskPanel.tsx` | “Retrieved N chunks…” — no citation list |
| `frontend/src/pages/ChatPage.tsx` | Maps `retrieved_chunks.length` → `retrievedChunkCount` |

---

## Advanced RAG conflict check

| Check | Result |
| ----- | ------ |
| `ADVANCED_RAG_ENABLED` in config / `.env.example` | **Not present** |
| `app/ai/rag/pipeline.py` / hybrid / rewrite / rerank / compress / citations / indexing | **Do not exist** |
| `app/ai/documents/chunkers/parent_child.py` | **Does not exist** |
| `app/ai/interfaces/{reranker,query_rewriter,context_compressor}.py` | **Do not exist** |
| HTTP `citations` on chat/RAG schemas | **Absent** |
| Frontend citation list UI | **Absent** |
| Parallel `app/ai/advanced_rag/` package | **Does not exist** |

No naming conflicts or partial implementations. Phase 1 scaffold is clear.

---

## Baseline metrics vs epic plan

Epic 02 Part II § Baseline (from Epic 01 Phase 12) vs this audit:

| Metric | Epic plan baseline | This audit | Delta |
| ------ | ------------------ | ---------- | ----- |
| Backend tests | 604 passed | **604 passed** | — |
| Backend `app/` coverage | Flag-off 88.19% / flag-on 87.61% | **87.61%** (`make test-cov` default settings) | Matches flag-on recorded figure |
| Frontend tests | (not in Epic 02 baseline table) | **177 passed** | — |
| Eval CLI | 5 passed (`2026-07-23T23:55:38Z`) | **5 passed** (`2026-07-25T05:11:59Z`) | — |
| Advanced RAG code | None | None | — |

---

## Phase 0 acceptance checklist

| Criterion | Status |
| --------- | ------ |
| All quality gates pass | ✅ Backend + frontend |
| Inventory documents real module paths only | ✅ |
| No repository code changes (app/frontend) | ✅ (audit + epic plan Phase 0 status only) |
| Audit doc published | ✅ |
| Baseline recorded | ✅ |
| Epic 01 Phase 12 / Epic 02 authorized | ✅ |
| User confirmed Phase 0 | ✅ |

---

## Completion record

| Metric | Result |
| ------ | ------ |
| Backend tests / coverage | **604 passed**, **87.61%** `app/` |
| Frontend tests | **177 passed** (35 files) |
| Eval CLI | **5 passed**, 0 failed, 0 skipped |
| Git commit | `feb1b91` |
| Audit doc | `docs/audits/post-mvp-v2-epic2-phase-0-baseline-audit.md` |
| Frontend lint / format / build | Pass |
| Advanced RAG present | No |

---

## Open items for user

None for Phase 0. User confirmed Phase 0 complete on 2026-07-25; do not start Phase 1 until explicitly requested.
