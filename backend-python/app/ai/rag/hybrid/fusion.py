"""Reciprocal Rank Fusion (RRF) for hybrid retrieval."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

T = TypeVar("T")

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[T]],
    *,
    key: Callable[[T], Hashable],
    rrf_k: int = DEFAULT_RRF_K,
) -> list[tuple[T, float]]:
    """Fuse ranked lists with Reciprocal Rank Fusion.

    Score for document ``d`` is ``Σ 1 / (rrf_k + rank_i(d))`` over lists where
    ``d`` appears. Ranks are 1-based. Empty lists are ignored. Results are
    sorted by fused score descending; ties keep first-seen item order.
    """
    if rrf_k < 1:
        raise ValueError(f"rrf_k must be >= 1; got {rrf_k}.")

    scores: dict[Hashable, float] = {}
    items: dict[Hashable, T] = {}
    first_seen: dict[Hashable, int] = {}
    order = 0

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            item_key = key(item)
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (rrf_k + rank)
            if item_key not in items:
                items[item_key] = item
                first_seen[item_key] = order
                order += 1

    return sorted(
        ((items[item_key], score) for item_key, score in scores.items()),
        key=lambda pair: (-pair[1], first_seen[key(pair[0])]),
    )
