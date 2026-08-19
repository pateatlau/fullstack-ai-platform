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
from app.ai.security.rbac.service import RbacService
from app.ai.security.rbac.models import Role, UserRoleAssignment
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


class _RoleStore:
    def __init__(self, user_roles: dict[uuid.UUID, set[str]]) -> None:
        self._user_roles = user_roles

    async def list_roles(self) -> list[Role]:
        return []

    async def get_role_by_name(self, name: str) -> Role | None:
        return None

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        return set()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        return sorted(self._user_roles.get(user_id, set()))

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        return False

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        return False

    async def bootstrap_admins(self, emails: list[str]) -> int:
        return 0

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        return []


@pytest.mark.anyio
async def test_rbac_enabled_policy_uses_resolved_operator_role() -> None:
    user_id = uuid.uuid4()
    policy = ApprovalPolicy(
        required_tool_names=frozenset(),
        rule_engine=RulePolicyEngine(
            [
                ApprovalRule(
                    name="reject-operators",
                    outcome=RuleOutcome.REJECT,
                    condition=RuleCondition(
                        field="caller_role",
                        operator=RuleOperator.EQ,
                        value="operator",
                    ),
                )
            ]
        ),
    )
    registry = _registry()
    runner = ToolRunner(
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        tool_registry=registry,
        hitl_enabled=True,
        approval_policy=policy,
        rbac_service=RbacService(_RoleStore({user_id: {"operator"}})),
        rbac_enforcement_enabled=True,
    )

    with pytest.raises(ToolCallRejectedByPolicyError) as exc_info:
        await runner.run_tool_steps(
            [
                PlannedStep(
                    step_id="s1",
                    action=StepAction.TOOL_CALL,
                    tool_calls=[
                        ToolCall(name="delete_file", arguments={"path": "/prod/x"})
                    ],
                )
            ],
            execution_id="exec-operator-policy",
            tool_context=ToolExecutionContext(caller=CallerContext.for_user(user_id)),
        )

    assert exc_info.value.matched_rule == "reject-operators"


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


@pytest.mark.anyio
async def test_caller_role_from_tool_context_is_passed_to_policy() -> None:
    registry = _registry()
    engine = RulePolicyEngine(
        [
            ApprovalRule(
                name="reject-guest-destructive",
                outcome=RuleOutcome.REJECT,
                condition=RuleCondition(
                    all_of=[
                        RuleCondition(
                            field="caller_role",
                            operator=RuleOperator.EQ,
                            value="guest",
                        ),
                        RuleCondition(
                            field="tool_category",
                            operator=RuleOperator.EQ,
                            value="destructive",
                        ),
                    ]
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
            execution_id="exec-guest-reject",
            tool_context=ToolExecutionContext(
                caller=CallerContext.anonymous(guest_id=uuid.uuid4())
            ),
        )

    assert exc_info.value.matched_rule == "reject-guest-destructive"
    assert _Handler.call_count == 0

    await runner.run_tool_steps(
        [step],
        execution_id="exec-user-ok",
        tool_context=_tool_context(),
    )
    assert _Handler.call_count == 1


@pytest.mark.anyio
async def test_trusted_policy_context_reaches_rule_engine() -> None:
    registry = _registry()
    engine = RulePolicyEngine(
        [
            ApprovalRule(
                name="reject-prod-high-cost",
                outcome=RuleOutcome.REJECT,
                condition=RuleCondition(
                    all_of=[
                        RuleCondition(
                            field="workspace",
                            operator=RuleOperator.EQ,
                            value="production",
                        ),
                        RuleCondition(
                            field="tenant",
                            operator=RuleOperator.EQ,
                            value="acme",
                        ),
                        RuleCondition(
                            field="estimated_cost",
                            operator=RuleOperator.GT,
                            value=100,
                        ),
                    ]
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
    step_with_untrusted_args = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(
                name="delete_file",
                # Untrusted LLM-supplied values must not satisfy policy rules.
                arguments={
                    "path": "/x",
                    "workspace": "staging",
                    "tenant": "other",
                    "estimated_cost": 1,
                },
            )
        ],
    )
    trusted_context = ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        workspace="production",
        tenant="acme",
        estimated_cost=250.0,
    )

    with pytest.raises(ToolCallRejectedByPolicyError) as exc_info:
        await runner.run_tool_steps(
            [step_with_untrusted_args],
            execution_id="exec-trusted-context",
            tool_context=trusted_context,
        )

    assert exc_info.value.matched_rule == "reject-prod-high-cost"
    assert _Handler.call_count == 0

    await runner.run_tool_steps(
        [
            PlannedStep(
                step_id="s2",
                action=StepAction.TOOL_CALL,
                tool_calls=[ToolCall(name="delete_file", arguments={"path": "/x"})],
            )
        ],
        execution_id="exec-trusted-context-ok",
        tool_context=ToolExecutionContext(
            caller=CallerContext.for_user(uuid.uuid4()),
            workspace="staging",
            tenant="acme",
            estimated_cost=250.0,
        ),
    )
    assert _Handler.call_count == 1
