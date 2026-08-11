"""HITL revise endpoint/service tests (Epic 09 Phase 3)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalNotFoundError,
    ApprovalValidationError,
)
from app.ai.hitl.models import ApprovalKind, ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class _NoopHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
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
        _NoopHandler(),
    )
    return registry


async def _pending_approval(
    store: InMemoryApprovalStore,
    *,
    owner_id: uuid.UUID,
) -> uuid.UUID:
    approval = await store.create(
        session_id=uuid.uuid4(),
        owner_id=owner_id,
        execution_id="exec-revise",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-revise", "status": "waiting_approval"},
    )
    return approval.id


@pytest.mark.anyio
async def test_revise_twice_records_ordered_revisions() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    service = AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=_registry(),
    )
    approval_id = await _pending_approval(store, owner_id=owner_id)

    first_edit = [
        ProposedToolCall(name="delete_file", arguments={"path": "/tmp/a"}, call_id="c1")
    ]
    second_edit = [
        ProposedToolCall(name="delete_file", arguments={"path": "/tmp/b"}, call_id="c1")
    ]

    await service.revise(approval_id, edited_calls=first_edit, owner_id=owner_id)
    updated, _ = await service.revise(
        approval_id, edited_calls=second_edit, owner_id=owner_id, note="second"
    )

    revisions = await store.list_revisions(
        approval_id, approval_kind=ApprovalKind.AGENT_TOOL
    )
    assert [item.revision_number for item in revisions] == [1, 2]
    assert updated.edited_calls == second_edit


@pytest.mark.anyio
async def test_revise_invalid_payload_raises_422() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    service = AgentApprovalService(
        approval_store=store,
        chat_store=FakeChatStore(),
        tool_registry=_registry(),
    )
    approval_id = await _pending_approval(store, owner_id=owner_id)

    with pytest.raises(ApprovalValidationError):
        await service.revise(
            approval_id,
            edited_calls=[
                ProposedToolCall(
                    name="delete_file",
                    arguments={"path": 123},  # type: ignore[arg-type]
                    call_id="c1",
                )
            ],
            owner_id=owner_id,
        )

    assert not store.revisions


@pytest.mark.anyio
async def test_revise_rejects_unknown_or_mismatched_calls() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    service = AgentApprovalService(
        approval_store=store,
        chat_store=FakeChatStore(),
        tool_registry=_registry(),
    )
    approval_id = await _pending_approval(store, owner_id=owner_id)

    with pytest.raises(ApprovalValidationError, match="Unknown edited call_id"):
        await service.revise(
            approval_id,
            edited_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/tmp/x"}, call_id="c9"
                )
            ],
            owner_id=owner_id,
        )

    with pytest.raises(ApprovalValidationError, match="Tool name mismatch"):
        await service.revise(
            approval_id,
            edited_calls=[
                ProposedToolCall(
                    name="other_tool", arguments={"path": "/tmp/x"}, call_id="c1"
                )
            ],
            owner_id=owner_id,
        )

    with pytest.raises(ApprovalValidationError, match="exactly 1 call"):
        await service.revise(
            approval_id,
            edited_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/a"}, call_id="c1"
                ),
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/b"}, call_id="c2"
                ),
            ],
            owner_id=owner_id,
        )

    assert not store.revisions


@pytest.mark.anyio
async def test_append_revision_unknown_approval_raises() -> None:
    store = InMemoryApprovalStore()

    with pytest.raises(ApprovalNotFoundError):
        await store.append_revision(
            approval_id=uuid.uuid4(),
            approval_kind=ApprovalKind.AGENT_TOOL,
            edited_by=uuid.uuid4(),
            edited_payload=[],
        )


@pytest.mark.anyio
async def test_revise_after_terminal_decision_raises_409() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    service = AgentApprovalService(
        approval_store=store,
        chat_store=FakeChatStore(),
        tool_registry=_registry(),
    )
    approval_id = await _pending_approval(store, owner_id=owner_id)
    await store.cas_decide(
        approval_id,
        owner_id=owner_id,
        status=ApprovalStatus.REJECTED,
        decided_by=owner_id,
    )

    with pytest.raises(ApprovalDecisionConflictError):
        await service.revise(
            approval_id,
            edited_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
                )
            ],
            owner_id=owner_id,
        )
