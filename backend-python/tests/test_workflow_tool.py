"""WorkflowExecutionTool integration tests (Epic 06 Phase 10)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator

import pytest

from app.ai.agent import StepAction
from app.ai.agent.executor import ToolRunner
from app.ai.agent.models.plan import PlannedStep
from app.ai.deps import get_tool_registry
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.workflow_tool import (
    WORKFLOW_EXECUTION_TOOL_DEFINITION,
    WORKFLOW_EXECUTION_TOOL_NAME,
    WorkflowExecutionToolHandler,
    create_workflow_execution_handler,
)
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.registration import register_production_tools
from app.ai.tools.schemas import ToolCall, ToolExecutionContext, ToolResult
from app.ai.tools.implementations.web_search import WebSearchResult
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


def _run_data(result: ToolResult) -> dict[str, object]:
    assert result.success is True
    assert isinstance(result.data, dict)
    return result.data


def _start_args(
    definition_id: uuid.UUID,
    *,
    idempotency_key: str,
) -> dict[str, object]:
    return {
        "action": "start",
        "definition_id": str(definition_id),
        "idempotency_key": idempotency_key,
    }


@pytest.fixture(autouse=True)
def _clear_tool_registry_cache() -> Iterator[None]:
    get_tool_registry.cache_clear()
    yield
    get_tool_registry.cache_clear()


def _settings(*, workflow_engine_enabled: bool = True) -> Settings:
    return Settings(
        openai_api_key="test-key",
        web_search_api_key="test-search-key",
        workflow_engine_enabled=workflow_engine_enabled,
    )


def _active_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Tool Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def owner_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def workflow_setup(
    owner_id: uuid.UUID,
) -> tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID]:
    store = FakeWorkflowStore()
    manager = WorkflowManager(store, settings=_settings())
    return store, manager, owner_id


async def _seed_definition(
    setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID]:
    store, manager, owner_id = setup
    definition = await store.create_definition(_active_definition(owner_id))
    return store, manager, definition.id


@pytest.fixture
def manager_factory(
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> WorkflowManager:
    _, manager, _ = workflow_setup
    return manager


@pytest.fixture
def workflow_handler(manager_factory: WorkflowManager) -> WorkflowExecutionToolHandler:
    async def _factory() -> WorkflowManager:
        return manager_factory

    return create_workflow_execution_handler(
        _settings(),
        manager_factory=_factory,
    )


@pytest.fixture
def user_context(owner_id: uuid.UUID) -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(owner_id),
        request_id="req-workflow-tool",
    )


@pytest.fixture
def guest_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.anonymous(guest_id=uuid.uuid4()),
        request_id="req-guest-workflow",
    )


@pytest.fixture
def executor(workflow_handler: WorkflowExecutionToolHandler) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(WORKFLOW_EXECUTION_TOOL_DEFINITION, workflow_handler)
    return ToolExecutor(registry=registry, settings=_settings())


@pytest.mark.anyio
async def test_tool_start_creates_run(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> None:
    _, _, definition_id = await _seed_definition(workflow_setup)

    result = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={
                "action": "start",
                "definition_id": str(definition_id),
                "idempotency_key": "tool-key-1",
                "input": {"topic": "demo"},
            },
        ),
        user_context,
    )

    assert result.success is True
    data = _run_data(result)
    assert data["workflow_definition_id"] == str(definition_id)
    assert data["status"] == RunStatus.RUNNING.value
    context = data["context"]
    assert isinstance(context, dict)
    trigger_input = context["trigger_input"]
    assert trigger_input == {"topic": "demo"}


@pytest.mark.anyio
async def test_tool_start_is_idempotent(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> None:
    _, manager, definition_id = await _seed_definition(workflow_setup)
    args = _start_args(definition_id, idempotency_key="tool-key-dedupe")

    first = await executor.execute(
        ToolCall(name=WORKFLOW_EXECUTION_TOOL_NAME, arguments=args),
        user_context,
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task
    second = await executor.execute(
        ToolCall(name=WORKFLOW_EXECUTION_TOOL_NAME, arguments=args),
        user_context,
    )

    assert first.success is True
    assert second.success is True
    assert _run_data(first)["id"] == _run_data(second)["id"]


@pytest.mark.anyio
async def test_tool_start_sets_session_id_from_context(
    workflow_handler: WorkflowExecutionToolHandler,
    user_context: ToolExecutionContext,
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> None:
    _, _, definition_id = await _seed_definition(workflow_setup)
    session_id = uuid.uuid4()
    context = user_context.model_copy(update={"session_id": session_id})

    result = await workflow_handler.execute(
        {
            "action": "start",
            "definition_id": str(definition_id),
            "idempotency_key": "session-key",
        },
        context,
    )

    assert result.success is True
    assert _run_data(result)["session_id"] == str(session_id)


@pytest.mark.anyio
async def test_tool_status_returns_run_detail(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
    owner_id: uuid.UUID,
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
) -> None:
    _, manager, definition_id = await _seed_definition(workflow_setup)
    run = await manager.start_run(
        definition_id,
        owner_id=owner_id,
        idempotency_key="status-key",
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task

    result = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={"action": "status", "run_id": str(run.id)},
        ),
        user_context,
    )

    assert result.success is True
    data = _run_data(result)
    assert data["id"] == str(run.id)
    assert "node_executions" in data


@pytest.mark.anyio
async def test_tool_status_not_found(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={"action": "status", "run_id": str(uuid.uuid4())},
        ),
        user_context,
    )

    assert result.success is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.anyio
async def test_tool_start_validation_errors(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    missing_key = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={
                "action": "start",
                "definition_id": str(uuid.uuid4()),
            },
        ),
        user_context,
    )
    assert missing_key.success is False
    assert missing_key.error_code == "validation_error"

    bad_uuid = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={
                "action": "start",
                "definition_id": "not-a-uuid",
                "idempotency_key": "key",
            },
        ),
        user_context,
    )
    assert bad_uuid.success is False
    assert bad_uuid.error_code == "validation_error"


@pytest.mark.anyio
async def test_tool_handler_never_raises(
    user_context: ToolExecutionContext,
) -> None:
    class ExplodingManager(WorkflowManager):
        async def start_run(self, *args, **kwargs):  # type: ignore[no-untyped-def, override]
            raise RuntimeError("boom")

    exploding = ExplodingManager(FakeWorkflowStore(), settings=_settings())

    async def _factory() -> WorkflowManager:
        return exploding

    handler = create_workflow_execution_handler(
        _settings(),
        manager_factory=_factory,
    )

    result = await handler.execute(
        {
            "action": "start",
            "definition_id": str(uuid.uuid4()),
            "idempotency_key": "boom",
        },
        user_context,
    )

    assert result.success is False
    assert result.error_code == "handler_error"


@pytest.mark.anyio
async def test_guest_denied_via_tool_authorizer(
    executor: ToolExecutor,
    guest_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(
            name=WORKFLOW_EXECUTION_TOOL_NAME,
            arguments={
                "action": "start",
                "definition_id": str(uuid.uuid4()),
                "idempotency_key": "guest-key",
            },
        ),
        guest_context,
    )

    assert result.success is False
    assert result.error_code == "forbidden"


def test_registration_gated_by_workflow_flag() -> None:
    class FakeSearchClient:
        async def search(
            self, query: str, *, max_results: int
        ) -> list[WebSearchResult]:
            del query, max_results
            return []

    registry = ToolRegistry()
    register_production_tools(
        registry,
        _settings(workflow_engine_enabled=False),
        web_search_client=FakeSearchClient(),
    )
    assert registry.get(WORKFLOW_EXECUTION_TOOL_NAME) is None

    registry = ToolRegistry()
    register_production_tools(
        registry,
        _settings(workflow_engine_enabled=True),
        web_search_client=FakeSearchClient(),
    )
    assert (
        registry.get(WORKFLOW_EXECUTION_TOOL_NAME) == WORKFLOW_EXECUTION_TOOL_DEFINITION
    )


def test_tool_schema_is_llm_callable() -> None:
    schema = WORKFLOW_EXECUTION_TOOL_DEFINITION.parameters
    assert schema["type"] == "object"
    assert "action" in schema["properties"]
    assert schema["required"] == ["action"]
    assert schema["additionalProperties"] is False


@pytest.mark.anyio
async def test_agent_tool_runner_invokes_workflow_tool(
    workflow_setup: tuple[FakeWorkflowStore, WorkflowManager, uuid.UUID],
    owner_id: uuid.UUID,
) -> None:
    _, manager, definition_id = await _seed_definition(workflow_setup)

    async def _factory() -> WorkflowManager:
        return manager

    registry = ToolRegistry()
    registry.register(
        WORKFLOW_EXECUTION_TOOL_DEFINITION,
        create_workflow_execution_handler(_settings(), manager_factory=_factory),
    )
    tool_executor = ToolExecutor(registry=registry, settings=_settings())
    tool_runner = ToolRunner(tool_executor=tool_executor)
    tool_context = ToolExecutionContext(
        caller=CallerContext.for_user(owner_id),
        request_id="req-agent-workflow",
    )

    aggregated = await tool_runner.run_tool_steps(
        [
            PlannedStep(
                step_id="workflow-start",
                action=StepAction.TOOL_CALL,
                tool_calls=[
                    ToolCall(
                        name=WORKFLOW_EXECUTION_TOOL_NAME,
                        arguments={
                            "action": "start",
                            "definition_id": str(definition_id),
                            "idempotency_key": "agent-key",
                        },
                        call_id="call-workflow-1",
                    )
                ],
            )
        ],
        execution_id="exec-workflow-tool",
        tool_context=tool_context,
    )

    assert aggregated.all_succeeded
    assert aggregated.records[0].result.success is True
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task
    runs = await manager.list_runs(owner_id=owner_id)
    assert len(runs) == 1
    assert runs[0].idempotency_key == "agent-key"
