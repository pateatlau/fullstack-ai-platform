"""Unit tests for Reciprocal Rank Fusion (Epic 02 Phase 4)."""

from __future__ import annotations

import pytest

from app.ai.rag.hybrid.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion


def test_rrf_default_k_is_sixty() -> None:
    assert DEFAULT_RRF_K == 60


def test_rrf_fuses_both_lists_and_orders_by_score() -> None:
    # Shared doc "b" ranks high in both → highest RRF score.
    dense = ["a", "b", "c"]
    lexical = ["b", "d", "a"]

    fused = reciprocal_rank_fusion(
        [dense, lexical],
        key=lambda item: item,
        rrf_k=60,
    )

    assert [item for item, _ in fused] == ["b", "a", "d", "c"]
    scores = {item: score for item, score in fused}
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
    assert scores["d"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63)


def test_rrf_empty_one_side_returns_other_side_order() -> None:
    dense = ["x", "y"]
    fused = reciprocal_rank_fusion(
        [dense, []],
        key=lambda item: item,
        rrf_k=60,
    )
    assert [item for item, _ in fused] == ["x", "y"]
    assert fused[0][1] == pytest.approx(1 / 61)
    assert fused[1][1] == pytest.approx(1 / 62)


def test_rrf_both_empty_returns_empty() -> None:
    assert reciprocal_rank_fusion([[], []], key=lambda item: item) == []


def test_rrf_rejects_invalid_k() -> None:
    with pytest.raises(ValueError, match="rrf_k must be >= 1"):
        reciprocal_rank_fusion([["a"]], key=lambda item: item, rrf_k=0)
