"""Tests for ``RouterNodeExecutor`` and conditional routing integration."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.conditions.evaluator import ConditionEvaluator
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.models import (
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.router_node import RouterNodeExecutor
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


def _edge(
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
    *,
    condition: dict[str, object] | None = None,
) -> WorkflowEdge:
    return WorkflowEdge(
        id=edge_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        condition=condition,
    )


def _definition(
    *,
    owner_id: uuid.UUID,
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Routing Workflow",
        entry_node_id=nodes[0].id,
        nodes=nodes,
        edges=edges,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(*, owner_id: uuid.UUID, definition_id: uuid.UUID) -> WorkflowRun:
    return WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition_id,
        owner_id=owner_id,
        idempotency_key="key-1",
        status=RunStatus.RUNNING,
        context=WorkflowContext(),
        current_node_ids=[],
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


class TestRouterNodeExecutor:
    @pytest.mark.anyio
    async def test_selects_first_matching_edge_in_exclusive_mode(self) -> None:
        router = WorkflowNode(id="route", type=NodeType.ROUTER, config={})
        edges = [
            _edge(
                "high",
                "route",
                "high_path",
                condition={
                    "field": "entry.score",
                    "operator": "gte",
                    "value": 10,
                },
            ),
            _edge(
                "low",
                "route",
                "low_path",
                condition={"field": "entry.score", "operator": "lt", "value": 10},
            ),
        ]
        context = WorkflowContext(variables={"entry": {"score": 12}})
        executor = RouterNodeExecutor()

        output = await executor.execute(
            router,
            context,
            NodeExecutionRequest(
                owner_id=uuid.uuid4(),
                execution_receipt_id="r:route:1",
                outgoing_edges=tuple(edges),
            ),
        )

        assert output == {"selected_edge_ids": ["high"]}

    @pytest.mark.anyio
    async def test_all_matching_mode_selects_every_match(self) -> None:
        router = WorkflowNode(
            id="route",
            type=NodeType.ROUTER,
            config={"routing_mode": "all_matching"},
        )
        edges = [
            _edge(
                "a",
                "route",
                "path_a",
                condition={"field": "entry.flag", "operator": "eq", "value": True},
            ),
            _edge(
                "b",
                "route",
                "path_b",
                condition={"field": "entry.flag", "operator": "eq", "value": True},
            ),
            _edge(
                "c",
                "route",
                "path_c",
                condition={"field": "entry.flag", "operator": "eq", "value": False},
            ),
        ]
        context = WorkflowContext(variables={"entry": {"flag": True}})
        executor = RouterNodeExecutor()

        output = await executor.execute(
            router,
            context,
            NodeExecutionRequest(
                owner_id=uuid.uuid4(),
                execution_receipt_id="r:route:1",
                outgoing_edges=tuple(edges),
            ),
        )

        assert output == {"selected_edge_ids": ["a", "b"]}

    @pytest.mark.anyio
    async def test_default_edge_matches_when_no_conditional_edge_matches(self) -> None:
        router = WorkflowNode(id="route", type=NodeType.ROUTER, config={})
        edges = [
            _edge(
                "specific",
                "route",
                "specific_path",
                condition={"field": "entry.score", "operator": "gte", "value": 100},
            ),
            _edge("default", "route", "fallback"),
        ]
        context = WorkflowContext(variables={"entry": {"score": 1}})
        executor = RouterNodeExecutor()

        output = await executor.execute(
            router,
            context,
            NodeExecutionRequest(
                owner_id=uuid.uuid4(),
                execution_receipt_id="r:route:1",
                outgoing_edges=tuple(edges),
            ),
        )

        assert output == {"selected_edge_ids": ["default"]}

    @pytest.mark.anyio
    async def test_no_match_without_default_fails(self) -> None:
        router = WorkflowNode(id="route", type=NodeType.ROUTER, config={})
        edges = [
            _edge(
                "only",
                "route",
                "path",
                condition={"field": "entry.score", "operator": "gte", "value": 100},
            )
        ]
        context = WorkflowContext(variables={"entry": {"score": 1}})
        executor = RouterNodeExecutor()

        with pytest.raises(
            WorkflowNodeExecutionError, match="matched no outgoing edge"
        ):
            await executor.execute(
                router,
                context,
                NodeExecutionRequest(
                    owner_id=uuid.uuid4(),
                    execution_receipt_id="r:route:1",
                    outgoing_edges=tuple(edges),
                ),
            )


class TestConditionalRoutingIntegration:
    @pytest.mark.anyio
    async def test_router_run_visits_selected_branch_and_skips_other(self) -> None:
        owner_id = uuid.uuid4()
        store = FakeWorkflowStore()
        definition = _definition(
            owner_id=owner_id,
            nodes=[
                WorkflowNode(id="entry", type=NodeType.TASK, config={}),
                WorkflowNode(id="route", type=NodeType.ROUTER, config={}),
                WorkflowNode(id="high_path", type=NodeType.TASK, config={}),
                WorkflowNode(id="low_path", type=NodeType.TASK, config={}),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[
                _edge("e1", "entry", "route"),
                _edge(
                    "high",
                    "route",
                    "high_path",
                    condition={
                        "field": "entry.score",
                        "operator": "gte",
                        "value": 10,
                    },
                ),
                _edge(
                    "low",
                    "route",
                    "low_path",
                    condition={"field": "entry.score", "operator": "lt", "value": 10},
                ),
                _edge("e3", "high_path", "end"),
                _edge("e4", "low_path", "end"),
            ],
        )
        await store.create_definition(definition)
        run = await store.create_run(
            _run(owner_id=owner_id, definition_id=definition.id)
        )

        class ScoreTaskExecutor:
            async def execute(
                self,
                node: WorkflowNode,
                context: WorkflowContext,
                request: NodeExecutionRequest,
            ) -> dict[str, object]:
                del context, request
                if node.id == "entry":
                    return {"score": 12}
                return {"branch": node.id}

        executor = WorkflowExecutor(
            store,
            {
                NodeType.TASK: ScoreTaskExecutor(),
                NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
            },
        )

        result = await executor.execute_run(run.id, owner_id=owner_id)

        assert result.status is RunStatus.COMPLETED
        with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
        assert with_executions is not None
        _, executions = with_executions
        by_node = {item.node_id: item for item in executions}
        assert by_node["high_path"].status is NodeStatus.SUCCEEDED
        assert by_node["low_path"].status is NodeStatus.SKIPPED
        skipped_ids = result.context.metadata.get("skipped_node_ids", [])
        assert isinstance(skipped_ids, list)
        assert "low_path" in skipped_ids

    @pytest.mark.anyio
    async def test_no_match_router_fails_run(self) -> None:
        owner_id = uuid.uuid4()
        store = FakeWorkflowStore()
        definition = _definition(
            owner_id=owner_id,
            nodes=[
                WorkflowNode(id="entry", type=NodeType.TASK, config={}),
                WorkflowNode(id="route", type=NodeType.ROUTER, config={}),
                WorkflowNode(id="path", type=NodeType.TASK, config={}),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[
                _edge("e1", "entry", "route"),
                _edge(
                    "only",
                    "route",
                    "path",
                    condition={"field": "entry.score", "operator": "gte", "value": 100},
                ),
                _edge("e3", "path", "end"),
            ],
        )
        await store.create_definition(definition)
        run = await store.create_run(
            _run(owner_id=owner_id, definition_id=definition.id)
        )

        class EntryTaskExecutor:
            async def execute(
                self,
                node: WorkflowNode,
                context: WorkflowContext,
                request: NodeExecutionRequest,
            ) -> dict[str, object]:
                del node, context, request
                return {"score": 1}

        workflow = WorkflowExecutor(
            store,
            {
                NodeType.TASK: EntryTaskExecutor(),
                NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
            },
        )

        result = await workflow.execute_run(run.id, owner_id=owner_id)

        assert result.status is RunStatus.FAILED
        assert "matched no outgoing edge" in (result.error or "")
