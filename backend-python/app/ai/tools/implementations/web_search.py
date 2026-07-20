"""Web search tool backed by Tavily (abstracted for test injection)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.ai.interfaces.tool_handler import ToolHandler
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.retry import retry_async

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
        "not available in the model's training data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["query"],
    },
)


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

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                success=False,
                error="Search query must be a non-empty string",
                error_code="validation_error",
            )

        max_results_raw = args.get("max_results")
        max_results = self._settings.web_search_max_results
        if isinstance(max_results_raw, int) and max_results_raw >= 1:
            max_results = min(max_results_raw, self._settings.web_search_max_results)

        start = time.perf_counter()
        try:
            results = await self._client.search(
                query.strip(),
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
