"""HITL orphaned snapshot sweep handler tests (Epic 10 Phase 3)."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text, update

from app.ai.agent.executor.result_aggregator import AggregatedToolResults
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.handlers.hitl_orphan_sweep import hitl_orphaned_snapshot_sweep
from app.ai.jobs.models import BackgroundJob, JobStatus
from app.core.config import Settings
from app.db.models import AgentToolApprovalRecord, ChatMessage, ChatSession, User
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.ai.jobs.conftest import make_queue_session_factory
from tests.fakes import FakeChatStore


def _sample_job(*, attempt_count: int = 1, max_attempts: int = 3) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=uuid.uuid4(),
        job_type="hitl_orphaned_snapshot_sweep",
        status=JobStatus.QUEUED,
        payload={"version": 1},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_orphan_sweep_disabled_when_background_jobs_flag_off() -> None:
    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=False,
    )

    result = await hitl_orphaned_snapshot_sweep(
        _sample_job(),
        settings=settings,
        session_factory=None,  # type: ignore[arg-type]
        build_approval_service=lambda _session: None,  # type: ignore[return-value, arg-type]
        build_resume_executor=lambda _session, _service: None,  # type: ignore[return-value, arg-type]
    )
    assert result.counts["scanned"] == 0


@pytest.mark.anyio
async def test_orphan_within_grace_period_is_not_touched(db_session) -> None:
    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"orphan-{uuid.uuid4().hex}",
        email=f"orphan-{uuid.uuid4().hex[:8]}@example.com",
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
        execution_id="exec-grace",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "x"}],
        paused_state={"execution_id": "exec-grace", "status": "waiting_approval"},
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_orphan_sweep_grace_seconds=3600,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import (
        build_agent_approval_service_for_session,
        build_hitl_resume_executor,
    )

    sweep_result = await hitl_orphaned_snapshot_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_approval_service=lambda session: build_agent_approval_service_for_session(
            session, settings
        ),
        build_resume_executor=lambda _session, service: build_hitl_resume_executor(
            settings,
            approval_service=service,
        ),
    )
    assert sweep_result.counts["resumed"] == 0
    assert sweep_result.counts["fail_safe"] == 0

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
    assert refreshed is not None
    assert refreshed.paused_scratchpad != []


@pytest.mark.anyio
async def test_orphaned_snapshot_resumes_to_complete_message(db_session) -> None:
    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"orphan-{uuid.uuid4().hex}",
        email=f"orphan-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=2)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-orphan",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "resume me"}],
        paused_state=AgentExecutionState(
            execution_id="exec-orphan",
            status=AgentExecutionStatus.WAITING_APPROVAL,
            current_iteration=1,
        ).model_dump(mode="json"),
    )
    decided_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
    placeholder = ChatMessage(
        session_id=chat_session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    user_message = ChatMessage(
        session_id=chat_session.id,
        seq=0,
        role="user",
        content="please continue",
        status="complete",
    )
    db_session.add_all([user_message, placeholder])
    await db_session.flush()
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(decided_at=decided_at)
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_enabled=True,
        hitl_orphan_sweep_grace_seconds=60,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import (
        build_agent_approval_service_for_session,
        build_hitl_resume_executor,
    )

    executor = build_hitl_resume_executor(settings)
    original_resume = executor.resume_from_approval

    async def _stub_resume(*args, **kwargs):
        del args, kwargs
        return AgentResponse(content="Recovered.", finish_reason="stop")

    executor.resume_from_approval = _stub_resume  # type: ignore[method-assign]

    with patch.object(
        AgentApprovalService,
        "_execute_approved_calls",
        AsyncMock(return_value=AggregatedToolResults(records=[])),
    ):
        result = await hitl_orphaned_snapshot_sweep(
            _sample_job(),
            settings=settings,
            session_factory=factory,
            build_approval_service=lambda session: (
                build_agent_approval_service_for_session(session, settings)
            ),
            build_resume_executor=lambda _session, _service: executor,
        )
    assert result.counts["resumed"] == 1

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
        message = await session.get(ChatMessage, placeholder.id)
    assert refreshed is not None
    assert refreshed.paused_scratchpad == []
    assert refreshed.paused_state == {}
    assert message is not None
    assert message.status == "complete"
    assert message.content == "Recovered."

    executor.resume_from_approval = original_resume


@pytest.mark.anyio
async def test_orphan_fail_safe_after_max_attempts(db_session) -> None:
    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"orphan-{uuid.uuid4().hex}",
        email=f"orphan-{uuid.uuid4().hex[:8]}@example.com",
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
        execution_id="exec-fail",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "boom"}],
        paused_state={"execution_id": "exec-fail", "status": "waiting_approval"},
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
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
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(
            decided_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(minutes=10)
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_enabled=True,
        hitl_orphan_sweep_grace_seconds=60,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import (
        build_agent_approval_service_for_session,
        build_hitl_resume_executor,
    )

    executor = build_hitl_resume_executor(settings)

    async def _fail_resume(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("resume failed")

    executor.resume_from_approval = _fail_resume  # type: ignore[method-assign]

    with patch.object(
        AgentApprovalService,
        "_execute_approved_calls",
        AsyncMock(return_value=AggregatedToolResults(records=[])),
    ):
        sweep_result = await hitl_orphaned_snapshot_sweep(
            _sample_job(attempt_count=3, max_attempts=3),
            settings=settings,
            session_factory=factory,
            build_approval_service=lambda session: (
                build_agent_approval_service_for_session(session, settings)
            ),
            build_resume_executor=lambda _session, _service: executor,
        )
    assert sweep_result.counts["fail_safe"] == 1

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
        message = await session.get(ChatMessage, placeholder.id)
    assert refreshed is not None
    assert refreshed.paused_scratchpad == []
    assert refreshed.paused_state == {}
    assert message is not None
    assert message.status == "error"


@pytest.mark.anyio
async def test_orphan_sweep_isolates_failures_with_savepoints(db_session) -> None:
    """One orphan's failure must not roll back another orphan's successful resume."""
    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"orphan-{uuid.uuid4().hex}",
        email=f"orphan-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=4)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    decided_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    orphan_state = {
        "execution_id": "exec-shared",
        "status": "waiting_approval",
        "current_iteration": 1,
    }

    async def _create_orphan(*, execution_id: str, content: str, seq: int):
        approval = await store.create(
            session_id=chat_session.id,
            owner_id=user.id,
            execution_id=execution_id,
            approval_correlation_id=uuid.uuid4(),
            proposed_calls=[
                ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
            ],
            paused_scratchpad=[{"kind": "thought", "content": content}],
            paused_state=orphan_state,
        )
        await store.cas_decide(
            approval.id,
            owner_id=user.id,
            status=ApprovalStatus.APPROVED,
            decided_by=user.id,
        )
        placeholder = ChatMessage(
            session_id=chat_session.id,
            seq=seq,
            role="assistant",
            content="",
            status="waiting_approval",
            pending_approval_id=approval.id,
        )
        db_session.add(placeholder)
        await db_session.flush()
        await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
        await db_session.execute(
            update(AgentToolApprovalRecord)
            .where(AgentToolApprovalRecord.id == approval.id)
            .values(decided_at=decided_at)
        )
        return approval, placeholder

    good_approval, good_placeholder = await _create_orphan(
        execution_id="exec-good",
        content="recover me",
        seq=1,
    )
    bad_approval, _bad_placeholder = await _create_orphan(
        execution_id="exec-bad",
        content="fail me",
        seq=3,
    )
    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            seq=0,
            role="user",
            content="please continue",
            status="complete",
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_enabled=True,
        hitl_orphan_sweep_grace_seconds=60,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import (
        build_agent_approval_service_for_session,
        build_hitl_resume_executor,
    )

    executor = build_hitl_resume_executor(settings)
    original_resume = executor.resume_from_approval

    async def _stub_resume(*args, **kwargs):
        del args, kwargs
        return AgentResponse(content="Recovered.", finish_reason="stop")

    executor.resume_from_approval = _stub_resume  # type: ignore[method-assign]

    original_orphan_resume = AgentApprovalService.resume_orphaned_approval

    async def _resume_with_forced_failure(
        self,
        approval_id: uuid.UUID,
        *,
        executor,
        fail_safe: bool = False,
    ) -> bool:
        if approval_id == bad_approval.id:
            raise RuntimeError("forced orphan failure")
        return await original_orphan_resume(
            self,
            approval_id,
            executor=executor,
            fail_safe=fail_safe,
        )

    with (
        patch.object(
            AgentApprovalService,
            "resume_orphaned_approval",
            _resume_with_forced_failure,
        ),
        patch.object(
            AgentApprovalService,
            "_execute_approved_calls",
            AsyncMock(return_value=AggregatedToolResults(records=[])),
        ),
    ):
        sweep_result = await hitl_orphaned_snapshot_sweep(
            _sample_job(attempt_count=3, max_attempts=3),
            settings=settings,
            session_factory=factory,
            build_approval_service=lambda session: (
                build_agent_approval_service_for_session(session, settings)
            ),
            build_resume_executor=lambda _session, _service: executor,
        )

    assert sweep_result.counts["resumed"] == 1
    assert sweep_result.counts["fail_safe"] == 1

    async with factory() as session:
        good_row = await AgentToolApprovalStore(session).get(good_approval.id)
        bad_row = await AgentToolApprovalStore(session).get(bad_approval.id)
        good_message = await session.get(ChatMessage, good_placeholder.id)
    assert good_row is not None
    assert good_row.paused_scratchpad == []
    assert good_row.paused_state == {}
    assert good_message is not None
    assert good_message.status == "complete"
    assert good_message.content == "Recovered."
    assert bad_row is not None
    assert bad_row.paused_scratchpad != []

    executor.resume_from_approval = original_resume


def test_build_hitl_resume_executor_shares_scratchpad_store_with_service() -> None:
    from app.ai.agent.planner.react_planner import ReActPlanner
    from app.ai.deps import build_hitl_resume_executor

    settings = Settings(openai_api_key="test-key", hitl_enabled=True)
    service = AgentApprovalService(
        approval_store=InMemoryApprovalStore(),
        chat_store=FakeChatStore(),
    )
    executor = build_hitl_resume_executor(settings, approval_service=service)

    assert executor._scratchpad_store is service._scratchpad_store
    planner = executor._planner
    assert isinstance(planner, ReActPlanner)
    assert planner._scratchpad_store is service._scratchpad_store
