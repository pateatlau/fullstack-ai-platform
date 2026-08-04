"""Shared identifier grammar for workflow graph ids and templating paths."""

from __future__ import annotations

import re

#: Single path segment accepted by ``arguments_template`` placeholders and dict keys.
IDENTIFIER_SEGMENT = r"[a-zA-Z0-9_]+"
IDENTIFIER_PATTERN = re.compile(rf"^{IDENTIFIER_SEGMENT}$")


def validate_identifier(value: str, *, field_name: str = "identifier") -> str:
    """Reject ids/keys that cannot be referenced via ``{{dot.path}}`` placeholders."""
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} {value!r} must contain only letters, digits, and underscores."
        )
    return value


def validate_identifier_dict_keys(
    value: dict[str, object], *, path: str = "trigger_input"
) -> dict[str, object]:
    """Recursively validate dict keys used in trigger input / nested objects."""
    for key, item in value.items():
        validate_identifier(key, field_name=f"{path} key")
        if isinstance(item, dict):
            validate_identifier_dict_keys(item, path=f"{path}.{key}")
    return value
