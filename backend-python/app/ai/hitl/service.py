"""Agent tool-call approval orchestration (Epic 09)."""

from __future__ import annotations

import datetime
import time
import uuid
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from app.ai.agent.executor.agent_executor import AgentExecutor

from app.ai.agent.executor.result_aggregator import (
    AggregatedToolResults,
    ToolRunRecord,
    aggregate_tool_results,
)
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.plan import PlannedStep
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.ai.agent.scratchpad.store import ScratchpadStore, get_scratchpad_store
from app.ai.agent.state.manager import AgentStateManager
from app.ai.hitl.exceptions import (
    AgentApprovalPauseError,
    ApprovalValidationError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)
from app.ai.observability.metrics.instruments import (
    record_agent_tool_approval_pending_delta,
)
from app.ai.observability.tracing.spans import (
    approval_span,
    elapsed_ms_since,
    record_approval_span_outcome,
    record_hitl_terminal_decision,
)
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.ai.tools.validator import ToolValidator
from app.db.models import ChatMessage


def normalize_hitl_reason(reason: str | None, *, max_length: int) -> str | None:
    """Truncate free-text decision reasons to the configured maximum length."""
    if reason is None:
        return None
    return reason if len(reason) <= max_length else reason[:max_length]


class AgentApprovalStore(Protocol):
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

    async def require_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval: ...

    async def cas_revise(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        edited_calls: list[ProposedToolCall],
    ) -> AgentToolApproval: ...

    async def append_revision(
        self,
        *,
        approval_id: uuid.UUID,
        approval_kind: ApprovalKind,
        edited_by: uuid.UUID,
        edited_payload: list[ProposedToolCall] | dict[str, object],
        note: str | None = None,
    ) -> ApprovalRevision: ...

    async def cas_decide(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        status: ApprovalStatus,
        decided_by: uuid.UUID,
        reason: str | None = None,
        edited_calls: list[ProposedToolCall] | None = None,
    ) -> AgentToolApproval: ...

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> list[ApprovalRevision]: ...


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

    async def update_message(
        self,
        message_id: uuid.UUID,
        *,
        content: str | None = None,
        status: str | None = None,
        finish_reason: str | None = None,
        pending_approval_id: uuid.UUID | None = None,
        clear_pending_approval: bool = False,
    ) -> ChatMessage | None: ...

    async def get_message(self, message_id: uuid.UUID) -> ChatMessage | None: ...

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None: ...


