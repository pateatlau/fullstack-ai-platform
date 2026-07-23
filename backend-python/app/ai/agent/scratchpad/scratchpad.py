"""Ephemeral working memory for a single agent execution (Phase 3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.agent.models.messages import AgentMessage, AgentMessageRole

ScratchpadEntryKind = Literal[
    "system", "user", "assistant", "tool", "thought", "observation"
]

# Compatible with ``LLMProvider`` tool-loop inputs (``ChatMessageInput``).
ScratchpadMessage = AgentMessage | dict[str, object]


class ScratchpadEntry(BaseModel):
    """One append-only note in execution-scoped working memory."""

    kind: ScratchpadEntryKind
    content: str = Field(min_length=1)
    tool_call_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    def to_message(self) -> ScratchpadMessage:
        """Convert this entry to a provider-compatible message shape."""
        if self.kind == "tool":
            if self.tool_call_id is None:
                raise ValueError("tool scratchpad entries require tool_call_id")
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "content": self.content,
            }

        role: AgentMessageRole
        if self.kind in ("thought", "observation"):
            # ReAct-style working notes are surfaced as assistant context.
            role = "assistant"
        else:
            role = self.kind  # system | user | assistant

        prefix = ""
        if self.kind == "thought":
            prefix = "Thought: "
        elif self.kind == "observation":
            prefix = "Observation: "

        return AgentMessage(role=role, content=f"{prefix}{self.content}")


class Scratchpad:
    """In-memory scratchpad for one agent execution — never persisted."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._entries: list[ScratchpadEntry] = []

    @property
    def entries(self) -> tuple[ScratchpadEntry, ...]:
        """Return an immutable view of stored entries."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, entry: ScratchpadEntry) -> None:
        """Append an entry to working memory."""
        self._entries.append(entry)

    def append_thought(
        self, content: str, *, metadata: dict[str, object] | None = None
    ) -> None:
        """Record planner/reasoning text."""
        self.append(
            ScratchpadEntry(
                kind="thought",
                content=content,
                metadata=dict(metadata or {}),
            )
        )

    def append_observation(
        self, content: str, *, metadata: dict[str, object] | None = None
    ) -> None:
        """Record an environment or tool observation."""
        self.append(
            ScratchpadEntry(
                kind="observation",
                content=content,
                metadata=dict(metadata or {}),
            )
        )

    def append_message(self, message: AgentMessage) -> None:
        """Record a conversational turn."""
        self.append(
            ScratchpadEntry(
                kind=message.role,
                content=message.content,
            )
        )

    def append_tool_result(
        self,
        *,
        tool_call_id: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Record a tool result for the provider tool loop."""
        self.append(
            ScratchpadEntry(
                kind="tool",
                content=content,
                tool_call_id=tool_call_id,
                metadata=dict(metadata or {}),
            )
        )

    def extend_messages(self, messages: list[AgentMessage]) -> None:
        """Seed or extend the scratchpad from request/history messages."""
        for message in messages:
            self.append_message(message)

    def clear(self) -> None:
        """Drop all entries (execution teardown)."""
        self._entries.clear()

    def to_message_context(self) -> list[ScratchpadMessage]:
        """Build provider-compatible messages from scratchpad entries."""
        return [entry.to_message() for entry in self._entries]
