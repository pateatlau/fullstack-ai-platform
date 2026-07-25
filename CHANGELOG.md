# Changelog

## V2 Epic 2

### Added

- Advanced RAG pipeline behind `ADVANCED_RAG_ENABLED` (hybrid dense + Postgres FTS with RRF, metadata filters, LLM query rewrite, parent-child retrieval, Cohere rerank, context compression)
- Structured citations in chat/RAG APIs and SSE, with minimal frontend citation rendering
- Parent-child chunking and sync indexing job hook for knowledge ingest
- Provider-agnostic RAG protocols (`QueryRewriter`, `Reranker`, `ContextCompressor`) and shared retrieval models

### Changed

- Chat and RAG hot paths optionally route through `AdvancedRetrievalPipeline` when the flag is on; V1 dense-only path remains the default when off
- Compact chat composer: single input + toolbar shell, collapsed provider/model picker, visible tool checkboxes, and hover tooltips for provider/model, web search, documents, and Manage

### Fixed

- Gemini provider compatibility issues
- Chat session isolation between authenticated users
- Clear chat state on logout
