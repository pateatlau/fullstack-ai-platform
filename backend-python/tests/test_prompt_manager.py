"""Prompt infrastructure regression and behavior tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.ai.deps import get_prompt_manager
from app.ai.prompts.exceptions import PromptNotFoundError, PromptRenderError
from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.ai.prompts.repository import PromptRepository

FIXTURES_ROOT = Path(__file__).resolve().parent / "data" / "prompts"


@pytest.fixture
def production_manager() -> PromptManager:
    return create_prompt_manager()


@pytest.fixture
def fixture_manager() -> PromptManager:
    return create_prompt_manager(prompts_root=FIXTURES_ROOT)


def test_summarize_system_snapshot(production_manager: PromptManager) -> None:
    rendered = production_manager.render("chat", "summarize_system", "1", {})
    assert (
        rendered == "You produce concise summaries of conversations, "
        "preserving key facts and decisions."
    )


def test_summarize_user_snapshot(production_manager: PromptManager) -> None:
    transcript = "user: hello\nassistant: hi"
    rendered = production_manager.render(
        "chat", "summarize_user", "1", {"transcript": transcript}
    )
    assert (
        rendered == "Summarize the conversation so far in a few sentences:\n\n"
        f"{transcript}"
    )


def test_context_summary_prefix_snapshot(production_manager: PromptManager) -> None:
    rendered = production_manager.render(
        "chat",
        "context_summary_prefix",
        "1",
        {"summary_content": "Earlier: greeting."},
    )
    assert rendered == "Summary of earlier conversation: Earlier: greeting."


def test_default_system_snapshot(production_manager: PromptManager) -> None:
    rendered = production_manager.render("chat", "default_system", "1", {})
    assert rendered == "You are a helpful assistant."


def test_missing_variable_raises_clear_error(
    fixture_manager: PromptManager,
) -> None:
    with pytest.raises(PromptRenderError, match="name"):
        fixture_manager.render("edge", "missing_var", "1", {})


def test_unknown_version_raises_clear_error(
    production_manager: PromptManager,
) -> None:
    with pytest.raises(PromptNotFoundError, match="summarize_system"):
        production_manager.render("chat", "summarize_system", "99", {})


def test_version_resolution_returns_expected_template(
    fixture_manager: PromptManager,
) -> None:
    v1 = fixture_manager.render("edge", "versioned", "1", {})
    v2 = fixture_manager.render("edge", "versioned", "2", {})
    assert v1 == "Version one only."
    assert v2 == "Version two content."


def test_template_cache_avoids_second_disk_read(
    production_manager: PromptManager,
) -> None:
    repository = production_manager._repository
    assert isinstance(repository, PromptRepository)
    original_resolve = repository._resolve_template_path

    call_count = 0

    def counting_resolve(category: str, name: str, version: str) -> Path:
        nonlocal call_count
        call_count += 1
        return original_resolve(category, name, version)

    with patch.object(
        repository, "_resolve_template_path", side_effect=counting_resolve
    ):
        production_manager.render("chat", "summarize_system", "1", {})
        production_manager.render("chat", "summarize_system", "1", {})

    assert call_count == 1
    assert repository.is_cached("chat", "summarize_system", "1")


def test_get_prompt_manager_returns_singleton() -> None:
    get_prompt_manager.cache_clear()
    first = get_prompt_manager()
    second = get_prompt_manager()
    assert first is second
    get_prompt_manager.cache_clear()


def test_fixture_special_characters(fixture_manager: PromptManager) -> None:
    rendered = fixture_manager.render(
        "edge",
        "special_chars",
        "1",
        {"label": "test", "body": "multi\nline"},
    )
    assert 'Special chars: test — "quotes" & <tags>' in rendered
    assert "multi\nline" in rendered


def test_fixture_multiline_body(fixture_manager: PromptManager) -> None:
    rendered = fixture_manager.render(
        "edge",
        "multiline_body",
        "1",
        {"value": "alpha\nbeta"},
    )
    assert rendered == "Line one\nLine two: alpha\nbeta"