class AgentApprovalService:
    """Pause/resume orchestration for agent tool-call approvals."""

    def __init__(
        self,
        *,
        approval_store: AgentApprovalStore,
        chat_store: HitlChatStore,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        tool_validator: ToolValidator | None = None,
        scratchpad_store: ScratchpadStore | None = None,
    ) -> None:
        self._approval_store = approval_store
        self._chat_store = chat_store
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._tool_validator = tool_validator or ToolValidator()
        self._scratchpad_store = scratchpad_store or get_scratchpad_store()

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
        paused_state = AgentStateManager.transition(
            state,
            AgentExecutionStatus.WAITING_APPROVAL,
        )
        with approval_span(
            approval_id=str(approval_correlation_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval_correlation_id),
        ) as span:
            approval = await self._approval_store.create(
                session_id=session_id,
                owner_id=owner_id,
                execution_id=execution_id,
                approval_correlation_id=approval_correlation_id,
                proposed_calls=proposed_calls,
                paused_scratchpad=scratchpad.to_snapshot(),
                paused_state=paused_state.model_dump(mode="json"),
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
            record_agent_tool_approval_pending_delta(1)
            record_approval_span_outcome(
                span,
                approval_id=str(approval.id),
                approval_status=ApprovalStatus.PENDING.value,
                edited=False,
            )
        return approval

    async def revise(
        self,
        approval_id: uuid.UUID,
        *,
        edited_calls: list[ProposedToolCall],
        owner_id: uuid.UUID,
        note: str | None = None,
    ) -> tuple[AgentToolApproval, ApprovalRevision]:
        """Pre-decision edit: validate, append revision, update ``edited_calls``."""
        approval = await self._approval_store.require_for_owner(
            approval_id,
            owner_id=owner_id,
        )
        _validate_edited_calls(
            edited_calls,
            approval.proposed_calls,
            self._tool_registry,
            self._tool_validator,
        )

        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            updated = await self._approval_store.cas_revise(
                approval_id,
                owner_id=owner_id,
                edited_calls=edited_calls,
            )
            revision = await self._approval_store.append_revision(
                approval_id=approval_id,
                approval_kind=ApprovalKind.AGENT_TOOL,
                edited_by=owner_id,
                edited_payload=edited_calls,
                note=note,
            )
            record_approval_span_outcome(
                span,
                approval_status=ApprovalStatus.PENDING.value,
                edited=True,
            )
        return updated, revision

    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        decision: Literal["approved", "rejected"],
        edited_calls: list[ProposedToolCall] | None = None,
        reason: str | None = None,
    ) -> ApprovalResult:
        """Record a terminal decision. Approve path requires follow-up resume call."""
        approval = await self._approval_store.require_for_owner(
            approval_id,
            owner_id=owner_id,
        )
        if edited_calls is not None:
            _validate_edited_calls(
                edited_calls,
                approval.proposed_calls,
                self._tool_registry,
                self._tool_validator,
            )

        if decision == "rejected":
            with approval_span(
                approval_id=str(approval_id),
                approval_kind=ApprovalKind.AGENT_TOOL.value,
                approval_correlation_id=str(approval.approval_correlation_id),
            ) as span:
                decided = await self._approval_store.cas_decide(
                    approval_id,
                    owner_id=owner_id,
                    status=ApprovalStatus.REJECTED,
                    decided_by=owner_id,
                    reason=reason,
                )
                await self._update_placeholder_message(
                    decided,
                    content="",
                    status="rejected",
                    finish_reason="rejected",
                    clear_pending=True,
                )
                record_agent_tool_approval_pending_delta(-1)
                assert decided.decided_at is not None
                record_hitl_terminal_decision(
                    span,
                    kind=ApprovalKind.AGENT_TOOL.value,
                    decision="rejected",
                    approval_status=ApprovalStatus.REJECTED.value,
                    requested_at=decided.requested_at,
                    decided_at=decided.decided_at,
                    edited=_has_edits(decided),
                )
            return _build_approval_result(
                decided,
                final_payload=_resolve_final_payload(decided),
                edited=_has_edits(decided),
            )

        # Approved: validate first, CAS, then append inline revision on success.
        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            decided = await self._approval_store.cas_decide(
                approval_id,
                owner_id=owner_id,
                status=ApprovalStatus.APPROVED,
                decided_by=owner_id,
                reason=reason,
                edited_calls=edited_calls,
            )
            if edited_calls is not None:
                await self._approval_store.append_revision(
                    approval_id=approval_id,
                    approval_kind=ApprovalKind.AGENT_TOOL,
                    edited_by=owner_id,
                    edited_payload=edited_calls,
                    note=reason,
                )
            record_agent_tool_approval_pending_delta(-1)
            assert decided.decided_at is not None
            record_hitl_terminal_decision(
                span,
                kind=ApprovalKind.AGENT_TOOL.value,
                decision="approved",
                approval_status=ApprovalStatus.APPROVED.value,
                requested_at=decided.requested_at,
                decided_at=decided.decided_at,
                edited=_has_edits(decided),
            )
        return _build_approval_result(
            decided,
            final_payload=_resolve_final_payload(decided),
            edited=_has_edits(decided),
        )

    async def approve_and_resume(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        executor: AgentExecutor,
        request: AgentRequest,
        context: AgentContext,
        tool_context: ToolExecutionContext,
        stream_publisher: StreamPublisher,
        edited_calls: list[ProposedToolCall] | None = None,
        reason: str | None = None,
    ) -> tuple[ApprovalResult, AgentResponse]:
        """Record approval, execute gated tools, and resume the ReAct loop."""
        approval = await self._approval_store.require_for_owner(
            approval_id,
            owner_id=owner_id,
        )
        if edited_calls is not None:
            _validate_edited_calls(
                edited_calls,
                approval.proposed_calls,
                self._tool_registry,
                self._tool_validator,
            )

        resume_start = time.perf_counter()
        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            decided = await self._approval_store.cas_decide(
                approval_id,
                owner_id=owner_id,
                status=ApprovalStatus.APPROVED,
                decided_by=owner_id,
                reason=reason,
                edited_calls=edited_calls,
            )
            if edited_calls is not None:
                await self._approval_store.append_revision(
                    approval_id=approval_id,
                    approval_kind=ApprovalKind.AGENT_TOOL,
                    edited_by=owner_id,
                    edited_payload=edited_calls,
                    note=reason,
                )
            result = _build_approval_result(
                decided,
                final_payload=_resolve_final_payload(decided),
                edited=_has_edits(decided),
            )
            calls = _calls_from_payload(result.final_payload, decided.proposed_calls)

            existing = self._scratchpad_store.get(decided.execution_id)
            if existing is not None:
                self._scratchpad_store.remove(decided.execution_id)
            stored = Scratchpad.from_snapshot(
                decided.execution_id,
                decided.paused_scratchpad,
            )
            self._scratchpad_store.create(decided.execution_id)
            scratchpad = self._scratchpad_store.require(decided.execution_id)
            for entry in stored.entries:
                scratchpad.append(entry)

            state = AgentExecutionState.model_validate(decided.paused_state)
            exec_context = tool_context.model_copy(
                update={
                    "session_id": decided.session_id,
                    "approval_correlation_id": decided.approval_correlation_id,
                }
            )
            try:
                tool_results = await self._execute_approved_calls(
                    calls,
                    execution_id=decided.execution_id,
                    tool_context=exec_context,
                    scratchpad=scratchpad,
                    stream_publisher=stream_publisher,
                )
            except Exception:
                await self._update_placeholder_message(
                    decided,
                    content="",
                    status="error",
                    finish_reason="error",
                    clear_pending=True,
                )
                raise
            for tool_name in tool_results.tools_used:
                state = AgentStateManager.record_tool_used(state, tool_name)

            last_planner_content = _extract_last_planner_content(scratchpad)
            try:
                response = await executor.resume_from_approval(
                    request,
                    context,
                    scratchpad=scratchpad,
                    state=state,
                    tool_context=exec_context,
                    tool_results=tool_results,
                    last_planner_content=last_planner_content,
                )
            except AgentApprovalPauseError:
                raise
            except Exception:
                await self._update_placeholder_message(
                    decided,
                    content="",
                    status="error",
                    finish_reason="error",
                    clear_pending=True,
                )
                raise

            if response.finish_reason == "waiting_approval":
                await self._update_placeholder_message(
                    decided,
                    content=response.content,
                    status="complete",
                    finish_reason="stop",
                    clear_pending=True,
                )
                await self._chat_store.mark_last_message_at(decided.session_id)
            else:
                tool_failed = any(
                    not record.result.success for record in tool_results.records
                )
                message_status = (
                    "error"
                    if tool_failed or response.finish_reason == "error"
                    else "complete"
                )
                await self._update_placeholder_message(
                    decided,
                    content=response.content,
                    status=message_status,
                    finish_reason=response.finish_reason,
                    clear_pending=True,
                )
                await self._chat_store.mark_last_message_at(decided.session_id)

            record_agent_tool_approval_pending_delta(-1)
            assert decided.decided_at is not None
            record_hitl_terminal_decision(
                span,
                kind=ApprovalKind.AGENT_TOOL.value,
                decision="approved",
                approval_status=ApprovalStatus.APPROVED.value,
                requested_at=decided.requested_at,
                decided_at=decided.decided_at,
                edited=_has_edits(decided),
                resume_latency_ms=elapsed_ms_since(resume_start),
            )
        return result, response

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        approval_kind: ApprovalKind = ApprovalKind.AGENT_TOOL,
    ) -> list[ApprovalRevision]:
        await self._approval_store.require_for_owner(approval_id, owner_id=owner_id)
        return await self._approval_store.list_revisions(
            approval_id,
            approval_kind=approval_kind,
        )

    async def get_owned_approval(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval:
        return await self._approval_store.require_for_owner(
            approval_id,
            owner_id=owner_id,
        )

    async def get_placeholder_message(
        self,
        approval: AgentToolApproval,
    ) -> ChatMessage | None:
        if approval.pending_message_id is None:
            return None
        return await self._chat_store.get_message(approval.pending_message_id)

    async def _execute_approved_calls(
        self,
        calls: list[ProposedToolCall],
        *,
        execution_id: str,
        tool_context: ToolExecutionContext,
        scratchpad: Scratchpad,
        stream_publisher: StreamPublisher,
    ) -> AggregatedToolResults:
        if self._tool_executor is None:
            raise RuntimeError("ToolExecutor is required to execute approved calls.")

        records: list[ToolRunRecord] = []
        for proposed in calls:
            call = ToolCall(
                name=proposed.name,
                arguments=dict(proposed.arguments),
                call_id=proposed.call_id,
            )
            await stream_publisher.publish(
                AgentStreamEvent.tool_start(
                    execution_id,
                    tool_name=call.name,
                    call_id=call.call_id or proposed.call_id,
                )
            )
            result = await self._tool_executor.execute(call, tool_context)
            await stream_publisher.publish(
                AgentStreamEvent.tool_end(
                    execution_id,
                    tool_name=call.name,
                    call_id=call.call_id or proposed.call_id,
                    success=result.success,
                )
            )
            records.append(
                ToolRunRecord(step_id=proposed.call_id, call=call, result=result)
            )

        aggregated = aggregate_tool_results(records)
        from app.ai.agent.executor.agent_executor import _record_tool_results

        _record_tool_results(scratchpad, aggregated.records)
        return aggregated

    async def _update_placeholder_message(
        self,
        approval: AgentToolApproval,
        *,
        content: str,
        status: str,
        finish_reason: str | None,
        clear_pending: bool,
    ) -> None:
        if approval.pending_message_id is None:
            return
        await self._chat_store.update_message(
            approval.pending_message_id,
            content=content,
            status=status,
            finish_reason=finish_reason,
            clear_pending_approval=clear_pending,
        )


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


def _validate_edited_calls(
    edited_calls: list[ProposedToolCall],
    proposed_calls: list[ProposedToolCall],
    registry: ToolRegistry | None,
    validator: ToolValidator,
) -> None:
    if registry is None:
        raise RuntimeError("ToolRegistry is required to validate edited calls.")
    if len(edited_calls) != len(proposed_calls):
        raise ApprovalValidationError(
            f"edited_calls must include exactly {len(proposed_calls)} call(s)."
        )

    proposed_by_id = {call.call_id: call for call in proposed_calls}
    seen_call_ids: set[str] = set()
    for call in edited_calls:
        if call.call_id in seen_call_ids:
            raise ApprovalValidationError(f"Duplicate edited call_id: {call.call_id}.")
        seen_call_ids.add(call.call_id)

        proposed = proposed_by_id.get(call.call_id)
        if proposed is None:
            raise ApprovalValidationError(f"Unknown edited call_id: {call.call_id}.")
        if call.name != proposed.name:
            raise ApprovalValidationError(
                f"Tool name mismatch for call_id {call.call_id}: "
                f"expected {proposed.name}, got {call.name}."
            )

        tool = registry.get(call.name)
        if tool is None:
            raise ApprovalValidationError(f"Unknown tool: {call.name}")
        detail = validator.validate(tool, call.arguments)
        if detail is not None:
            raise ApprovalValidationError(detail.message)


def _resolve_final_payload(
    approval: AgentToolApproval,
) -> list[ProposedToolCall]:
    if approval.edited_calls is not None:
        return approval.edited_calls
    return approval.proposed_calls


def _calls_from_payload(
    payload: dict[str, object] | list[ProposedToolCall] | None,
    fallback: list[ProposedToolCall],
) -> list[ProposedToolCall]:
    if payload is None:
        return fallback
    if isinstance(payload, list):
        return payload
    return fallback


def _has_edits(approval: AgentToolApproval) -> bool:
    return approval.edited_calls is not None


def _build_approval_result(
    approval: AgentToolApproval,
    *,
    final_payload: list[ProposedToolCall],
    edited: bool,
) -> ApprovalResult:
    decided_at = approval.decided_at or datetime.datetime.now(datetime.UTC)
    return ApprovalResult(
        approval_id=approval.id,
        approval_kind=ApprovalKind.AGENT_TOOL,
        status=approval.status,
        edited=edited,
        final_payload=final_payload,
        reason=approval.reason,
        approver=approval.decided_by,
        decided_at=decided_at,
        approval_correlation_id=approval.approval_correlation_id,
    )


def _extract_last_planner_content(scratchpad: Scratchpad) -> str | None:
    for entry in reversed(scratchpad.entries):
        if entry.kind == "thought" and entry.content:
            return entry.content
        if entry.provider_message is not None:
            content = entry.provider_message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


def raise_pause(approval: AgentToolApproval) -> None:
    """Raise the canonical pause exception after persistence completes."""
    raise AgentApprovalPauseError(approval)
