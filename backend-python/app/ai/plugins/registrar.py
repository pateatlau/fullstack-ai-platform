"""PluginRegistrar — staged contribution facade (platform wiring in later phases)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.interfaces.tool_handler import ToolHandler
from app.ai.plugins.exceptions import PluginRegistrationError
from app.ai.plugins.models import PluginContributionKind
from app.ai.tools.schemas import ToolDefinition


@dataclass
class StagedTool:
    definition: ToolDefinition
    handler: ToolHandler


@dataclass
class StagedPrompt:
    name: str
    version: str
    source: str | None = None
    path: str | None = None


@dataclass
class StagedWorkflowNode:
    node_type: str
    executor_factory: Callable[..., Any]
    config_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class StagedMcpServer:
    config: dict[str, Any]


@dataclass
class CommittedContributions:
    tools: list[StagedTool] = field(default_factory=list)
    prompts: list[StagedPrompt] = field(default_factory=list)
    workflow_nodes: list[StagedWorkflowNode] = field(default_factory=list)
    mcp_servers: list[StagedMcpServer] = field(default_factory=list)


class PluginRegistrar:
    """Stages plugin contributions until ``commit()`` promotes them."""

    def __init__(self, *, plugin_id: str, plugin_dir: Path) -> None:
        self._plugin_id = plugin_id
        self._plugin_dir = plugin_dir
        self._lock = threading.Lock()
        self._closed = False
        self._committed = False
        self._staging = CommittedContributions()
        self._committed_contributions = CommittedContributions()

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def plugin_dir(self) -> Path:
        return self._plugin_dir

    @property
    def committed(self) -> CommittedContributions:
        return self._committed_contributions

    @property
    def is_closed(self) -> bool:
        return self._closed

    def register_tool(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
    ) -> None:
        with self._lock:
            self._ensure_open_unlocked()
            self._staging.tools.append(
                StagedTool(definition=definition, handler=handler)
            )

    def register_prompt_template(
        self,
        *,
        name: str,
        version: str,
        source: str | None = None,
        path: str | None = None,
    ) -> None:
        with self._lock:
            self._ensure_open_unlocked()
            if source is None and path is None:
                raise PluginRegistrationError(
                    "register_prompt_template requires source or path."
                )
            if source is not None and path is not None:
                raise PluginRegistrationError(
                    "register_prompt_template accepts source or path, not both."
                )
            self._staging.prompts.append(
                StagedPrompt(name=name, version=version, source=source, path=path)
            )

    def register_workflow_node_type(
        self,
        *,
        node_type: str,
        executor_factory: Callable[..., Any],
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._ensure_open_unlocked()
            self._staging.workflow_nodes.append(
                StagedWorkflowNode(
                    node_type=node_type,
                    executor_factory=executor_factory,
                    config_schema=config_schema or {},
                )
            )

    def register_mcp_server(self, config: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_open_unlocked()
            self._staging.mcp_servers.append(StagedMcpServer(config=dict(config)))

    def commit(self) -> None:
        with self._lock:
            self._ensure_open_unlocked()
            self._committed_contributions = CommittedContributions(
                tools=list(self._staging.tools),
                prompts=list(self._staging.prompts),
                workflow_nodes=list(self._staging.workflow_nodes),
                mcp_servers=list(self._staging.mcp_servers),
            )
            self._staging = CommittedContributions()
            self._committed = True

    def rollback(self) -> None:
        with self._lock:
            self._staging = CommittedContributions()

    def close(self) -> None:
        """Reject further staging (e.g. after registration wait timeout)."""
        with self._lock:
            self._closed = True
            self._staging = CommittedContributions()

    def contribution_kinds(self) -> list[PluginContributionKind]:
        """Contribution kinds present after ``commit()``."""
        committed = self._committed_contributions
        kinds: list[PluginContributionKind] = []
        if committed.tools:
            kinds.append(PluginContributionKind.TOOL)
        if committed.prompts:
            kinds.append(PluginContributionKind.PROMPT)
        if committed.workflow_nodes:
            kinds.append(PluginContributionKind.WORKFLOW_NODE)
        if committed.mcp_servers:
            kinds.append(PluginContributionKind.MCP_SERVER)
        return kinds

    def _ensure_open_unlocked(self) -> None:
        if self._closed:
            raise PluginRegistrationError(
                "Plugin registration is closed; contributions cannot be staged."
            )
        if self._committed:
            raise PluginRegistrationError(
                "Plugin registration already committed; cannot stage more contributions."
            )
