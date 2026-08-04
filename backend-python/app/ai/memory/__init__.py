"""Memory subsystem public API (stable after Phase 1).

See ``docs/plans/post-mvp-v2-epic-05-memory-system.md`` Part I § Public APIs.
Components not yet implemented (``SemanticRetriever``, ``MemoryContextBuilder``,
``MemoryPromptInjector``,
``MemoryPolicyEngine``, ``LifecycleManager``, ``MemoryQualityEvaluator``) are
added in later phases.
"""

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
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.ai.memory.quality import MemoryQualityEvaluator
from app.ai.memory.summarizer import ConversationSummaryService

__all__ = [
    "CandidateMemory",
    "ConversationSummaryService",
    "LifecycleState",
    "MemoryAccessDeniedError",
    "MemoryContext",
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
]
