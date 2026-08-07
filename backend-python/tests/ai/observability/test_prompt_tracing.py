"""PromptManager span instrumentation tests."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.core.config import Settings

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "data" / "prompts"


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


@pytest.fixture
def fixture_manager() -> PromptManager:
    return create_prompt_manager(prompts_root=FIXTURES_ROOT)


def test_prompt_render_emits_span_with_metadata_not_content(
    memory_exporter: InMemorySpanExporter,
    fixture_manager: PromptManager,
) -> None:
    rendered = fixture_manager.render(
        "edge",
        "special_chars",
        "1",
        {"label": "secret-label", "body": "secret body text"},
    )

    assert "Special chars: secret-label" in rendered
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "prompt.render"
    attributes = dict(spans[0].attributes or {})
    assert attributes["category"] == "edge"
    assert attributes["name"] == "special_chars"
    assert attributes["version"] == "1"
    assert attributes["variable_count"] == 2
    assert attributes["rendered_length_chars"] == len(rendered)
    assert "secret-label" not in attributes.values()
    assert "secret body text" not in attributes.values()
    assert rendered not in attributes.values()


def test_prompt_render_return_value_unchanged(
    fixture_manager: PromptManager,
) -> None:
    first = fixture_manager.render("edge", "versioned", "1", {})
    second = fixture_manager.render("edge", "versioned", "1", {})
    assert first == second == "Version one only."


def test_prompt_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    fixture_manager: PromptManager,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        rendered = fixture_manager.render("edge", "versioned", "1", {})

    assert rendered == "Version one only."
    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0


def test_prompt_business_failure_propagates(
    memory_exporter: InMemorySpanExporter,
    fixture_manager: PromptManager,
) -> None:
    with pytest.raises(Exception, match="name"):
        fixture_manager.render("edge", "missing_var", "1", {})

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
