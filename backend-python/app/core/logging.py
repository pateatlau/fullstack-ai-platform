"""Structured logging with environment-aware formatters and redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, TypedDict

from app.core.config import Settings

LogContext = TypedDict(
    "LogContext",
    {
        "request_id": str,
        "user_id": str,
        "route": str,
        "method": str,
        "status_code": int,
        "latency_ms": int,
        "provider": str,
        "model": str,
    },
    total=False,
)

_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("_log_context")

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|token|password|authorization|id_token|jwt|credential)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

_STANDARD_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}

_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41m",
}
_RESET_COLOR = "\033[0m"

_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")


def bind_context(**kwargs: Any) -> None:
    """Merge request-scoped fields into the current logging context."""
    current = get_log_context()
    for key, value in kwargs.items():
        if value is not None:
            current[key] = value
    _LOG_CONTEXT.set(current)


def clear_context() -> None:
    _LOG_CONTEXT.set({})


def get_log_context() -> dict[str, Any]:
    try:
        return dict(_LOG_CONTEXT.get())
    except LookupError:
        return {}


def sanitize_value(key: str, value: Any) -> Any:
    """Redact sensitive values before they reach log output."""
    if _SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"

    if key in {"content", "message_content", "messages", "prompt", "body"}:
        return "[REDACTED]"

    if isinstance(value, str):
        return sanitize_message(value)

    if isinstance(value, (list, tuple)):
        return [sanitize_value(key, item) for item in value]

    if isinstance(value, dict):
        return {
            item_key: sanitize_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }

    return value


def sanitize_message(message: str) -> str:
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", message)
    redacted = _API_KEY_PATTERN.sub("sk-[REDACTED]", redacted)
    redacted = _JWT_PATTERN.sub("[REDACTED-JWT]", redacted)
    return redacted


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key not in _STANDARD_RECORD_ATTRS:
            extras[key] = sanitize_value(key, value)
    return extras


def _format_record_exception(
    formatter: logging.Formatter, record: logging.LogRecord
) -> str | None:
    """Return formatted traceback text from ``exc_info`` or precomputed ``exc_text``."""
    if record.exc_info:
        return formatter.formatException(record.exc_info)
    if record.exc_text:
        return record.exc_text
    return None


def _build_extra(fields: dict[str, Any]) -> dict[str, Any]:
    merged = {**get_log_context(), **fields}
    return {
        key: sanitize_value(key, value)
        for key, value in merged.items()
        if value is not None
    }


class ContextInjectFilter(logging.Filter):
    """Attach contextvars fields to records that do not already define them."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in get_log_context().items():
            if not hasattr(record, key):
                setattr(record, key, sanitize_value(key, value))
        return True


class JsonFormatter(logging.Formatter):
    """Production JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "message": sanitize_message(record.getMessage()),
            "logger": record.name,
        }
        payload.update(_record_extras(record))
        exc_text = _format_record_exception(self, record)
        if exc_text:
            payload["exception"] = sanitize_message(exc_text)
        return json.dumps(payload, default=str)


class DevelopmentFormatter(logging.Formatter):
    """Human-readable single-line formatter for local development."""

    def __init__(self, *, use_color: bool = False) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        level = record.levelname
        if self._use_color:
            color = _LEVEL_COLORS.get(level, "")
            level = f"{color}{level}{_RESET_COLOR}"

        extras = _record_extras(record)
        extra_suffix = (
            " ".join(f"{key}={value}" for key, value in sorted(extras.items()))
            if extras
            else ""
        )
        message = sanitize_message(record.getMessage())
        parts = [timestamp, level, record.name, message]
        if extra_suffix:
            parts.append(extra_suffix)
        line = " ".join(parts)
        exc_text = _format_record_exception(self, record)
        if exc_text:
            line = f"{line}\n{sanitize_message(exc_text)}"
        return line


class StructuredLogger:
    """Logger wrapper that merges contextvars and structured fields into records."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(
        self,
        level: int,
        message: str,
        *args: object,
        exc_info: bool | BaseException | tuple[Any, ...] | None = None,
        **fields: object,
    ) -> None:
        formatted = message % args if args else message
        self._logger.log(
            level,
            sanitize_message(formatted),
            extra=_build_extra(fields),
            exc_info=exc_info,
        )

    def debug(self, message: str, *args: object, **fields: object) -> None:
        self._log(logging.DEBUG, message, *args, exc_info=None, **fields)

    def info(self, message: str, *args: object, **fields: object) -> None:
        self._log(logging.INFO, message, *args, exc_info=None, **fields)

    def warning(
        self,
        message: str,
        *args: object,
        exc_info: bool | BaseException | tuple[Any, ...] | None = None,
        **fields: object,
    ) -> None:
        self._log(logging.WARNING, message, *args, exc_info=exc_info, **fields)

    def error(
        self,
        message: str,
        *args: object,
        exc_info: bool | BaseException | tuple[Any, ...] | None = None,
        **fields: object,
    ) -> None:
        self._log(logging.ERROR, message, *args, exc_info=exc_info, **fields)

    def exception(self, message: str, *args: object, **fields: object) -> None:
        self._log(logging.ERROR, message, *args, exc_info=True, **fields)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)


def setup_logging(
    settings: Settings,
    *,
    handler: logging.Handler | None = None,
) -> None:
    """Configure the root logger for the current environment."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.log_level))

    stream_handler = handler or logging.StreamHandler(sys.stdout)
    if settings.is_development:
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        stream_handler.setFormatter(DevelopmentFormatter(use_color=use_color))
    else:
        stream_handler.setFormatter(JsonFormatter())

    stream_handler.addFilter(ContextInjectFilter())
    stream_handler.setLevel(getattr(logging, settings.log_level))
    root.addHandler(stream_handler)

    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
