"""Tests for ``GraphValidator`` (Epic 06 Phase 2)."""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

import pytest

from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)

if TYPE_CHECKING:
    from app.ai.tools.registry import ToolRegistry

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
                _node("fork_a", NodeType.FORK, config={"join_node_id": "join"}),
                _node("fork_b", NodeType.FORK, config={"join_node_id": "join"}),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork_a"}),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork_a"),
                _edge("e2", "start", "fork_b"),
                _edge("e3", "fork_a", "join"),
                _edge("e4", "fork_b", "end"),
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


class TestGraphValidatorNodeConfigs:
    def test_valid_llm_node_config_passes(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.LLM,
                    config={"prompt_template": "Hello {{ variables.name }}"},
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        _VALIDATOR.validate(definition)

    def test_llm_node_missing_prompt_template_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node("start", NodeType.LLM, config={}),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="prompt_template"):
            _VALIDATOR.validate(definition)

    def test_llm_node_invalid_file_template_reference_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.LLM,
                    config={"prompt_template": "@workflow/only-two"},
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="@category/name/version"):
            _VALIDATOR.validate(definition)

    def test_valid_agent_node_config_passes(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.AGENT,
                    config={
                        "goal": "Research topic",
                        "tool_names": ["web_search"],
                        "max_iterations": 3,
                    },
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        _VALIDATOR.validate(definition)

    def test_agent_node_missing_goal_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node("start", NodeType.AGENT, config={"tool_names": ["web_search"]}),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="goal"):
            _VALIDATOR.validate(definition)

    def test_agent_node_invalid_tool_names_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.AGENT,
                    config={"goal": "Do work", "tool_names": []},
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="tool_names"):
            _VALIDATOR.validate(definition)

    def test_agent_node_file_template_goal_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.AGENT,
                    config={"goal": "@workflow/transform/1"},
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="goal"):
            _VALIDATOR.validate(definition)

    def test_agent_node_file_template_instructions_is_rejected(self) -> None:
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.AGENT,
                    config={
                        "goal": "Do work",
                        "instructions": "@workflow/transform/1",
                    },
                ),
                _node("end", NodeType.TERMINAL),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="instructions"):
            _VALIDATOR.validate(definition)

    def test_approval_node_requires_approved_edge_id_for_multiple_unconditional_outgoing(
        self,
    ) -> None:
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node("approve", NodeType.APPROVAL, config={}),
                _node("left", NodeType.TASK),
                _node("right", NodeType.TASK),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "approve"),
                _edge("e2", "approve", "left"),
                _edge("e3", "approve", "right"),
                _edge("e4", "left", "end"),
                _edge("e5", "right", "end"),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="approved_edge_id"):
            _VALIDATOR.validate(definition)

    def test_approval_node_requires_approved_edge_id_for_single_conditional_outgoing(
        self,
    ) -> None:
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node("approve", NodeType.APPROVAL, config={}),
                _node("next", NodeType.TASK),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "approve"),
                _edge(
                    "e2",
                    "approve",
                    "next",
                    condition={
                        "field": "trigger_input.flag",
                        "operator": "eq",
                        "value": True,
                    },
                ),
                _edge("e3", "next", "end"),
            ],
        )
        with pytest.raises(WorkflowValidationError, match="approved_edge_id"):
            _VALIDATOR.validate(definition)

    def test_approval_node_allows_single_unconditional_edge_without_config(
        self,
    ) -> None:
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node(
                    "approve", NodeType.APPROVAL, config={"rejected_edge_id": "reject"}
                ),
                _node("approved_task", NodeType.TASK),
                _node("rejected_task", NodeType.TASK),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "approve"),
                _edge("approved", "approve", "approved_task"),
                _edge(
                    "reject",
                    "approve",
                    "rejected_task",
                    condition={
                        "field": "trigger_input.flag",
                        "operator": "eq",
                        "value": False,
                    },
                ),
                _edge("e3", "approved_task", "end"),
                _edge("e4", "rejected_task", "end"),
            ],
        )
        _VALIDATOR.validate(definition)


def _hitl_validator(
    registry: "ToolRegistry",
    *,
    hitl_enabled: bool = True,
    required_tool_names: frozenset[str] = frozenset(),
) -> GraphValidator:
    from app.ai.hitl.policy import ApprovalPolicy

    policy = ApprovalPolicy(required_tool_names=required_tool_names)
    return GraphValidator(
        max_nodes_per_definition=20,
        max_parallel_branches=8,
        hitl_enabled=hitl_enabled,
        tool_registry=registry,
        approval_policy=policy if hitl_enabled else None,
    )


def _sensitive_tool_registry(*, tool_name: str = "delete_file") -> "ToolRegistry":
    from app.ai.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=tool_name,
            description="Sensitive tool",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        _NoOpHandler(),
    )
    registry.register(
        ToolDefinition(
            name="echo",
            description="Safe echo",
            parameters={"type": "object", "properties": {}},
        ),
        _NoOpHandler(),
    )
    return registry


