"""Tests for ``MemoryPromptInjector`` (Phase 8 chat prompt-assembly integration)."""

from __future__ import annotations

import datetime
import uuid

from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.prompt_injector import MemoryPromptInjector
from app.ai.prompts.manager import create_prompt_manager
from app.schemas.chat import ChatMessageSchema

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _record(content: str, *, memory_type: MemoryType = MemoryType.USER) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=memory_type,
        scope=(
            MemoryScope.USER if memory_type is MemoryType.USER else MemoryScope.PROJECT
        ),
        owner_id=uuid.uuid4(),
        project_id=uuid.uuid4() if memory_type is MemoryType.PROJECT else None,
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


def _messages() -> list[ChatMessageSchema]:
    return [ChatMessageSchema(role="user", content="What's the weather like?")]


def _injector() -> MemoryPromptInjector:
    return MemoryPromptInjector(create_prompt_manager())


class TestMemoryPromptInjector:
    def test_empty_context_returns_messages_unchanged(self) -> None:
        injector = _injector()
        messages = _messages()

        result = injector.inject(messages, MemoryContext())

        assert result is messages

    def test_conversation_summary_alone_does_not_trigger_injection(self) -> None:
        """Summary text is injected separately (context_summary_prefix); the
        Memory block only covers memories/preferences.
        """
        injector = _injector()
        messages = _messages()

        result = injector.inject(
            messages, MemoryContext(conversation_summary="Earlier, we discussed X.")
        )

        assert result is messages

    def test_user_memories_are_prepended_as_a_system_message(self) -> None:
        injector = _injector()
        messages = _messages()
        context = MemoryContext(user_memories=[_record("Prefers concise answers.")])

        result = injector.inject(messages, context)

        assert len(result) == len(messages) + 1
        assert result[0].role == "system"
        assert "Prefers concise answers." in result[0].content
        assert result[1:] == messages

    def test_conversation_memories_are_rendered(self) -> None:
        injector = _injector()
        context = MemoryContext(
            conversation_memories=[_record("Asked about async generators earlier.")]
        )

        result = injector.inject(_messages(), context)

        assert result[0].role == "system"
        assert "Asked about async generators earlier." in result[0].content

    def test_project_memories_and_preferences_are_both_rendered(self) -> None:
        injector = _injector()
        context = MemoryContext(
            project_memories=[
                _record("Uses PostgreSQL.", memory_type=MemoryType.PROJECT)
            ],
            preferences={"response_tone": "formal"},
        )

        result = injector.inject(_messages(), context)

        block = result[0].content
        assert "Uses PostgreSQL." in block
        assert "response_tone" in block
        assert "formal" in block

    def test_injected_message_does_not_mutate_input_list(self) -> None:
        injector = _injector()
        messages = _messages()
        original_len = len(messages)
        context = MemoryContext(user_memories=[_record("Some fact.")])

        injector.inject(messages, context)

        assert len(messages) == original_len
