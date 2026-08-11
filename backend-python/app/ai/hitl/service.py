"""Agent tool-call approval orchestration (Epic 09)."""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.plan import PlannedStep
from app.ai.agent.models.state import AgentExecutionState
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.ai.hitl.exceptions import AgentApprovalPauseError
from app.ai.hitl.models import AgentToolApproval, ProposedToolCall
from app.db.models import ChatMessage


class AgentToolApprovalWriter(Protocol):
    async def create(
        self,
        *,
        session_id: uuid.UUID,
        owner_id: uuid.UUID,
        execution_id: str,
        approval_correlation_id: uuid.UUID,
        proposed_calls: list[ProposedToolCall],
        paused_scratchpad: list[dict[str, object]],
        paused_state: dict[str, object],
    ) -> AgentToolApproval: ...

    async def link_pending_message(
        self,
        approval_id: uuid.UUID,
        *,
        pending_message_id: uuid.UUID,
    ) -> AgentToolApproval | None: ...


class HitlChatStore(Protocol):
    async def allocate_seq(self, session_id: uuid.UUID) -> int: ...

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        status: str = "complete",
        finish_reason: str | None = None,
        client_message_id: str | None = None,
        pending_approval_id: uuid.UUID | None = None,
    ) -> ChatMessage: ...

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None: ...


class AgentApprovalService:
    """Pause/resume orchestration for agent tool-call approvals."""

    def __init__(
        self,
        *,
        approval_store: AgentToolApprovalWriter,
        chat_store: HitlChatStore,
    ) -> None:
        self._approval_store = approval_store
        self._chat_store = chat_store

    async def pause(
        self,
        step: PlannedStep,
        *,
        scratchpad: Scratchpad,
        state: AgentExecutionState,
        session_id: uuid.UUID,
        owner_id: uuid.UUID,
        execution_id: str,
        stream_publisher: StreamPublisher,
        provider: str | None = None,
        model: str | None = None,
    ) -> AgentToolApproval:
        """Snapshot state, persist approval + placeholder message, emit SSE event."""
        proposed_calls = _proposed_calls_from_step(step)
        approval_correlation_id = uuid.uuid4()
        approval = await self._approval_store.create(
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            approval_correlation_id=approval_correlation_id,
            proposed_calls=proposed_calls,
            paused_scratchpad=scratchpad.to_snapshot(),
            paused_state=state.model_dump(mode="json"),
        )

        assistant_seq = await self._chat_store.allocate_seq(session_id)
        placeholder = await self._chat_store.add_message(
            session_id=session_id,
            seq=assistant_seq,
            role="assistant",
            content="",
            provider=provider,
            model=model,
            status="waiting_approval",
            pending_approval_id=approval.id,
        )
        await self._chat_store.mark_last_message_at(session_id)

        linked = await self._approval_store.link_pending_message(
            approval.id,
            pending_message_id=placeholder.id,
        )
        approval = linked or approval

        await stream_publisher.publish(
            AgentStreamEvent.approval_required(
                execution_id,
                approval_id=approval.id,
                approval_correlation_id=approval.approval_correlation_id,
                proposed_calls=[
                    call.model_dump(mode="json") for call in proposed_calls
                ],
            )
        )
        return approval


def _proposed_calls_from_step(step: PlannedStep) -> list[ProposedToolCall]:
    calls: list[ProposedToolCall] = []
    for index, call in enumerate(step.tool_calls):
        call_id = call.call_id or f"{step.step_id}-{index}"
        calls.append(
            ProposedToolCall(
                name=call.name,
                arguments=dict(call.arguments),
                call_id=call_id,
            )
        )
    return calls


def raise_pause(approval: AgentToolApproval) -> None:
    """Raise the canonical pause exception after persistence completes."""
    raise AgentApprovalPauseError(approval)
