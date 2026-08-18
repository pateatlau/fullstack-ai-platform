"""Typed inputs, rules, and verdicts for governance guardrails."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.security.rules_engine import RuleCondition


class GuardrailContext(BaseModel):
    """One unit of RAG, tool-argument, or MCP-result content to scan."""

    content_text: str
    source: Literal["rag_chunk", "tool_argument", "mcp_result"]
    tool_name: str | None = None
    document_id: str | None = None
    mcp_server: str | None = None
    caller_role: str | None = None

    def resolve_field(self, field: str) -> Any:
        return getattr(self, field, None) if hasattr(self, field) else None


class GuardrailAction(str, enum.Enum):
    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"


class GuardrailRule(BaseModel):
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    description: str | None = None
    created_at: datetime | None = None
    priority: int = 100
    condition: RuleCondition
    action: GuardrailAction


class GuardrailVerdict(BaseModel):
    action: GuardrailAction
    matched_rule_id: str | None = None
    matched_rule_version: int | None = None
    evidence_snippet: str | None = None
