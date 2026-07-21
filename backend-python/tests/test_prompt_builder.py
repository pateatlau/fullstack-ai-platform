"""Unit tests for PromptBuilder template rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.prompts.manager import create_prompt_manager
from app.ai.rag.prompt_builder import PromptBuilder, _parse_template_ref
from app.core.config import Settings

FIXTURES_ROOT = Path(__file__).resolve().parent / "data" / "prompts"


def _builder(*, template: str = "rag/answer/v1") -> PromptBuilder:
    return PromptBuilder(
        prompt_manager=create_prompt_manager(),
        settings=Settings(
            openai_api_key="test-key",
            rag_default_prompt_template=template,
        ),
    )


def test_prompt_builder_renders_default_template() -> None:
    builder = _builder()
    question = "What is the refund policy?"
    context = "[1]\nRefunds within 30 days."

    result = builder.build(question=question, context=context)

    assert result.system_prompt is None
    assert "Use the following context to answer the question." in result.user_prompt
    assert "Context:" in result.user_prompt
    assert context in result.user_prompt
    assert f"Question: {question}" in result.user_prompt


def test_prompt_builder_default_template_snapshot() -> None:
    builder = _builder()
    question = "Summarize the key point."
    context = "[1]\nThe platform supports document ingestion."

    result = builder.build(question=question, context=context)

    assert result.user_prompt == (
        "Use the following context to answer the question. "
        "If the context does not contain enough information, say so clearly.\n\n"
        "Context:\n"
        f"{context}\n\n"
        f"Question: {question}"
    )


def test_prompt_builder_template_override() -> None:
    manager = create_prompt_manager(prompts_root=FIXTURES_ROOT)
    builder = PromptBuilder(
        prompt_manager=manager,
        settings=Settings(openai_api_key="test-key"),
    )

    result = builder.build(
        question="Q",
        context="C",
        template_ref="edge/versioned/v2",
    )

    assert result.user_prompt == "Version two content."


def test_prompt_builder_with_instructions() -> None:
    builder = _builder()
    instructions = "Respond in one sentence."

    result = builder.build(
        question="What is RAG?",
        context="[1]\nRetrieval augmented generation.",
        instructions=instructions,
    )

    assert result.user_prompt.startswith(instructions)
    assert "Retrieval augmented generation." in result.user_prompt


def test_prompt_builder_empty_context_still_renders() -> None:
    builder = _builder()

    result = builder.build(question="Any question?", context="")

    assert "Question: Any question?" in result.user_prompt
    assert "Context:" in result.user_prompt


def test_parse_template_ref_accepts_rag_answer_v1() -> None:
    assert _parse_template_ref("rag/answer/v1") == ("rag", "answer", "1")


def test_parse_template_ref_rejects_invalid_format() -> None:
    with pytest.raises(ValueError, match="Invalid template reference"):
        _parse_template_ref("rag/answer")
