"""Pure text helpers shared across services."""

from __future__ import annotations

import re


def derive_session_title(content: str, *, max_length: int = 50) -> str | None:
    """First-line, whitespace-normalized, truncated title.

    Returns ``None`` when the first line is empty or whitespace-only after
    normalization.
    """
    first_line = re.split(r"\r\n|\r|\n", content, maxsplit=1)[0]
    normalized = re.sub(r"\s+", " ", first_line).strip()
    if not normalized:
        return None
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length]
