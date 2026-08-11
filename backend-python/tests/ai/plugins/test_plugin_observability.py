"""Plugin load observability tests (Epic 08 Phase 7)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.ai.observability.metrics.instruments import MetricInstruments
from app.ai.observability.metrics.labels import FAILURE_CODE_REGISTRY
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.plugins import PluginStatus
from app.core.config import Settings
from tests.ai.plugins.conftest import FIXTURES_ROOT, load_plugins, plugin_settings


def _plugin_dir_settings(
    plugin_dir: str,
    *,
    allowlist: list[str] | None = None,
) -> Settings:
    """Settings with one plugin container directory and optional allowlist."""
    return plugin_settings(
        directories=[plugin_dir],
        allowlist=allowlist or [],
    ).model_copy(update={"observability_enabled": True})


def _span_attributes(span) -> dict:
    return dict(span.attributes or {})


def _find_load_span(spans, *, plugin_id: str | None = None):
    load_spans = [span for span in spans if span.name == "plugin.load"]
    if plugin_id is None:
        return load_spans[0] if len(load_spans) == 1 else None
    for span in load_spans:
        attributes = _span_attributes(span)
        if attributes.get("plugin_id") == plugin_id:
            return span
    return None


def _metric_data_points(metric_reader: InMemoryMetricReader, metric_name: str):
    data = metric_reader.get_metrics_data()
    assert data is not None
    points = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == metric_name:
                    points.extend(metric.data.data_points)
    return points


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()
    MetricInstruments.reset_for_tests()
    MeterRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = _plugin_dir_settings(
        str(FIXTURES_ROOT),
        allowlist=["com.test.minimal"],
    )
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    MeterRegistry._initialized = True
    MeterRegistry._enabled = True
    MetricInstruments.initialize()
    return reader


class TestPluginLoadSpans:
    def test_successful_load_emits_plugin_load_span(
        self,
        memory_exporter: InMemorySpanExporter,
    ) -> None:
        _, registry, _, _ = load_plugins(
            _plugin_dir_settings(str(FIXTURES_ROOT), allowlist=["com.test.minimal"]),
        )
        record = registry.get("com.test.minimal")
        assert record is not None
        assert record.status == PluginStatus.LOADED

        span = _find_load_span(
            memory_exporter.get_finished_spans(),
            plugin_id="com.test.minimal",
        )
        assert span is not None
        attributes = _span_attributes(span)
        assert attributes["plugin_id"] == "com.test.minimal"
        assert attributes["status"] == "loaded"
        assert attributes["load_duration_ms"] >= 0
        assert "failure_code" not in attributes

    def test_failed_load_emits_plugin_load_span_with_failure_code(
        self,
        memory_exporter: InMemorySpanExporter,
    ) -> None:
        _, registry, _, _ = load_plugins(
            _plugin_dir_settings(
                str(FIXTURES_ROOT),
                allowlist=["com.test.unsupported"],
            ),
        )
        record = registry.get("com.test.unsupported")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "unsupported_api_version"

        span = _find_load_span(
            memory_exporter.get_finished_spans(),
            plugin_id="com.test.unsupported",
        )
        assert span is not None
        attributes = _span_attributes(span)
        assert attributes["plugin_id"] == "com.test.unsupported"
        assert attributes["status"] == "failed"
        assert attributes["failure_code"] == "unsupported_api_version"
        assert span.status.status_code == StatusCode.ERROR

    def test_tool_plugin_span_includes_contribution_kinds(
        self,
        memory_exporter: InMemorySpanExporter,
    ) -> None:
        _, registry, _, _ = load_plugins(
            _plugin_dir_settings(str(FIXTURES_ROOT), allowlist=["com.test.tool"]),
        )
        record = registry.get("com.test.tool")
        assert record is not None
        assert record.status == PluginStatus.LOADED

        span = _find_load_span(
            memory_exporter.get_finished_spans(),
            plugin_id="com.test.tool",
        )
        assert span is not None
        attributes = _span_attributes(span)
        assert attributes["contribution_kind"] == "tool"

    def test_observability_disabled_emits_no_plugin_spans(self) -> None:
        exporter = InMemorySpanExporter()
        settings = plugin_settings(
            directories=[str(FIXTURES_ROOT)],
            allowlist=["com.test.minimal"],
        ).model_copy(update={"observability_enabled": False})
        TracerRegistry.initialize(
            settings,
            extra_span_processors=[SimpleSpanProcessor(exporter)],
        )
        load_plugins(settings)
        assert [
            span for span in exporter.get_finished_spans() if span.name == "plugin.load"
        ] == []


class TestPluginLoadMetrics:
    def test_successful_load_increments_plugins_loaded_total(
        self,
        memory_exporter: InMemorySpanExporter,
        metric_reader: InMemoryMetricReader,
    ) -> None:
        settings = _plugin_dir_settings(
            str(FIXTURES_ROOT), allowlist=["com.test.minimal"]
        )
        TracerRegistry.initialize(
            settings,
            extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
        )
        load_plugins(settings)

        points = _metric_data_points(metric_reader, "plugins_loaded_total")
        none_points = [
            point
            for point in points
            if (point.attributes or {}).get("failure_code") == "none"
        ]
        assert len(none_points) == 1
        assert none_points[0].value == 1

    def test_failed_load_increments_plugin_load_failures_total(
        self,
        memory_exporter: InMemorySpanExporter,
        metric_reader: InMemoryMetricReader,
    ) -> None:
        settings = _plugin_dir_settings(
            str(FIXTURES_ROOT),
            allowlist=["com.test.unsupported"],
        )
        TracerRegistry.initialize(
            settings,
            extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
        )
        load_plugins(settings)

        points = _metric_data_points(metric_reader, "plugin_load_failures_total")
        failure_points = [
            point
            for point in points
            if (point.attributes or {}).get("failure_code") == "unsupported_api_version"
        ]
        assert len(failure_points) == 1
        assert failure_points[0].value == 1
        assert "unsupported_api_version" in FAILURE_CODE_REGISTRY

    def test_manifest_not_found_failure_code(
        self,
        memory_exporter: InMemorySpanExporter,
        metric_reader: InMemoryMetricReader,
        tmp_path,
    ) -> None:
        (tmp_path / "empty-plugin").mkdir()
        settings = _plugin_dir_settings(str(tmp_path))
        TracerRegistry.initialize(
            settings,
            extra_span_processors=[SimpleSpanProcessor(memory_exporter)],
        )
        load_plugins(settings)

        points = _metric_data_points(metric_reader, "plugin_load_failures_total")
        failure_points = [
            point
            for point in points
            if (point.attributes or {}).get("failure_code") == "manifest_not_found"
        ]
        assert len(failure_points) == 1

    def test_metrics_disabled_when_observability_off(self) -> None:
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)
        MeterRegistry._initialized = True
        MeterRegistry._enabled = False
        MetricInstruments.initialize()

        settings = plugin_settings(
            directories=[str(FIXTURES_ROOT)],
            allowlist=["com.test.minimal"],
        ).model_copy(update={"observability_enabled": False})
        load_plugins(settings)

        data = reader.get_metrics_data()
        assert data is None or not any(
            metric.name in {"plugins_loaded_total", "plugin_load_failures_total"}
            for rm in data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        )
