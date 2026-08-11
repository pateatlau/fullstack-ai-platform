"""Workflow node plugin integration tests (Epic 08 Phase 4)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.plugins import PluginContributionKind, PluginStatus
from app.ai.plugins.workflow.plugin_node import PluginNodeExecutor
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.core.config import Settings
from tests.ai.plugins.conftest import load_plugins, plugin_settings
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)
PLUGIN_ID = "com.test.workflow"
NODE_TYPE = "echo"


def _plugin_node(
    node_id: str = "echo_step",
    *,
    plugin_id: str = PLUGIN_ID,
    plugin_node_type: str = NODE_TYPE,
    message_key: str | object = "input_text",
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        type=NodeType.PLUGIN,
        config={
            "plugin_id": plugin_id,
            "plugin_node_type": plugin_node_type,
            "message_key": message_key,
        },
    )


def _definition(
    *,
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
    owner_id: uuid.UUID | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id or uuid.uuid4(),
        name="Plugin Workflow",
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
        idempotency_key="plugin-workflow-key",
        status=RunStatus.RUNNING,
        context=WorkflowContext(trigger_input={"input_text": "hello-plugin"}),
        current_node_ids=[],
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


@pytest.fixture
def workflow_plugin_registry() -> WorkflowPluginRegistry:
    return WorkflowPluginRegistry()


class TestWorkflowPluginLoad:
    def test_workflow_node_registered(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        report, registry, _tools, _prompts = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )

        assert report.loaded_count == 1
        record = registry.get(PLUGIN_ID)
        assert record is not None
        assert record.status is PluginStatus.LOADED
        assert PluginContributionKind.WORKFLOW_NODE in record.contributions
        assert workflow_plugin_registry.has(PLUGIN_ID, NODE_TYPE)
        schema = workflow_plugin_registry.get_config_schema(PLUGIN_ID, NODE_TYPE)
        assert schema is not None
        assert schema["required"] == ["plugin_id", "plugin_node_type"]


class TestGraphValidatorPluginNodes:
    def test_flag_off_rejects_plugin_node(self) -> None:
        definition = _definition(
            nodes=[
                _plugin_node(),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(plugins_enabled=False)

        with pytest.raises(WorkflowValidationError, match="PLUGINS_ENABLED is false"):
            validator.validate(definition)

    def test_flag_off_rejects_malformed_plugin_node_before_config_validation(
        self,
    ) -> None:
        definition = _definition(
            nodes=[
                WorkflowNode(id="echo_step", type=NodeType.PLUGIN, config={}),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(plugins_enabled=False)

        with pytest.raises(WorkflowValidationError, match="PLUGINS_ENABLED is false"):
            validator.validate(definition)

    def test_unknown_plugin_id_rejected(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        _, plugin_registry, _, _ = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        definition = _definition(
            nodes=[
                _plugin_node(plugin_id="com.missing.plugin"),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(
            plugins_enabled=True,
            plugin_registry=plugin_registry,
            workflow_plugin_registry=workflow_plugin_registry,
        )

        with pytest.raises(WorkflowValidationError, match="unknown or unloaded plugin"):
            validator.validate(definition)

    def test_unknown_node_type_rejected(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        _, plugin_registry, _, _ = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        definition = _definition(
            nodes=[
                _plugin_node(plugin_node_type="missing"),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(
            plugins_enabled=True,
            plugin_registry=plugin_registry,
            workflow_plugin_registry=workflow_plugin_registry,
        )

        with pytest.raises(WorkflowValidationError, match="unknown node type"):
            validator.validate(definition)

    def test_valid_plugin_node_passes(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        _, plugin_registry, _, _ = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        definition = _definition(
            nodes=[
                _plugin_node(),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(
            plugins_enabled=True,
            plugin_registry=plugin_registry,
            workflow_plugin_registry=workflow_plugin_registry,
        )

        validator.validate(definition)

    def test_invalid_plugin_config_schema_rejected(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        _, plugin_registry, _, _ = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        definition = _definition(
            nodes=[
                _plugin_node(message_key=123),  # type: ignore[arg-type]
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )
        validator = GraphValidator(
            plugins_enabled=True,
            plugin_registry=plugin_registry,
            workflow_plugin_registry=workflow_plugin_registry,
        )

        with pytest.raises(WorkflowValidationError, match="message_key"):
            validator.validate(definition)


class TestWorkflowPluginExecution:
    @pytest.mark.anyio
    async def test_single_plugin_node_run_completes(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        owner_id = uuid.uuid4()
        store = FakeWorkflowStore()
        definition = _definition(
            nodes=[
                _plugin_node(),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
            owner_id=owner_id,
        )
        await store.create_definition(definition)
        run = _run(owner_id=owner_id, definition_id=definition.id)
        run = await store.create_run(run)

        settings = Settings(plugins_enabled=True)
        executor = WorkflowExecutor(
            store,
            {
                NodeType.PLUGIN: PluginNodeExecutor(
                    workflow_plugin_registry=workflow_plugin_registry,
                    settings=settings,
                )
            },
        )

        result = await executor.execute_run(run.id, owner_id=owner_id)

        assert result.status is RunStatus.COMPLETED
        assert result.context.variables["echo_step"] == {"value": "hello-plugin"}
        assert result.current_node_ids == []


class TestWorkflowManagerPluginValidation:
    @pytest.mark.anyio
    async def test_create_definition_rejects_plugin_when_flag_off(self) -> None:
        manager = WorkflowManager(
            FakeWorkflowStore(),
            settings=Settings(plugins_enabled=False),
        )
        definition = _definition(
            nodes=[
                _plugin_node(),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )

        with pytest.raises(WorkflowValidationError, match="PLUGINS_ENABLED is false"):
            await manager.create_definition(definition)

    @pytest.mark.anyio
    async def test_create_definition_accepts_loaded_plugin_node(
        self,
        workflow_plugin_registry: WorkflowPluginRegistry,
    ) -> None:
        _, plugin_registry, _, _ = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            workflow_plugin_registry=workflow_plugin_registry,
        )
        store = FakeWorkflowStore()
        settings = Settings(plugins_enabled=True)
        manager = WorkflowManager(
            store,
            settings=settings,
            node_executors={
                NodeType.PLUGIN: PluginNodeExecutor(
                    workflow_plugin_registry=workflow_plugin_registry,
                    settings=settings,
                )
            },
            plugin_registry=plugin_registry,
            workflow_plugin_registry=workflow_plugin_registry,
        )
        definition = _definition(
            nodes=[
                _plugin_node(),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="echo_step", to_node_id="end")],
        )

        created = await manager.create_definition(definition)

        assert created == definition


class TestPluginNodeExecutorValidation:
    @pytest.mark.anyio
    async def test_sync_execute_method_rejected(self) -> None:
        registry = WorkflowPluginRegistry()

        class SyncExecutor:
            def execute(
                self,
                node: WorkflowNode,
                context: WorkflowContext,
                request: NodeExecutionRequest,
            ) -> dict[str, object]:
                del node, context, request
                return {}

        registry.register(
            plugin_id="com.test.bad",
            node_type="sync",
            executor_factory=lambda _ctx: SyncExecutor(),
        )
        dispatcher = PluginNodeExecutor(
            workflow_plugin_registry=registry,
            settings=Settings(plugins_enabled=True),
        )
        node = WorkflowNode(
            id="bad",
            type=NodeType.PLUGIN,
            config={
                "plugin_id": "com.test.bad",
                "plugin_node_type": "sync",
            },
        )

        with pytest.raises(WorkflowNodeExecutionError, match="not async") as exc:
            await dispatcher.execute(
                node,
                WorkflowContext(),
                NodeExecutionRequest(
                    owner_id=uuid.uuid4(),
                    execution_receipt_id="receipt-1",
                ),
            )

        assert exc.value.error_code == "invalid_executor"

    @pytest.mark.anyio
    async def test_missing_execute_rejected(self) -> None:
        registry = WorkflowPluginRegistry()
        registry.register(
            plugin_id="com.test.bad",
            node_type="missing",
            executor_factory=lambda _ctx: object(),
        )
        dispatcher = PluginNodeExecutor(
            workflow_plugin_registry=registry,
            settings=Settings(plugins_enabled=True),
        )
        node = WorkflowNode(
            id="bad",
            type=NodeType.PLUGIN,
            config={
                "plugin_id": "com.test.bad",
                "plugin_node_type": "missing",
            },
        )

        with pytest.raises(WorkflowNodeExecutionError, match="NodeExecutor") as exc:
            await dispatcher.execute(
                node,
                WorkflowContext(),
                NodeExecutionRequest(
                    owner_id=uuid.uuid4(),
                    execution_receipt_id="receipt-1",
                ),
            )

        assert exc.value.error_code == "invalid_executor"
