"""Generic RAG framework components (domain-agnostic)."""

from app.ai.rag.context_builder import BuiltContext, ContextBuilder
from app.ai.rag.prompt_builder import BuiltPrompt, PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RAGResponse, RetrievedChunkMeta
from app.ai.rag.service import EMPTY_CORPUS_MESSAGE, RAGService

__all__ = [
    "BuiltContext",
    "BuiltPrompt",
    "ContextBuilder",
    "EMPTY_CORPUS_MESSAGE",
    "PromptBuilder",
    "RAGResponse",
    "RAGService",
    "RetrievedChunkMeta",
    "Retriever",
]
