"""OTel metric instruments — Epic 06 + LLM/tool/agent equivalents."""

from __future__ import annotations

from typing import Any

from opentelemetry.metrics import Counter, Histogram, UpDownCounter

from app.ai.observability.metrics.labels import (
    ALLOWED_LABEL_KEYS,
    build_metric_attributes,
)
from app.ai.observability.metrics.meter import MeterRegistry, get_meter
from app.core.logging import get_logger

logger = get_logger(__name__)

_INSTRUMENT_LABEL_KEYS: dict[str, frozenset[str]] = {
    "llm_requests_total": frozenset({"provider", "model", "status"}),
    "llm_token_usage": frozenset({"provider", "model"}),
    "llm_cost_usd_total": frozenset({"provider", "model"}),
    "tool_calls_total": frozenset({"tool_name", "status"}),
    "tool_call_latency_ms": frozenset({"tool_name", "status"}),
    "agent_iterations_total": frozenset({"status"}),
    "workflow_runs_started": frozenset({"workflow_type"}),
    "workflow_runs_completed": frozenset({"workflow_type", "status"}),
    "workflow_runs_failed": frozenset({"workflow_type", "status"}),
    "workflow_retry_count": frozenset({"node_type"}),
    "workflow_node_execution_latency_ms": frozenset({"node_type", "status"}),
    "workflow_checkpoint_latency_ms": frozenset({"workflow_type"}),
    "workflow_approval_pending_count": frozenset({"workflow_type"}),
    "workflow_parallel_branch_count": frozenset({"workflow_type"}),
    "plugins_loaded_total": frozenset({"failure_code"}),
    "plugin_load_failures_total": frozenset({"failure_code"}),
    "agent_tool_approval_pending_count": frozenset(),
    "approval_decisions_total": frozenset({"kind", "decision"}),
    "hitl_approval_decision_latency_ms": frozenset({"kind"}),
    "hitl_resume_latency_ms": frozenset({"kind"}),
    "hitl_tool_execution_latency_ms": frozenset({"kind"}),
}


def instrument_label_keys(name: str) -> frozenset[str]:
    """Return the allowlisted label-key subset for a named instrument."""
    return _INSTRUMENT_LABEL_KEYS[name]


def all_instrument_label_keys() -> dict[str, frozenset[str]]:
    return dict(_INSTRUMENT_LABEL_KEYS)


class MetricInstruments:
    """Process-wide OTel counters/histograms for observability metrics."""

    _instance: MetricInstruments | None = None

    def __init__(self) -> None:
        meter = get_meter("app.ai.observability.metrics")
        self.llm_requests_total: Counter = meter.create_counter("llm_requests_total")
        self.llm_token_usage: Histogram = meter.create_histogram(
            "llm_token_usage",
            unit="token",
        )
        self.llm_cost_usd_total: Counter = meter.create_counter(
            "llm_cost_usd_total",
            unit="USD",
        )
        self.tool_calls_total: Counter = meter.create_counter("tool_calls_total")
        self.tool_call_latency_ms: Histogram = meter.create_histogram(
            "tool_call_latency_ms",
            unit="ms",
        )
        self.agent_iterations_total: Counter = meter.create_counter(
            "agent_iterations_total"
        )
        self.workflow_runs_started: Counter = meter.create_counter(
            "workflow_runs_started"
        )
        self.workflow_runs_completed: Counter = meter.create_counter(
            "workflow_runs_completed"
        )
        self.workflow_runs_failed: Counter = meter.create_counter(
            "workflow_runs_failed"
        )
        self.workflow_retry_count: Counter = meter.create_counter(
            "workflow_retry_count"
        )
        self.workflow_node_execution_latency_ms: Histogram = meter.create_histogram(
            "workflow_node_execution_latency_ms",
            unit="ms",
        )
        self.workflow_checkpoint_latency_ms: Histogram = meter.create_histogram(
            "workflow_checkpoint_latency_ms",
            unit="ms",
        )
        self.workflow_approval_pending_count: UpDownCounter = (
            meter.create_up_down_counter("workflow_approval_pending_count")
        )
        self.workflow_parallel_branch_count: Histogram = meter.create_histogram(
            "workflow_parallel_branch_count",
            unit="branch",
        )
        self.plugins_loaded_total: Counter = meter.create_counter(
            "plugins_loaded_total"
        )
        self.plugin_load_failures_total: Counter = meter.create_counter(
            "plugin_load_failures_total"
        )
        self.agent_tool_approval_pending_count: UpDownCounter = (
            meter.create_up_down_counter("agent_tool_approval_pending_count")
        )
        self.approval_decisions_total: Counter = meter.create_counter(
            "approval_decisions_total"
        )
        self.hitl_approval_decision_latency_ms: Histogram = meter.create_histogram(
            "hitl_approval_decision_latency_ms",
            unit="ms",
        )
        self.hitl_resume_latency_ms: Histogram = meter.create_histogram(
            "hitl_resume_latency_ms",
            unit="ms",
        )
        self.hitl_tool_execution_latency_ms: Histogram = meter.create_histogram(
            "hitl_tool_execution_latency_ms",
            unit="ms",
        )

    @classmethod
    def initialize(cls) -> None:
        if not MeterRegistry.is_enabled():
            cls._instance = None
            return
        if cls._instance is None:
            cls._instance = cls()

    @classmethod
    def get(cls) -> MetricInstruments | None:
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._instance = None


