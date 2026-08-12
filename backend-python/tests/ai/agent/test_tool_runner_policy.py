"""ToolRunner integration with the rule-based policy engine (recommendation #1/#5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.ai.agent.executor import ToolRunner
from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.exceptions import (
    AgentApprovalPauseError,
    ToolCallRejectedByPolicyError,
)
from app.ai.hitl.models import ApprovalStatus
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.rules import (
    ApprovalRule,
    RuleCondition,
    RuleOperator,
    RuleOutcome,
    RulePolicyEngine,
)
from app.ai.hitl.service import AgentApprovalService
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class _Handler:
    call_count: int = 0

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        _Handler.call_count += 1
        return ToolResult(success=True, data={})


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    _Handler.call_count = 0
    yield


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
            category="destructive",
            risk_level="high",
        ),
        _Handler(),
    )
    return registry


def _tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(caller=CallerContext.for_user(uuid.uuid4()))


@pytest.mark.anyio
async def test_reject_outcome_raises_and_never_dispatches() -> None:
    registry = _registry()
    engine = RulePolicyEngine(
        [
            ApprovalRule(
                name="reject-destructive",
                outcome=RuleOutcome.REJECT,
                condition=RuleCondition(
                    field="tool_category", operator=RuleOperator.EQ, value="destructive"
                ),
            )
        ]
    )
    policy = ApprovalPolicy(required_tool_names=frozenset(), rule_engine=engine)
    chat_store = FakeChatStore()
    runner = ToolRunner(
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        tool_registry=registry,
        hitl_enabled=True,
        approval_policy=policy,
        approval_service=AgentApprovalService(
            approval_store=InMemoryApprovalStore(), chat_store=chat_store
        ),
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/prod/x"})],
    )

    with pytest.raises(ToolCallRejectedByPolicyError) as exc_info:
        await runner.run_tool_steps(
            [step],
            execution_id="exec-reject",
            tool_context=_tool_context(),
        )

    assert exc_info.value.tool_name == "delete_file"
    assert exc_info.value.matched_rule == "reject-destructive"
    assert _Handler.call_count == 0


@pytest.mark.anyio
async def test_required_stages_from_matched_rule_are_persisted_on_pause() -> None:
    registry = _registry()
    engine = RulePolicyEngine(
        [
            ApprovalRule(
                name="multi-stage-destructive",
                outcome=RuleOutcome.REQUIRE_APPROVAL,
                required_stages=["manager", "security"],
                condition=RuleCondition(
                    field="tool_category", operator=RuleOperator.EQ, value="destructive"
                ),
            )
        ]
    )
    policy = ApprovalPolicy(required_tool_names=frozenset(), rule_engine=engine)
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    owner_id = uuid.uuid4()
    session = await chat_store.create_session(user_id=owner_id)
    runner = ToolRunner(
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        tool_registry=registry,
        stream_publisher=InMemoryStreamPublisher(),
        hitl_enabled=True,
        approval_policy=policy,
        approval_service=AgentApprovalService(
            approval_store=store, chat_store=chat_store
        ),
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-stages")
    state = AgentExecutionState(
        execution_id="exec-stages",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/prod/x"})],
    )

    with pytest.raises(AgentApprovalPauseError) as exc_info:
        await runner.run_tool_steps(
            [step],
            execution_id="exec-stages",
            tool_context=_tool_context(),
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=owner_id,
        )

    approval = exc_info.value.approval
    assert approval.required_stages == ["manager", "security"]
    assert approval.stage_decisions == []
    assert approval.status == ApprovalStatus.PENDING
    assert _Handler.call_count == 0
