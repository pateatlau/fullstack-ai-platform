"""Plugin manifest schema and YAML loading."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai.mcp.config import McpConnectionConfig
from app.ai.plugins.constants import SUPPORTED_PLUGIN_API_VERSIONS
from app.ai.plugins.exceptions import PluginManifestError
from app.ai.plugins.models import (
    PluginContributionKind,
    PluginDependency,
    PluginLoadFailureReason,
)

_PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9.-]*$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
_VALID_CONTRIBUTIONS = frozenset(kind.value for kind in PluginContributionKind)


class PluginManifest(BaseModel):
    """Validated plugin manifest (``plugin.yaml``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    name: str
    version: str
    api_version: str
    description: str | None = None
    entrypoint: str
    contributions: list[PluginContributionKind] = Field(default_factory=list)
    author: str | None = None
    homepage: str | None = None
    repository: str | None = None
    documentation: str | None = None
    license: str | None = None
    dependencies: list[PluginDependency] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    mcp_servers: list[McpConnectionConfig] = Field(default_factory=list)
    min_platform_version: str | None = None

    @field_validator("plugin_id")
    @classmethod
    def validate_plugin_id(cls, value: str) -> str:
        if not value or not _PLUGIN_ID_PATTERN.match(value):
            raise ValueError(
                "plugin_id must be non-empty and match ^[a-z][a-z0-9.-]*$."
            )
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER_PATTERN.match(value):
            raise ValueError(
                "version must be a valid SemVer 2.0.0 string (MAJOR.MINOR.PATCH)."
            )
        return value

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError("entrypoint must use module:callable form.")
        module, attr = value.split(":", 1)
        if not module or not attr:
            raise ValueError("entrypoint must use module:callable form.")
        return value

    @field_validator("contributions", mode="before")
    @classmethod
    def normalize_contributions(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("contributions must be a list.")
        normalized: list[PluginContributionKind] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("Each contribution must be a string.")
            if item not in _VALID_CONTRIBUTIONS:
                raise ValueError(
                    f"Invalid contribution '{item}'. "
                    f"Allowed: {', '.join(sorted(_VALID_CONTRIBUTIONS))}."
                )
            normalized.append(PluginContributionKind(item))
        return normalized

    @field_validator("dependencies", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("dependencies must be a list.")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object.")
        for key in value:
            if not isinstance(key, str):
                raise ValueError("metadata keys must be strings.")
        return value

    @model_validator(mode="after")
    def validate_mcp_servers_when_declared(self) -> Self:
        if (
            PluginContributionKind.MCP_SERVER in self.contributions
            and not self.mcp_servers
        ):
            raise ValueError(
                "mcp_servers is required when contributions include mcp_server."
            )
        return self


def load_manifest_from_yaml(path: Path) -> PluginManifest:
    """Parse and validate ``plugin.yaml`` at *path*."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginManifestError("Unable to read plugin manifest.") from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise PluginManifestError("Plugin manifest is not valid YAML.") from exc

    if raw is None:
        raise PluginManifestError("Plugin manifest is empty.")
    if not isinstance(raw, dict):
        raise PluginManifestError("Plugin manifest root must be a mapping.")

    try:
        return PluginManifest.model_validate(raw)
    except Exception as exc:
        message = _format_validation_error(exc)
        raise PluginManifestError(message) from exc


def try_load_manifest_from_yaml(
    path: Path,
) -> tuple[PluginManifest | None, PluginLoadFailureReason | None]:
    """Best-effort manifest load returning partial identity on some failures."""
    try:
        return load_manifest_from_yaml(path), None
    except PluginManifestError as exc:
        return None, PluginLoadFailureReason(
            code="invalid_manifest",
            message=str(exc),
        )


def extract_partial_identity(path: Path) -> dict[str, Any]:
    """Best-effort identity fields from a malformed manifest (nullable values)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, Any] = {}

    plugin_id = raw.get("plugin_id")
    if isinstance(plugin_id, str) and _PLUGIN_ID_PATTERN.match(plugin_id):
        result["plugin_id"] = plugin_id
    else:
        result["plugin_id"] = None

    for key in (
        "name",
        "version",
        "api_version",
        "author",
        "homepage",
        "repository",
        "documentation",
        "license",
    ):
        value = raw.get(key)
        result[key] = value if isinstance(value, str) else None

    deps_raw = raw.get("dependencies")
    if isinstance(deps_raw, list):
        deps: list[PluginDependency] = []
        for item in deps_raw:
            if isinstance(item, dict) and isinstance(item.get("plugin_id"), str):
                version = item.get("version")
                deps.append(
                    PluginDependency(
                        plugin_id=item["plugin_id"],
                        version=version if isinstance(version, str) else None,
                    )
                )
        result["dependencies"] = deps

    meta = raw.get("metadata")
    if isinstance(meta, dict) and all(isinstance(k, str) for k in meta):
        result["metadata"] = dict(meta)

    return result


def _format_validation_error(exc: Exception) -> str:
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False)
        if errors:
            first = errors[0]
            loc = ".".join(str(part) for part in first.get("loc", ()))
            msg = first.get("msg", "Invalid manifest field.")
            if loc:
                return f"Invalid manifest field '{loc}': {msg}"
            return f"Invalid manifest: {msg}"
    return str(exc)


def validate_api_version(manifest: PluginManifest) -> PluginLoadFailureReason | None:
    """Return a failure reason when ``api_version`` is unsupported."""
    if manifest.api_version in SUPPORTED_PLUGIN_API_VERSIONS:
        return None
    expected = sorted(SUPPORTED_PLUGIN_API_VERSIONS)
    return PluginLoadFailureReason(
        code="unsupported_api_version",
        message=f"Plugin api_version '{manifest.api_version}' is not supported",
        expected_api_versions=expected,
        manifest_api_version=manifest.api_version,
    )


def manifest_identity_fields(manifest: PluginManifest) -> dict[str, Any]:
    """Identity and metadata fields copied onto ``PluginRecord``."""
    return {
        "plugin_id": manifest.plugin_id,
        "name": manifest.name,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "author": manifest.author,
        "homepage": manifest.homepage,
        "repository": manifest.repository,
        "documentation": manifest.documentation,
        "license": manifest.license,
        "dependencies": list(manifest.dependencies),
        "metadata": dict(manifest.metadata),
    }
