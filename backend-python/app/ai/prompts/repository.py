"""Load and cache versioned Jinja2 prompt templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined, Template

from app.ai.prompts.exceptions import (
    PromptNotFoundError,
    PromptTemplateAlreadyRegisteredError,
)

_TEMPLATE_SUFFIXES = (".j2", ".jinja2")


class PromptRepository:
    """Resolve and cache prompt templates from category subdirectories."""

    def __init__(self, prompts_root: Path | None = None) -> None:
        self._prompts_root = prompts_root or Path(__file__).resolve().parent
        self._cache: dict[tuple[str, str, str], Template] = {}
        # Plugin templates keyed by (category, name, version); category is plugin/{plugin_id}.
        self._plugin_overlay: dict[tuple[str, str, str], str] = {}
        self._environment = Environment(
            autoescape=False,
            undefined=StrictUndefined,
        )

    def get_template(self, category: str, name: str, version: str) -> Template:
        """Return a cached Jinja2 template for the given identity.

        Lookup order: in-memory cache, then filesystem (built-in prompts), then
        the plugin overlay. Plugin templates use ``plugin/{plugin_id}`` categories
        and never override built-in categories outside ``plugin/``.
        """
        key = (category, name, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if self._template_exists_on_filesystem(category, name, version):
            source = self._resolve_template_path(category, name, version).read_text(
                encoding="utf-8"
            )
        else:
            overlay_source = self._plugin_overlay.get(key)
            if overlay_source is None:
                raise PromptNotFoundError(category, name, version)
            source = overlay_source

        template = self._environment.from_string(source)
        self._cache[key] = template
        return template

    def register_plugin_template(
        self,
        *,
        plugin_id: str,
        name: str,
        version: str,
        source: str,
    ) -> None:
        """Register a plugin prompt template under ``plugin/{plugin_id}``."""
        category = self._plugin_category(plugin_id)
        key = (category, name, version)
        if key in self._plugin_overlay or self._template_exists_on_filesystem(
            category,
            name,
            version,
        ):
            raise PromptTemplateAlreadyRegisteredError(category, name, version)
        self._plugin_overlay[key] = source
        self._cache.pop(key, None)

    def unregister_plugin_template(
        self,
        *,
        plugin_id: str,
        name: str,
        version: str,
    ) -> None:
        """Remove a plugin template from the overlay (used during plugin rollback)."""
        key = (self._plugin_category(plugin_id), name, version)
        self._plugin_overlay.pop(key, None)
        self._cache.pop(key, None)

    def list_plugin_templates(self) -> list[tuple[str, str, str]]:
        """Return registered plugin template identities ``(category, name, version)``."""
        return sorted(self._plugin_overlay.keys())

    def is_cached(self, category: str, name: str, version: str) -> bool:
        """Return whether the template is already in the in-memory cache."""
        return (category, name, version) in self._cache

    @staticmethod
    def _plugin_category(plugin_id: str) -> str:
        return f"plugin/{plugin_id}"

    def _template_exists_on_filesystem(
        self,
        category: str,
        name: str,
        version: str,
    ) -> bool:
        category_dir = self._prompts_root / category
        filename_stem = f"{name}.v{version}"
        for suffix in _TEMPLATE_SUFFIXES:
            if (category_dir / f"{filename_stem}{suffix}").is_file():
                return True
        return False

    def _resolve_template_path(self, category: str, name: str, version: str) -> Path:
        category_dir = self._prompts_root / category
        filename_stem = f"{name}.v{version}"
        for suffix in _TEMPLATE_SUFFIXES:
            candidate = category_dir / f"{filename_stem}{suffix}"
            if candidate.is_file():
                return candidate

        raise PromptNotFoundError(category, name, version)
