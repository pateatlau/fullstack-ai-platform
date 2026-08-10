"""Plugin inventory and load-report models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PluginStatus(str, Enum):
    """V2 plugin lifecycle states (loaded or failed only)."""

    LOADED = "loaded"
    FAILED = "failed"


class PluginContributionKind(str, Enum):
    """Contribution kinds a plugin may register."""

    TOOL = "tool"
    PROMPT = "prompt"
    WORKFLOW_NODE = "workflow_node"
    MCP_SERVER = "mcp_server"


class PluginDependency(BaseModel):
    """Reserved manifest dependency entry (informational in V2)."""

    plugin_id: str
    version: str | None = None


class PluginLoadFailureReason(BaseModel):
    """Structured, JSON-serializable plugin load failure."""

    code: str
    message: str
    expected_api_versions: list[str] | None = None
    manifest_api_version: str | None = None


class PluginRecord(BaseModel):
    """Startup snapshot for one discovery candidate."""

    plugin_id: str | None = None
    name: str | None = None
    version: str | None = None
    api_version: str | None = None
    status: PluginStatus
    contributions: list[PluginContributionKind] = Field(default_factory=list)
    load_duration_ms: float = 0.0
    author: str | None = None
    homepage: str | None = None
    repository: str | None = None
    documentation: str | None = None
    license: str | None = None
    dependencies: list[PluginDependency] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    failure: PluginLoadFailureReason | None = None


class PluginLoadReport(BaseModel):
    """Summary returned by ``PluginLoader.load_all()``."""

    loaded_count: int = 0
    failed_count: int = 0
    total_load_duration_ms: float = 0.0
    records: list[PluginRecord] = Field(default_factory=list)
