"""Web search tool backed by Tavily (abstracted for test injection)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import httpx

from app.ai.interfaces.tool_handler import ToolHandler
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.retry import retry_async

# Full calendar dates only (YYYY-MM-DD). Bare years must not suppress grounding.
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

_RELATIVE_TIME_QUERY_RE = re.compile(
    r"\b(?P<term>today|tonight|yesterday|tomorrow|latest|current|now|recent|"
    r"recently|this\s+(?:week|month|year)|breaking)\b",
    re.IGNORECASE,
)

# Day offsets from "today" for terms that map to a single calendar date.
_RELATIVE_DAY_OFFSETS: dict[str, int] = {
    "today": 0,
    "tonight": 0,
    "yesterday": -1,
    "tomorrow": 1,
}

_logger = get_logger(__name__)

WEB_SEARCH_TOOL_NAME = "web_search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


class WebSearchClient(Protocol):
    """Minimal search client contract (Tavily or test double)."""

    async def search(
        self, query: str, *, max_results: int
    ) -> list[WebSearchResult]: ...


class TavilySearchClient:
    """HTTP client for the Tavily search API."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
        }

        async def _request() -> httpx.Response:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(TAVILY_SEARCH_URL, json=payload)
                response.raise_for_status()
                return response

        response = await retry_async(_request)
        data = response.json()
        return _normalize_tavily_results(data)


def _normalize_tavily_results(data: object) -> list[WebSearchResult]:
    if not isinstance(data, dict):
        return []
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []

    normalized: list[WebSearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        snippet = item.get("content") or item.get("snippet") or ""
        if isinstance(title, str) and isinstance(url, str):
            normalized.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=str(snippet),
                )
            )
    return normalized


WEB_SEARCH_TOOL_DEFINITION = ToolDefinition(
    name=WEB_SEARCH_TOOL_NAME,
    description=(
        "Search the web for current information, recent events, and facts "
        "not available in the model's training data. For time-sensitive topics, "
        "include specific dates in the query."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Single search query string. Parameter name must be "
                    "'query' (singular), not 'queries'. Prefer concrete dates "
                    "(for example month and year) over relative phrases "
                    "like 'this summer' when recency matters."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


def normalize_web_search_arguments(
    arguments: dict[str, object],
) -> dict[str, object]:
    """Coerce common LLM argument-shape mistakes before schema validation.

    Some models (notably Gemini Flash-Lite) emit ``queries: [str, ...]`` instead
    of ``query: str``, or send whole-number floats for ``max_results``.
    """
    normalized = dict(arguments)

    query = normalized.get("query")
    if not (isinstance(query, str) and query.strip()):
        alias = normalized.pop("queries", None)
        if isinstance(alias, str) and alias.strip():
            normalized["query"] = alias.strip()
        elif isinstance(alias, list):
            parts = [str(item).strip() for item in alias if str(item).strip()]
            if parts:
                # Keep a single query string; join only when the model batched terms.
                normalized["query"] = parts[0] if len(parts) == 1 else " ".join(parts)

    max_results = normalized.get("max_results")
    if isinstance(max_results, float) and max_results.is_integer():
        normalized["max_results"] = int(max_results)

    return normalized


def _reference_utc_date(
    *,
    today_label: str | None = None,
    now: datetime | None = None,
) -> date:
    if today_label is not None:
        iso_token = today_label.split(" ", 1)[0]
        return date.fromisoformat(iso_token)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.date()


def _resolve_relative_term_date(term: str, *, today: date) -> date:
    """Map a relative time word to the calendar day that should ground the query."""
    normalized = " ".join(term.lower().split())
    offset = _RELATIVE_DAY_OFFSETS.get(normalized, 0)
    return today + timedelta(days=offset)


def ground_web_search_query(
    query: str,
    *,
    today_label: str | None = None,
    now: datetime | None = None,
) -> str:
    """Append an explicit calendar date when the query uses relative time words.

    Full ISO dates (``YYYY-MM-DD``) count as already grounded. Bare years do not —
    queries like ``India news today 2026`` still receive a concrete day. Relative
    terms such as ``yesterday`` / ``tomorrow`` resolve to that day's ISO date.
    """
    cleaned = query.strip()
    if not cleaned:
        return cleaned

    # Already includes a full calendar day; bare years alone are not enough.
    if _ISO_DATE_RE.search(cleaned):
        return cleaned

    match = _RELATIVE_TIME_QUERY_RE.search(cleaned)
    if match is None:
        return cleaned

    today = _reference_utc_date(today_label=today_label, now=now)
    resolved = _resolve_relative_term_date(match.group("term"), today=today)
    return f"{cleaned} {resolved.isoformat()}"


class WebSearchToolHandler:
    """Execute web search and return normalized result envelopes."""

    def __init__(
        self,
        *,
        client: WebSearchClient,
        settings: Settings,
    ) -> None:
        self._client = client
        self._settings = settings

    @staticmethod
    def normalize_arguments(arguments: dict[str, object]) -> dict[str, object]:
        return normalize_web_search_arguments(arguments)

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        args = normalize_web_search_arguments(args)
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                success=False,
                error="Search query must be a non-empty string",
                error_code="validation_error",
            )

        grounded_query = ground_web_search_query(query)
        max_results_raw = args.get("max_results")
        max_results = self._settings.web_search_max_results
        if isinstance(max_results_raw, int) and max_results_raw >= 1:
            max_results = min(max_results_raw, self._settings.web_search_max_results)

        start = time.perf_counter()
        try:
            results = await self._client.search(
                grounded_query,
                max_results=max_results,
            )
        except httpx.HTTPStatusError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _logger.warning(
                "Web search provider HTTP error",
                search_latency_ms=latency_ms,
                status_code=exc.response.status_code,
            )
            return ToolResult(
                success=False,
                error="Web search provider returned an error",
                error_code="provider_error",
            )
        except Exception:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _logger.warning(
                "Web search provider failure",
                search_latency_ms=latency_ms,
                exc_info=True,
            )
            return ToolResult(
                success=False,
                error="Web search is temporarily unavailable",
                error_code="provider_error",
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        _logger.info(
            "Web search completed",
            search_latency_ms=latency_ms,
            result_count=len(results),
            query=grounded_query,
        )
        return ToolResult(
            success=True,
            data={
                "results": [
                    {"title": r.title, "url": r.url, "snippet": r.snippet}
                    for r in results
                ]
            },
        )


def create_web_search_handler(
    *,
    settings: Settings,
    client: WebSearchClient | None = None,
) -> ToolHandler:
    """Build a web search handler wired to Tavily unless a client is injected."""
    if client is None:
        if not settings.web_search_api_key:
            raise ValueError("WEB_SEARCH_API_KEY is required for web search")
        client = TavilySearchClient(api_key=settings.web_search_api_key)
    return WebSearchToolHandler(client=client, settings=settings)


def create_tavily_client(settings: Settings) -> TavilySearchClient:
    """Construct the default Tavily client from application settings."""
    if not settings.web_search_api_key:
        raise ValueError("WEB_SEARCH_API_KEY is required for web search")
    return TavilySearchClient(api_key=settings.web_search_api_key)
