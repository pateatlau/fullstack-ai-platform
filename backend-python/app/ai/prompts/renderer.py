"""Jinja2 rendering with strict undefined variable handling."""

from __future__ import annotations

from jinja2 import Template
from jinja2.exceptions import UndefinedError

from app.ai.prompts.exceptions import PromptRenderError


class PromptRenderer:
    """Render cached templates with typed variable injection."""

    def render(
        self,
        template: Template,
        variables: dict[str, object],
        *,
        category: str,
        name: str,
        version: str,
    ) -> str:
        """Render a template; missing variables raise ``PromptRenderError``."""
        try:
            return template.render(**variables)
        except UndefinedError as exc:
            raise PromptRenderError(category, name, version, str(exc)) from exc
