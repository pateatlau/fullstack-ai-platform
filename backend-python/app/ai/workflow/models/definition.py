"""Canonical workflow definition models (public API — stable after Phase 1)."""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ai.workflow.models.identifiers import validate_identifier


class NodeType(StrEnum):
    """Supported workflow node types (Part I § Node Types)."""

    TASK = "task"
    LLM = "llm"
    AGENT = "agent"
    ROUTER = "router"
    FORK = "fork"
    JOIN = "join"
    APPROVAL = "approval"
    TERMINAL = "terminal"


class DefinitionStatus(StrEnum):
    """Lifecycle status for a ``WorkflowDefinition``."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class NodeRetryPolicy(BaseModel):
    """Per-node retry configuration (Part I § WorkflowNode)."""

    max_retries: int = Field(default=3, ge=0)
    base_delay_seconds: float = Field(default=1.0, ge=0.0)


class WorkflowNode(BaseModel):
    """A single node in a workflow graph."""

    id: str = Field(min_length=1)
    type: NodeType
    config: dict[str, object] = Field(default_factory=dict)
    retry_policy: NodeRetryPolicy | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_identifier(value, field_name="node id")


class WorkflowEdge(BaseModel):
    """A directed edge between two workflow nodes."""

    id: str = Field(min_length=1)
    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    condition: dict[str, object] | None = None

    @field_validator("from_node_id", "to_node_id")
    @classmethod
    def _validate_node_reference(cls, value: str) -> str:
        return validate_identifier(value, field_name="node id")


class WorkflowDefinition(BaseModel):
    """Canonical workflow graph definition (Part I § Canonical Workflow Representation)."""

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str = Field(min_length=1)
    description: str | None = None
    version: int = Field(default=1, ge=1)
    status: DefinitionStatus = DefinitionStatus.DRAFT
    entry_node_id: str = Field(min_length=1)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @model_validator(mode="after")
    def _validate_graph_references(self) -> WorkflowDefinition:
        node_ids = {node.id for node in self.nodes}
        if self.entry_node_id not in node_ids:
            raise ValueError(
                f"entry_node_id {self.entry_node_id!r} is not present in nodes."
            )

        edge_ids: set[str] = set()
        for edge in self.edges:
            if edge.id in edge_ids:
                raise ValueError(f"Duplicate edge id {edge.id!r}.")
            edge_ids.add(edge.id)
            if edge.from_node_id not in node_ids:
                raise ValueError(
                    f"Edge {edge.id!r} references unknown from_node_id "
                    f"{edge.from_node_id!r}."
                )
            if edge.to_node_id not in node_ids:
                raise ValueError(
                    f"Edge {edge.id!r} references unknown to_node_id "
                    f"{edge.to_node_id!r}."
                )

        seen_node_ids: set[str] = set()
        for node in self.nodes:
            if node.id in seen_node_ids:
                raise ValueError(f"Duplicate node id {node.id!r}.")
            seen_node_ids.add(node.id)

        return self

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank.")
        return stripped

    @field_validator("entry_node_id")
    @classmethod
    def _validate_entry_node_id(cls, value: str) -> str:
        return validate_identifier(value, field_name="entry_node_id")
