"""Runtime time context for prompts that need a grounded "today"."""

from __future__ import annotations

from datetime import datetime, timezone


def current_utc_date_label(*, now: datetime | None = None) -> str:
    """Return a stable UTC date label for LLM system prompts.

    Example: ``2026-07-25 (Saturday)``.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d (%A)")
