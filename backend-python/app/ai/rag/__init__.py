"""Generic RAG framework components (domain-agnostic)."""

from app.ai.rag.context_builder import BuiltContext, ContextBuilder
from app.ai.rag.pipeline import (
    AdvancedRetrievalPipeline,
    DefaultAdvancedRetrievalPipeline,
)
from app.ai.rag.prompt_builder import BuiltPrompt, PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    Citation,
    IndexingJobState,
    IndexingJobStatus,
    MetadataFilter,
    RAGResponse,
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
    RetrievedChunkMeta,
)
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE, RAGService

__all__ = [
    "AdvancedRetrievalPipeline",
    "BuiltContext",
    "BuiltPrompt",
    "Citation",
    "ContextBuilder",
    "DefaultAdvancedRetrievalPipeline",
    "EMPTY_CORPUS_MESSAGE",
    "IndexingJobState",
    "IndexingJobStatus",
    "MetadataFilter",
    "PromptBuilder",
    "RAGResponse",
    "RAGService",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievedCandidate",
    "RetrievedChunkMeta",
    "Retriever",
]
