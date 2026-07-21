"""Unit tests for evaluation metric helpers."""

from __future__ import annotations

import uuid

from app.ai.evaluation.metrics import (
    TARGET_RAG_RESPONSE_MS,
    TARGET_RETRIEVAL_MS,
    answer_matches,
    faithfulness_score,
    hallucination_detected,
    latency_within_target,
    precision,
    recall,
)


def test_precision_and_recall_known_sets() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    c = uuid.uuid4()
    retrieved = {a, b}
    relevant = {a, c}

    assert precision(retrieved, relevant) == 0.5
    assert recall(retrieved, relevant) == 0.5


def test_empty_set_precision_recall_conventions() -> None:
    empty: set[uuid.UUID] = set()
    one = {uuid.uuid4()}

    assert precision(empty, empty) == 1.0
    assert recall(empty, empty) == 1.0
    assert precision(empty, one) == 0.0
    assert recall(empty, one) == 0.0
    assert precision(one, empty) == 0.0
    assert recall(one, empty) == 0.0


def test_answer_matches_modes() -> None:
    assert answer_matches(
        "Plain text fixture content.", "plain text fixture content", "exact"
    )
    assert answer_matches(
        "The answer mentions plain text fixture content here.",
        "plain text fixture content",
        "contains",
    )
    assert answer_matches(
        "plain text fixture contents",
        "plain text fixture content",
        "fuzzy",
    )
    assert not answer_matches("unrelated", "plain text fixture content", "contains")


def test_faithfulness_and_hallucination_heuristics() -> None:
    context = "Plain text fixture content."
    faithful_answer = "It contains plain text fixture content."
    hallucinated_answer = "It contains classified satellite telemetry."

    assert faithfulness_score(context, faithful_answer) >= 0.5
    assert not hallucination_detected(context, faithful_answer)
    assert hallucination_detected(context, hallucinated_answer)


def test_latency_within_target() -> None:
    assert latency_within_target("retrieval", 120, TARGET_RETRIEVAL_MS)
    assert not latency_within_target(
        "e2e", TARGET_RAG_RESPONSE_MS + 1, TARGET_RAG_RESPONSE_MS
    )
