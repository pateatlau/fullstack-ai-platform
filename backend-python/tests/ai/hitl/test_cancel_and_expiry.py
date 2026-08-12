"""Approval cancellation + lazy expiration tests (Epic 09 recommendations #2/#3)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall, RequestMetadata
from app.ai.hitl.service import AgentApprovalService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class _Handler:
    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={})


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            requires_approval=True,
        ),
        _Handler(),
    )
    return registry


def _service(
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    *,
    approval_timeout_hours: int = 0,
) -> AgentApprovalService:
    registry = _registry()
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        scratchpad_store=ScratchpadStore(),
        approval_timeout_hours=approval_timeout_hours,
    )


async def _seed_pending(
    *,
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    owner_id: uuid.UUID,
    expires_at: datetime.datetime | None = None,
) -> uuid.UUID:
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-cancel",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-cancel", "status": "waiting_approval"},
        expires_at=expires_at,
    )
    placeholder = await chat_store.add_message(
        session_id=session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    return approval.id


class TestCancel:
    @pytest.mark.anyio
    async def test_cancel_transitions_pending_to_cancelled(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id
        )

        result = await service.cancel(
            approval_id, owner_id=owner_id, reason="no longer needed"
        )

        assert result.status == ApprovalStatus.CANCELLED
        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.CANCELLED
        assert approval.reason == "no longer needed"

    @pytest.mark.anyio
    async def test_cancel_captures_request_metadata(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id
        )

        await service.cancel(
            approval_id,
            owner_id=owner_id,
            request_metadata=RequestMetadata(
                request_id="req-1", source_ip="10.0.0.1", client_metadata={"ua": "test"}
            ),
        )

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.request_id == "req-1"
        assert approval.source_ip == "10.0.0.1"
        assert approval.client_metadata == {"ua": "test"}

    @pytest.mark.anyio
    async def test_cancel_already_decided_raises_conflict(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id
        )
        await service.decide(approval_id, owner_id=owner_id, decision="rejected")

        with pytest.raises(ApprovalDecisionConflictError):
            await service.cancel(approval_id, owner_id=owner_id)

    @pytest.mark.anyio
    async def test_cancel_wrong_owner_raises_not_found(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id
        )

        with pytest.raises(ApprovalNotFoundError):
            await service.cancel(approval_id, owner_id=uuid.uuid4())

    @pytest.mark.anyio
    async def test_cancel_updates_placeholder_message(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id
        )
        approval = await store.get(approval_id)
        assert approval is not None

        await service.cancel(approval_id, owner_id=owner_id)

        messages = await chat_store.list_messages(approval.session_id)
        assistant = next(m for m in messages if m.role == "assistant")
        assert assistant.status == "stopped"
        assert assistant.finish_reason == "cancelled"


class TestLazyExpiration:
    @pytest.mark.anyio
    async def test_expired_approval_raises_on_decide(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id, expires_at=past
        )

        with pytest.raises(ApprovalExpiredError):
            await service.decide(approval_id, owner_id=owner_id, decision="rejected")

    @pytest.mark.anyio
    async def test_touching_expired_approval_transitions_status(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id, expires_at=past
        )

        with pytest.raises(ApprovalExpiredError):
            await service.get_owned_approval(approval_id, owner_id=owner_id)

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.EXPIRED

    @pytest.mark.anyio
    async def test_expired_approval_cannot_be_cancelled(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id, expires_at=past
        )

        with pytest.raises(ApprovalExpiredError):
            await service.cancel(approval_id, owner_id=owner_id)

    @pytest.mark.anyio
    async def test_future_expiry_does_not_expire(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
        approval_id = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id, expires_at=future
        )

        approval = await service.get_owned_approval(approval_id, owner_id=owner_id)
        assert approval.status == ApprovalStatus.PENDING

    @pytest.mark.anyio
    async def test_zero_timeout_hours_disables_expiry_on_pause(self) -> None:
        """``approval_timeout_hours=0`` (the default) means approvals never expire."""
        from app.ai.hitl.service import _compute_expires_at

        assert _compute_expires_at(0) is None
        assert _compute_expires_at(-1) is None
        computed = _compute_expires_at(24)
        assert computed is not None
        assert computed > datetime.datetime.now(datetime.UTC)
