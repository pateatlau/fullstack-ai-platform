"""HITL approval expiry sweep handler tests (Epic 10 Phase 3)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.handlers.hitl_expiry import hitl_approval_expiry_sweep
from app.ai.jobs.models import BackgroundJob, JobStatus
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.providers.postgres import PostgresWorkflowStore
from app.core.config import Settings
from app.db.identity import SqlUserStore
from app.db.models import AgentToolApprovalRecord, ChatMessage, ChatSession, User
from tests.ai.jobs.conftest import make_queue_session_factory


async def _hitl_tables_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    return bool(result)


async def _workflow_tables_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.workflow_runs') IS NOT NULL")
    )
    return bool(result)


def _sample_job() -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=uuid.uuid4(),
        job_type="hitl_approval_expiry_sweep",
        status=JobStatus.QUEUED,
        payload={"version": 1},
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_expiry_sweep_disabled_when_background_jobs_flag_off() -> None:
    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=False,
        hitl_approval_timeout_hours=1,
    )

    result = await hitl_approval_expiry_sweep(
        _sample_job(),
        settings=settings,
        session_factory=None,  # type: ignore[arg-type]
        build_workflow_manager=lambda _session: None,  # type: ignore[return-value, arg-type]
    )
    assert result.counts["scanned"] == 0


@pytest.mark.anyio
async def test_expiry_sweep_skips_when_timeout_zero(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"expiry-{uuid.uuid4().hex}",
        email=f"expiry-{uuid.uuid4().hex[:8]}@example.com",
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
        execution_id="exec-old",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "x"}],
        paused_state={"execution_id": "exec-old", "status": "waiting_approval"},
    )
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(
            requested_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=48)
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_approval_timeout_hours=0,
        workflow_approval_timeout_hours=0,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import build_workflow_manager_for_session

    result = await hitl_approval_expiry_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_workflow_manager=lambda session: build_workflow_manager_for_session(
            session, settings
        ),
    )
    assert result.counts["agent_expired"] == 0

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
    assert refreshed is not None
    assert refreshed.status is ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_agent_approval_past_timeout_expires_linked_message(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"expiry-{uuid.uuid4().hex}",
        email=f"expiry-{uuid.uuid4().hex[:8]}@example.com",
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
        execution_id="exec-expire",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "x"}],
        paused_state={"execution_id": "exec-expire", "status": "waiting_approval"},
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
            requested_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=2)
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_approval_timeout_hours=1,
        workflow_approval_timeout_hours=0,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import build_workflow_manager_for_session

    result = await hitl_approval_expiry_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_workflow_manager=lambda session: build_workflow_manager_for_session(
            session, settings
        ),
    )
    assert result.counts["agent_expired"] == 1

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
        message = await session.get(ChatMessage, placeholder.id)
    assert refreshed is not None
    assert refreshed.status is ApprovalStatus.EXPIRED
    assert refreshed.paused_scratchpad == []
    assert refreshed.paused_state == {}
    assert message is not None
    assert message.status == "expired"
    assert message.pending_approval_id is None


@pytest.mark.anyio
async def test_expiry_sweep_does_not_touch_terminal_rows(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"expiry-{uuid.uuid4().hex}",
        email=f"expiry-{uuid.uuid4().hex[:8]}@example.com",
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
        execution_id="exec-approved",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={},
    )
    decided = await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(
            requested_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=48)
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        hitl_approval_timeout_hours=1,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import build_workflow_manager_for_session

    result = await hitl_approval_expiry_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_workflow_manager=lambda session: build_workflow_manager_for_session(
            session, settings
        ),
    )
    assert result.counts["agent_expired"] == 0

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
    assert refreshed is not None
    assert refreshed.status is ApprovalStatus.APPROVED
    assert refreshed.decided_at == decided.decided_at


@pytest.mark.anyio
async def test_workflow_approval_node_past_timeout_expires(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner = await SqlUserStore(db_session).create(
        sub=f"wf-expiry-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    now = datetime.datetime.now(datetime.UTC)
    definition = WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner.id,
        name="approval-expiry",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="approve",
        nodes=[
            WorkflowNode(id="approve", type=NodeType.APPROVAL, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="approve", to_node_id="end"),
        ],
        created_at=now,
        updated_at=now,
    )
    workflow_store = PostgresWorkflowStore(
        db_session, Settings(openai_api_key="test-key")
    )
    persisted_definition = await workflow_store.create_definition(definition)
    run = await workflow_store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=persisted_definition.id,
            owner_id=owner.id,
            idempotency_key=f"key-{uuid.uuid4().hex}",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(trigger_input={}),
            current_node_ids=["approve"],
            checkpoint_version=1,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
    )
    execution = await workflow_store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="approve",
            node_type=NodeType.APPROVAL,
            attempt=1,
            status=NodeStatus.WAITING_APPROVAL,
            input={},
            started_at=now - datetime.timedelta(hours=3),
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        workflow_engine_enabled=True,
        hitl_enabled=True,
        hitl_approval_timeout_hours=0,
        workflow_approval_timeout_hours=1,
    )
    factory = make_queue_session_factory(db_session.bind)
    from app.ai.deps import build_workflow_manager_for_session

    result = await hitl_approval_expiry_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_workflow_manager=lambda session: build_workflow_manager_for_session(
            session, settings
        ),
    )
    assert result.counts["workflow_expired"] == 1

    async with factory() as session:
        store = PostgresWorkflowStore(session, Settings(openai_api_key="test-key"))
        with_executions = await store.get_run_with_executions(run.id, owner_id=owner.id)
    assert with_executions is not None
    updated_run, executions = with_executions
    expired = next(item for item in executions if item.id == execution.id)
    assert expired.status is NodeStatus.FAILED
    assert expired.decision is not None
    assert expired.decision.value == "expired"
    assert updated_run.status is RunStatus.FAILED
