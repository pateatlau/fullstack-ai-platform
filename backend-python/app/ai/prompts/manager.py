"""PromptManager facade: resolve template identity and render to string."""

from __future__ import annotations

from pathlib import Path

from app.ai.observability.tracing.spans import _set_span_attributes, prompt_span
from app.ai.prompts.repository import PromptRepository
from app.ai.prompts.renderer import PromptRenderer


class PromptManager:
    """Stateless facade combining repository lookup and Jinja2 rendering."""

    def __init__(
        self,
        repository: PromptRepository | None = None,
        renderer: PromptRenderer | None = None,
    ) -> None:
        self._repository = repository or PromptRepository()
        self._renderer = renderer or PromptRenderer()

    def render(
        self,
        category: str,
        name: str,
        version: str,
        variables: dict[str, object],
    ) -> str:
        with prompt_span(category, name, version) as span:
            template = self._repository.get_template(category, name, version)
            rendered = self._renderer.render(
                template,
                variables,
                category=category,
                name=name,
                version=version,
            )
            if span is not None:
                _set_span_attributes(
                    span,
                    {
                        "variable_count": len(variables),
                        "rendered_length_chars": len(rendered),
                    },
                )
            return rendered


def create_prompt_manager(prompts_root: Path | None = None) -> PromptManager:
    """Construct a ``PromptManager`` (used by DI and offline scripts)."""
    repository = PromptRepository(prompts_root=prompts_root)
    return PromptManager(repository=repository, renderer=PromptRenderer())
