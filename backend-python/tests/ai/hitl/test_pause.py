"""HITL pause path unit tests (Epic 09 Phase 2)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.scratchpad import Scratchpad, ScratchpadEntry
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.exceptions import AgentApprovalPauseError
from app.ai.hitl.models import AgentToolApproval, ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService, _resolve_orphan_resume_model
from app.db.models import ChatMessage
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.tools.schemas import ToolCall
from app.db.models import ChatSession, User
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class TestScratchpadSnapshot:
    def test_round_trip(self) -> None:
        scratchpad = Scratchpad("exec-1")
        scratchpad.append(ScratchpadEntry(kind="thought", content="planning delete"))
        scratchpad.append_tool_result(
            tool_call_id="c1",
            content='{"success": true}',
        )
        restored = Scratchpad.from_snapshot("exec-2", scratchpad.to_snapshot())
        assert len(restored) == len(scratchpad)
        assert restored.to_snapshot() == scratchpad.to_snapshot()


@pytest.mark.anyio
async def test_pause_persists_approval_and_placeholder_message() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    approval_store = InMemoryApprovalStore()
    service = AgentApprovalService(
        approval_store=approval_store,
        chat_store=chat_store,
    )
    publisher = InMemoryStreamPublisher()
    scratchpad = Scratchpad("exec-pause")
    scratchpad.append_thought("about to run sensitive tool")
    state = AgentExecutionState(
        execution_id="exec-pause",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="step-1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1")
        ],
    )

    approval = await service.pause(
        step,
        scratchpad=scratchpad,
        state=state,
        session_id=session.id,
        owner_id=session.user_id,  # type: ignore[arg-type]
        execution_id="exec-pause",
        stream_publisher=publisher,
        provider="openai",
        model="gpt-4o-mini",
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.proposed_calls == [
        ProposedToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1")
    ]
    assert approval.paused_state["status"] == AgentExecutionStatus.WAITING_APPROVAL
    assert approval.pending_message_id is not None
    messages = await chat_store.list_messages(session.id)
    placeholder = next(m for m in messages if m.id == approval.pending_message_id)
    assert placeholder.status == "waiting_approval"
    assert placeholder.content == ""
    assert placeholder.pending_approval_id == approval.id
    assert publisher.events[-1].type.value == "approval_required"


@pytest.mark.anyio
async def test_raise_pause_raises_canonical_error() -> None:
    from app.ai.hitl.service import raise_pause
    import datetime

    approval = AgentToolApproval(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        execution_id="exec-1",
        approval_correlation_id=uuid.uuid4(),
        status=ApprovalStatus.PENDING,
        proposed_calls=[],
        paused_scratchpad=[],
        paused_state={},
        requested_at=datetime.datetime.now(datetime.UTC),
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    with pytest.raises(AgentApprovalPauseError) as exc_info:
        raise_pause(approval)
    assert exc_info.value.approval.id == approval.id


@pytest.mark.anyio
async def test_resume_orphaned_approval_propagates_pause_error_when_fail_safe() -> None:
    import datetime
    from unittest.mock import AsyncMock, patch

    approval_id = uuid.uuid4()
    approval = AgentToolApproval(
        id=approval_id,
        session_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        execution_id="exec-orphan",
        approval_correlation_id=uuid.uuid4(),
        status=ApprovalStatus.APPROVED,
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "resume"}],
        paused_state={"execution_id": "exec-orphan", "status": "waiting_approval"},
        requested_at=datetime.datetime.now(datetime.UTC),
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    store = InMemoryApprovalStore()
    store.rows.append(approval)
    service = AgentApprovalService(
        approval_store=store,
        chat_store=FakeChatStore(),
    )
    pause_error = AgentApprovalPauseError(approval)

    with patch.object(
        service,
        "_resume_after_approval_decision",
        AsyncMock(side_effect=pause_error),
    ):
        with pytest.raises(AgentApprovalPauseError):
            await service.resume_orphaned_approval(
                approval_id,
                executor=AsyncMock(),
                fail_safe=True,
            )


@pytest.mark.anyio
async def test_agent_tool_approval_store_create(db_session) -> None:
    from sqlalchemy import text

    result = await db_session.execute(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result.scalar():
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-{uuid.uuid4().hex}",
        email=f"hitl-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=1)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-db",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "hi"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "x"}],
        paused_state={"execution_id": "exec-db", "status": "executing"},
    )
    assert approval.status == ApprovalStatus.PENDING
    fetched = await store.get(approval.id)
    assert fetched is not None
    assert fetched.proposed_calls[0].name == "echo"


@pytest.mark.anyio
async def test_link_pending_message_refreshes_updated_at(db_session) -> None:
    import datetime

    from sqlalchemy import text, update

    from app.db.models import AgentToolApprovalRecord, ChatMessage

    result = await db_session.execute(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result.scalar():
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-{uuid.uuid4().hex}",
        email=f"hitl-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=1)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-link",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "hi"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "x"}],
        paused_state={"execution_id": "exec-link", "status": "waiting_approval"},
    )
    stale_updated_at = approval.updated_at - datetime.timedelta(minutes=5)
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(updated_at=stale_updated_at)
    )
    await db_session.flush()

    placeholder = ChatMessage(
        session_id=chat_session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    db_session.add(placeholder)
    await db_session.flush()

    linked = await store.link_pending_message(
        approval.id,
        pending_message_id=placeholder.id,
    )

    assert linked is not None
    assert linked.pending_message_id == placeholder.id
    assert linked.updated_at > stale_updated_at


class TestResolveOrphanResumeModel:
    @staticmethod
    def _message(
        *,
        message_id: uuid.UUID,
        session_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
        model: str | None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            seq=seq,
            role=role,
            content=content,
            model=model,
        )
        message.id = message_id
        return message

    def test_prefers_pending_placeholder_model(self) -> None:
        session_id = uuid.uuid4()
        pending_id = uuid.uuid4()
        messages = [
            self._message(
                message_id=uuid.uuid4(),
                session_id=session_id,
                seq=0,
                role="user",
                content="hi",
                model="gpt-4o",
            ),
            self._message(
                message_id=pending_id,
                session_id=session_id,
                seq=1,
                role="assistant",
                content="",
                model="gpt-4.1",
            ),
        ]
        assert (
            _resolve_orphan_resume_model(
                messages,
                pending_message_id=pending_id,
                default_model="fallback",
            )
            == "gpt-4.1"
        )

    def test_falls_back_to_latest_message_with_model(self) -> None:
        session_id = uuid.uuid4()
        messages = [
            self._message(
                message_id=uuid.uuid4(),
                session_id=session_id,
                seq=0,
                role="user",
                content="hi",
                model="gpt-4o",
            ),
            self._message(
                message_id=uuid.uuid4(),
                session_id=session_id,
                seq=1,
                role="assistant",
                content="done",
                model="claude-3-opus",
            ),
        ]
        assert (
            _resolve_orphan_resume_model(
                messages,
                pending_message_id=None,
                default_model="fallback",
            )
            == "claude-3-opus"
        )

    def test_uses_default_when_no_message_models(self) -> None:
        session_id = uuid.uuid4()
        messages = [
            self._message(
                message_id=uuid.uuid4(),
                session_id=session_id,
                seq=0,
                role="user",
                content="hi",
                model=None,
            ),
        ]
        assert (
            _resolve_orphan_resume_model(
                messages,
                pending_message_id=None,
                default_model="fallback-model",
            )
            == "fallback-model"
        )
