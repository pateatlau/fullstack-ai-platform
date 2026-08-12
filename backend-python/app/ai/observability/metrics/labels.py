"""Metric label registries and normalization (Part I § Metric Cardinality Policy)."""

from __future__ import annotations

ALLOWED_LABEL_KEYS = frozenset(
    {
        "provider",
        "model",
        "tool_name",
        "workflow_type",
        "node_type",
        "status",
        "failure_code",
        "kind",
        "decision",
        "job_type",
        "outcome",
    }
)

FORBIDDEN_LABEL_KEYS = frozenset(
    {
        "user_id",
        "guest_id",
        "session_id",
        "request_id",
        "trace_id",
        "span_id",
        "workflow_run_id",
        "workflow_node_id",
        "message_id",
        "job_id",
    }
)

PROVIDER_REGISTRY = frozenset({"openai", "gemini", "groq", "anthropic", "other"})
TOOL_NAME_REGISTRY = frozenset({"web_search", "workflow_execution", "other"})
NODE_TYPE_REGISTRY = frozenset(
    {
        "task",
        "llm",
        "agent",
        "router",
        "fork",
        "join",
        "approval",
        "terminal",
        "other",
    }
)
WORKFLOW_TYPE_REGISTRY = frozenset({"standard", "other"})
STATUS_REGISTRY = frozenset({"succeeded", "failed", "skipped", "other"})
FAILURE_CODE_REGISTRY = frozenset(
    {
        "none",
        "manifest_not_found",
        "invalid_manifest",
        "unsupported_api_version",
        "entrypoint_import_error",
        "registration_error",
        "timeout",
        "allowlist_excluded",
        "other",
    }
)
APPROVAL_KIND_REGISTRY = frozenset({"agent_tool", "workflow_node", "other"})
DECISION_REGISTRY = frozenset({"approved", "rejected", "other"})
JOB_TYPE_REGISTRY = frozenset(
    {
        "hitl_approval_expiry_sweep",
        "hitl_orphaned_snapshot_sweep",
        "workflow_run_retention_cleanup",
        "rag_document_indexing",
        "scheduled_evaluation_run",
        "other",
    }
)
OUTCOME_REGISTRY = frozenset({"succeeded", "dead_letter", "other"})

_OTHER = "other"

_model_registry: frozenset[str] = frozenset({_OTHER})


def set_model_registry(models: frozenset[str]) -> None:
    """Bind priced model keys for ``normalize_metric_label('model', ...)``."""
    global _model_registry
    _model_registry = frozenset(models) | {_OTHER}


def reset_model_registry_for_tests() -> None:
    global _model_registry
    _model_registry = frozenset({_OTHER})


def normalize_metric_label(dimension: str, raw_value: str | None) -> str:
    """Map a raw label value to a bounded registry member (``other`` fallback)."""
    if dimension not in ALLOWED_LABEL_KEYS:
        raise ValueError(f"Unsupported metric label dimension: {dimension!r}")

    if raw_value is None or not str(raw_value).strip():
        return _OTHER

    value = str(raw_value).strip()

    if dimension == "provider":
        return value if value in PROVIDER_REGISTRY else _OTHER
    if dimension == "model":
        return value if value in _model_registry else _OTHER
    if dimension == "tool_name":
        return value if value in TOOL_NAME_REGISTRY else _OTHER
    if dimension == "node_type":
        return value if value in NODE_TYPE_REGISTRY else _OTHER
    if dimension == "workflow_type":
        return value if value in WORKFLOW_TYPE_REGISTRY else _OTHER
    if dimension == "status":
        return _normalize_status(value)
    if dimension == "failure_code":
        return value if value in FAILURE_CODE_REGISTRY else _OTHER
    if dimension == "kind":
        return value if value in APPROVAL_KIND_REGISTRY else _OTHER
    if dimension == "decision":
        return value if value in DECISION_REGISTRY else _OTHER
    if dimension == "job_type":
        return value if value in JOB_TYPE_REGISTRY else _OTHER
    if dimension == "outcome":
        return value if value in OUTCOME_REGISTRY else _OTHER
    return _OTHER


def _normalize_status(raw_status: str) -> str:
    lowered = raw_status.lower()
    if lowered in {"succeeded", "completed", "success", "ok", "stop"}:
        return "succeeded"
    if lowered in {"failed", "error", "cancelled", "canceled", "rejected"}:
        return "failed"
    if lowered == "skipped":
        return "skipped"
    if lowered in STATUS_REGISTRY:
        return lowered
    return _OTHER


def build_metric_attributes(**raw_labels: str | None) -> dict[str, str]:
    """Return normalized label attributes, rejecting forbidden keys."""
    attributes: dict[str, str] = {}
    for key, raw_value in raw_labels.items():
        if key in FORBIDDEN_LABEL_KEYS:
            raise ValueError(f"Forbidden metric label key: {key!r}")
        if key not in ALLOWED_LABEL_KEYS:
            raise ValueError(f"Disallowed metric label key: {key!r}")
        attributes[key] = normalize_metric_label(key, raw_value)
    return attributes
