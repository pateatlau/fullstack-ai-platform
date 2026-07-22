"""Shared assertions for provider error normalization tests."""

from __future__ import annotations

import json
from typing import Any

PROVIDER_ERROR_FORBIDDEN_SUBSTRINGS = (
    "Groq",
    "Anthropic",
    "OpenAI",
    "Tavily",
    "API key",
    "api_key",
    "sk-",
    "provider exploded",
    "rate limited",
)


def assert_no_provider_sdk_leakage(message: str) -> None:
    lowered = message.lower()
    for forbidden in PROVIDER_ERROR_FORBIDDEN_SUBSTRINGS:
        assert forbidden.lower() not in lowered, (
            f"Response message leaked forbidden substring {forbidden!r}: {message!r}"
        )


def assert_json_error_has_no_sdk_leakage(body: dict[str, Any]) -> None:
    assert_no_provider_sdk_leakage(body["error"]["message"])


def assert_sse_error_frame_has_no_sdk_leakage(response_text: str) -> None:
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if payload.get("type") == "error":
            assert_no_provider_sdk_leakage(payload["message"])
