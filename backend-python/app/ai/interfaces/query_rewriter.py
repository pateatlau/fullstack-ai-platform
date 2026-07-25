"""Query rewriter protocol for advanced RAG (public API — stable after Phase 1)."""

from __future__ import annotations

import uuid
from typing import Protocol


class QueryRewriter(Protocol):
    """Rewrite a user question into a retrieval query at most once per request."""

    async def rewrite(self, query: str, *, user_id: uuid.UUID) -> str: ...
