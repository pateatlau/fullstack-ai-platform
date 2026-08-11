"""Plugin discovery and loading."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app.ai.plugins.exceptions import PluginRegistrationError
from app.ai.plugins.manifest import (
    PluginManifest,
    extract_partial_identity,
    try_load_manifest_from_yaml,
    validate_api_version,
)
from app.ai.plugins.models import PluginLoadFailureReason, PluginLoadReport
from app.ai.plugins.registrar import PluginRegistrar
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.observability.tracing.spans import plugin_span, record_plugin_load_outcome
from app.ai.prompts.repository import PromptRepository
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, order=True)
class DiscoveryCandidate:
    """One immediate child directory under a configured plugin directory."""

    sort_key: tuple[int, str, int]
    plugin_dir: Path
    discovery_ordinal: int


class PluginLoader:
    """Discovers plugin manifests and loads entrypoints into ``PluginRegistry``."""

    def __init__(
        self,
        settings: Settings,
        registry: PluginRegistry,
        *,
        tool_registry: ToolRegistry | None = None,
        prompt_repository: PromptRepository | None = None,
        workflow_plugin_registry: WorkflowPluginRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._tool_registry = tool_registry
        self._prompt_repository = prompt_repository
        self._workflow_plugin_registry = workflow_plugin_registry

    def discover(self) -> list[DiscoveryCandidate]:
        """Return discovery candidates in deterministic load order."""
        candidates: list[DiscoveryCandidate] = []
        ordinal = 0

        for directory in self._resolve_plugin_directories():
            if not directory.is_dir():
                logger.warning(
                    "Plugin directory does not exist; skipping: %s",
                    directory,
                )
                continue
            for child in sorted(directory.iterdir(), key=lambda path: path.name):
                if child.is_dir():
                    candidates.append(
                        DiscoveryCandidate(
                            sort_key=(1, "", ordinal),
                            plugin_dir=child,
                            discovery_ordinal=ordinal,
                        )
                    )
                    ordinal += 1

        manifest_by_dir: dict[Path, PluginManifest | None] = {}
        for candidate in candidates:
            manifest_path = candidate.plugin_dir / "plugin.yaml"
            if not manifest_path.is_file():
                manifest_by_dir[candidate.plugin_dir] = None
                continue
            manifest, _failure = try_load_manifest_from_yaml(manifest_path)
            manifest_by_dir[candidate.plugin_dir] = manifest

        ordered: list[DiscoveryCandidate] = []
        for candidate in candidates:
            manifest = manifest_by_dir.get(candidate.plugin_dir)
            if manifest is not None:
                sort_key = (0, manifest.plugin_id, candidate.discovery_ordinal)
            else:
                sort_key = (1, "", candidate.discovery_ordinal)
            ordered.append(
                DiscoveryCandidate(
                    sort_key=sort_key,
                    plugin_dir=candidate.plugin_dir,
                    discovery_ordinal=candidate.discovery_ordinal,
                )
            )

        ordered.sort()
        return ordered

    def load_all(self) -> PluginLoadReport:
        """Discover and load all plugins; never raises uncaught exceptions."""
        if not self._settings.plugins_enabled:
            return PluginLoadReport()

        total_start = time.monotonic()
        seen_plugin_ids: set[str] = set()

        try:
            candidates = self.discover()
        except Exception:
            logger.exception("Plugin discovery failed.")
            return PluginLoadReport(
                total_load_duration_ms=_elapsed_ms(total_start),
            )

        for candidate in candidates:
            try:
                self._load_candidate(
                    candidate.plugin_dir,
                    seen_plugin_ids=seen_plugin_ids,
                )
            except Exception:
                logger.exception(
                    "Unexpected plugin load failure.",
                    extra={"discovery_ordinal": candidate.discovery_ordinal},
                )
                self._registry.mark_failed(
                    failure=PluginLoadFailureReason(
                        code="registration_error",
                        message="Unexpected plugin load failure.",
                    ),
                )

        return PluginLoadReport(
            loaded_count=self._registry.loaded_count,
            failed_count=self._registry.failed_count,
            total_load_duration_ms=_elapsed_ms(total_start),
            records=self._registry.list_records(),
        )

    def _load_candidate(
        self,
        plugin_dir: Path,
        *,
        seen_plugin_ids: set[str],
    ) -> None:
        manifest_path = plugin_dir / "plugin.yaml"
        load_start = time.monotonic()
        plugin_id: str | None = None

        with plugin_span() as span:

            def _observe(
                *,
                status: str,
                resolved_plugin_id: str | None = None,
                contribution_kinds: list[str] | None = None,
                failure_code: str | None = None,
            ) -> None:
                record_plugin_load_outcome(
                    span,
                    plugin_id=resolved_plugin_id
                    if resolved_plugin_id is not None
                    else plugin_id,
                    status=status,
                    load_duration_ms=_elapsed_ms(load_start),
                    contribution_kinds=contribution_kinds,
                    failure_code=failure_code,
                )

            if not manifest_path.is_file():
                self._registry.mark_failed(
                    failure=PluginLoadFailureReason(
                        code="manifest_not_found",
                        message="Plugin manifest not found.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(status="failed", failure_code="manifest_not_found")
                return

            manifest, parse_failure = try_load_manifest_from_yaml(manifest_path)
            if manifest is None:
                partial = extract_partial_identity(manifest_path)
                plugin_id = partial.get("plugin_id")
                self._registry.mark_failed(
                    failure=parse_failure
                    or PluginLoadFailureReason(
                        code="invalid_manifest",
                        message="Invalid plugin manifest.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                    plugin_id=partial.get("plugin_id"),
                    name=partial.get("name"),
                    version=partial.get("version"),
                    api_version=partial.get("api_version"),
                    author=partial.get("author"),
                    homepage=partial.get("homepage"),
                    repository=partial.get("repository"),
                    documentation=partial.get("documentation"),
                    license=partial.get("license"),
                    dependencies=partial.get("dependencies"),
                    metadata=partial.get("metadata"),
                )
                failure_code = (
                    parse_failure.code
                    if parse_failure is not None
                    else "invalid_manifest"
                )
                _observe(status="failed", failure_code=failure_code)
                return

            plugin_id = manifest.plugin_id

            api_failure = validate_api_version(manifest)
            if api_failure is not None:
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=api_failure,
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code=api_failure.code,
                )
                return

            allowlist = self._settings.plugin_allowlist
            if allowlist and manifest.plugin_id not in allowlist:
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="allowlist_excluded",
                        message="Plugin is not included in plugin_allowlist.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="allowlist_excluded",
                )
                return

            if manifest.plugin_id in seen_plugin_ids:
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="invalid_manifest",
                        message=f"Duplicate plugin_id '{manifest.plugin_id}'.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="invalid_manifest",
                )
                return

            seen_plugin_ids.add(manifest.plugin_id)

            try:
                register_fn = _load_entrypoint_callable(
                    plugin_dir=plugin_dir,
                    plugin_id=manifest.plugin_id,
                    entrypoint=manifest.entrypoint,
                )
            except Exception:
                logger.warning(
                    "Plugin entrypoint import failed.",
                    extra={"plugin_id": manifest.plugin_id},
                )
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="entrypoint_import_error",
                        message="Unable to import plugin entrypoint.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="entrypoint_import_error",
                )
                return

            registrar = PluginRegistrar(
                plugin_id=manifest.plugin_id,
                plugin_dir=plugin_dir,
                tool_registry=self._tool_registry,
                prompt_repository=self._prompt_repository,
                workflow_plugin_registry=self._workflow_plugin_registry,
                plugin_registry=self._registry,
            )

            for mcp_server in manifest.mcp_servers:
                try:
                    registrar.register_mcp_server(mcp_server.to_dict())
                except PluginRegistrationError as exc:
                    self._registry.mark_failed(
                        manifest=manifest,
                        failure=PluginLoadFailureReason(
                            code="registration_error",
                            message=str(exc),
                        ),
                        load_duration_ms=_elapsed_ms(load_start),
                    )
                    _observe(
                        status="failed",
                        resolved_plugin_id=manifest.plugin_id,
                        failure_code="registration_error",
                    )
                    return

            wait_timeout_seconds = (
                self._settings.plugin_registration_wait_timeout_seconds
            )
            registration_error: BaseException | None = None
            registration_complete = threading.Event()

            def _run_registration() -> None:
                nonlocal registration_error
                try:
                    register_fn(registrar)
                except BaseException as exc:
                    registration_error = exc
                finally:
                    registration_complete.set()

            # Cooperative wait: loader stops waiting after N seconds; register() may
            # continue in the daemon thread until it returns (in-process V2 boundary).
            thread = threading.Thread(
                target=_run_registration,
                name=f"plugin-register-{manifest.plugin_id}",
                daemon=True,
            )
            thread.start()

            if not registration_complete.wait(timeout=wait_timeout_seconds):
                registrar.close()
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="timeout",
                        message=(
                            "Plugin registration did not complete within "
                            f"{wait_timeout_seconds}s wait limit"
                        ),
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="timeout",
                )
                return

            if registration_error is not None:
                registrar.rollback()
                if isinstance(registration_error, PluginRegistrationError):
                    self._registry.mark_failed(
                        manifest=manifest,
                        failure=PluginLoadFailureReason(
                            code="registration_error",
                            message=str(registration_error),
                        ),
                        load_duration_ms=_elapsed_ms(load_start),
                    )
                    _observe(
                        status="failed",
                        resolved_plugin_id=manifest.plugin_id,
                        failure_code="registration_error",
                    )
                    return
                logger.warning(
                    "Plugin registration failed.",
                    extra={"plugin_id": manifest.plugin_id},
                )
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="registration_error",
                        message="Plugin registration failed.",
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="registration_error",
                )
                return

            try:
                registrar.commit()
            except PluginRegistrationError as exc:
                registrar.rollback()
                self._registry.mark_failed(
                    manifest=manifest,
                    failure=PluginLoadFailureReason(
                        code="registration_error",
                        message=str(exc),
                    ),
                    load_duration_ms=_elapsed_ms(load_start),
                )
                _observe(
                    status="failed",
                    resolved_plugin_id=manifest.plugin_id,
                    failure_code="registration_error",
                )
                return

            contributions = registrar.contribution_kinds()
            self._registry.mark_loaded(
                manifest,
                contributions=contributions,
                load_duration_ms=_elapsed_ms(load_start),
            )
            _observe(
                status="loaded",
                resolved_plugin_id=manifest.plugin_id,
                contribution_kinds=[kind.value for kind in contributions],
            )

    def _resolve_plugin_directories(self) -> list[Path]:
        resolved: list[Path] = []
        for entry in self._settings.plugin_directories:
            path = Path(entry)
            if not path.is_absolute():
                path = _BACKEND_ROOT / path
            resolved.append(path)
        return resolved


def _elapsed_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000.0, 3)


def _sanitize_plugin_id(plugin_id: str) -> str:
    return plugin_id.replace(".", "_").replace("-", "_")


def _resolve_entrypoint_module_file(plugin_dir: Path, module_name: str) -> Path:
    if not module_name or any(part in {"", ".."} for part in module_name.split(".")):
        raise ValueError("Invalid entrypoint module name.")

    module_file = plugin_dir.joinpath(*module_name.split(".")).with_suffix(".py")
    if not module_file.is_file():
        raise FileNotFoundError("Entrypoint module file not found.")

    plugin_root = plugin_dir.resolve()
    if not module_file.resolve().is_relative_to(plugin_root):
        raise ValueError("Entrypoint module escapes plugin directory.")
    return module_file


def _register_plugin_package_modules(
    *,
    plugin_dir: Path,
    plugin_id: str,
    module_name: str,
) -> str:
    unique_root = f"_plugin_{_sanitize_plugin_id(plugin_id)}"
    parts = module_name.split(".")
    for index in range(1, len(parts)):
        pkg_relative = parts[:index]
        unique_pkg_name = f"{unique_root}.{'.'.join(pkg_relative)}"
        if unique_pkg_name in sys.modules:
            continue
        pkg_dir = plugin_dir.joinpath(*pkg_relative)
        spec = importlib.machinery.ModuleSpec(
            unique_pkg_name,
            loader=None,
            is_package=True,
        )
        package = importlib.util.module_from_spec(spec)
        package.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
        sys.modules[unique_pkg_name] = package
    return f"{unique_root}.{module_name}"


def _load_entrypoint_module(
    *,
    plugin_dir: Path,
    plugin_id: str,
    module_name: str,
) -> ModuleType:
    module_file = _resolve_entrypoint_module_file(plugin_dir, module_name)
    full_module_name = _register_plugin_package_modules(
        plugin_dir=plugin_dir,
        plugin_id=plugin_id,
        module_name=module_name,
    )
    cached = sys.modules.get(full_module_name)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(full_module_name, module_file)
    if spec is None or spec.loader is None:
        raise ImportError("Unable to create entrypoint module spec.")

    module = importlib.util.module_from_spec(spec)
    parts = module_name.split(".")
    module.__package__ = (
        ".".join(full_module_name.split(".")[:-1]) if len(parts) > 1 else ""
    )
    sys.modules[full_module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_entrypoint_callable(
    *,
    plugin_dir: Path,
    plugin_id: str,
    entrypoint: str,
) -> Callable[..., object]:
    module_name, attr_name = entrypoint.split(":", 1)
    if not attr_name:
        raise ValueError("Invalid entrypoint callable name.")
    module = _load_entrypoint_module(
        plugin_dir=plugin_dir,
        plugin_id=plugin_id,
        module_name=module_name,
    )
    register_fn = getattr(module, attr_name)
    if not callable(register_fn):
        raise TypeError("Plugin entrypoint is not callable.")
    return register_fn
