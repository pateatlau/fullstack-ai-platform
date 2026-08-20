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

_reconciled_pending_count: int | None = None
_reconciled_dead_letter_count: int | None = None


def reset_job_queue_depth_reconciliation_for_tests() -> None:
    """Clear cached DB depth snapshots between tests."""
    global _reconciled_pending_count, _reconciled_dead_letter_count
    _reconciled_pending_count = None
    _reconciled_dead_letter_count = None


def reconcile_job_queue_depth_metrics(
    *,
    pending_count: int,
    dead_letter_count: int,
) -> None:
    """Sync depth UpDownCounters to committed DB queue state."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    global _reconciled_pending_count, _reconciled_dead_letter_count

    def _emit() -> None:
        global _reconciled_pending_count, _reconciled_dead_letter_count
        if _reconciled_pending_count is None:
            if pending_count:
                instruments.jobs_pending_count.add(pending_count)
        elif pending_count != _reconciled_pending_count:
            instruments.jobs_pending_count.add(
                pending_count - _reconciled_pending_count
            )

        if _reconciled_dead_letter_count is None:
            if dead_letter_count:
                instruments.jobs_dead_letter_count.add(dead_letter_count)
        elif dead_letter_count != _reconciled_dead_letter_count:
            instruments.jobs_dead_letter_count.add(
                dead_letter_count - _reconciled_dead_letter_count
            )

        _reconciled_pending_count = pending_count
        _reconciled_dead_letter_count = dead_letter_count

    _record("jobs_pending_count", _emit)


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
    # Business-level HITL metrics (recommendation #7). ``approval_decisions_total``
    # already breaks decisions down by ``decision`` label; these add explicit
    # request/expiry/cancellation counters and a seconds-unit duration
    # histogram so dashboards can query familiar Prometheus-style names.
    "approval_requests_total": frozenset({"kind"}),
    "approval_expired_total": frozenset({"kind"}),
    "approval_cancelled_total": frozenset({"kind"}),
    "approval_duration_seconds": frozenset({"kind", "decision"}),
    # Queue metrics (Epic 10) — infrastructure-level depth/throughput counters.
    "jobs_enqueued_total": frozenset({"job_type"}),
    "jobs_completed_total": frozenset({"job_type", "outcome"}),
    "job_retries_total": frozenset({"job_type"}),
    "jobs_pending_count": frozenset(),
    "jobs_dead_letter_count": frozenset(),
    # Handler metrics (Epic 10) — per-attempt execution duration by handler type.
    "job_duration_ms": frozenset({"job_type"}),
    # Epic 11 Phase 9 — Security & Governance observability.
    "authz_denied_total": frozenset({"permission_key", "resource_type"}),
    "role_assignments_total": frozenset({"role_name", "action"}),
    "guardrail_verdicts_total": frozenset({"source", "action"}),
    "audit_events_total": frozenset({"action", "outcome"}),
    "audit_write_failures_total": frozenset(),
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
        self.approval_requests_total: Counter = meter.create_counter(
            "approval_requests_total"
        )
        self.approval_expired_total: Counter = meter.create_counter(
            "approval_expired_total"
        )
        self.approval_cancelled_total: Counter = meter.create_counter(
            "approval_cancelled_total"
        )
        self.approval_duration_seconds: Histogram = meter.create_histogram(
            "approval_duration_seconds",
            unit="s",
        )
        self.jobs_enqueued_total: Counter = meter.create_counter("jobs_enqueued_total")
        self.jobs_completed_total: Counter = meter.create_counter(
            "jobs_completed_total"
        )
        self.job_retries_total: Counter = meter.create_counter("job_retries_total")
        self.jobs_pending_count: UpDownCounter = meter.create_up_down_counter(
            "jobs_pending_count"
        )
        self.jobs_dead_letter_count: UpDownCounter = meter.create_up_down_counter(
            "jobs_dead_letter_count"
        )
        self.job_duration_ms: Histogram = meter.create_histogram(
            "job_duration_ms",
            unit="ms",
        )
        # Epic 11 Phase 3 stub — full security observability lands in Phase 9.
        self.audit_write_failures_total: Counter = meter.create_counter(
            "audit_write_failures_total"
        )
        # Epic 11 Phase 9 — Security & Governance observability.
        self.authz_denied_total: Counter = meter.create_counter("authz_denied_total")
        self.role_assignments_total: Counter = meter.create_counter(
            "role_assignments_total"
        )
        self.guardrail_verdicts_total: Counter = meter.create_counter(
            "guardrail_verdicts_total"
        )
        self.audit_events_total: Counter = meter.create_counter("audit_events_total")

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
        reset_job_queue_depth_reconciliation_for_tests()


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
        instruments.approval_duration_seconds.record(
            decision_latency_ms / 1000,
            decision_labels,
        )

    _record("approval_decisions_total", _emit)


def record_approval_requested_metric(*, kind: str) -> None:
    """Increment ``approval_requests_total`` when a new approval is created."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(kind=kind)

    def _emit() -> None:
        instruments.approval_requests_total.add(1, labels)

    _record("approval_requests_total", _emit)


