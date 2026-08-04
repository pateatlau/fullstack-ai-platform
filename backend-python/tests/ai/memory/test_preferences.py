"""Tests for user preference validation and normalization (Phase 4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.memory.models import UserPreferenceItem, UserPreferenceUpsert
from app.ai.memory.preferences import (
    normalize_preferences,
    validate_preference_key,
    validate_preference_value,
)


class TestPreferenceValidation:
    def test_validate_preference_key_accepts_snake_case(self) -> None:
        assert validate_preference_key("response_tone") == "response_tone"
        assert validate_preference_key("preferred_units") == "preferred_units"

    def test_validate_preference_key_strips_whitespace(self) -> None:
        assert validate_preference_key("  response_tone  ") == "response_tone"

    @pytest.mark.parametrize(
        "key",
        ["", "ResponseTone", "response-tone", "1tone", "tone space"],
    )
    def test_validate_preference_key_rejects_invalid_values(self, key: str) -> None:
        with pytest.raises(ValueError):
            validate_preference_key(key)

    def test_validate_preference_value_requires_object(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            validate_preference_value("concise")

        assert validate_preference_value({"tone": "concise"}) == {"tone": "concise"}


class TestPreferenceNormalization:
    def test_normalize_preferences_sorts_keys_deterministically(self) -> None:
        raw: dict[str, dict[str, object]] = {
            "response_tone": {"tone": "concise"},
            "preferred_units": {"system": "metric"},
        }

        normalized = normalize_preferences(raw)

        assert list(normalized.keys()) == ["preferred_units", "response_tone"]
        assert normalized["response_tone"] == {"tone": "concise"}


class TestPreferenceModels:
    def test_user_preference_upsert_validates_value(self) -> None:
        item = UserPreferenceUpsert(value={"tone": "concise"})
        assert item.value == {"tone": "concise"}

        with pytest.raises(ValidationError):
            UserPreferenceUpsert(value="concise")  # type: ignore[arg-type]

    def test_user_preference_item_validates_key_and_value(self) -> None:
        item = UserPreferenceItem(key="response_tone", value={"tone": "concise"})
        assert item.key == "response_tone"

        with pytest.raises(ValidationError):
            UserPreferenceItem(key="Bad Key", value={"tone": "concise"})
