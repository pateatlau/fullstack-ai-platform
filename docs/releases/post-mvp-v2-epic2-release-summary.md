# Post-MVP V2 Epic 02 Release Summary

**Release name:** Post-MVP V2 Epic 02 — Advanced RAG
**Release date:** 2026-07-25
**Validation:** Phase 12 final acceptance (see [post-mvp-v2-epic-02-advanced-rag.md](../plans/post-mvp-v2-epic-02-advanced-rag.md))
**Git commit (validation base):** `a8dddc9` + Phase 12 test-isolation / docs on branch `feat/v2-epic-02-advanced-rag-phase-12`

---

## Summary vs Epic 01

Epic 01 shipped a reusable agent runtime behind `AGENT_RUNTIME_ENABLED`. **V2 Epic 02 extends the existing V1 RAG stack** under `app/ai/rag/` (hybrid FTS + dense RRF, query rewrite, parent-child retrieval, Cohere rerank, faithful compression, structured citations, thin indexing job) behind `ADVANCED_RAG_ENABLED` (default **off**).

| Area | Epic 01 / V1 RAG | V2 Epic 02 |
| ---- | ---------------- | ---------- |
| Document retrieval | Dense `Retriever` → `ContextBuilder` | Same when flag off; `AdvancedRetrievalPipeline` when on |
| Hybrid / FTS | None | Postgres `tsvector` + dense → RRF |
| Rerank | None | Cohere `rerank-v3.5` via `httpx` adapter (Protocol) |
| Citations | `retrieved_chunks` debug meta only | Additive `citations` + minimal frontend list |
| Chunking | `RecursiveChunker` | Parent-child when advanced flag on (re-upload for existing docs) |
| Indexing | Sync ingest in-request | `IndexingJob` + `SyncIndexingRunner` (`TODO(epic-9)` queue) |
| Agent boundary | Pre-handoff RAG in `UnifiedChatService` | Unchanged — no RAG-in-agent |

---

## Delivered (Phases 0–11)

| Phase | Deliverable |
| ----- | ----------- |
| 0 | Baseline audit |
| 1 | Models/Protocols, `ADVANCED_RAG_ENABLED`, pipeline skeleton |
| 2 | `ParentChildChunker` + parent expand helper |
| 3 | Metadata filters on store + candidate stage |
| 4 | FTS migration, `lexical_search`, `HybridRetriever` + RRF |
| 5 | `LLMQueryRewriter` (at most once; failure → original) |
| 6 | `CohereReranker` (`httpx`; timeout → pre-rerank order) |
| 7 | `FaithfulContextCompressor` (select/trim/remove only) |
| 8 | `CitationBuilder` + additive API/`citation_count` SSE |
| 9 | `IndexingJob` + `SyncIndexingRunner` |
| 10 | Chat/RAG DI + flag branches |
| 11 | Minimal frontend citation rendering |

**Stable public APIs** (Phase 1 freeze): `AdvancedRetrievalPipeline`, `RetrievalRequest` / `RetrievalResult` / `RetrievedCandidate` / `Citation` / `MetadataFilter`; Protocols `QueryRewriter`, `Reranker`, `ContextCompressor`, `IndexingJob`; additive `VectorStore` filters + `lexical_search`.

---

## Feature flag

| Variable | Default | Behaviour |
| -------- | ------- | --------- |
| `ADVANCED_RAG_ENABLED` | `false` | Off: V1 dense retrieve → context. On (with `RAG_ENABLED`): rewrite → hybrid → filter → parent expand → rerank → compress → cite on document chat and `/api/rag/ask`. |

Optional: `COHERE_API_KEY` for rerank (missing key keeps RRF order). Parent-child ingest applies when the advanced flag is on; existing corpora keep V1 chunks until re-uploaded.

**Rollback:** set `ADVANCED_RAG_ENABLED=false` (no breaking API contract change; `citations` remain additive/`null` on V1).

---

## Breaking Changes

**None.** Chat/RAG HTTP contracts are additive (`citations`, SSE `citation_count`). Default flag remains off.

---

## Migration / Upgrade Notes

1. Pull release; ensure `backend-python/.env.example` includes `ADVANCED_RAG_ENABLED=false` and advanced knobs (`hybrid_*`, `rrf_k`, `query_rewrite_enabled`, `rerank_*`, parent/child sizes, `citation_snippet_max_chars`).
2. Run Alembic migrations (FTS `content_tsv` + GIN from Phase 4 — migration `0005`).
3. Keep the flag **off** in production until you intentionally enable advanced retrieval.
4. To exercise locally: `RAG_ENABLED=true`, `ADVANCED_RAG_ENABLED=true`, optional `COHERE_API_KEY`; re-upload documents for parent-child quality.
5. Ops: GIN index on `document_chunks.content_tsv` — monitor size/write cost on large corpora.

---

## Known Limitations and Deferred Items

| Item | Status |
| ---- | ------ |
| Default flag flip to `true` | Deferred — requires explicit ops decision |
| Queue-backed indexing / workers / retries | Epic 9 (`TODO(epic-9):`) |
| Rich citation UX (hover, PDF jump, SSE snippets) | Out of Epic 02 scope |
| RAG eval harness expansion | Epic 6 |
| MCP / memory / workflows / HITL | Later epics |
| Existing V1 corpora without re-upload | No silent hybrid quality claims |

---

## Verification Metrics (Phase 12 — 2026-07-25)

| Gate | Result |
| ---- | ------ |
| Flag-off `ADVANCED_RAG_ENABLED=false make test-cov` | **723 passed**, **88.78%** coverage on `app/` |
| Flag-on `ADVANCED_RAG_ENABLED=true make test-cov` | **723 passed**, **88.79%** coverage on `app/` |
| Advanced RAG epic packages | **95.62%** (`ai/rag` + documents + vectorstores + related interfaces; gate ≥80%) |
| `ai/rag/` package | **96.95%** |
| Agent flag-off `AGENT_RUNTIME_ENABLED=false make test-cov` | **723 passed**, **89.30%** `app/` |
| Agent flag-on `AGENT_RUNTIME_ENABLED=true make test-cov` | **723 passed**, **88.79%** `app/` |
| `make eval` | **5/5** passed (`backend-python/.eval/eval-report.json`, timestamp `2026-07-25T15:26:10Z`) |
| Frontend | lint / format / **186** Vitest / build — all pass |
| Docker Compose smoke (`--profile python`, rebuilt) | Health **200**, `/api/health/ready` **200** (`db: ok`), frontend **200** |

**Test isolation note:** V1 document-path / knowledge / eval-runner tests force `advanced_rag_enabled=False` (or clear process `ADVANCED_RAG_ENABLED`) so full-suite runs with `ADVANCED_RAG_ENABLED=true` remain green. Advanced behaviour is covered by `tests/ai/rag/` and Phase 10 parity tests with fakes.

---

## References

- Epic plan: [docs/plans/post-mvp-v2-epic-02-advanced-rag.md](../plans/post-mvp-v2-epic-02-advanced-rag.md)
- Phase 0 audit: [docs/audits/post-mvp-v2-epic2-phase-0-baseline-audit.md](../audits/post-mvp-v2-epic2-phase-0-baseline-audit.md)
- Prior release: [docs/releases/post-mvp-v2-epic1-release-summary.md](./post-mvp-v2-epic1-release-summary.md)
- Backend reference: [backend-python/README.md](../../backend-python/README.md)
- Docker local dev: [DOCKER_COMPOSE.md](../../DOCKER_COMPOSE.md)
