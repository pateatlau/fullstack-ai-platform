"""Dispatcher for ``NodeType.PLUGIN`` workflow nodes."""

from __future__ import annotations

import inspect

from app.ai.plugins.workflow.context import WorkflowPluginExecutorContext
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import (
    NodeExecutionRequest,
    NodeExecutor,
    WorkflowNodeExecutionError,
)
from app.core.config import Settings


class PluginNodeExecutor:
    """Routes plugin workflow nodes to registered plugin executors."""

    def __init__(
        self,
        *,
        workflow_plugin_registry: WorkflowPluginRegistry,
        settings: Settings,
    ) -> None:
        self._registry = workflow_plugin_registry
        self._settings = settings
        self._executor_cache: dict[tuple[str, str], NodeExecutor] = {}

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        plugin_id = node.config.get("plugin_id")
        plugin_node_type = node.config.get("plugin_node_type")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise WorkflowNodeExecutionError(
                f"Plugin node {node.id!r} requires config.plugin_id.",
                error_code="invalid_config",
            )
        if not isinstance(plugin_node_type, str) or not plugin_node_type.strip():
            raise WorkflowNodeExecutionError(
                f"Plugin node {node.id!r} requires config.plugin_node_type.",
                error_code="invalid_config",
            )

        factory = self._registry.get(plugin_id, plugin_node_type)
        if factory is None:
            raise WorkflowNodeExecutionError(
                f"Plugin node {node.id!r} references unknown executor "
                f"({plugin_id!r}, {plugin_node_type!r}).",
                error_code="not_found",
            )

        cache_key = (plugin_id, plugin_node_type)
        executor = self._executor_cache.get(cache_key)
        if executor is None:
            factory_context = WorkflowPluginExecutorContext(settings=self._settings)
            executor = _validate_plugin_executor(
                factory(factory_context),
                node_id=node.id,
            )
            self._executor_cache[cache_key] = executor

        result = await executor.execute(node, context, request)
        if not isinstance(result, dict):
            raise WorkflowNodeExecutionError(
                f"Plugin node {node.id!r} executor returned non-object output.",
                error_code="invalid_output",
            )
        return result


def _validate_plugin_executor(created: object, *, node_id: str) -> NodeExecutor:
    execute = getattr(created, "execute", None)
    if not callable(execute):
        raise WorkflowNodeExecutionError(
            f"Plugin node {node_id!r} executor factory did not return a NodeExecutor.",
            error_code="invalid_executor",
        )
    if not inspect.iscoroutinefunction(execute):
        raise WorkflowNodeExecutionError(
            f"Plugin node {node_id!r} executor factory returned an object whose "
            "execute method is not async.",
            error_code="invalid_executor",
        )
    return created  # type: ignore[return-value]
