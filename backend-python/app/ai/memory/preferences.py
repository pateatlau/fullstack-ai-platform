"""User preference validation and normalization (Epic 05 Phase 4)."""

from __future__ import annotations

import re

_PREFERENCE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def validate_preference_key(key: str) -> str:
    """Validate a preference key (snake_case identifier)."""
    normalized = key.strip()
    if not normalized:
        raise ValueError("Preference key must not be empty.")
    if not _PREFERENCE_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Preference key must start with a letter and contain only "
            "lowercase letters, digits, and underscores."
        )
    return normalized


def validate_preference_value(value: object) -> dict[str, object]:
    """Validate a structured preference value (JSON object)."""
    if not isinstance(value, dict):
        raise ValueError("Preference value must be a JSON object.")
    return dict(value)


def normalize_preferences(raw: dict[str, dict[str, object]]) -> dict[str, object]:
    """Return preferences in deterministic key order for ``MemoryContext``."""
    canonical: dict[str, object] = {}
    for key in sorted(raw):
        canonical[key] = validate_preference_value(raw[key])
    return canonical
