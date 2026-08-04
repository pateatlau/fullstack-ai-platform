"""Rolling conversation summary façade over V1 ``SessionSummary`` storage.

``ConversationSummaryService`` is the Memory subsystem entry point for session
summaries. It reuses existing ``session_summaries`` persistence and delegates
generation to ``ChatService._maybe_summarize`` — no parallel summary storage.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.memory.models import MemoryContext
from app.ai.prompts.manager import PromptManager
from app.core.caller import CallerContext
from app.core.logging import get_logger
from app.db.models import ChatMessage, SessionSummary
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema, ProviderName

logger = get_logger(__name__)


class SummaryChatStore(Protocol):
    async def get_latest_summary(
        self, session_id: uuid.UUID
    ) -> SessionSummary | None: ...

    async def list_messages_after_seq(
        self, session_id: uuid.UUID, after_seq: int
    ) -> list[ChatMessage]: ...


class SummarizationDelegate(Protocol):
    async def _maybe_summarize(
        self,
        *,
        caller: CallerContext,
        session_id: uuid.UUID,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> None: ...


async def assemble_context_messages(
    chat_store: SummaryChatStore,
    prompt_manager: PromptManager,
    session_id: uuid.UUID,
) -> list[ChatMessageSchema]:
    latest = await chat_store.get_latest_summary(session_id)
    covered = latest.covers_through_seq if latest is not None else 0
    pending = await chat_store.list_messages_after_seq(session_id, covered)

    assembled: list[ChatMessageSchema] = []
    if latest is not None:
        summary_content = prompt_manager.render(
            "chat",
            "context_summary_prefix",
            "1",
            {"summary_content": latest.content},
        )
        assembled.append(
            ChatMessageSchema.model_construct(
                role="system",
                content=summary_content,
            )
        )
    for message in pending:
        assembled.append(
            ChatMessageSchema.model_construct(
                role=message.role, content=message.content
            )
        )
    return assembled


class ConversationSummaryService:
    """Memory façade over ``ChatStore`` summary methods and ``_maybe_summarize``."""

    def __init__(
        self,
        *,
        chat_store: SummaryChatStore,
        prompt_manager: PromptManager,
    ) -> None:
        self._chat_store = chat_store
        self._prompt_manager = prompt_manager

    async def retrieve_summary(self, session_id: uuid.UUID) -> MemoryContext:
        """Return ``MemoryContext`` with ``conversation_summary`` populated."""
        try:
            latest = await self._chat_store.get_latest_summary(session_id)
        except Exception:  # noqa: BLE001 - retrieval must not block chat
            logger.warning(
                "Conversation summary retrieval failed",
                session_id=str(session_id),
                exc_info=True,
            )
            return MemoryContext()
        return MemoryContext(
            conversation_summary=latest.content if latest is not None else None,
            metadata={
                "summary_version": latest.version,
                "covers_through_seq": latest.covers_through_seq,
            }
            if latest is not None
            else {},
        )

    async def build_context_messages(
        self, session_id: uuid.UUID
    ) -> list[ChatMessageSchema]:
        """Deterministic context assembly for persisted sessions (flag-on path)."""
        try:
            return await assemble_context_messages(
                self._chat_store, self._prompt_manager, session_id
            )
        except Exception:  # noqa: BLE001 - fall back to caller-supplied history
            logger.warning(
                "Conversation context assembly failed",
                session_id=str(session_id),
                exc_info=True,
            )
            return []

    async def trigger_summarization(
        self,
        *,
        delegate: SummarizationDelegate,
        caller: CallerContext,
        session_id: uuid.UUID,
        provider: LLMProvider,
        provider_name: ProviderName,
        model: str,
    ) -> None:
        """Invoke existing threshold summarization via ``ChatService``."""
        try:
            await delegate._maybe_summarize(
                caller=caller,
                session_id=session_id,
                provider=provider,
                provider_name=provider_name,
                model=model,
            )
        except Exception:  # noqa: BLE001 - summary is best-effort
            logger.warning(
                "Conversation summarization failed",
                session_id=str(session_id),
                exc_info=True,
            )

    async def clear_summary(
        self, *, session_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """Remove rolling summaries for an owned session."""
        get_owned = getattr(self._chat_store, "get_owned_session", None)
        delete_summaries = getattr(
            self._chat_store, "delete_summaries_for_session", None
        )
        if get_owned is None or delete_summaries is None:
            from app.ai.memory.exceptions import MemoryAccessDeniedError

            raise MemoryAccessDeniedError(
                "Summary clearing is not supported by the configured chat store."
            )

        owned = await get_owned(session_id, user_id=owner_id)
        if owned is None:
            from app.ai.memory.exceptions import MemoryAccessDeniedError

            raise MemoryAccessDeniedError(
                "Access to clear summary for this session is denied."
            )
        await delete_summaries(session_id)