def record_approval_expired_metric(*, kind: str) -> None:
    """Increment ``approval_expired_total`` when a pending approval lapses."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(kind=kind)

    def _emit() -> None:
        instruments.approval_expired_total.add(1, labels)

    _record("approval_expired_total", _emit)


def record_approval_cancelled_metric(*, kind: str) -> None:
    """Increment ``approval_cancelled_total`` when a requester withdraws."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(kind=kind)

    def _emit() -> None:
        instruments.approval_cancelled_total.add(1, labels)

    _record("approval_cancelled_total", _emit)


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


def record_job_enqueued(*, job_type: str) -> None:
    """Queue metric: increment enqueue counter (depth reconciled separately)."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(job_type=job_type)

    def _emit() -> None:
        instruments.jobs_enqueued_total.add(1, labels)

    _record("jobs_enqueued_total", _emit)


def record_job_succeeded(*, job_type: str) -> None:
    """Queue metric: record terminal success (depth reconciled separately)."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(job_type=job_type, outcome="succeeded")

    def _emit() -> None:
        instruments.jobs_completed_total.add(1, labels)

    _record("jobs_completed_total", _emit)


def record_job_dead_lettered(*, job_type: str) -> None:
    """Queue metric: record dead-letter terminal outcome (depth reconciled separately)."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(job_type=job_type, outcome="dead_letter")

    def _emit() -> None:
        instruments.jobs_completed_total.add(1, labels)

    _record("jobs_completed_total", _emit)


def record_job_retry(*, job_type: str) -> None:
    """Queue metric: increment retry counter when a failed attempt is re-queued."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(job_type=job_type)

    def _emit() -> None:
        instruments.job_retries_total.add(1, labels)

    _record("job_retries_total", _emit)


def record_job_duration_ms(*, job_type: str, duration_ms: int) -> None:
    """Handler metric: per-attempt handler execution duration by ``job_type``."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(job_type=job_type)

    def _emit() -> None:
        instruments.job_duration_ms.record(duration_ms, labels)

    _record("job_duration_ms", _emit)


def record_audit_write_failure() -> None:
    """Epic 11 Phase 3 stub: count ``AuditLogger.record()`` DB/validation failures."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    def _emit() -> None:
        instruments.audit_write_failures_total.add(1)

    _record("audit_write_failures_total", _emit)


def record_authz_denied(
    *,
    permission_key: str,
    resource_type: str | None = None,
) -> None:
    """Epic 11 Phase 9: authorization denial counter.

    Labels: permission_key (always bounded), resource_type (optional, bounded).
    Never includes actor_user_id (unbounded cardinality).
    """
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(
        permission_key=permission_key,
        **({"resource_type": resource_type} if resource_type else {}),
    )

    def _emit() -> None:
        instruments.authz_denied_total.add(1, labels)

    _record("authz_denied_total", _emit)


def record_role_assignment(
    *,
    role_name: str,
    action: str,  # "assigned" or "revoked"
) -> None:
    """Epic 11 Phase 9: role assignment/revocation counter."""
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(role_name=role_name, action=action)

    def _emit() -> None:
        instruments.role_assignments_total.add(1, labels)

    _record("role_assignments_total", _emit)


def record_guardrail_verdict(
    *,
    source: str,
    action: str,  # "allow", "flag", or "block"
) -> None:
    """Epic 11 Phase 9: guardrail verdict counter.

    Labels: source (rag_chunk, tool_argument, mcp_result), action (allow, flag, block).
    Never includes matched_rule_id (unbounded cardinality).
    """
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(source=source, action=action)

    def _emit() -> None:
        instruments.guardrail_verdicts_total.add(1, labels)

    _record("guardrail_verdicts_total", _emit)


def record_audit_event(
    *,
    action: str,
    outcome: str,
) -> None:
    """Epic 11 Phase 9: audit event counter.

    Labels: action (canonical taxonomy), outcome (succeeded or failed).
    Never includes actor_user_id/audit_event_id (unbounded cardinality).
    """
    instruments = MetricInstruments.get()
    if instruments is None:
        return

    labels = build_metric_attributes(action=action, outcome=outcome)

    def _emit() -> None:
        instruments.audit_events_total.add(1, labels)

    _record("audit_events_total", _emit)


def assert_label_keys_allowlisted() -> None:
    """Validate every instrument's label-key set against the global allowlist."""
    for name, keys in _INSTRUMENT_LABEL_KEYS.items():
        if not keys.issubset(ALLOWED_LABEL_KEYS):
            raise ValueError(
                f"Instrument {name!r} uses disallowed label keys: "
                f"{sorted(keys - ALLOWED_LABEL_KEYS)}"
            )
