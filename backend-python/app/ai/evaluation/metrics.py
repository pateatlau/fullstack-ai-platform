"""Evaluation metric helpers for prompt, retrieval, and end-to-end levels."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Literal

AnswerMatchMode = Literal["exact", "contains", "fuzzy"]

# Performance soft targets from the V1 plan (milliseconds).
TARGET_RETRIEVAL_MS = 150
TARGET_VECTOR_SEARCH_MS = 100
TARGET_RAG_RESPONSE_MS = 8000

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def precision(retrieved_ids: set[uuid.UUID], relevant_ids: set[uuid.UUID]) -> float:
    """Relevant retrieved / total retrieved.

    Empty-set convention: returns 1.0 when both sets are empty; 0.0 when retrieved
    is empty but relevant is not; 0.0 when relevant is empty but retrieved is not.
    """
    if not retrieved_ids:
        return 1.0 if not relevant_ids else 0.0
    if not relevant_ids:
        return 0.0
    intersection = retrieved_ids & relevant_ids
    return len(intersection) / len(retrieved_ids)


def recall(retrieved_ids: set[uuid.UUID], relevant_ids: set[uuid.UUID]) -> float:
    """Relevant retrieved / total relevant.

    Empty-set convention: returns 1.0 when both sets are empty; 0.0 when relevant
    is empty but retrieved is not; 0.0 when retrieved is empty but relevant is not.
    """
    if not relevant_ids:
        return 1.0 if not retrieved_ids else 0.0
    if not retrieved_ids:
        return 0.0
    intersection = retrieved_ids & relevant_ids
    return len(intersection) / len(relevant_ids)


def latency_within_target(stage: str, ms: int, target_ms: int) -> bool:
    """Return True when latency is within the plan soft target for ``stage``."""
    del stage
    return ms <= target_ms


def answer_matches(
    actual: str,
    expected: str,
    mode: AnswerMatchMode = "contains",
) -> bool:
    """Compare an answer to ground truth using exact, contains, or fuzzy matching."""
    normalized_actual = _normalize_text(actual)
    normalized_expected = _normalize_text(expected)
    if mode == "exact":
        return normalized_actual == normalized_expected
    if mode == "contains":
        return normalized_expected in normalized_actual
    ratio = SequenceMatcher(None, normalized_actual, normalized_expected).ratio()
    return ratio >= 0.8


def faithfulness_score(
    context: str,
    answer: str,
    judge_fn: Callable[[str, str, str], tuple[bool, bool]] | None = None,
) -> float:
    """Heuristic token overlap by default; optional LLM-as-judge when provided."""
    if judge_fn is not None:
        faithful, _ = judge_fn(context, "", answer)
        return 1.0 if faithful else 0.0

    context_tokens = _significant_tokens(context)
    if not context_tokens:
        return 0.0
    answer_tokens = _significant_tokens(answer)
    if not answer_tokens:
        return 1.0
    overlap = answer_tokens & context_tokens
    return len(overlap) / len(answer_tokens)


def hallucination_detected(
    context: str,
    answer: str,
    judge_fn: Callable[[str, str, str], tuple[bool, bool]] | None = None,
) -> bool:
    """Detect unsupported claims via judge or heuristic token absence from context."""
    if judge_fn is not None:
        _, hallucination = judge_fn(context, "", answer)
        return hallucination

    context_tokens = _significant_tokens(context)
    answer_tokens = _significant_tokens(answer)
    if not answer_tokens:
        return False
    unsupported = answer_tokens - context_tokens
    # Flag when a meaningful share of answer tokens are absent from context.
    return len(unsupported) / len(answer_tokens) > 0.5


def _normalize_text(value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    return normalized.rstrip(".,!?;:")


def _significant_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_PATTERN.findall(value.lower()) if len(token) > 2}
