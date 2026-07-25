"""Hybrid dense + lexical retrieval with Reciprocal Rank Fusion."""

from app.ai.rag.hybrid.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.ai.rag.hybrid.retriever import HybridRetriever

__all__ = [
    "DEFAULT_RRF_K",
    "HybridRetriever",
    "reciprocal_rank_fusion",
]
