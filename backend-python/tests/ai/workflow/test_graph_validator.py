"""Tests for ``GraphValidator`` (Epic 06 Phase 2)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)

_NOW = datetime.datetime.now(datetime.UTC)
_VALIDATOR = GraphValidator(max_nodes_per_definition=5, max_parallel_branches=2)


def _node(
    node_id: str,
    node_type: NodeType = NodeType.TASK,
    *,
    config: dict[str, object] | None = None,
) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, config=config or {})


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
    entry_node_id: str = "start",
    nodes: list[WorkflowNode] | None = None,
    edges: list[WorkflowEdge] | None = None,
) -> WorkflowDefinition:
    resolved_nodes = nodes or [_node("start"), _node("end", NodeType.TERMINAL)]
    resolved_edges = edges or [_edge("e1", "start", "end")]
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name="Test Workflow",
        entry_node_id=entry_node_id,
        nodes=resolved_nodes,
        edges=resolved_edges,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestGraphValidatorAcceptance:
    def test_valid_linear_graph_passes(self) -> None:
        _VALIDATOR.validate(_definition())

    def test_active_status_still_requires_valid_graph(self) -> None:
        definition = _definition(
            edges=[
                _edge("e1", "start", "end"),
                _edge("e2", "end", "start"),
            ],
        )
        definition.status = DefinitionStatus.ACTIVE

        with pytest.raises(WorkflowValidationError, match="Cycle detected"):
            _VALIDATOR.validate(definition)


class TestGraphValidatorCycles:
    def test_cycle_is_rejected(self) -> None:
        definition = _definition(
            nodes=[_node("start"), _node("mid"), _node("end", NodeType.TERMINAL)],
            edges=[
                _edge("e1", "start", "mid"),
                _edge("e2", "mid", "end"),
                _edge("e3", "end", "start"),
            ],
            entry_node_id="start",
        )

        with pytest.raises(WorkflowValidationError, match="Cycle detected"):
            _VALIDATOR.validate(definition)


class TestGraphValidatorReachability:
    def test_unreachable_node_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node("start"),
                _node("orphan"),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[_edge("e1", "start", "end")],
        )

        with pytest.raises(WorkflowValidationError, match="unreachable"):
            _VALIDATOR.validate(definition)


class TestGraphValidatorDanglingEdges:
    def test_dangling_from_node_is_rejected(self) -> None:
        definition = WorkflowDefinition.model_construct(
            id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            name="Test Workflow",
            entry_node_id="start",
            nodes=[_node("start"), _node("end", NodeType.TERMINAL)],
            edges=[
                WorkflowEdge(
                    id="e1",
                    from_node_id="missing",
                    to_node_id="end",
                )
            ],
            created_at=_NOW,
            updated_at=_NOW,
        )

        with pytest.raises(WorkflowValidationError, match="unknown from_node_id"):
            _VALIDATOR.validate(definition)


class TestGraphValidatorForkJoin:
    def test_fork_without_join_reference_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node("start"),
                _node("fork", NodeType.FORK),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork"),
                _edge("e2", "fork", "end"),
            ],
        )

        with pytest.raises(WorkflowValidationError, match="join_node_id"):
            _VALIDATOR.validate(definition)

    def test_mismatched_fork_join_pair_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node("start"),
                _node("fork-a", NodeType.FORK, config={"join_node_id": "join"}),
                _node("fork-b", NodeType.FORK, config={"join_node_id": "join"}),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork-a"}),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork-a"),
                _edge("e2", "start", "fork-b"),
                _edge("e3", "fork-a", "join"),
                _edge("e4", "fork-b", "end"),
                _edge("e5", "join", "end"),
            ],
        )

        with pytest.raises(WorkflowValidationError, match="paired bidirectionally"):
            _VALIDATOR.validate(definition)

    def test_valid_fork_join_pair_passes(self) -> None:
        definition = _definition(
            nodes=[
                _node("start"),
                _node("fork", NodeType.FORK, config={"join_node_id": "join"}),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork"}),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork"),
                _edge("e2", "fork", "end"),
                _edge("e3", "fork", "join"),
                _edge("e4", "join", "end"),
            ],
        )

        _VALIDATOR.validate(definition)

    def test_fork_exceeding_parallel_branch_limit_is_rejected(self) -> None:
        validator = GraphValidator(max_nodes_per_definition=20, max_parallel_branches=2)
        definition = _definition(
            nodes=[
                _node("start"),
                _node("fork", NodeType.FORK, config={"join_node_id": "join"}),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork"}),
                _node("b1"),
                _node("b2"),
                _node("b3"),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork"),
                _edge("e2", "fork", "b1"),
                _edge("e3", "fork", "b2"),
                _edge("e4", "fork", "b3"),
                _edge("e5", "b1", "join"),
                _edge("e6", "b2", "join"),
                _edge("e7", "b3", "join"),
                _edge("e8", "join", "end"),
            ],
        )

        with pytest.raises(
            WorkflowValidationError, match="workflow_max_parallel_branches"
        ):
            validator.validate(definition)


class TestGraphValidatorConditions:
    def test_valid_leaf_condition_passes(self) -> None:
        definition = _definition(
            edges=[
                _edge(
                    "e1",
                    "start",
                    "end",
                    condition={
                        "field": "variables.start.status",
                        "operator": "eq",
                        "value": "ok",
                    },
                )
            ],
        )

        _VALIDATOR.validate(definition)

    def test_invalid_condition_operator_is_rejected(self) -> None:
        definition = _definition(
            edges=[
                _edge(
                    "e1",
                    "start",
                    "end",
                    condition={"field": "x", "operator": "eval", "value": "1"},
                )
            ],
        )

        with pytest.raises(WorkflowValidationError, match="operator"):
            _VALIDATOR.validate(definition)

    def test_composite_condition_passes(self) -> None:
        definition = _definition(
            edges=[
                _edge(
                    "e1",
                    "start",
                    "end",
                    condition={
                        "all": [
                            {
                                "field": "variables.start.ok",
                                "operator": "exists",
                            },
                            {
                                "field": "variables.start.count",
                                "operator": "gte",
                                "value": 1,
                            },
                        ]
                    },
                )
            ],
        )

        _VALIDATOR.validate(definition)


class TestGraphValidatorLimits:
    def test_max_nodes_per_definition_is_enforced(self) -> None:
        nodes = [_node(f"n{i}") for i in range(6)]
        nodes.append(_node("end", NodeType.TERMINAL))
        edges = [_edge(f"e{i}", f"n{i}", "end") for i in range(6)]
        definition = _definition(
            entry_node_id="n0",
            nodes=nodes,
            edges=edges,
        )

        with pytest.raises(
            WorkflowValidationError, match="workflow_max_nodes_per_definition"
        ):
            _VALIDATOR.validate(definition)
