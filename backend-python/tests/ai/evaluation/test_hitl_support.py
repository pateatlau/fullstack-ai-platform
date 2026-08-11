"""Unit tests for EvalHitlApprovalStore parity with production store behavior."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.evaluation.hitl_support import EvalHitlApprovalStore
from app.ai.hitl.exceptions import ApprovalNotFoundError
from app.ai.hitl.models import ApprovalKind, ProposedToolCall


async def _create_pending(store: EvalHitlApprovalStore) -> uuid.UUID:
    approval = await store.create(
        session_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        execution_id="eval-support",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "hello"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "eval-support", "status": "waiting_approval"},
    )
    return approval.id


@pytest.mark.anyio
async def test_create_serializes_proposed_calls_through_json() -> None:
    store = EvalHitlApprovalStore()
    approval_id = await _create_pending(store)

    approval = await store.get(approval_id)
    assert approval is not None
    assert approval.proposed_calls[0].name == "send_notification"
    assert approval.proposed_calls[0].arguments == {"message": "hello"}


@pytest.mark.anyio
async def test_link_pending_message_refreshes_updated_at() -> None:
    store = EvalHitlApprovalStore()
    approval_id = await _create_pending(store)
    approval = await store.get(approval_id)
    assert approval is not None
    stale_updated_at = approval.updated_at - datetime.timedelta(minutes=5)
    store.rows[0] = approval.model_copy(update={"updated_at": stale_updated_at})

    linked = await store.link_pending_message(
        approval_id,
        pending_message_id=uuid.uuid4(),
    )

    assert linked is not None
    assert linked.updated_at > stale_updated_at


@pytest.mark.anyio
async def test_append_revision_rejects_mismatched_approval_kind() -> None:
    store = EvalHitlApprovalStore()
    approval_id = await _create_pending(store)

    with pytest.raises(ApprovalNotFoundError, match="not found for kind"):
        await store.append_revision(
            approval_id=approval_id,
            approval_kind=ApprovalKind.WORKFLOW_NODE,
            edited_by=uuid.uuid4(),
            edited_payload={"message": "edited"},
        )

    assert not store.revisions
