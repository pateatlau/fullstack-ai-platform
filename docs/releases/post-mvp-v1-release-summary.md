# Post-MVP V1 Release Summary

**Release date:** 2026-07-21
**Validation:** Phase 13 — Final V1 Validation (see [implementation plan](../plans/post-mvp-v1-implementation-plan.md))

## What Shipped

Post-MVP V1 transforms the MVP chatbot into a reusable AI platform on the Python backend, with a minimal frontend surface for documents and generic RAG.

| Capability | Description |
| ---------- | ----------- |
| Prompt infrastructure | Centralized Jinja2 templates with versioning and regression tests; no hardcoded prompts in business logic |
| Tool platform | Full lifecycle: registry → validation → authorization → execution → normalization |
| Web search | First production tool; non-streaming chat path when `TOOLS_ENABLED=true` |
| Knowledge platform | Upload → parse (PDF, DOCX, MD, TXT) → chunk → embed → store |
| pgvector | Primary vector store with `user_id`-scoped similarity search |
| Generic RAG Framework | End-to-end question → retrieved context → LLM response; domain-agnostic (`app/ai/rag/`) |
| Evaluation CLI | Prompt, retrieval, and end-to-end quality measurement |
| Document/RAG API | Auth-only REST endpoints for upload, list, delete, and RAG ask |
| Frontend `/documents` | Separate route for upload, list, delete, and generic RAG UI |
| Observability | Structured log metrics for RAG, retrieval, tools, and ingestion |
| Configuration matrix | Env-driven settings with startup validation and feature flags |

**MVP preserved:** Chat, auth, streaming, persistence, rate limits, and error envelopes remain stable when `RAG_ENABLED=false` and `TOOLS_ENABLED=false` (defaults).

## How to Run Locally

### Docker Compose (recommended)

```bash
# From repository root
docker compose --profile python up -d --build

# Verify health
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/health/ready
```

Postgres uses `pgvector/pgvector:pg16`. Frontend is served on port 80 (nginx); backend on 8000.

### Feature flags

In `backend-python/.env`:

```dotenv
RAG_ENABLED=true          # Enable POST /api/rag/ask
TOOLS_ENABLED=false         # Web search in non-streaming chat (requires WEB_SEARCH_API_KEY)
OPENAI_API_KEY=...          # Required when RAG enabled with EMBEDDING_PROVIDER=openai
```

With both flags off, no new secrets are required and behavior matches the MVP baseline.

### Dev servers

```bash
cd backend-python && cp .env.example .env && uv sync && make run
cd frontend && cp .env.example .env && npm install && npm run dev
```

Authenticated users open `/documents` after Google OAuth login.

## How to Run Eval

```bash
cd backend-python
make eval
```

- Sample dataset: `tests/data/evaluation/sample.yaml`
- JSON report: `.eval/eval-report.json`
- Levels: prompt, retrieval, end-to-end

Offline mode uses fake embeddings and mocked LLM where applicable; retrieval/e2e need local Postgres with pgvector.

## Baseline Metrics Snapshot (Phase 13 — 2026-07-21)

| Metric | Value |
| ------ | ----- |
| Backend tests | 342 passed, 88.25% coverage on `app/`, 12.35s |
| Frontend tests | 106 passed (Vitest) |
| Eval pass count | 5/5 (prompt=2, retrieval=2, e2e=1) |
| Eval retrieval mean precision/recall | 1.0 / 1.0 |
| Eval mean latency (retrieval) | 57.5 ms (soft target: 150 ms) |
| Eval mean latency (e2e) | 49 ms (soft target: 8000 ms) |

Phase 10 baseline comparison: pass count unchanged; retrieval latency variance (14 ms → 57.5 ms) within soft target.

## Known Limitations / V2 Deferrals

- **No streaming RAG** — RAG responses are non-streaming only
- **No tool calling during SSE stream** — tools disabled when `stream=true`
- **Auth-only documents** — guests cannot upload or query corpora
- **Separate `/documents` route** — no unified chat+RAG composer
- **Single embedding provider** — OpenAI only in V1
- **Single vector store** — pgvector only; no Chroma/Pinecone/Qdrant
- **Sync ingestion** — no async queue/worker
- **No hybrid retrieval, reranking, citations, or metadata filtering**
- **No domain-specific RAG apps** — Customer Care, Legal, HR, etc. are post-V1
- **No MCP, memory, agents, or additional tools** beyond web search
- **Node.js AI infrastructure parity** — deferred
- **Staging smoke test** — pending operator credentials (local Docker validated)

## Documentation

- [Implementation plan](../plans/post-mvp-v1-implementation-plan.md)
- [Architecture spec](../references/post-MVP-V1-Architecture-and-Technical-Design-Specs.md)
- [Backend README](../../backend-python/README.md)
- [Frontend README](../../frontend/README.md) — Documents and RAG UI (Phase 12)
