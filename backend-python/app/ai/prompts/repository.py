"""Load and cache versioned Jinja2 prompt templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined, Template

from app.ai.prompts.exceptions import PromptNotFoundError

_TEMPLATE_SUFFIXES = (".j2", ".jinja2")


class PromptRepository:
    """Resolve and cache prompt templates from category subdirectories."""

    def __init__(self, prompts_root: Path | None = None) -> None:
        self._prompts_root = prompts_root or Path(__file__).resolve().parent
        self._cache: dict[tuple[str, str, str], Template] = {}
        self._environment = Environment(
            autoescape=False,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )

    def get_template(self, category: str, name: str, version: str) -> Template:
        """Return a cached Jinja2 template for the given identity."""
        key = (category, name, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        template_path = self._resolve_template_path(category, name, version)
        source = template_path.read_text(encoding="utf-8")
        template = self._environment.from_string(source)
        self._cache[key] = template
        return template

    def is_cached(self, category: str, name: str, version: str) -> bool:
        """Return whether the template is already in the in-memory cache."""
        return (category, name, version) in self._cache

    def _resolve_template_path(self, category: str, name: str, version: str) -> Path:
        category_dir = self._prompts_root / category
        filename_stem = f"{name}.v{version}"
        for suffix in _TEMPLATE_SUFFIXES:
            candidate = category_dir / f"{filename_stem}{suffix}"
            if candidate.is_file():
                return candidate

        raise PromptNotFoundError(category, name, version)
