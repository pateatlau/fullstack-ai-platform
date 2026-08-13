"""In-memory HITL dependencies for offline eval runs (no Postgres required)."""

from __future__ import annotations

import datetime
import uuid

from typing import Literal

from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
    RequestMetadata,
    StageDecision,
    redact_terminal_client_audit_fields,
)
from app.db.models import ChatMessage, ChatSession


def _serialize_tool_calls(calls: list[ProposedToolCall]) -> list[ProposedToolCall]:
    """Round-trip tool calls through JSON like ``AgentToolApprovalStore``."""
    return [
        ProposedToolCall.model_validate(call.model_dump(mode="json")) for call in calls
    ]


def _serialize_edited_payload(
    edited_payload: list[ProposedToolCall] | dict[str, object],
) -> dict[str, object] | list[ProposedToolCall]:
    if isinstance(edited_payload, list):
        return _serialize_tool_calls(edited_payload)
    return edited_payload


class EvalHitlApprovalStore:
    """Minimal approval store for HITL eval agent cases."""

    def __init__(self) -> None:
        self.rows: list[AgentToolApproval] = []
        self.revisions: list[ApprovalRevision] = []

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
    ) -> AgentToolApproval:
        now = datetime.datetime.now(datetime.UTC)
        row = AgentToolApproval(
            id=uuid.uuid4(),
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            approval_correlation_id=approval_correlation_id,
            status=ApprovalStatus.PENDING,
            proposed_calls=_serialize_tool_calls(proposed_calls),
            edited_calls=None,
            reason=None,
            paused_scratchpad=paused_scratchpad,
            paused_state=paused_state,
            pending_message_id=None,
            requested_at=now,
            decided_at=None,
            decided_by=None,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            required_stages=list(required_stages or []),
            request_id=request_metadata.request_id if request_metadata else None,
            source_ip=request_metadata.source_ip if request_metadata else None,
            client_metadata=(
                request_metadata.client_metadata if request_metadata else {}
            ),
        )
        self.rows.append(row)
        return row

    async def get(self, approval_id: uuid.UUID) -> AgentToolApproval | None:
        return next((row for row in self.rows if row.id == approval_id), None)

    async def get_any(self, approval_id: uuid.UUID) -> AgentToolApproval | None:
        """Owner-agnostic fetch (Epic 11 Phase 2 RBAC path; unused in eval runs)."""
        return await self.get(approval_id)

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        row = await self.get(approval_id)
        if row is None or row.owner_id != owner_id:
            return None
        if (
            row.status is ApprovalStatus.PENDING
            and row.expires_at is not None
            and row.expires_at <= datetime.datetime.now(datetime.UTC)
        ):
            now = datetime.datetime.now(datetime.UTC)
            expired = redact_terminal_client_audit_fields(
                row.model_copy(
                    update={
                        "status": ApprovalStatus.EXPIRED,
                        "source_ip": None,
                        "client_metadata": {},
                        "version": row.version + 1,
                        "updated_at": now,
                    }
                )
            )
            self._replace(expired)
            return expired
        return redact_terminal_client_audit_fields(row)

    async def require_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        return row

    async def link_pending_message(
        self,
        approval_id: uuid.UUID,
        *,
        pending_message_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        row = await self.get(approval_id)
        if row is None:
            return None
        now = datetime.datetime.now(datetime.UTC)
        updated = row.model_copy(
            update={"pending_message_id": pending_message_id, "updated_at": now}
        )
        self._replace(updated)
        return updated

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
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        now = datetime.datetime.now(datetime.UTC)
        updates: dict[str, object] = {
            "status": status,
            "decided_by": decided_by,
            "decided_at": now,
            "reason": reason,
            "comments": comments,
            "version": row.version + 1,
            "updated_at": now,
        }
        if edited_calls is not None:
            updates["edited_calls"] = _serialize_tool_calls(edited_calls)
        updates["source_ip"] = None
        updates["client_metadata"] = {}
        if request_metadata is not None:
            updates["request_id"] = request_metadata.request_id
        updated = redact_terminal_client_audit_fields(row.model_copy(update=updates))
        self._replace(updated)
        return updated

    async def cas_cancel(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        reason: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        now = datetime.datetime.now(datetime.UTC)
        updates: dict[str, object] = {
            "status": ApprovalStatus.CANCELLED,
            "decided_by": owner_id,
            "decided_at": now,
            "reason": reason,
            "version": row.version + 1,
            "updated_at": now,
            "source_ip": None,
            "client_metadata": {},
        }
        if request_metadata is not None:
            updates["request_id"] = request_metadata.request_id
        updated = redact_terminal_client_audit_fields(row.model_copy(update=updates))
        self._replace(updated)
        return updated

    async def cas_revise(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        edited_calls: list[ProposedToolCall],
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        updated = row.model_copy(
            update={
                "edited_calls": _serialize_tool_calls(edited_calls),
                "version": row.version + 1,
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        self._replace(updated)
        return updated

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
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        entry = StageDecision(
            stage=stage,
            decision=decision,
            decided_by=decided_by,
            decided_at=datetime.datetime.now(datetime.UTC),
            reason=reason,
            comments=comments,
        )
        updated = row.model_copy(
            update={
                "stage_decisions": [*row.stage_decisions, entry],
                "version": row.version + 1,
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        self._replace(updated)
        return updated

    async def rollback_last_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
    ) -> None:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None or row.status != ApprovalStatus.PENDING:
            return
        if not row.stage_decisions:
            return
        last = row.stage_decisions[-1]
        if last.stage != stage or last.decision != decision:
            return
        updated = row.model_copy(
            update={
                "stage_decisions": row.stage_decisions[:-1],
                "version": row.version + 1,
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        self._replace(updated)

    async def append_revision(
        self,
        *,
        approval_id: uuid.UUID,
        approval_kind: ApprovalKind,
        edited_by: uuid.UUID,
        edited_payload: list[ProposedToolCall] | dict[str, object],
        note: str | None = None,
    ) -> ApprovalRevision:
        if approval_kind != ApprovalKind.AGENT_TOOL:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found for kind {approval_kind.value}."
            )
        if await self.get(approval_id) is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found for kind {approval_kind.value}."
            )
        matching = [
            item
            for item in self.revisions
            if item.approval_id == approval_id and item.approval_kind == approval_kind
        ]
        revision_number = len(matching) + 1
        revision = ApprovalRevision(
            id=uuid.uuid4(),
            approval_id=approval_id,
            approval_kind=approval_kind,
            revision_number=revision_number,
            edited_by=edited_by,
            edited_at=datetime.datetime.now(datetime.UTC),
            edited_payload=_serialize_edited_payload(edited_payload),
            note=note,
        )
        self.revisions.append(revision)
        return revision

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> list[ApprovalRevision]:
        return sorted(
            [
                item
                for item in self.revisions
                if item.approval_id == approval_id
                and item.approval_kind == approval_kind
            ],
            key=lambda item: item.revision_number,
        )

    async def claim_pause_snapshot(
        self, approval_id: uuid.UUID
    ) -> AgentToolApproval | None:
        row = await self.get(approval_id)
        if row is None or row.status is not ApprovalStatus.APPROVED:
            return None
        if not (bool(row.paused_scratchpad) or bool(row.paused_state)):
            return None
        claimed = row.model_copy(deep=True)
        self._replace(
            row.model_copy(
                update={
                    "paused_scratchpad": [],
                    "paused_state": {},
                    "version": row.version + 1,
                    "updated_at": datetime.datetime.now(datetime.UTC),
                }
            )
        )
        return claimed

    async def clear_pause_snapshot(self, approval_id: uuid.UUID) -> None:
        row = await self.get(approval_id)
        if row is None or row.status is ApprovalStatus.PENDING:
            return
        self._replace(
            row.model_copy(
                update={
                    "paused_scratchpad": [],
                    "paused_state": {},
                    "version": row.version + 1,
                    "updated_at": datetime.datetime.now(datetime.UTC),
                }
            )
        )

    def _replace(self, updated: AgentToolApproval) -> None:
        self.rows = [
            updated if existing.id == updated.id else existing for existing in self.rows
        ]


class EvalHitlChatStore:
    """Minimal chat store for HITL eval agent cases."""

    def __init__(self) -> None:
        self.sessions: dict[uuid.UUID, ChatSession] = {}
        self.messages: dict[uuid.UUID, ChatMessage] = {}

    async def create_session(self, *, user_id: uuid.UUID) -> ChatSession:
        session = ChatSession(
            id=uuid.uuid4(),
            user_id=user_id,
            guest_id=None,
            title=None,
            next_seq=1,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.sessions[session.id] = session
        return session

    async def allocate_seq(self, session_id: uuid.UUID) -> int:
        session = self.sessions[session_id]
        seq = session.next_seq
        session.next_seq += 1
        return seq

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
    ) -> ChatMessage:
        message = ChatMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            seq=seq,
            role=role,
            content=content,
            provider=provider,
            model=model,
            status=status,
            finish_reason=finish_reason,
            client_message_id=client_message_id,
            pending_approval_id=pending_approval_id,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        self.messages[message.id] = message
        return message

    async def update_message(
        self,
        message_id: uuid.UUID,
        *,
        content: str | None = None,
        status: str | None = None,
        finish_reason: str | None = None,
        pending_approval_id: uuid.UUID | None = None,
        clear_pending_approval: bool = False,
    ) -> ChatMessage | None:
        message = self.messages.get(message_id)
        if message is None:
            return None
        if content is not None:
            message.content = content
        if status is not None:
            message.status = status
        if finish_reason is not None:
            message.finish_reason = finish_reason
        if pending_approval_id is not None:
            message.pending_approval_id = pending_approval_id
        if clear_pending_approval:
            message.pending_approval_id = None
        return message

    async def get_message(self, message_id: uuid.UUID) -> ChatMessage | None:
        return self.messages.get(message_id)

    async def list_messages(self, session_id: uuid.UUID) -> list[ChatMessage]:
        return sorted(
            (m for m in self.messages.values() if m.session_id == session_id),
            key=lambda m: m.seq,
        )

    async def mark_last_message_at(self, session_id: uuid.UUID) -> None:
        del session_id
