"""Integration tests for git-tracked reference plugins (Epic 08 Phase 8)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.ai.plugins import PluginContributionKind, PluginStatus
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.prompts.manager import PromptManager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.plugins.conftest import load_plugins

REFERENCE_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"
ECHO_TOOL_PLUGIN_ID = "com.example.echo"
ECHO_WORKFLOW_PLUGIN_ID = "com.example.echo.workflow"
ECHO_TOOL_NAME = f"{ECHO_TOOL_PLUGIN_ID}.ping"


def _reference_settings(**overrides: object) -> Settings:
    base = {
        "plugins_enabled": True,
        "plugin_directories": [str(REFERENCE_PLUGINS_ROOT)],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestReferencePluginsLoad:
    def test_both_reference_plugins_load(self) -> None:
        report, registry, tools, prompts = load_plugins(_reference_settings())

        assert report.loaded_count == 2
        assert report.failed_count == 0

        echo_tool = registry.get(ECHO_TOOL_PLUGIN_ID)
        assert echo_tool is not None
        assert echo_tool.status == PluginStatus.LOADED
        assert PluginContributionKind.TOOL in echo_tool.contributions
        assert PluginContributionKind.PROMPT in echo_tool.contributions

        echo_workflow = registry.get(ECHO_WORKFLOW_PLUGIN_ID)
        assert echo_workflow is not None
        assert echo_workflow.status == PluginStatus.LOADED
        assert PluginContributionKind.WORKFLOW_NODE in echo_workflow.contributions

        assert tools.get(ECHO_TOOL_NAME) is not None
        assert (
            prompts.get_template("plugin/com.example.echo", "greeting", "1") is not None
        )
        assert (
            prompts.get_template("plugin/com.example.echo", "farewell", "1") is not None
        )


class TestReferencePluginTool:
    @pytest.mark.anyio
    async def test_echo_ping_tool_executes(self) -> None:
        _report, _registry, tools, _prompts = load_plugins(_reference_settings())
        executor = ToolExecutor(registry=tools, settings=_reference_settings())
        result = await executor.execute(
            ToolCall(name=ECHO_TOOL_NAME, arguments={"message": "hello-reference"}),
            ToolExecutionContext(caller=CallerContext.for_user(uuid.uuid4())),
        )

        assert result.success is True
        assert result.data == {"message": "hello-reference"}


class TestReferencePluginPrompt:
    def test_echo_greeting_template_renders(self) -> None:
        _report, _registry, _tools, prompts = load_plugins(_reference_settings())
        manager = PromptManager(repository=prompts)
        rendered = manager.render(
            "plugin/com.example.echo",
            "greeting",
            "1",
            {"user_name": "Reference"},
        )
        assert rendered == "Hello Reference!"


class TestReferencePluginWorkflowNode:
    def test_echo_workflow_node_registered(
        self,
    ) -> None:
        workflow_registry = WorkflowPluginRegistry()
        load_plugins(
            _reference_settings(),
            workflow_plugin_registry=workflow_registry,
        )

        assert workflow_registry.has(ECHO_WORKFLOW_PLUGIN_ID, "echo")
