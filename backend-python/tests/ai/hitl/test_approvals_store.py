"""ApprovalsStore aggregation tests (Epic 09 Phase 6)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text

from app.ai.hitl.models import ApprovalKind, ApprovalStatus, ProposedToolCall
from app.ai.hitl.store import AgentToolApprovalStore, ApprovalsStore
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
from app.db.models import ChatSession

_NOW = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)


def _active_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Approval Audit Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="approve", type=NodeType.APPROVAL, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="approve"),
            WorkflowEdge(id="e2", from_node_id="approve", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _hitl_tables_available(session) -> bool:
    result = await session.execute(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    return bool(result.scalar())


async def _workflow_tables_available(session) -> bool:
    result = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'workflow_runs'"
        )
    )
    return result == 1


@pytest.mark.anyio
async def test_approvals_store_owner_scoped_list_and_detail(db_session) -> None:
    if not await _hitl_tables_available(db_session):
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner_a = await SqlUserStore(db_session).create(
        sub=f"owner-a-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    owner_b = await SqlUserStore(db_session).create(
        sub=f"owner-b-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    agent_store = AgentToolApprovalStore(db_session)
    approvals_store = ApprovalsStore(db_session)
    workflow_store = PostgresWorkflowStore(db_session, Settings())
    definition = await workflow_store.create_definition(_active_definition(owner_a.id))

    session_a = ChatSession(user_id=owner_a.id, next_seq=1)
    session_b = ChatSession(user_id=owner_b.id, next_seq=1)
    db_session.add_all([session_a, session_b])
    await db_session.flush()

    agent = await agent_store.create(
        session_id=session_a.id,
        owner_id=owner_a.id,
        execution_id="exec-a",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/a"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "secret": "scratchpad-data"}],
        paused_state={"execution_id": "exec-a"},
    )
    await agent_store.create(
        session_id=session_b.id,
        owner_id=owner_b.id,
        execution_id="exec-b",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/b"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-b"},
    )

    run = await workflow_store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_a.id,
            idempotency_key=f"key-{uuid.uuid4().hex}",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(trigger_input={}),
            current_node_ids=["approve"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    workflow_execution = await workflow_store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="approve",
            node_type=NodeType.APPROVAL,
            attempt=1,
            status=NodeStatus.WAITING_APPROVAL,
            input={"trigger_input": {"token": "secret-input"}},
            started_at=_NOW,
        )
    )

    entries, total = await approvals_store.list_for_owner(owner_a.id)
    assert total == 2
    assert {item.kind for item in entries} == {
        ApprovalKind.AGENT_TOOL,
        ApprovalKind.WORKFLOW_NODE,
    }

    other_entries, other_total = await approvals_store.list_for_owner(owner_b.id)
    assert other_total == 1
    assert other_entries[0].kind is ApprovalKind.AGENT_TOOL

    pending_entries, pending_total = await approvals_store.list_for_owner(
        owner_a.id,
        status=ApprovalStatus.PENDING,
    )
    assert pending_total == 2

    detail = await approvals_store.get_for_owner(agent.id, owner_id=owner_a.id)
    assert detail is not None
    assert detail.revision_count == 0
    assert detail.tool_calls is not None
    assert detail.tool_calls[0].name == "delete_file"

    assert await approvals_store.get_for_owner(agent.id, owner_id=owner_b.id) is None

    workflow_detail = await approvals_store.get_for_owner(
        workflow_execution.id,
        owner_id=owner_a.id,
    )
    assert workflow_detail is not None
    assert workflow_detail.workflow_run_id == run.id
    assert workflow_detail.decide_url.endswith("/approve")

    payload = detail.model_dump_json()
    assert "scratchpad" not in payload
    assert "secret-input" not in payload

    await agent_store.append_revision(
        approval_id=agent.id,
        approval_kind=ApprovalKind.AGENT_TOOL,
        edited_by=owner_a.id,
        edited_payload=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/edited"}, call_id="c1"
            )
        ],
    )
    await agent_store.append_revision(
        approval_id=workflow_execution.id,
        approval_kind=ApprovalKind.WORKFLOW_NODE,
        edited_by=owner_a.id,
        edited_payload={"field": "edited"},
    )

    refreshed = await approvals_store.get_for_owner(agent.id, owner_id=owner_a.id)
    assert refreshed is not None
    assert refreshed.revision_count == 1
    assert refreshed.edited is True

    refreshed_workflow = await approvals_store.get_for_owner(
        workflow_execution.id,
        owner_id=owner_a.id,
    )
    assert refreshed_workflow is not None
    assert refreshed_workflow.revision_count == 1
    assert refreshed_workflow.edited is True
