"""``MemoryPromptInjector`` — augments chat messages with retrieved Memory context.

Pure prompt-assembly layer: consumes an already-built ``MemoryContext`` and
never touches storage (Part I § MemoryContext boundary). Conversation summary
text is injected separately by the existing Phase 2 ``context_summary_prefix``
path, so only user/project memories and preferences are rendered here.
"""

from __future__ import annotations

from app.ai.memory.models import MemoryContext, MemoryRecord
from app.ai.prompts.manager import PromptManager
from app.schemas.chat import ChatMessageSchema


class MemoryPromptInjector:
    """Renders ``chat/memory_context/v1`` and prepends it as a system message."""

    def __init__(self, prompt_manager: PromptManager) -> None:
        self._prompt_manager = prompt_manager

    def inject(
        self,
        messages: list[ChatMessageSchema],
        context: MemoryContext,
    ) -> list[ChatMessageSchema]:
        """Prepend a rendered memory block, or return ``messages`` unchanged.

        Renders ``conversation_memories``, ``user_memories``, ``project_memories``,
        and ``preferences`` (Part I § MemoryContext ordering) — ``conversation_summary``
        is injected separately (existing ``context_summary_prefix`` path) to avoid
        duplicating it in-prompt.
        """
        memory_content = _render_memory_content(context)
        if not memory_content:
            return messages

        rendered = self._prompt_manager.render(
            "chat", "memory_context", "1", {"memory_content": memory_content}
        )
        memory_message = ChatMessageSchema.model_construct(
            role="system", content=rendered
        )
        return [memory_message, *messages]


def _render_memory_content(context: MemoryContext) -> str:
    sections = [
        _format_memory_section(
            "Relevant moments from this conversation:",
            context.conversation_memories,
        ),
        _format_memory_section(
            "What you know about this user from earlier conversations:",
            context.user_memories,
        ),
        _format_memory_section(
            "Relevant context remembered for this project/session:",
            context.project_memories,
        ),
        _format_preferences_section(context.preferences),
    ]
    return "\n\n".join(section for section in sections if section)


def _format_memory_section(heading: str, records: list[MemoryRecord]) -> str:
    if not records:
        return ""
    lines = "\n".join(f"- {record.content}" for record in records)
    return f"{heading}\n{lines}"


def _format_preferences_section(preferences: dict[str, object]) -> str:
    if not preferences:
        return ""
    lines = "\n".join(
        f"- {key}: {value}"
        for key, value in sorted(preferences.items(), key=lambda item: item[0])
    )
    return f"User preferences to honor:\n{lines}"
