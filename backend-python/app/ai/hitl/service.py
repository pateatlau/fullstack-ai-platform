"""Agent tool-call approval orchestration (Epic 09)."""

from __future__ import annotations

import datetime
import time
import uuid
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from app.ai.agent.executor.agent_executor import AgentExecutor

from app.ai.agent.executor.result_aggregator import (
    AggregatedToolResults,
    ToolRunRecord,
    aggregate_tool_results,
)
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.messages import AgentMessage, AgentMessageRole
from app.ai.agent.models.plan import PlannedStep
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.ai.agent.scratchpad.store import ScratchpadStore, get_scratchpad_store
from app.ai.agent.state.manager import AgentStateManager
from app.ai.agent.streaming import NoOpStreamPublisher
from app.ai.hitl.exceptions import (
    AgentApprovalPauseError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalValidationError,
    HitlError,
    StagePermissionInvalidError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
    RequestMetadata,
)
from app.ai.hitl.notifications import (
    ApprovalNotificationEvent,
    ApprovalNotificationEventType,
    NotificationDispatcher,
)
from app.ai.observability.metrics.instruments import (
    record_agent_tool_approval_pending_delta,
    record_approval_cancelled_metric,
    record_approval_requested_metric,
    record_hitl_decision_metrics,
    record_hitl_resume_latency_ms,
)
from app.ai.observability.tracing.spans import (
    approval_span,
    elapsed_ms_since,
    hitl_decision_latency_ms,
    record_approval_span_outcome,
)
from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.models import AuditOutcome
from app.ai.security.rbac.permissions import PermissionKey
from app.ai.security.rbac.service import RbacService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.ai.tools.validator import ToolValidator
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.db.models import ChatMessage
from app.middleware.correlation_id import get_request_id
from app.middleware.rate_limit import check_rate_limit_bucket

if TYPE_CHECKING:
    from app.ai.security.audit.logger import AuditLogger


def normalize_hitl_reason(reason: str | None, *, max_length: int) -> str | None:
    """Truncate free-text decision reasons to the configured maximum length."""
    if reason is None:
        return None
    return reason if len(reason) <= max_length else reason[:max_length]


