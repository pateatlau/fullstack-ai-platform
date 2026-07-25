"""Tests for prompt time-context helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from app.ai.prompts.time_context import current_utc_date_label


def test_current_utc_date_label_formats_stable_utc_string() -> None:
    label = current_utc_date_label(
        now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    )
    assert label == "2026-07-25 (Saturday)"


def test_current_utc_date_label_converts_naive_to_utc() -> None:
    label = current_utc_date_label(now=datetime(2026, 7, 25, 12, 0))
    assert label == "2026-07-25 (Saturday)"
