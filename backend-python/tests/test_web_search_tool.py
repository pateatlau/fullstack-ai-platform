"""Web search tool handler tests."""

from __future__ import annotations

import logging

import httpx
import pytest
from pytest import MonkeyPatch

from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_DEFINITION,
    WebSearchResult,
    WebSearchToolHandler,
)
from app.ai.tools.schemas import ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.provider_error_assertions import assert_no_provider_sdk_leakage

pytestmark = pytest.mark.anyio


class FakeWebSearchClient:
    def __init__(
        self,
        *,
        results: list[WebSearchResult] | None = None,
        error: Exception | None = None,
        attempts: list[str] | None = None,
    ) -> None:
        self._results = results or []
        self._error = error
        self.attempts = attempts if attempts is not None else []

    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        self.attempts.append(query)
        if self._error is not None:
            raise self._error
        del max_results
        return self._results


@pytest.fixture
def user_context() -> ToolExecutionContext:
    import uuid

    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-search-1",
    )


async def test_web_search_returns_normalized_results(
    user_context: ToolExecutionContext,
) -> None:
    client = FakeWebSearchClient(
        results=[
            WebSearchResult(
                title="Example",
                url="https://example.com",
                snippet="Example snippet",
            )
        ]
    )
    handler = WebSearchToolHandler(
        client=client,
        settings=Settings(web_search_max_results=5),
    )

    result = await handler.execute({"query": "latest news"}, user_context)

    assert result.success is True
    assert result.data == {
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "snippet": "Example snippet",
            }
        ]
    }


async def test_search_retry_on_429(monkeypatch: MonkeyPatch) -> None:
    attempts = {"count": 0}

    class FlakyTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            del request
            attempts["count"] += 1
            if attempts["count"] < 2:
                return httpx.Response(429, json={"error": "rate limited"})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Retry OK",
                            "url": "https://retry.example",
                            "content": "after retry",
                        }
                    ]
                },
            )

    async def sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.core.retry.asyncio.sleep", sleep)

    transport = FlakyTransport()

    async def _request() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": "test-key", "query": "retry me", "max_results": 3},
            )
            response.raise_for_status()
            return response

    from app.core.retry import retry_async

    response = await retry_async(_request)
    from app.ai.tools.implementations.web_search import _normalize_tavily_results

    results = _normalize_tavily_results(response.json())

    assert attempts["count"] == 2
    assert results[0].title == "Retry OK"


async def test_search_provider_failure_returns_normalized_error(
    user_context: ToolExecutionContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.ai.tools.implementations.web_search")
    client = FakeWebSearchClient(error=RuntimeError("provider down"))
    handler = WebSearchToolHandler(
        client=client,
        settings=Settings(web_search_max_results=5),
    )

    result = await handler.execute({"query": "secret-query"}, user_context)

    assert result.success is False
    assert result.error_code == "provider_error"
    assert result.error is not None
    assert_no_provider_sdk_leakage(result.error)
    assert "secret-query" not in caplog.text


async def test_search_latency_ms_emitted(
    user_context: ToolExecutionContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.tools.implementations.web_search")
    client = FakeWebSearchClient(
        results=[WebSearchResult(title="T", url="https://t.example", snippet="s")]
    )
    handler = WebSearchToolHandler(
        client=client,
        settings=Settings(web_search_max_results=5),
    )

    await handler.execute({"query": "latency test"}, user_context)

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.tools.implementations.web_search"
        and "Web search completed" in record.message
    ]
    assert len(records) == 1
    assert getattr(records[0], "search_latency_ms") is not None
    assert "latency test" not in caplog.text


def test_web_search_definition_schema() -> None:
    assert WEB_SEARCH_TOOL_DEFINITION.name == "web_search"
    assert "query" in WEB_SEARCH_TOOL_DEFINITION.parameters["properties"]
