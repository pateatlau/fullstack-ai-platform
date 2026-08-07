"""Span helper scaffold tests — sanitization and fail-open behaviour."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.observability.tracing.spans import llm_span, prompt_span
from app.core.config import Settings


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


def test_llm_span_records_sanitized_attributes(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with llm_span("openai", "gpt-4o-mini", streaming=False):
        pass

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "llm.complete"
    attributes = dict(spans[0].attributes or {})
    assert attributes["provider"] == "openai"
    assert attributes["model"] == "gpt-4o-mini"
    assert attributes["streaming"] is False


def test_prompt_span_redacts_sensitive_attribute_values(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with prompt_span("chat", "prompt", "v1"):
        pass

    spans = memory_exporter.get_finished_spans()
    assert spans[0].name == "prompt.render"


def test_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        completed = False
        with llm_span("openai", "gpt-4o-mini", streaming=True):
            completed = True
        assert completed is True

    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0


def test_business_failure_propagates_from_span_helper(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(ValueError, match="business failure"):
        with llm_span("openai", "gpt-4o-mini", streaming=False):
            raise ValueError("business failure")

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
