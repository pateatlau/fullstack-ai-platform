"""Postgres full-text search helpers for hybrid retrieval."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func

# Part I hybrid defaults: english config + to_tsvector / plainto_tsquery.
FTS_LANGUAGE = "english"


def plain_tsquery(query: str) -> ColumnElement[Any]:
    """Build a ``plainto_tsquery`` expression for the configured FTS language."""
    return func.plainto_tsquery(FTS_LANGUAGE, query)


def ts_rank(
    tsv_column: Any,
    tsquery: ColumnElement[Any],
) -> ColumnElement[Any]:
    """BM25-ish rank of a ``tsvector`` against a ``tsquery``."""
    return func.ts_rank(tsv_column, tsquery)
