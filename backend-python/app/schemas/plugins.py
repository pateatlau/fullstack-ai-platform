"""Plugin inventory REST API schemas (Epic 08 Phase 6).

Responses expose bounded inventory fields only — never manifest ``metadata``,
filesystem paths, entrypoint module paths, or template bodies.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.plugins.models import PluginContributionKind, PluginStatus

__all__ = [
    "PluginDependencyResponse",
    "PluginInventoryDetail",
    "PluginInventoryItem",
    "PluginInventoryListResponse",
    "PluginLoadFailureResponse",
]


class PluginDependencyResponse(BaseModel):
    """Informational manifest dependency entry (detail endpoint only)."""

    plugin_id: str
    version: str | None = None


class PluginLoadFailureResponse(BaseModel):
    """Safe plugin load failure subset for REST responses."""

    code: str
    message: str
    expected_api_versions: list[str] | None = None
    manifest_api_version: str | None = None


class PluginInventoryItem(BaseModel):
    """One plugin record in the inventory list."""

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
    failure: PluginLoadFailureResponse | None = None


class PluginInventoryDetail(PluginInventoryItem):
    """Detail view for one resolvable plugin record."""

    dependencies: list[PluginDependencyResponse] = Field(default_factory=list)


class PluginInventoryListResponse(BaseModel):
    """Inventory list returned by ``GET /api/plugins``."""

    plugins: list[PluginInventoryItem] = Field(default_factory=list)
