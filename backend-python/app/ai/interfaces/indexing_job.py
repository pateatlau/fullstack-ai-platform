"""Indexing job protocol for advanced RAG (public API — stable after Phase 1)."""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.rag.schemas import IndexingJobStatus


class IndexingJob(Protocol):
    """Thin async-ingest interface; queue-backed runners deferred to Epic 9."""

    async def submit(self, *, document_id: uuid.UUID, user_id: uuid.UUID) -> str: ...

    async def get_status(self, job_id: str) -> IndexingJobStatus: ...
