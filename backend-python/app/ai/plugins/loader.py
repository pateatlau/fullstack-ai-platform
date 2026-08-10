"""Plugin discovery and loading."""

from __future__ import annotations

import importlib
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path

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
    ) -> None:
        self._settings = settings
        self._registry = registry

    def discover(self) -> list[DiscoveryCandidate]:
        """Return discovery candidates in deterministic load order."""
        candidates: list[DiscoveryCandidate] = []
        ordinal = 0

        for directory in self._resolve_plugin_directories():
            if not directory.is_dir():
                logger.warning("Plugin directory does not exist; skipping.")
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

        if not manifest_path.is_file():
            self._registry.mark_failed(
                failure=PluginLoadFailureReason(
                    code="manifest_not_found",
                    message="Plugin manifest not found.",
                ),
                load_duration_ms=_elapsed_ms(load_start),
            )
            return

        manifest, parse_failure = try_load_manifest_from_yaml(manifest_path)
        if manifest is None:
            partial = extract_partial_identity(manifest_path)
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
            return

        api_failure = validate_api_version(manifest)
        if api_failure is not None:
            self._registry.mark_failed(
                manifest=manifest,
                failure=api_failure,
                load_duration_ms=_elapsed_ms(load_start),
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
            return

        seen_plugin_ids.add(manifest.plugin_id)

        plugin_dir_str = str(plugin_dir.resolve())
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)

        module_name, attr_name = manifest.entrypoint.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            register_fn = getattr(module, attr_name)
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
            return

        if not callable(register_fn):
            self._registry.mark_failed(
                manifest=manifest,
                failure=PluginLoadFailureReason(
                    code="entrypoint_import_error",
                    message="Plugin entrypoint is not callable.",
                ),
                load_duration_ms=_elapsed_ms(load_start),
            )
            return

        registrar = PluginRegistrar(
            plugin_id=manifest.plugin_id,
            plugin_dir=plugin_dir,
        )
        timeout_seconds = self._settings.plugin_load_timeout_seconds

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(register_fn, registrar)
                try:
                    future.result(timeout=timeout_seconds)
                except FuturesTimeoutError:
                    registrar.close()
                    self._registry.mark_failed(
                        manifest=manifest,
                        failure=PluginLoadFailureReason(
                            code="timeout",
                            message=(
                                f"Plugin registration exceeded "
                                f"{timeout_seconds}s timeout"
                            ),
                        ),
                        load_duration_ms=_elapsed_ms(load_start),
                    )
                    return
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
            return
        except Exception:
            registrar.rollback()
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
            return

        self._registry.mark_loaded(
            manifest,
            contributions=registrar.contribution_kinds(),
            load_duration_ms=_elapsed_ms(load_start),
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