def _record(
    operation: str,
    callback: Any,
) -> None:
    if not MeterRegistry.is_enabled():
        return
    try:
        callback()
    except Exception as exc:
        logger.warning(
            "Observability metric recording failed",
            metric=operation,
            error=str(exc),
            exc_info=True,
        )


def record_llm_request_metrics(
    *,
    provider: str,
    model: str,
    succeeded: bool,
    total_tokens: int | None,
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    status = "succeeded" if succeeded else "failed"
    labels = build_metric_attributes(
        provider=provider,
        model=model,
        status=status,
    )

    def _emit() -> None:
        instruments.llm_requests_total.add(1, labels)
        if total_tokens is not None and total_tokens >= 0:
            token_labels = build_metric_attributes(provider=provider, model=model)
            instruments.llm_token_usage.record(total_tokens, token_labels)

    _record("llm_requests_total", _emit)


def record_llm_cost_metric(
    *,
    provider: str,
    model: str,
    cost_usd: float,
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(provider=provider, model=model)

    def _emit() -> None:
        instruments.llm_cost_usd_total.add(cost_usd, labels)

    _record("llm_cost_usd_total", _emit)


def record_tool_call_metrics(
    *,
    tool_name: str,
    success: bool,
    latency_ms: int,
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    status = "succeeded" if success else "failed"
    labels = build_metric_attributes(tool_name=tool_name, status=status)

    def _emit() -> None:
        instruments.tool_calls_total.add(1, labels)
        instruments.tool_call_latency_ms.record(latency_ms, labels)

    _record("tool_calls_total", _emit)


def record_agent_iteration_metric(*, succeeded: bool = True) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    status = "succeeded" if succeeded else "failed"
    labels = build_metric_attributes(status=status)

    def _emit() -> None:
        instruments.agent_iterations_total.add(1, labels)

    _record("agent_iterations_total", _emit)


def record_workflow_run_started(*, workflow_type: str = "standard") -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(workflow_type=workflow_type)

    def _emit() -> None:
        instruments.workflow_runs_started.add(1, labels)

    _record("workflow_runs_started", _emit)


def record_workflow_run_terminal(
    *,
    status: str,
    workflow_type: str = "standard",
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    normalized_status = build_metric_attributes(status=status)["status"]
    labels = build_metric_attributes(
        workflow_type=workflow_type,
        status=normalized_status,
    )

    def _emit() -> None:
        if normalized_status == "succeeded":
            instruments.workflow_runs_completed.add(1, labels)
        elif normalized_status == "failed":
            instruments.workflow_runs_failed.add(1, labels)

    _record("workflow_runs_completed", _emit)


def record_workflow_retry(*, node_type: str) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(node_type=node_type)

    def _emit() -> None:
        instruments.workflow_retry_count.add(1, labels)

    _record("workflow_retry_count", _emit)


def record_workflow_node_execution_metric(
    *,
    node_type: str,
    status: str,
    latency_ms: int,
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(node_type=node_type, status=status)

    def _emit() -> None:
        instruments.workflow_node_execution_latency_ms.record(latency_ms, labels)

    _record("workflow_node_execution_latency_ms", _emit)


def record_workflow_checkpoint_metric(
    *,
    latency_ms: int,
    workflow_type: str = "standard",
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(workflow_type=workflow_type)

    def _emit() -> None:
        instruments.workflow_checkpoint_latency_ms.record(latency_ms, labels)

    _record("workflow_checkpoint_latency_ms", _emit)


def record_workflow_approval_pending_delta(
    delta: int,
    *,
    workflow_type: str = "standard",
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(workflow_type=workflow_type)

    def _emit() -> None:
        instruments.workflow_approval_pending_count.add(delta, labels)

    _record("workflow_approval_pending_count", _emit)


def record_plugin_load_metrics(*, succeeded: bool, failure_code: str | None) -> None:
    """Record plugin load success/failure counters (``failure_code`` label only)."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    raw_code = "none" if succeeded else (failure_code or "other")
    labels = build_metric_attributes(failure_code=raw_code)

    def _emit() -> None:
        if succeeded:
            instruments.plugins_loaded_total.add(1, labels)
        else:
            instruments.plugin_load_failures_total.add(1, labels)

    counter_name = "plugins_loaded_total" if succeeded else "plugin_load_failures_total"
    _record(counter_name, _emit)


def record_workflow_parallel_branch_metric(
    *,
    branch_count: int,
    workflow_type: str = "standard",
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(workflow_type=workflow_type)

    def _emit() -> None:
        instruments.workflow_parallel_branch_count.record(branch_count, labels)

    _record("workflow_parallel_branch_count", _emit)


def record_agent_tool_approval_pending_delta(delta: int) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    def _emit() -> None:
        instruments.agent_tool_approval_pending_count.add(delta)

    _record("agent_tool_approval_pending_count", _emit)


def record_hitl_decision_metrics(
    *,
    kind: str,
    decision: str,
    decision_latency_ms: int,
) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    kind_labels = build_metric_attributes(kind=kind)
    decision_labels = build_metric_attributes(kind=kind, decision=decision)

    def _emit() -> None:
        instruments.approval_decisions_total.add(1, decision_labels)
        instruments.hitl_approval_decision_latency_ms.record(
            decision_latency_ms,
            kind_labels,
        )

    _record("approval_decisions_total", _emit)


def record_hitl_resume_latency_ms(*, kind: str, latency_ms: int) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(kind=kind)

    def _emit() -> None:
        instruments.hitl_resume_latency_ms.record(latency_ms, labels)

    _record("hitl_resume_latency_ms", _emit)


def record_hitl_tool_execution_latency_ms(*, kind: str, latency_ms: int) -> None:
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(kind=kind)

    def _emit() -> None:
        instruments.hitl_tool_execution_latency_ms.record(latency_ms, labels)

    _record("hitl_tool_execution_latency_ms", _emit)


def assert_label_keys_allowlisted() -> None:
    """Validate every instrument's label-key set against the global allowlist."""
    for name, keys in _INSTRUMENT_LABEL_KEYS.items():
        if not keys.issubset(ALLOWED_LABEL_KEYS):
            raise ValueError(
                f"Instrument {name!r} uses disallowed label keys: "
                f"{sorted(keys - ALLOWED_LABEL_KEYS)}"
            )
