"""Memory subsystem public API (stable after Phase 1).

See ``docs/plans/post-mvp-v2-epic-05-memory-system.md`` Part I § Public APIs.
Components not yet implemented (``MemoryPromptInjector``, ``MemoryPolicyEngine``,
``LifecycleManager``) are added in later phases.
"""

from app.ai.memory.context_builder import MemoryContextBuilder
from app.ai.memory.exceptions import (
    MemoryAccessDeniedError,
    MemoryError,
    MemoryNotFoundError,
)
from app.ai.memory.interfaces import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.events import MemoryEvent, MemoryEventType
from app.ai.memory.extraction import CandidateMemory, MemoryExtractor
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import (
    MemoryContext,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    UserPreferenceItem,
    UserPreferenceListResponse,
    UserPreferenceUpsert,
)
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.ai.memory.quality import MemoryQualityEvaluator
from app.ai.memory.semantic_retriever import SemanticRetriever
from app.ai.memory.summarizer import ConversationSummaryService

__all__ = [
    "CandidateMemory",
    "ConversationSummaryService",
    "LifecycleState",
    "MemoryAccessDeniedError",
    "MemoryContext",
    "MemoryContextBuilder",
    "MemoryError",
    "MemoryEvent",
    "MemoryEventType",
    "MemoryExtractor",
    "MemoryManager",
    "MemoryNotFoundError",
    "MemoryProvider",
    "MemoryQualityEvaluator",
    "MemoryRecord",
    "MemoryScope",
    "MemoryType",
    "PgVectorMemoryProvider",
    "SemanticRetriever",
    "UserPreferenceItem",
    "UserPreferenceListResponse",
    "UserPreferenceUpsert",
]
