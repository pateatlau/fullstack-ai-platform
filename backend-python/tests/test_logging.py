"""Structured logging formatter, context, and redaction tests."""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from app.core.config import Settings
from app.core.logging import (
    DevelopmentFormatter,
    JsonFormatter,
    StructuredLogger,
    bind_context,
    clear_context,
    get_logger,
    sanitize_message,
    sanitize_value,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging_context():  # pyright: ignore[reportUnusedFunction]
    clear_context()
    yield
    clear_context()


def _make_record(
    *,
    message: str = "hello",
    level: int = logging.INFO,
    name: str = "test.logger",
    **extras: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_required_fields() -> None:
    formatter = JsonFormatter()
    record = _make_record(
        message="Chat completion",
        provider="openai",
        model="gpt-4o-mini",
        latency_ms=42,
    )

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["message"] == "Chat completion"
    assert payload["logger"] == "test.logger"
    assert "timestamp" in payload
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["latency_ms"] == 42


def test_development_formatter_emits_readable_single_line() -> None:
    formatter = DevelopmentFormatter(use_color=False)
    record = _make_record(
        message="Request completed",
        route="/api/chat",
        method="POST",
        status_code=200,
        latency_ms=15,
    )

    line = formatter.format(record)

    assert "Request completed" in line
    assert "route=/api/chat" in line
    assert "method=POST" in line
    assert "status_code=200" in line
    assert "latency_ms=15" in line
    assert line.count("\n") == 0


def test_json_formatter_includes_exception_from_exc_info() -> None:
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    record = _make_record(message="Request failed")
    record.exc_info = exc_info

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "Request failed"
    assert "exception" in payload
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


def test_development_formatter_includes_exception_from_exc_info() -> None:
    formatter = DevelopmentFormatter(use_color=False)
    try:
        raise RuntimeError("dev details")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = _make_record(message="Unhandled error")
    record.exc_info = exc_info

    output = formatter.format(record)

    assert output.startswith("20")
    assert "Unhandled error" in output
    assert "RuntimeError" in output
    assert "dev details" in output
    assert output.count("\n") >= 1


def test_structured_logger_exception_emits_trace_in_json_logs() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(app_env="production", log_level="ERROR")
    setup_logging(settings, handler=handler)

    try:
        raise ValueError("logged failure")
    except ValueError:
        get_logger("test.exception").exception("Operation failed")

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "Operation failed"
    assert "exception" in payload
    assert "ValueError" in payload["exception"]
    assert "logged failure" in payload["exception"]


def test_log_level_filtering() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(app_env="production", log_level="WARNING")

    setup_logging(settings, handler=handler)
    logger = get_logger("test.filter")

    logger.info("hidden")
    logger.warning("visible")

    output = stream.getvalue()
    assert "hidden" not in output
    assert "visible" in output


def test_sanitize_message_redacts_secrets() -> None:
    raw = (
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig "
        "and key sk-live-secret-value"
    )
    sanitized = sanitize_message(raw)

    assert "eyJhbGci" not in sanitized
    assert "sk-live-secret-value" not in sanitized
    assert "Bearer [REDACTED]" in sanitized
    assert "sk-[REDACTED]" in sanitized


def test_sanitize_value_redacts_sensitive_keys_and_message_content() -> None:
    assert sanitize_value("openai_api_key", "sk-secret") == "[REDACTED]"
    assert sanitize_value("id_token", "token-value") == "[REDACTED]"
    assert sanitize_value("content", "full message body") == "[REDACTED]"


def test_bind_context_merges_into_structured_logs() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(app_env="production", log_level="INFO")
    setup_logging(settings, handler=handler)

    bind_context(request_id="req-123", route="/api/chat")
    get_logger("test.context").info(
        "Chat completion", provider="groq", model="test-model"
    )

    payload = json.loads(stream.getvalue().strip())
    assert payload["request_id"] == "req-123"
    assert payload["route"] == "/api/chat"
    assert payload["provider"] == "groq"
    assert payload["model"] == "test-model"


def test_structured_logger_does_not_log_raw_tokens_in_fields() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    settings = Settings(app_env="production", log_level="INFO")
    setup_logging(settings, handler=handler)

    StructuredLogger("test.redaction").info(
        "Auth attempt",
        id_token="super-secret-google-token",
        jwt="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
    )

    payload = json.loads(stream.getvalue().strip())
    assert payload["id_token"] == "[REDACTED]"
    assert payload["jwt"] == "[REDACTED]"
