"""ApprovalPolicy unit tests (Epic 09 Phase 1)."""

from __future__ import annotations

from app.ai.hitl import ApprovalPolicy
from app.ai.tools.schemas import ToolDefinition


def _tool(*, name: str = "echo", requires_approval: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test tool",
        parameters={"type": "object", "properties": {}},
        requires_approval=requires_approval,
    )


class TestApprovalPolicy:
    def test_tool_flagged_requires_approval(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset())
        assert policy.requires_approval(_tool(requires_approval=True)) is True

    def test_config_flagged_requires_approval(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset({"delete_file"}))
        assert policy.requires_approval(_tool(name="delete_file")) is True

    def test_both_sources_flag_tool(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset({"send_email"}))
        assert policy.requires_approval(
            _tool(name="send_email", requires_approval=True)
        )

    def test_neither_source_does_not_require_approval(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset({"other_tool"}))
        assert policy.requires_approval(_tool(name="echo")) is False

    def test_config_cannot_unflag_tool_authored_flag(self) -> None:
        policy = ApprovalPolicy(required_tool_names=frozenset())
        assert policy.requires_approval(_tool(requires_approval=True)) is True
