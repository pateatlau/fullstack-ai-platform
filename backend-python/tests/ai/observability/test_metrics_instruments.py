"""Metric instrument and label cardinality tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.ai.observability.metrics.instruments import (
    MetricInstruments,
    all_instrument_label_keys,
    assert_label_keys_allowlisted,
    record_llm_cost_metric,
    record_llm_request_metrics,
    record_tool_call_metrics,
    record_workflow_run_started,
)
from app.ai.observability.metrics.labels import (
    ALLOWED_LABEL_KEYS,
    FORBIDDEN_LABEL_KEYS,
    NODE_TYPE_REGISTRY,
    PROVIDER_REGISTRY,
    STATUS_REGISTRY,
    TOOL_NAME_REGISTRY,
    WORKFLOW_TYPE_REGISTRY,
    build_metric_attributes,
    normalize_metric_label,
    reset_model_registry_for_tests,
    set_model_registry,
)
from app.ai.observability.metrics.meter import MeterRegistry


@pytest.fixture(autouse=True)
def _reset_metric_state() -> Iterator[None]:
    reset_model_registry_for_tests()
    set_model_registry(frozenset({"gpt-4o-mini", "gpt-4o"}))
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    yield
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    reset_model_registry_for_tests()


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    MeterRegistry._initialized = True
    MeterRegistry._enabled = True
    MetricInstruments.initialize()
    return reader


def test_normalize_metric_label_provider_and_model() -> None:
    assert normalize_metric_label("provider", "openai") == "openai"
    assert normalize_metric_label("provider", "unknown-vendor") == "other"
    assert normalize_metric_label("model", "gpt-4o-mini") == "gpt-4o-mini"
    assert normalize_metric_label("model", "claude-random") == "other"


def test_normalize_metric_label_tool_node_and_status() -> None:
    assert normalize_metric_label("tool_name", "web_search") == "web_search"
    assert normalize_metric_label("tool_name", "mcp_custom_tool") == "other"
    assert normalize_metric_label("node_type", "approval") == "approval"
    assert normalize_metric_label("node_type", "plugin_node") == "other"
    assert normalize_metric_label("status", "completed") == "succeeded"
    assert normalize_metric_label("status", "weird") == "other"


def test_build_metric_attributes_rejects_forbidden_keys() -> None:
    with pytest.raises(ValueError, match="Forbidden"):
        build_metric_attributes(user_id="abc")

    with pytest.raises(ValueError, match="Disallowed"):
        build_metric_attributes(custom="value")


def test_instrument_label_keys_subset_of_allowlist() -> None:
    assert_label_keys_allowlisted()
    for name, keys in all_instrument_label_keys().items():
        assert keys.issubset(ALLOWED_LABEL_KEYS), name
        assert keys.isdisjoint(FORBIDDEN_LABEL_KEYS), name


def test_llm_counter_and_histogram_increment(
    metric_reader: InMemoryMetricReader,
) -> None:
    record_llm_request_metrics(
        provider="openai",
        model="gpt-4o-mini",
        succeeded=True,
        total_tokens=42,
    )

    data = metric_reader.get_metrics_data()
    assert data is not None
    resource_metrics = data.resource_metrics
    assert resource_metrics

    metric_names = {
        metric.name
        for rm in resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert "llm_requests_total" in metric_names
    assert "llm_token_usage" in metric_names


def test_llm_cost_counter_increments(metric_reader: InMemoryMetricReader) -> None:
    record_llm_cost_metric(provider="openai", model="gpt-4o-mini", cost_usd=0.00123)

    data = metric_reader.get_metrics_data()
    assert data is not None
    names = {
        metric.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert "llm_cost_usd_total" in names


def test_tool_metrics_use_registry_label_values(
    metric_reader: InMemoryMetricReader,
) -> None:
    record_tool_call_metrics(tool_name="mcp_weather", success=True, latency_ms=12)

    data = metric_reader.get_metrics_data()
    assert data is not None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "tool_calls_total":
                    continue
                for data_point in metric.data.data_points:
                    attributes = data_point.attributes or {}
                    assert attributes["tool_name"] in TOOL_NAME_REGISTRY


def test_workflow_run_started_emits_normalized_labels(
    metric_reader: InMemoryMetricReader,
) -> None:
    record_workflow_run_started(workflow_type="standard")

    data = metric_reader.get_metrics_data()
    assert data is not None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "workflow_runs_started":
                    continue
                for data_point in metric.data.data_points:
                    attributes = data_point.attributes or {}
                    assert attributes["workflow_type"] in WORKFLOW_TYPE_REGISTRY


def test_registry_members_only_for_sample_inputs() -> None:
    samples = [
        ("provider", "mystery", PROVIDER_REGISTRY),
        ("model", "gpt-unknown", frozenset({"gpt-4o-mini", "gpt-4o", "other"})),
        ("tool_name", "mcp_x", TOOL_NAME_REGISTRY),
        ("node_type", "bad", NODE_TYPE_REGISTRY),
        ("workflow_type", "plugin", WORKFLOW_TYPE_REGISTRY),
        ("status", "unknown", STATUS_REGISTRY),
    ]
    for dimension, raw, registry in samples:
        normalized = normalize_metric_label(dimension, raw)
        assert normalized in registry
        assert normalized == "other"
