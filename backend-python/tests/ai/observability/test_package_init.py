"""Package import tests — verify app.ai.observability has no circular imports."""

from __future__ import annotations

import importlib


def test_package_imports_cleanly() -> None:
    module = importlib.import_module("app.ai.observability")

    assert hasattr(module, "__all__")
    for name in module.__all__:
        assert hasattr(module, name), f"app.ai.observability is missing export {name!r}"


def test_public_api_surface_matches_locked_scope() -> None:
    module = importlib.import_module("app.ai.observability")

    assert set(module.__all__) == {
        "OBSERVABILITY_ENABLED",
        "MeterRegistry",
        "ObservabilityConfigError",
        "ObservabilityDisabledError",
        "ObservabilityError",
        "TracerRegistry",
        "TracingLLMProvider",
        "agent_span",
        "format_span_context",
        "get_meter",
        "get_tracer",
        "http_server_span",
        "llm_span",
        "memory_span",
        "prompt_span",
        "rag_span",
        "tool_span",
        "voice_span",
        "workflow_span",
    }


def test_subpackages_import_independently() -> None:
    importlib.import_module("app.ai.observability.tracing.provider")
    importlib.import_module("app.ai.observability.tracing.spans")
    importlib.import_module("app.ai.observability.tracing.provider_wrapper")
    importlib.import_module("app.ai.observability.metrics.meter")
    importlib.import_module("app.ai.observability.metrics.instruments")
    importlib.import_module("app.ai.observability.metrics.labels")
    importlib.import_module("app.ai.observability.cost.pricing")
    importlib.import_module("app.ai.observability.cost.calculator")
    importlib.import_module("app.ai.observability.aggregation.usage_aggregator")
    importlib.import_module("app.ai.observability.exceptions")