class _NoOpHandler:
    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={})


class TestGraphValidatorApprovalRequiredToolReachability:
    def test_hitl_enabled_without_dependencies_fails_fast(self) -> None:
        validator = GraphValidator(hitl_enabled=True)

        with pytest.raises(WorkflowValidationError, match="misconfigured"):
            validator.validate(_definition())

    def test_sensitive_task_without_approval_is_rejected(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node(
                    "risky",
                    NodeType.TASK,
                    config={"tool_name": "delete_file", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "risky"),
                _edge("e2", "risky", "end"),
            ],
        )

        with pytest.raises(
            WorkflowValidationError, match="approval-required tool 'delete_file'"
        ):
            validator.validate(definition)

    def test_sensitive_task_preceded_by_approval_passes(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node("approve", NodeType.APPROVAL, config={"approved_edge_id": "ok"}),
                _node(
                    "risky",
                    NodeType.TASK,
                    config={"tool_name": "delete_file", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "approve"),
                _edge("ok", "approve", "risky"),
                _edge("e3", "risky", "end"),
            ],
        )

        validator.validate(definition)

    def test_unflagged_tool_is_unaffected(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node(
                    "safe",
                    NodeType.TASK,
                    config={"tool_name": "echo", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "safe"),
                _edge("e2", "safe", "end"),
            ],
        )

        validator.validate(definition)

    def test_sensitive_agent_node_without_approval_is_rejected(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.AGENT,
                    config={"goal": "Do work", "tool_names": ["delete_file"]},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[_edge("e1", "start", "end")],
        )

        with pytest.raises(WorkflowValidationError, match="approval-required tool"):
            validator.validate(definition)

    def test_config_flagged_tool_without_definition_flag_is_rejected(self) -> None:
        from app.ai.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="send_email",
                description="Send email",
                parameters={"type": "object", "properties": {}},
            ),
            _NoOpHandler(),
        )
        validator = _hitl_validator(
            registry, required_tool_names=frozenset({"send_email"})
        )
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.TASK,
                    config={"tool_name": "send_email", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[_edge("e1", "start", "end")],
        )

        with pytest.raises(
            WorkflowValidationError, match="approval-required tool 'send_email'"
        ):
            validator.validate(definition)

    def test_hitl_disabled_skips_reachability_check(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry, hitl_enabled=False)
        definition = _definition(
            nodes=[
                _node(
                    "start",
                    NodeType.TASK,
                    config={"tool_name": "delete_file", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[_edge("e1", "start", "end")],
        )

        validator.validate(definition)

    def test_parallel_branch_missing_approval_is_rejected(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node("fork", NodeType.FORK, config={"join_node_id": "join"}),
                _node("left", NodeType.TASK),
                _node(
                    "right",
                    NodeType.TASK,
                    config={"tool_name": "delete_file", "arguments_template": {}},
                ),
                _node("approve", NodeType.APPROVAL, config={"approved_edge_id": "ok"}),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork"}),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork"),
                _edge("e2", "fork", "left"),
                _edge("e3", "fork", "right"),
                _edge("e4", "left", "approve"),
                _edge("ok", "approve", "join"),
                _edge("e6", "right", "join"),
                _edge("e7", "join", "end"),
            ],
        )

        with pytest.raises(WorkflowValidationError, match="approval-required tool"):
            validator.validate(definition)

    def test_parallel_branches_both_preceded_by_approval_passes(self) -> None:
        registry = _sensitive_tool_registry()
        validator = _hitl_validator(registry)
        definition = _definition(
            nodes=[
                _node("start", NodeType.TASK),
                _node("fork", NodeType.FORK, config={"join_node_id": "join"}),
                _node("left", NodeType.TASK),
                _node("right", NodeType.TASK),
                _node(
                    "approve_left",
                    NodeType.APPROVAL,
                    config={"approved_edge_id": "left_ok"},
                ),
                _node(
                    "approve_right",
                    NodeType.APPROVAL,
                    config={"approved_edge_id": "right_ok"},
                ),
                _node("join", NodeType.JOIN, config={"fork_node_id": "fork"}),
                _node(
                    "risky",
                    NodeType.TASK,
                    config={"tool_name": "delete_file", "arguments_template": {}},
                ),
                _node("end", NodeType.TERMINAL),
            ],
            edges=[
                _edge("e1", "start", "fork"),
                _edge("e2", "fork", "left"),
                _edge("e3", "fork", "right"),
                _edge("e4", "left", "approve_left"),
                _edge("e5", "right", "approve_right"),
                _edge("left_ok", "approve_left", "join"),
                _edge("right_ok", "approve_right", "join"),
                _edge("e8", "join", "risky"),
                _edge("e9", "risky", "end"),
            ],
        )

        validator.validate(definition)
