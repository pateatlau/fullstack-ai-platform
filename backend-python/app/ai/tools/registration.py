"""Register production tools on the application-scoped registry."""

from __future__ import annotations

from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_DEFINITION,
    WebSearchClient,
    create_web_search_handler,
)
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings


def register_production_tools(
    registry: ToolRegistry,
    settings: Settings,
    *,
    web_search_client: WebSearchClient | None = None,
) -> None:
    """Register V1 production tools when ``tools_enabled`` is true."""
    if settings.web_search_provider != "tavily":
        raise ValueError(
            f"Unsupported WEB_SEARCH_PROVIDER '{settings.web_search_provider}'. "
            "Supported providers: tavily."
        )

    handler = create_web_search_handler(settings=settings, client=web_search_client)
    registry.register(WEB_SEARCH_TOOL_DEFINITION, handler)
