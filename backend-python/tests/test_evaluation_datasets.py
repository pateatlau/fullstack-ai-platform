"""Tests for evaluation dataset loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.evaluation.datasets import (
    EvalDatasetError,
    load_dataset,
    load_workflow_fixture,
)

DATA_DIR = Path(__file__).resolve().parent / "data" / "evaluation"


def test_load_valid_sample_yaml() -> None:
    dataset = load_dataset(DATA_DIR / "sample.yaml")

    assert dataset.path.name == "sample.yaml"
    assert len(dataset.cases) == 7
    levels = {case.level for case in dataset.cases}
    assert levels == {"prompt", "retrieval", "e2e", "agent", "workflow"}


def test_load_invalid_yaml_missing_cases(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("name: broken\n", encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="cases"):
        load_dataset(path)


def test_load_invalid_prompt_case_missing_assertions(tmp_path: Path) -> None:
    path = tmp_path / "bad_prompt.yaml"
    path.write_text(
        """
cases:
  - id: bad_prompt
    level: prompt
    prompt_category: chat
    prompt_name: summarize_system
    prompt_version: '1'
    prompt_variables: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="expected_render"):
        load_dataset(path)


def test_load_invalid_level(tmp_path: Path) -> None:
    path = tmp_path / "bad_level.yaml"
    path.write_text(
        """
cases:
  - id: x
    level: unknown
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="level"):
        load_dataset(path)


def test_load_malformed_yaml_raises_eval_dataset_error(tmp_path: Path) -> None:
    path = tmp_path / "malformed.yaml"
    path.write_text("cases:\n  - id: [unclosed\n", encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="Failed to parse YAML dataset"):
        load_dataset(path)


def test_load_malformed_json_raises_eval_dataset_error(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text('{"cases": [', encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="Failed to parse JSON dataset"):
        load_dataset(path)


def test_load_agent_case_parses_expected_fields(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        """
cases:
  - id: agent_case
    level: agent
    goal: Do the thing
    instructions: Use echo
    expected_tool_calls:
      - echo
    expected_outcome: done
    model: gpt-4o-mini
    temperature: 0.2
""".strip(),
        encoding="utf-8",
    )

    dataset = load_dataset(path)
    case = dataset.cases[0]

    assert case.level == "agent"
    assert case.goal == "Do the thing"
    assert case.expected_tool_calls == ("echo",)
    assert case.model == "gpt-4o-mini"
    assert case.temperature == 0.2


def test_load_agent_case_rejects_boolean_temperature(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        """
cases:
  - id: agent_case
    level: agent
    goal: Do the thing
    expected_outcome: done
    temperature: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="temperature must be a number"):
        load_dataset(path)


def test_load_workflow_case_requires_definition_or_fixture(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
cases:
  - id: bad_workflow
    level: workflow
    expected_terminal_status: completed
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="workflow_definition"):
        load_dataset(path)


def test_load_workflow_case_parses_inline_definition(tmp_path: Path) -> None:
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
cases:
  - id: wf_case
    level: workflow
    expected_terminal_status: completed
    trigger_input:
      topic: release
    workflow_definition:
      name: Inline Workflow
      entry_node_id: start
      nodes:
        - id: start
          type: task
          config:
            tool_name: echo
        - id: end
          type: terminal
          config: {}
      edges:
        - id: e1
          from_node_id: start
          to_node_id: end
""".strip(),
        encoding="utf-8",
    )

    dataset = load_dataset(path)
    case = dataset.cases[0]

    assert case.level == "workflow"
    assert case.expected_terminal_status == "completed"
    assert case.trigger_input == {"topic": "release"}
    assert case.workflow_definition is not None
    assert case.workflow_definition["entry_node_id"] == "start"


def test_load_workflow_fixture_validates_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ai.evaluation.datasets.WORKFLOW_FIXTURES_ROOT", tmp_path)
    (tmp_path / "incomplete.yaml").write_text("name: Broken\n", encoding="utf-8")

    with pytest.raises(
        EvalDatasetError, match="missing required field 'entry_node_id'"
    ):
        load_workflow_fixture("incomplete.yaml")


def test_load_workflow_fixture_validates_node_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ai.evaluation.datasets.WORKFLOW_FIXTURES_ROOT", tmp_path)
    (tmp_path / "bad_nodes.yaml").write_text(
        """
name: Broken
entry_node_id: start
nodes:
  - id: start
edges: []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="workflow nodes require id and type"):
        load_workflow_fixture("bad_nodes.yaml")


def test_load_workflow_fixture_returns_valid_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ai.evaluation.datasets.WORKFLOW_FIXTURES_ROOT", tmp_path)
    (tmp_path / "echo.yaml").write_text(
        """
name: Echo Workflow
entry_node_id: start
nodes:
  - id: start
    type: task
    config:
      tool_name: echo
  - id: end
    type: terminal
    config: {}
edges:
  - id: e1
    from_node_id: start
    to_node_id: end
""".strip(),
        encoding="utf-8",
    )

    spec = load_workflow_fixture("echo.yaml")

    assert spec["entry_node_id"] == "start"
    assert spec["name"] == "Echo Workflow"
