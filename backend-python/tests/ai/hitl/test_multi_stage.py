"""Multi-stage approval checklist tests (Epic 09 recommendation #5)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent.executor import AgentExecutor, ToolRunner
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher, NoOpStreamPublisher
from app.ai.hitl.exceptions import ApprovalValidationError, HitlError
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore, FakeProvider


class _Handler:
    call_count: int = 0

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        _Handler.call_count += 1
        return ToolResult(success=True, data={})


@pytest.fixture(autouse=True)
def _reset() -> None:
    _Handler.call_count = 0


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
            requires_approval=True,
        ),
        _Handler(),
    )
    return registry


def _service(
    store: InMemoryApprovalStore, chat_store: FakeChatStore
) -> AgentApprovalService:
    registry = _registry()
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        scratchpad_store=ScratchpadStore(),
    )


async def _seed_pending(
    *,
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    owner_id: uuid.UUID,
    required_stages: list[str],
) -> tuple[uuid.UUID, uuid.UUID]:
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-stage",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "delete"}],
        paused_state={
            "execution_id": "exec-stage",
            "status": "waiting_approval",
        },
        required_stages=required_stages,
    )
    placeholder = await chat_store.add_message(
        session_id=session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    return approval.id, session.id


def _resume_executor(
    *, registry: ToolRegistry, scratchpad_store: ScratchpadStore
) -> AgentExecutor:
    from app.ai.prompts.manager import create_prompt_manager

    provider = FakeProvider(response="Done.")
    runner = ToolRunner(
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=False,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )


class TestRecordStageApproval:
    @pytest.mark.anyio
    async def test_intermediate_stage_stays_pending(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security", "compliance"],
        )

        result = await service.record_stage_approval(
            approval_id, decider_id=owner_id, reason="looks fine"
        )

        assert result.status == ApprovalStatus.PENDING
        assert result.outstanding_stages == ["security", "compliance"]
        approval = await store.get(approval_id)
        assert approval is not None
        assert len(approval.stage_decisions) == 1
        assert approval.stage_decisions[0].stage == "manager"
        assert approval.stage_decisions[0].decision == "approved"
        assert _Handler.call_count == 0

    @pytest.mark.anyio
    async def test_intermediate_stage_persists_comments(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security"],
        )

        result = await service.record_stage_approval(
            approval_id,
            decider_id=owner_id,
            reason="manager ok",
            comments="please review carefully",
        )

        assert result.status == ApprovalStatus.PENDING
        assert result.comments == "please review carefully"
        approval = await store.get(approval_id)
        assert approval is not None
        assert len(approval.stage_decisions) == 1
        assert approval.stage_decisions[0].comments == "please review carefully"

    @pytest.mark.anyio
    async def test_second_of_three_stages_leaves_one_outstanding(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security", "compliance"],
        )
        await service.record_stage_approval(approval_id, decider_id=owner_id)

        result = await service.record_stage_approval(approval_id, decider_id=owner_id)

        assert result.status == ApprovalStatus.PENDING
        assert result.outstanding_stages == ["compliance"]

    @pytest.mark.anyio
    async def test_rejects_call_on_final_stage(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager"],
        )

        with pytest.raises(ApprovalValidationError):
            await service.record_stage_approval(approval_id, decider_id=owner_id)

    @pytest.mark.anyio
    async def test_rejects_call_when_no_stages_configured(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store, chat_store=chat_store, owner_id=owner_id, required_stages=[]
        )

        with pytest.raises(ApprovalValidationError):
            await service.record_stage_approval(approval_id, decider_id=owner_id)


class TestFinalStageDecide:
    @pytest.mark.anyio
    async def test_rejecting_at_any_stage_terminates_whole_approval(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security"],
        )
        await service.record_stage_approval(approval_id, decider_id=owner_id)

        result = await service.decide(
            approval_id, decider_id=owner_id, decision="rejected"
        )

        assert result.status == ApprovalStatus.REJECTED
        approval = await store.get(approval_id)
        assert approval is not None
        assert len(approval.stage_decisions) == 2
        assert approval.stage_decisions[-1].decision == "rejected"
        assert _Handler.call_count == 0

    @pytest.mark.anyio
    async def test_final_stage_approval_executes_via_approve_and_resume(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        scratchpad_store = ScratchpadStore()
        approval_id, session_id = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security"],
        )
        await service.record_stage_approval(approval_id, decider_id=owner_id)
        registry = _registry()
        caller = CallerContext.for_user(owner_id)

        _, response = await service.approve_and_resume(
            approval_id,
            decider_id=owner_id,
            executor=_resume_executor(
                registry=registry, scratchpad_store=scratchpad_store
            ),
            request=AgentRequest(
                messages=[AgentMessage(role="user", content="delete")],
                model="gpt-4o-mini",
                config=AgentConfig(max_iterations=2),
            ),
            context=AgentContext(
                execution_id="exec-stage",
                caller=caller,
                session_id=session_id,
            ),
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
        )

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED
        assert len(approval.stage_decisions) == 2
        assert [entry.stage for entry in approval.stage_decisions] == [
            "manager",
            "security",
        ]
        assert response.finish_reason == "stop"
        assert _Handler.call_count == 1

    @pytest.mark.anyio
    async def test_single_stage_approval_records_and_executes_in_one_call(self) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        scratchpad_store = ScratchpadStore()
        approval_id, session_id = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager"],
        )
        registry = _registry()
        caller = CallerContext.for_user(owner_id)

        await service.approve_and_resume(
            approval_id,
            decider_id=owner_id,
            executor=_resume_executor(
                registry=registry, scratchpad_store=scratchpad_store
            ),
            request=AgentRequest(
                messages=[AgentMessage(role="user", content="delete")],
                model="gpt-4o-mini",
                config=AgentConfig(max_iterations=2),
            ),
            context=AgentContext(
                execution_id="exec-stage",
                caller=caller,
                session_id=session_id,
            ),
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
        )

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.APPROVED
        assert len(approval.stage_decisions) == 1
        assert _Handler.call_count == 1


class TestIntermediateStageGuard:
    @pytest.mark.anyio
    async def test_decide_approved_rejects_when_intermediate_stages_remain(
        self,
    ) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security", "compliance"],
        )

        with pytest.raises(ApprovalValidationError):
            await service.decide(approval_id, decider_id=owner_id, decision="approved")

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.stage_decisions == []
        assert _Handler.call_count == 0

    @pytest.mark.anyio
    async def test_approve_and_resume_rejects_when_intermediate_stages_remain(
        self,
    ) -> None:
        owner_id = uuid.uuid4()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        scratchpad_store = ScratchpadStore()
        approval_id, session_id = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager", "security"],
        )
        registry = _registry()
        caller = CallerContext.for_user(owner_id)

        with pytest.raises(ApprovalValidationError):
            await service.approve_and_resume(
                approval_id,
                decider_id=owner_id,
                executor=_resume_executor(
                    registry=registry, scratchpad_store=scratchpad_store
                ),
                request=AgentRequest(
                    messages=[AgentMessage(role="user", content="delete")],
                    model="gpt-4o-mini",
                    config=AgentConfig(max_iterations=2),
                ),
                context=AgentContext(
                    execution_id="exec-stage",
                    caller=caller,
                    session_id=session_id,
                ),
                tool_context=ToolExecutionContext(caller=caller),
                stream_publisher=InMemoryStreamPublisher(),
            )

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.stage_decisions == []
        assert _Handler.call_count == 0


class _FailCasDecideStore(InMemoryApprovalStore):
    """Fails cas_decide once to exercise stage-append rollback."""

    def __init__(self) -> None:
        super().__init__()
        self._fail_cas = True

    async def cas_decide(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self._fail_cas:
            self._fail_cas = False
            raise HitlError("simulated CAS failure")
        return await super().cas_decide(*args, **kwargs)


class TestStageAppendRollback:
    @pytest.mark.anyio
    async def test_cas_failure_rolls_back_appended_stage_decision(self) -> None:
        owner_id = uuid.uuid4()
        store = _FailCasDecideStore()
        chat_store = FakeChatStore()
        service = _service(store, chat_store)
        approval_id, _ = await _seed_pending(
            store=store,
            chat_store=chat_store,
            owner_id=owner_id,
            required_stages=["manager"],
        )

        with pytest.raises(HitlError, match="simulated CAS failure"):
            await service.decide(approval_id, decider_id=owner_id, decision="rejected")

        approval = await store.get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.stage_decisions == []
