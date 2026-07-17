"""Usage recording helpers (plan Section 5.7).

Normalizes provider-reported token usage into a ``UsageRecord``, falling back to
a best-effort estimate when the provider omits usage. The ``token_source`` field
distinguishes exact (``provider_reported``) from approximate (``estimated``)
counts, and this is explicitly non-billing observability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.providers.base import ProviderUsage

TokenSource = Literal["provider_reported", "estimated"]

# Rough heuristic used only when a provider does not report token usage.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class UsageRecord:
    """Normalized usage for one generation, ready to persist as a usage event."""

    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    token_source: TokenSource
    kind: str = "chat"


def estimate_tokens(text: str) -> int:
    """Best-effort token estimate for text (≈4 characters per token)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def build_usage_record(
    *,
    provider: str,
    model: str,
    provider_usage: ProviderUsage | None,
    prompt_text: str,
    completion_text: str,
    kind: str = "chat",
) -> UsageRecord:
    """Prefer provider-reported usage; otherwise estimate (plan Section 5.7)."""
    if provider_usage is not None and (
        provider_usage.prompt_tokens is not None
        or provider_usage.completion_tokens is not None
        or provider_usage.total_tokens is not None
    ):
        prompt_tokens = provider_usage.prompt_tokens
        completion_tokens = provider_usage.completion_tokens
        total_tokens = provider_usage.total_tokens
        if total_tokens is None and (
            prompt_tokens is not None or completion_tokens is not None
        ):
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        return UsageRecord(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            token_source="provider_reported",
            kind=kind,
        )

    prompt_tokens = estimate_tokens(prompt_text)
    completion_tokens = estimate_tokens(completion_text)
    return UsageRecord(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        token_source="estimated",
        kind=kind,
    )
