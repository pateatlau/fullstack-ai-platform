"""Render configurable RAG prompt templates via PromptManager."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.prompts.manager import PromptManager
from app.core.config import Settings


@dataclass(frozen=True)
class BuiltPrompt:
    """Rendered prompt portions for downstream LLM orchestration (Phase 9)."""

    system_prompt: str | None
    user_prompt: str


class PromptBuilder:
    """Render a RAG template with question, context, and optional instructions."""

    def __init__(
        self,
        *,
        prompt_manager: PromptManager,
        settings: Settings,
    ) -> None:
        self._prompt_manager = prompt_manager
        self._settings = settings

    def build(
        self,
        *,
        question: str,
        context: str,
        template_ref: str | None = None,
        instructions: str | None = None,
    ) -> BuiltPrompt:
        category, name, version = _parse_template_ref(
            template_ref or self._settings.rag_default_prompt_template
        )
        variables: dict[str, object] = {
            "question": question,
            "context": context,
            "instructions": instructions or "",
        }
        rendered = self._prompt_manager.render(
            category,
            name,
            version,
            variables,
        )
        return BuiltPrompt(system_prompt=None, user_prompt=rendered)


def _parse_template_ref(ref: str) -> tuple[str, str, str]:
    """Parse ``{category}/{name}/v{version}`` (e.g. ``rag/answer/v1``)."""
    parts = ref.strip("/").split("/")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid template reference {ref!r}; expected category/name/v{{version}}."
        )
    category, name, version_part = parts
    version = version_part[1:] if version_part.startswith("v") else version_part
    if not category or not name or not version:
        raise ValueError(
            f"Invalid template reference {ref!r}; expected category/name/v{{version}}."
        )
    return category, name, version
