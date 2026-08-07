"""Observability domain exceptions (stable public API after Phase 1)."""


class ObservabilityError(Exception):
    """Base error for observability configuration or query failures."""


class ObservabilityConfigError(ObservabilityError):
    """Raised when observability configuration is invalid at startup."""


class ObservabilityDisabledError(ObservabilityError):
    """Raised when an observability-only operation is invoked while disabled."""
