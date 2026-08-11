"""Tests for PluginEvalRunner."""

from __future__ import annotations

from pathlib import Path

from unittest.mock import AsyncMock

import pytest

from app.ai.evaluation.datasets import EvalCase, load_dataset
from app.ai.evaluation.runners import PluginEvalRunner, REFERENCE_PLUGINS_ROOT
from app.core.config import Settings

DATASET = Path("tests/data/evaluation/sample.yaml")


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "plugins_enabled": True,
        "workflow_engine_enabled": True,
        "plugin_directories": [str(REFERENCE_PLUGINS_ROOT)],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _plugin_tool_case(**overrides: object) -> EvalCase:
    base = {
        "id": "plugin_tool",
        "level": "plugin",
        "plugin_kind": "tool",
        "plugin_tool_name": "com.example.echo.ping",
        "plugin_tool_arguments": {"message": "hello"},
        "expected_tool_data": {"message": "hello"},
    }
    base.update(overrides)
    return EvalCase(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_plugin_eval_runner_skips_when_plugins_disabled() -> None:
    runner = PluginEvalRunner(settings=_settings(plugins_enabled=False))
    result = await runner.run_case(_plugin_tool_case())

    assert result.skipped is True
    assert result.skip_reason == "PLUGINS_ENABLED=false"


@pytest.mark.anyio
async def test_plugin_eval_runner_tool_case_passes() -> None:
    runner = PluginEvalRunner(settings=_settings())
    result = await runner.run_case(_plugin_tool_case())

    assert result.skipped is False
    assert result.passed is True
    assert result.correctness is True


@pytest.mark.anyio
async def test_plugin_eval_runner_prompt_case_passes() -> None:
    runner = PluginEvalRunner(settings=_settings())
    result = await runner.run_case(
        EvalCase(
            id="plugin_prompt",
            level="plugin",
            plugin_kind="prompt",
            prompt_category="plugin/com.example.echo",
            prompt_name="greeting",
            prompt_version="1",
            prompt_variables={"user_name": "Eval"},
            expected_render_contains=("Hello Eval",),
        )
    )

    assert result.passed is True
    assert result.prompt_version == "1"


@pytest.mark.anyio
async def test_plugin_eval_runner_workflow_skips_without_postgres() -> None:
    runner = PluginEvalRunner(settings=_settings(), session=None)
    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "plugin_echo_workflow_node"
    )
    result = await runner.run_case(workflow_case)

    assert result.skipped is True
    assert "Postgres not available" in (result.skip_reason or "")


@pytest.mark.anyio
async def test_plugin_eval_runner_workflow_skips_when_engine_disabled() -> None:
    session = AsyncMock()
    runner = PluginEvalRunner(
        settings=_settings(workflow_engine_enabled=False),
        session=session,
    )
    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "plugin_echo_workflow_node"
    )
    result = await runner.run_case(workflow_case)

    assert result.skipped is True
    assert result.skip_reason == "WORKFLOW_ENGINE_ENABLED=false"


def test_dataset_parses_plugin_cases() -> None:
    dataset = load_dataset(DATASET)
    plugin_cases = [case for case in dataset.cases if case.level == "plugin"]
    assert len(plugin_cases) == 3
    kinds = {case.plugin_kind for case in plugin_cases}
    assert kinds == {"tool", "prompt", "workflow"}
