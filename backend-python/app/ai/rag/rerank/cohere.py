"""Cohere ``rerank-v3.5`` adapter via httpx (Phase 6).

HTTP client usage stays in this module. The pipeline depends only on the
:class:`~app.ai.interfaces.reranker.Reranker` Protocol.

On timeout, HTTP, or parse failure: return the pre-rerank candidate list
unchanged (``final_score`` preserved) and log ``rerank_failed`` without
raw query/document text.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Literal

import httpx

from app.ai.rag.schemas import RetrievedCandidate
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)

_DEFAULT_MODEL = "rerank-v3.5"
_DEFAULT_BASE_URL = "https://api.cohere.com"


class CohereReranker:
    """Rerank candidates with Cohere Rerank API v2 over httpx."""

    def __init__(
        self,
        *,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = (settings.cohere_api_key or "").strip()
        self._model = settings.rerank_model or _DEFAULT_MODEL
        self._timeout_ms = settings.rerank_timeout_ms
        self._transport = transport
        self._rerank_url = f"{base_url.rstrip('/')}/v2/rerank"

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedCandidate],
        *,
        top_n: int,
    ) -> list[RetrievedCandidate]:
        if not candidates:
            return []
        if top_n < 1:
            return list(candidates)

        start = time.perf_counter()
        if not self._api_key:
            _log_rerank(start, failed=True, reason="missing_api_key")
            return list(candidates)

        documents = [_document_text(candidate) for candidate in candidates]
        effective_top_n = min(top_n, len(candidates))
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": effective_top_n,
        }
        timeout = httpx.Timeout(self._timeout_ms / 1000.0)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._rerank_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            _log_rerank(start, failed=True, reason="timeout")
            return list(candidates)
        except httpx.HTTPError:
            _log_rerank(start, failed=True, reason="http_error")
            return list(candidates)
        except Exception:
            _log_rerank(start, failed=True, reason="exception")
            return list(candidates)

        reranked = _apply_rerank_results(candidates, data, top_n=effective_top_n)
        if reranked is None:
            _log_rerank(start, failed=True, reason="invalid_response")
            return list(candidates)

        _log_rerank(
            start,
            failed=False,
            result_count=len(reranked),
            input_count=len(candidates),
        )
        return reranked


def _document_text(candidate: RetrievedCandidate) -> str:
    """Prefer expanded parent text; fall back to child/flat chunk content."""
    if candidate.parent is not None and candidate.parent.strip():
        return candidate.parent
    return candidate.chunk.content


def _apply_rerank_results(
    candidates: list[RetrievedCandidate],
    data: object,
    *,
    top_n: int,
) -> list[RetrievedCandidate] | None:
    if not isinstance(data, dict):
        return None
    raw_results = data.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        return None

    reranked: list[RetrievedCandidate] = []
    seen: set[int] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score")
        if not isinstance(index, int) or index < 0 or index >= len(candidates):
            continue
        if index in seen:
            continue
        if not isinstance(score, (int, float)):
            continue
        seen.add(index)
        relevance = float(score)
        reranked.append(
            replace(
                candidates[index],
                rerank_score=relevance,
                final_score=relevance,
            )
        )
        if len(reranked) >= top_n:
            break

    if not reranked:
        return None
    return reranked


def _log_rerank(
    start: float,
    *,
    failed: bool,
    reason: Literal[
        "missing_api_key",
        "timeout",
        "http_error",
        "exception",
        "invalid_response",
    ]
    | None = None,
    result_count: int | None = None,
    input_count: int | None = None,
) -> None:
    latency_ms = int((time.perf_counter() - start) * 1000)
    if failed:
        _logger.warning(
            "Rerank failed; keeping pre-rerank order",
            rerank_latency_ms=latency_ms,
            rerank_failed=True,
            rerank_failure_reason=reason,
        )
    else:
        _logger.info(
            "Rerank completed",
            rerank_latency_ms=latency_ms,
            rerank_failed=False,
            rerank_result_count=result_count,
            rerank_input_count=input_count,
        )
