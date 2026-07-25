"""AI framework protocols added incrementally per phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "ContextCompressor",
    "EmbeddingProvider",
    "IndexingJob",
    "QueryRewriter",
    "Reranker",
    "ScoredChunk",
    "ToolHandler",
    "VectorStore",
]

if TYPE_CHECKING:
    from app.ai.interfaces.context_compressor import ContextCompressor
    from app.ai.interfaces.embedding_provider import EmbeddingProvider
    from app.ai.interfaces.indexing_job import IndexingJob
    from app.ai.interfaces.query_rewriter import QueryRewriter
    from app.ai.interfaces.reranker import Reranker
    from app.ai.interfaces.tool_handler import ToolHandler
    from app.ai.interfaces.vector_store import ScoredChunk, VectorStore


def __getattr__(name: str) -> object:
    if name == "ContextCompressor":
        from app.ai.interfaces.context_compressor import ContextCompressor

        return ContextCompressor
    if name == "EmbeddingProvider":
        from app.ai.interfaces.embedding_provider import EmbeddingProvider

        return EmbeddingProvider
    if name == "IndexingJob":
        from app.ai.interfaces.indexing_job import IndexingJob

        return IndexingJob
    if name == "QueryRewriter":
        from app.ai.interfaces.query_rewriter import QueryRewriter

        return QueryRewriter
    if name == "Reranker":
        from app.ai.interfaces.reranker import Reranker

        return Reranker
    if name == "ScoredChunk":
        from app.ai.interfaces.vector_store import ScoredChunk

        return ScoredChunk
    if name == "ToolHandler":
        from app.ai.interfaces.tool_handler import ToolHandler

        return ToolHandler
    if name == "VectorStore":
        from app.ai.interfaces.vector_store import VectorStore

        return VectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
