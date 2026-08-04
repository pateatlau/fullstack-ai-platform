"""Package import tests — verify app.ai.workflow has no circular imports."""

from __future__ import annotations

import importlib


def test_package_imports_cleanly() -> None:
    module = importlib.import_module("app.ai.workflow")

    assert hasattr(module, "__all__")
    for name in module.__all__:
        assert hasattr(module, name), f"app.ai.workflow is missing export {name!r}"


def test_public_api_surface_matches_locked_scope() -> None:
    """Part I § Public APIs: stable after Phase 1."""
    module = importlib.import_module("app.ai.workflow")

    assert set(module.__all__) == {
        "ApprovalDecision",
        "ConditionEvaluator",
        "DefinitionStatus",
        "GraphValidator",
        "NodeRetryPolicy",
        "NodeStatus",
        "NodeType",
        "PostgresWorkflowStore",
        "RunStatus",
        "WorkflowAccessDeniedError",
        "WorkflowContext",
        "WorkflowDefinition",
        "WorkflowEdge",
        "WorkflowError",
        "WorkflowEvent",
        "WorkflowEventType",
        "WorkflowExecutor",
        "WorkflowManager",
        "WorkflowNode",
        "WorkflowNodeExecution",
        "WorkflowNotFoundError",
        "WorkflowRun",
        "WorkflowStore",
        "WorkflowValidationError",
    }


def test_subpackages_import_independently() -> None:
    importlib.import_module("app.ai.workflow.models")
    importlib.import_module("app.ai.workflow.models.definition")
    importlib.import_module("app.ai.workflow.models.run")
    importlib.import_module("app.ai.workflow.models.context")
    importlib.import_module("app.ai.workflow.exceptions")
    importlib.import_module("app.ai.workflow.manager")
    importlib.import_module("app.ai.workflow.interfaces")
    importlib.import_module("app.ai.workflow.interfaces.workflow_store")
    importlib.import_module("app.ai.workflow.providers")
    importlib.import_module("app.ai.workflow.providers.postgres")
    importlib.import_module("app.ai.workflow.events")
    importlib.import_module("app.ai.workflow.graph.validator")
    importlib.import_module("app.ai.workflow.conditions.evaluator")
    importlib.import_module("app.ai.workflow.engine.executor")