def _emit_agent_decision_metrics(
    span: Span | None,
    *,
    decided: AgentToolApproval,
    decision: Literal["approved", "rejected"],
    edited: bool,
) -> int | None:
    """Record terminal decision metrics immediately after a successful CAS write."""
    record_agent_tool_approval_pending_delta(-1)
    assert decided.decided_at is not None
    decision_latency_ms = hitl_decision_latency_ms(
        decided.requested_at,
        decided.decided_at,
    )
    if decision_latency_ms is not None:
        record_hitl_decision_metrics(
            kind=ApprovalKind.AGENT_TOOL.value,
            decision=decision,
            decision_latency_ms=decision_latency_ms,
        )
    record_approval_span_outcome(
        span,
        approval_status=(
            ApprovalStatus.APPROVED.value
            if decision == "approved"
            else ApprovalStatus.REJECTED.value
        ),
        approval_decision=decision,
        decision_latency_ms=decision_latency_ms,
        edited=edited,
    )
    return decision_latency_ms


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
        expires_at: datetime.datetime | None = None,
        required_stages: list[str] | None = None,
        request_metadata: RequestMetadata | None = None,
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
        comments: str | None = None,
        edited_calls: list[ProposedToolCall] | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval: ...

    async def cas_cancel(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        reason: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval: ...

    async def append_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
        decided_by: uuid.UUID,
        reason: str | None = None,
        comments: str | None = None,
    ) -> AgentToolApproval: ...

    async def rollback_last_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
    ) -> None: ...

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> list[ApprovalRevision]: ...

    async def get(self, approval_id: uuid.UUID) -> AgentToolApproval | None: ...

    async def get_any(self, approval_id: uuid.UUID) -> AgentToolApproval | None: ...

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None: ...

    async def claim_pause_snapshot(
        self, approval_id: uuid.UUID
    ) -> AgentToolApproval | None: ...

    async def clear_pause_snapshot(self, approval_id: uuid.UUID) -> None: ...


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

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]: ...

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
        approval_timeout_hours: int = 0,
        default_model: str = "gpt-4o-mini",
        notification_dispatcher: NotificationDispatcher | None = None,
        rbac_service: RbacService | None = None,
        rbac_enforcement_enabled: bool = False,
        audit_logger: "AuditLogger | None" = None,
        settings: Settings | None = None,
    ) -> None:
        self._approval_store = approval_store
        self._chat_store = chat_store
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._tool_validator = tool_validator or ToolValidator()
        self._scratchpad_store = scratchpad_store or get_scratchpad_store()
        self._approval_timeout_hours = approval_timeout_hours
        self._default_model = default_model
        self._notification_dispatcher = notification_dispatcher
        self._rbac_service = rbac_service
        self._rbac_enforcement_enabled = rbac_enforcement_enabled
        self._audit_logger = audit_logger
        self._settings = settings or get_settings()

    async def _notify(self, event: ApprovalNotificationEvent) -> None:
        if self._notification_dispatcher is not None:
            await self._notification_dispatcher.dispatch(event)

    def _rbac_active(self) -> bool:
        """True when Epic 11 RBAC enforcement should gate stage decisions."""
        return self._rbac_enforcement_enabled and self._rbac_service is not None

    async def _check_stage_permission(
        self,
        *,
        approval_id: uuid.UUID,
        decider_id: uuid.UUID,
        stage: str,
    ) -> None:
        """Verify the decider holds ``stage`` (or ``approvals:decide_all``).

        Checked immediately before the ``StageDecision`` write (never as an
        earlier, separate check) so a concurrent role revocation cannot
        leave a stale, already-granted decision in flight.
        """
        if not self._rbac_active():
            return
        assert self._rbac_service is not None
        from app.ai.security.observability.wrappers import (
            authz_span_context,
            record_authz_allowed,
            record_authz_denial,
        )

        decide_all = await self._rbac_service.authorize(
            decider_id, PermissionKey.APPROVALS_DECIDE_ALL
        )
        if decide_all.allowed:
            with authz_span_context(
                actor_user_id=str(decider_id),
                permission_key=PermissionKey.APPROVALS_DECIDE_ALL.value,
            ) as span:
                record_authz_allowed(
                    span,
                    actor_user_id=str(decider_id),
                    permission_key=PermissionKey.APPROVALS_DECIDE_ALL.value,
                    resource_type="approval",
                )
            return
        stage_decision = await self._rbac_service.authorize(decider_id, stage)
        with authz_span_context(
            actor_user_id=str(decider_id),
            permission_key=stage,
        ) as span:
            if not stage_decision.allowed:
                record_authz_denial(
                    span,
                    actor_user_id=str(decider_id),
                    permission_key=stage,
                    resource_type="approval",
                )
                if self._audit_logger is not None:
                    await self._audit_logger.record(
                        actor=CallerContext.for_user(decider_id),
                        action=AuditAction.APPROVAL_STAGE_DENIED.value,
                        outcome=AuditOutcome.DENIED,
                        resource_type="approval",
                        resource_id=str(approval_id),
                        metadata={"stage": stage},
                    )
                raise StagePermissionInvalidError(stage)
            record_authz_allowed(
                span,
                actor_user_id=str(decider_id),
                permission_key=stage,
                resource_type="approval",
            )

    async def _resolve_approval_for_decider(
        self,
        approval_id: uuid.UUID,
        *,
        decider_id: uuid.UUID,
    ) -> AgentToolApproval:
        """Fetch an approval the decider may act on.

        The approval's owner may always act on it (V1 behaviour, unchanged).
        When RBAC enforcement is enabled, a non-owner caller holding
        ``approvals:decide_all`` or the permission for the current
        outstanding stage may also fetch it (Epic 11 Phase 2 — the deciding
        user, not the approval's owner, is who stage permissions are
        evaluated against).
        """
        approval = await self._approval_store.get_for_owner(
            approval_id, owner_id=decider_id
        )
        if approval is not None:
            if approval.status is ApprovalStatus.EXPIRED:
                raise ApprovalExpiredError(
                    f"Approval {approval_id} expired at "
                    f"{approval.expires_at.isoformat() if approval.expires_at else 'unknown'}."
                )
            return approval

        not_found = ApprovalNotFoundError(
            f"Approval {approval_id} not found or not owned by caller."
        )
        if not self._rbac_active():
            raise not_found

        candidate = await self._approval_store.get_any(approval_id)
        if candidate is None:
            raise not_found

        assert self._rbac_service is not None
        decide_all = await self._rbac_service.authorize(
            decider_id, PermissionKey.APPROVALS_DECIDE_ALL
        )
        authorized = decide_all.allowed
        if not authorized and _has_outstanding_stages(candidate):
            stage_decision = await self._rbac_service.authorize(
                decider_id, _current_stage(candidate)
            )
            authorized = stage_decision.allowed

        # Authorization is resolved before revealing status/expiry to avoid
        # leaking approval existence to a caller who isn't entitled to see it.
        if not authorized:
            raise not_found
        if candidate.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{candidate.expires_at.isoformat() if candidate.expires_at else 'unknown'}."
            )
        return candidate

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
        required_stages: list[str] | None = None,
    ) -> AgentToolApproval:
        """Snapshot state, persist approval + placeholder message, emit SSE event."""
        proposed_calls = _proposed_calls_from_step(step)
        approval_correlation_id = uuid.uuid4()
        paused_state = AgentStateManager.transition(
            state,
            AgentExecutionStatus.WAITING_APPROVAL,
        )
        expires_at = _compute_expires_at(self._approval_timeout_hours)
        request_metadata = RequestMetadata(request_id=get_request_id())
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
                expires_at=expires_at,
                required_stages=required_stages,
                request_metadata=request_metadata,
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
            record_approval_requested_metric(kind=ApprovalKind.AGENT_TOOL.value)
            record_approval_span_outcome(
                span,
                approval_id=str(approval.id),
                approval_status=ApprovalStatus.PENDING.value,
                edited=False,
            )
        await self._notify(
            ApprovalNotificationEvent(
                event_type=ApprovalNotificationEventType.REQUESTED,
                approval_id=approval.id,
                approval_kind=ApprovalKind.AGENT_TOOL,
                occurred_at=approval.requested_at,
                summary=f"Approval requested for {len(proposed_calls)} tool call(s).",
                metadata={"execution_id": execution_id},
            )
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
        decider_id: uuid.UUID,
        decision: Literal["approved", "rejected"],
        edited_calls: list[ProposedToolCall] | None = None,
        reason: str | None = None,
        comments: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> ApprovalResult:
        """Record a terminal decision. Approve path requires follow-up resume call."""
        if self._settings.security_rate_limit_extensions_enabled:
            from app.ai.security.quotas.store import check_daily_usage_quota

            daily_allowed = await check_daily_usage_quota(
                str(decider_id),
                "approval_decision",
                self._settings.approval_decision_daily_quota,
            )
            if not daily_allowed:
                from app.core.errors import RateLimitExceededError

                raise RateLimitExceededError(
                    message="Daily approval decision quota exceeded.",
                )
            retry_after = await check_rate_limit_bucket(
                f"approval_decision:{decider_id}",
                self._settings.approval_decision_per_minute,
            )
            if retry_after is not None:
                from app.core.errors import RateLimitExceededError

                raise RateLimitExceededError(
                    retry_after_seconds=retry_after,
                    message="Approval decision rate limit exceeded.",
                )
        approval = await self._resolve_approval_for_decider(
            approval_id,
            decider_id=decider_id,
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
                if _has_outstanding_stages(approval):
                    decided = await self._cas_decide_with_stage_append(
                        approval_id,
                        decider_id=decider_id,
                        approval=approval,
                        stage_decision="rejected",
                        status=ApprovalStatus.REJECTED,
                        reason=reason,
                        comments=comments,
                        request_metadata=request_metadata,
                    )
                else:
                    decided = await self._approval_store.cas_decide(
                        approval_id,
                        owner_id=approval.owner_id,
                        status=ApprovalStatus.REJECTED,
                        decided_by=decider_id,
                        reason=reason,
                        comments=comments,
                        request_metadata=request_metadata,
                    )
                _emit_agent_decision_metrics(
                    span,
                    decided=decided,
                    decision="rejected",
                    edited=_has_edits(decided),
                )
                await self._update_placeholder_message(
                    decided,
                    content="",
                    status="rejected",
                    finish_reason="rejected",
                    clear_pending=True,
                )
            if self._audit_logger is not None:
                await self._audit_logger.record(
                    actor=CallerContext.for_user(decider_id),
                    action=AuditAction.APPROVAL_DECIDED.value,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="approval",
                    resource_id=str(approval_id),
                    metadata={"decision": "rejected"},
                )
            await self._notify_decided(decided)
            return _build_approval_result(
                decided,
                final_payload=_resolve_final_payload(decided),
                edited=_has_edits(decided),
            )

        # Approved: validate first, CAS, then append inline revision on success.
        _assert_terminal_approve_allowed(approval, approval_id)
        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            if _has_outstanding_stages(approval):
                decided = await self._cas_decide_with_stage_append(
                    approval_id,
                    decider_id=decider_id,
                    approval=approval,
                    stage_decision="approved",
                    status=ApprovalStatus.APPROVED,
                    reason=reason,
                    comments=comments,
                    edited_calls=edited_calls,
                    request_metadata=request_metadata,
                )
            else:
                decided = await self._approval_store.cas_decide(
                    approval_id,
                    owner_id=approval.owner_id,
                    status=ApprovalStatus.APPROVED,
                    decided_by=decider_id,
                    reason=reason,
                    comments=comments,
                    edited_calls=edited_calls,
                    request_metadata=request_metadata,
                )
            if edited_calls is not None:
                await self._approval_store.append_revision(
                    approval_id=approval_id,
                    approval_kind=ApprovalKind.AGENT_TOOL,
                    edited_by=decider_id,
                    edited_payload=edited_calls,
                    note=reason,
                )
            _emit_agent_decision_metrics(
                span,
                decided=decided,
                decision="approved",
                edited=_has_edits(decided),
            )
        if self._audit_logger is not None:
            await self._audit_logger.record(
                actor=CallerContext.for_user(decider_id),
                action=AuditAction.APPROVAL_DECIDED.value,
                outcome=AuditOutcome.SUCCESS,
                resource_type="approval",
                resource_id=str(approval_id),
                metadata={"decision": "approved"},
            )
        await self._notify_decided(decided)
        return _build_approval_result(
            decided,
            final_payload=_resolve_final_payload(decided),
            edited=_has_edits(decided),
        )

    async def record_stage_approval(
        self,
        approval_id: uuid.UUID,
        *,
        decider_id: uuid.UUID,
        reason: str | None = None,
        comments: str | None = None,
    ) -> ApprovalResult:
        """Record one intermediate multi-stage checklist approval (recommendation #5).

        Used only when :attr:`AgentToolApproval.required_stages` has more than
        one outstanding stage after this decision — the approval stays
        ``pending`` and no tool execution happens. Callers must route the
        *final* stage's approval through :meth:`approve_and_resume` instead.
        """
        approval = await self._resolve_approval_for_decider(
            approval_id,
            decider_id=decider_id,
        )
        if not _has_outstanding_stages(approval) or _is_final_stage(approval):
            raise ApprovalValidationError(
                f"Approval {approval_id} must be finalized via decide()/"
                "approve_and_resume(), not record_stage_approval()."
            )
        stage = _current_stage(approval)
        await self._check_stage_permission(
            approval_id=approval_id, decider_id=decider_id, stage=stage
        )
        updated = await self._approval_store.append_stage_decision(
            approval_id,
            owner_id=approval.owner_id,
            stage=stage,
            decision="approved",
            decided_by=decider_id,
            reason=reason,
            comments=comments,
        )
        if self._audit_logger is not None:
            await self._audit_logger.record(
                actor=CallerContext.for_user(decider_id),
                action=AuditAction.APPROVAL_STAGE_COMPLETED.value,
                outcome=AuditOutcome.SUCCESS,
                resource_type="approval",
                resource_id=str(approval_id),
                metadata={"stage": stage, "decision": "approved"},
            )
        return ApprovalResult(
            approval_id=updated.id,
            approval_kind=ApprovalKind.AGENT_TOOL,
            status=updated.status,
            edited=_has_edits(updated),
            final_payload=_resolve_final_payload(updated),
            reason=reason,
            approver=decider_id,
            decided_at=datetime.datetime.now(datetime.UTC),
            approval_correlation_id=updated.approval_correlation_id,
            comments=comments,
            outstanding_stages=_remaining_stages(updated),
        )

    async def cancel(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        reason: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> ApprovalResult:
        """Withdraw a pending approval request (recommendation #2).

        Distinct from ``rejected``: cancellation is a requester-initiated
        withdrawal (for example the underlying chat/tool call is no longer
        needed), not a reviewer declining the request.
        """
        approval = await self._approval_store.require_for_owner(
            approval_id,
            owner_id=owner_id,
        )
        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            cancelled = await self._approval_store.cas_cancel(
                approval_id,
                owner_id=owner_id,
                reason=reason,
                request_metadata=request_metadata,
            )
            record_agent_tool_approval_pending_delta(-1)
            record_approval_cancelled_metric(kind=ApprovalKind.AGENT_TOOL.value)
            record_approval_span_outcome(
                span,
                approval_status=ApprovalStatus.CANCELLED.value,
                edited=_has_edits(cancelled),
            )
            await self._update_placeholder_message(
                cancelled,
                content="",
                status="stopped",
                finish_reason="cancelled",
                clear_pending=True,
            )
        await self._notify(
            ApprovalNotificationEvent(
                event_type=ApprovalNotificationEventType.CANCELLED,
                approval_id=cancelled.id,
                approval_kind=ApprovalKind.AGENT_TOOL,
                occurred_at=cancelled.decided_at or datetime.datetime.now(datetime.UTC),
                summary="Approval request cancelled by requester.",
                metadata={},
            )
        )
        return _build_approval_result(
            cancelled,
            final_payload=_resolve_final_payload(cancelled),
            edited=_has_edits(cancelled),
        )

    async def _notify_decided(self, decided: AgentToolApproval) -> None:
        await self._notify(
            ApprovalNotificationEvent(
                event_type=ApprovalNotificationEventType.DECIDED,
                approval_id=decided.id,
                approval_kind=ApprovalKind.AGENT_TOOL,
                occurred_at=decided.decided_at or datetime.datetime.now(datetime.UTC),
                summary=f"Approval {decided.status.value} by reviewer.",
                metadata={"decision": decided.status.value},
            )
        )

    async def approve_and_resume(
        self,
        approval_id: uuid.UUID,
        *,
        decider_id: uuid.UUID,
        executor: AgentExecutor,
        request: AgentRequest,
        context: AgentContext,
        tool_context: ToolExecutionContext,
        stream_publisher: StreamPublisher,
        edited_calls: list[ProposedToolCall] | None = None,
        reason: str | None = None,
        comments: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> tuple[ApprovalResult, AgentResponse]:
        """Record approval, execute gated tools, and resume the ReAct loop."""
        approval = await self._resolve_approval_for_decider(
            approval_id,
            decider_id=decider_id,
        )
        if edited_calls is not None:
            _validate_edited_calls(
                edited_calls,
                approval.proposed_calls,
                self._tool_registry,
                self._tool_validator,
            )

        _assert_terminal_approve_allowed(approval, approval_id)

        with approval_span(
            approval_id=str(approval_id),
            approval_kind=ApprovalKind.AGENT_TOOL.value,
            approval_correlation_id=str(approval.approval_correlation_id),
        ) as span:
            if _has_outstanding_stages(approval):
                decided = await self._cas_decide_with_stage_append(
                    approval_id,
                    decider_id=decider_id,
                    approval=approval,
                    stage_decision="approved",
                    status=ApprovalStatus.APPROVED,
                    reason=reason,
                    comments=comments,
                    edited_calls=edited_calls,
                    request_metadata=request_metadata,
                )
            else:
                decided = await self._approval_store.cas_decide(
                    approval_id,
                    owner_id=approval.owner_id,
                    status=ApprovalStatus.APPROVED,
                    decided_by=decider_id,
                    reason=reason,
                    comments=comments,
                    edited_calls=edited_calls,
                    request_metadata=request_metadata,
                )
            if edited_calls is not None:
                await self._approval_store.append_revision(
                    approval_id=approval_id,
                    approval_kind=ApprovalKind.AGENT_TOOL,
                    edited_by=decider_id,
                    edited_payload=edited_calls,
                    note=reason,
                )
            _emit_agent_decision_metrics(
                span,
                decided=decided,
                decision="approved",
                edited=_has_edits(decided),
            )
            if self._audit_logger is not None:
                await self._audit_logger.record(
                    actor=CallerContext.for_user(decider_id),
                    action=AuditAction.APPROVAL_DECIDED.value,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="approval",
                    resource_id=str(approval_id),
                    metadata={"decision": "approved"},
                )
            await self._notify_decided(decided)
            result = _build_approval_result(
                decided,
                final_payload=_resolve_final_payload(decided),
                edited=_has_edits(decided),
            )
            response = await self._resume_after_approval_decision(
                decided,
                executor=executor,
                request=request,
                context=context,
                tool_context=tool_context,
                stream_publisher=stream_publisher,
            )
        return result, response

    async def resume_orphaned_approval(
        self,
        approval_id: uuid.UUID,
        *,
        executor: AgentExecutor,
        fail_safe: bool = False,
    ) -> bool:
        """Re-run Stages 2–4 for a crash-orphaned approved approval row."""
        approval = await self._approval_store.claim_pause_snapshot(approval_id)
        if approval is None:
            return False

        request, context, tool_context = await self._build_orphan_resume_context(
            approval
        )
        try:
            await self._resume_after_approval_decision(
                approval,
                executor=executor,
                request=request,
                context=context,
                tool_context=tool_context,
                stream_publisher=NoOpStreamPublisher(),
            )
        except AgentApprovalPauseError:
            raise
        except Exception:
            if fail_safe:
                await self._update_placeholder_message(
                    approval,
                    content="",
                    status="error",
                    finish_reason="error",
                    clear_pending=True,
                )
                return False
            raise
        return True

    async def _resume_after_approval_decision(
        self,
        decided: AgentToolApproval,
        *,
        executor: AgentExecutor,
        request: AgentRequest,
        context: AgentContext,
        tool_context: ToolExecutionContext,
        stream_publisher: StreamPublisher,
    ) -> AgentResponse:
        """Execute approved tool calls and resume the ReAct loop (Stages 2–4)."""
        resume_start = time.perf_counter()
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
        approved_context = tool_context.model_copy(
            update={
                "session_id": decided.session_id,
                "approval_correlation_id": decided.approval_correlation_id,
            }
        )
        try:
            tool_results = await self._execute_approved_calls(
                calls,
                execution_id=decided.execution_id,
                tool_context=approved_context,
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
        resume_context = approved_context.model_copy(
            update={"approval_correlation_id": None}
        )
        try:
            response = await executor.resume_from_approval(
                request,
                context,
                scratchpad=scratchpad,
                state=state,
                tool_context=resume_context,
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

        record_hitl_resume_latency_ms(
            kind=ApprovalKind.AGENT_TOOL.value,
            latency_ms=elapsed_ms_since(resume_start),
        )
        return response

    async def _build_orphan_resume_context(
        self,
        approval: AgentToolApproval,
    ) -> tuple[AgentRequest, AgentContext, ToolExecutionContext]:
        messages = await self._chat_store.list_messages(approval.session_id)
        agent_messages = [
            AgentMessage(
                role=cast(AgentMessageRole, message.role),
                content=message.content,
            )
            for message in messages
            if message.role in {"user", "system", "assistant"} and message.content
        ]
        if not agent_messages:
            agent_messages = [AgentMessage(role="user", content="continue")]
        state = AgentExecutionState.model_validate(approval.paused_state)
        caller = CallerContext.for_user(approval.owner_id)
        resume_model = _resolve_orphan_resume_model(
            messages,
            pending_message_id=approval.pending_message_id,
            default_model=self._default_model,
        )
        return (
            AgentRequest(
                messages=agent_messages,
                model=resume_model,
                config=AgentConfig(max_iterations=max(2, state.current_iteration + 2)),
            ),
            AgentContext(
                execution_id=approval.execution_id,
                caller=caller,
                session_id=approval.session_id,
            ),
            ToolExecutionContext(
                caller=caller,
                session_id=approval.session_id,
                approval_correlation_id=approval.approval_correlation_id,
            ),
        )

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
        decider_id: uuid.UUID,
    ) -> AgentToolApproval:
        return await self._resolve_approval_for_decider(
            approval_id,
            decider_id=decider_id,
        )

    async def get_placeholder_message(
        self,
        approval: AgentToolApproval,
    ) -> ChatMessage | None:
        if approval.pending_message_id is None:
            return None
        return await self._chat_store.get_message(approval.pending_message_id)

    async def _cas_decide_with_stage_append(
        self,
        approval_id: uuid.UUID,
        *,
        decider_id: uuid.UUID,
        approval: AgentToolApproval,
        stage_decision: Literal["approved", "rejected"],
        status: ApprovalStatus,
        reason: str | None = None,
        comments: str | None = None,
        edited_calls: list[ProposedToolCall] | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval:
        """Append the current stage, then CAS — rolling back the append on failure.

        ``approval.owner_id`` (never ``decider_id``) scopes the store's CAS
        writes, since Epic 11 Phase 2 lets an RBAC-authorized non-owner
        decide a stage; ``decided_by`` always records the actual decider.
        """
        stage = _current_stage(approval)
        await self._check_stage_permission(
            approval_id=approval_id, decider_id=decider_id, stage=stage
        )
        await self._approval_store.append_stage_decision(
            approval_id,
            owner_id=approval.owner_id,
            stage=stage,
            decision=stage_decision,
            decided_by=decider_id,
            reason=reason,
            comments=comments,
        )
        try:
            result = await self._approval_store.cas_decide(
                approval_id,
                owner_id=approval.owner_id,
                status=status,
                decided_by=decider_id,
                reason=reason,
                comments=comments,
                edited_calls=edited_calls,
                request_metadata=request_metadata,
            )
            if self._audit_logger is not None:
                await self._audit_logger.record(
                    actor=CallerContext.for_user(decider_id),
                    action=AuditAction.APPROVAL_STAGE_COMPLETED.value,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="approval",
                    resource_id=str(approval_id),
                    metadata={"stage": stage, "decision": stage_decision},
                )
            return result
        except HitlError:
            await self._approval_store.rollback_last_stage_decision(
                approval_id,
                owner_id=approval.owner_id,
                stage=stage,
                decision=stage_decision,
            )
            raise

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
        comments=approval.comments,
    )


def _has_pause_snapshot(approval: AgentToolApproval) -> bool:
    return bool(approval.paused_scratchpad) or bool(approval.paused_state)


def _resolve_orphan_resume_model(
    messages: list[ChatMessage],
    *,
    pending_message_id: uuid.UUID | None,
    default_model: str,
) -> str:
    """Pick the session model for orphan resume from persisted chat rows."""
    if pending_message_id is not None:
        for message in messages:
            if message.id == pending_message_id and message.model:
                return message.model
    for message in reversed(messages):
        if message.model:
            return message.model
    return default_model


def _compute_expires_at(
    approval_timeout_hours: int,
) -> datetime.datetime | None:
    """``None`` disables expiry (matches the ``hitl_approval_timeout_hours=0`` default)."""
    if approval_timeout_hours <= 0:
        return None
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        hours=approval_timeout_hours
    )


def _remaining_stages(approval: AgentToolApproval) -> list[str]:
    """Required stages not yet recorded in ``stage_decisions``."""
    return approval.required_stages[len(approval.stage_decisions) :]


def _has_outstanding_stages(approval: AgentToolApproval) -> bool:
    """True while at least one required checklist stage has no decision yet."""
    return bool(_remaining_stages(approval))


def _assert_terminal_approve_allowed(
    approval: AgentToolApproval,
    approval_id: uuid.UUID,
) -> None:
    """Reject decide/approve_and_resume when intermediate checklist stages remain."""
    if _has_outstanding_stages(approval) and not _is_final_stage(approval):
        raise ApprovalValidationError(
            f"Approval {approval_id} has intermediate checklist stages remaining; "
            "use record_stage_approval() before finalizing via decide()/"
            "approve_and_resume()."
        )


def _is_final_stage(approval: AgentToolApproval) -> bool:
    """True when exactly one required stage remains (the caller may finalize)."""
    return len(_remaining_stages(approval)) == 1


def _current_stage(approval: AgentToolApproval) -> str:
    remaining = _remaining_stages(approval)
    assert remaining, "caller must check _has_outstanding_stages first"
    return remaining[0]


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
