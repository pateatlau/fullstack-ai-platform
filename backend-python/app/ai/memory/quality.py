"""Deterministic memory quality evaluation (Part I § Quality gate)."""

from __future__ import annotations

import re

from app.ai.memory.extraction import CandidateMemory
from app.ai.memory.models import MemoryRecord
from app.core.config import Settings

_EPHEMERAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bin this (conversation|session|chat|thread|message)\b", re.I),
    re.compile(r"\bright now\b", re.I),
    re.compile(
        r"\bcurrently (discussing|talking|working on|looking at|exploring)\b",
        re.I,
    ),
    re.compile(r"\bat the moment\b", re.I),
    re.compile(r"\bfor now\b", re.I),
    re.compile(r"\bjust (said|mentioned|asked|noted)\b", re.I),
    re.compile(
        r"\bas I (just )?(said|mentioned) (earlier )?(in this|above|before)\b",
        re.I,
    ),
    re.compile(r"\bthis (question|request|query|turn|topic)\b", re.I),
)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity in ``[0.0, 1.0]`` for equal-length vectors."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


def _normalized_content(content: str) -> str:
    return " ".join(content.lower().split())


def _is_ephemeral(content: str) -> bool:
    return any(pattern.search(content) for pattern in _EPHEMERAL_PATTERNS)


def _effective_quality_score(candidate: CandidateMemory) -> float:
    computed = candidate.confidence * 0.6 + candidate.importance * 0.4
    if candidate.quality_score == 0.5 and (
        candidate.confidence != 0.5 or candidate.importance != 0.5
    ):
        return max(0.0, min(1.0, computed))
    return candidate.quality_score


class MemoryQualityEvaluator:
    """Score and filter candidate memories before persistence."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def filter_preliminary(
        self, candidates: list[CandidateMemory]
    ) -> list[CandidateMemory]:
        """Reject low-confidence, low-quality, and session-ephemeral candidates."""
        approved: list[CandidateMemory] = []
        seen_content: set[str] = set()

        for candidate in candidates:
            content_key = _normalized_content(candidate.content)
            if not content_key or content_key in seen_content:
                continue
            if candidate.confidence < self._settings.memory_min_confidence:
                continue
            if (
                _effective_quality_score(candidate)
                < self._settings.memory_min_quality_score
            ):
                continue
            if _is_ephemeral(candidate.content):
                continue
            seen_content.add(content_key)
            approved.append(
                candidate.model_copy(
                    update={"quality_score": _effective_quality_score(candidate)}
                )
            )

        return approved

    def dedupe_by_embedding(
        self,
        candidates: list[CandidateMemory],
        embeddings: list[list[float] | None],
        existing_records: list[MemoryRecord],
    ) -> list[tuple[CandidateMemory, list[float]]]:
        """Reject duplicates via cosine similarity against batch peers and stored records."""
        threshold = self._settings.memory_dedupe_similarity_threshold
        approved: list[tuple[CandidateMemory, list[float]]] = []
        approved_embeddings: list[list[float]] = []

        existing_with_embeddings = [
            record for record in existing_records if record.embedding is not None
        ]

        for candidate, embedding in zip(candidates, embeddings, strict=True):
            if embedding is None:
                continue

            if self._is_duplicate(embedding, approved_embeddings, threshold):
                continue
            if self._is_duplicate(
                embedding,
                [
                    record.embedding
                    for record in existing_with_embeddings
                    if record.embedding
                ],
                threshold,
            ):
                continue

            approved.append((candidate, embedding))
            approved_embeddings.append(embedding)

        return approved

    @staticmethod
    def _is_duplicate(
        embedding: list[float],
        others: list[list[float]],
        threshold: float,
    ) -> bool:
        return any(cosine_similarity(embedding, other) >= threshold for other in others)
