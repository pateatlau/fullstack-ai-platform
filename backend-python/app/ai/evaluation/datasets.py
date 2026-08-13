"""Load and validate evaluation case datasets from YAML or JSON."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

EvalLevel = Literal[
    "prompt", "retrieval", "e2e", "agent", "workflow", "plugin", "hitl", "jobs"
]
JobScenario = Literal[
    "hitl_expiry_agent",
    "hitl_expiry_workflow",
    "orphan_sweep_resume",
    "workflow_retention",
    "rag_indexing",
    "scheduled_eval",
]
HitlSurface = Literal["agent", "workflow"]
HitlDecision = Literal["approve", "approve_with_edits", "reject"]
AnswerMatchMode = Literal["exact", "contains", "fuzzy"]
PluginKind = Literal["tool", "prompt", "workflow"]

WORKFLOW_FIXTURES_ROOT = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "evaluation" / "workflows"
)


class EvalDatasetError(ValueError):
    """Raised when a dataset file fails schema validation."""


@dataclass(frozen=True)
class EvalCase:
    """Single evaluation case loaded from a dataset file."""

    id: str
    level: EvalLevel
    question: str | None = None
    expected_answer: str | None = None
    expected_answer_match: AnswerMatchMode = "contains"
    relevant_chunk_ids: tuple[uuid.UUID, ...] = ()
    document_fixture: str | None = None
    prompt_category: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    prompt_variables: dict[str, object] = field(default_factory=dict)
    expected_render_contains: tuple[str, ...] = ()
    expected_render_exact: str | None = None
    goal: str | None = None
    instructions: str | None = None
    expected_tool_calls: tuple[str, ...] = ()
    expected_outcome: str | None = None
    expected_outcome_match: AnswerMatchMode = "contains"
    workflow_definition: dict[str, object] | None = None
    workflow_fixture: str | None = None
    trigger_input: dict[str, object] = field(default_factory=dict)
    expected_terminal_status: str | None = None
    model: str | None = None
    temperature: float | None = None
    plugin_kind: PluginKind | None = None
    plugin_tool_name: str | None = None
    plugin_tool_arguments: dict[str, object] = field(default_factory=dict)
    expected_tool_data: dict[str, object] | None = None
    hitl_surface: HitlSurface | None = None
    hitl_decision: HitlDecision | None = None
    hitl_edited_calls: tuple[dict[str, object], ...] = ()
    hitl_edited_arguments: dict[str, object] = field(default_factory=dict)
    job_type: str | None = None
    job_scenario: JobScenario | None = None


@dataclass(frozen=True)
class EvalDataset:
    """Validated collection of evaluation cases."""

    path: Path
    cases: tuple[EvalCase, ...]


def load_dataset(path: Path) -> EvalDataset:
    """Load cases from a YAML or JSON dataset file."""
    if not path.is_file():
        raise EvalDatasetError(f"Dataset file not found: {path}")

    raw = _read_dataset_file(path)
    if not isinstance(raw, dict):
        raise EvalDatasetError("Dataset root must be a mapping with a 'cases' key.")

    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list):
        raise EvalDatasetError("Dataset must contain a 'cases' list.")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for index, case_raw in enumerate(cases_raw):
        if not isinstance(case_raw, dict):
            raise EvalDatasetError(f"Case at index {index} must be a mapping.")
        case = _parse_case(case_raw, index=index)
        if case.id in seen_ids:
            raise EvalDatasetError(f"Duplicate case id '{case.id}'.")
        seen_ids.add(case.id)
        cases.append(case)

    if not cases:
        raise EvalDatasetError("Dataset must contain at least one case.")

    return EvalDataset(path=path, cases=tuple(cases))


def filter_cases(dataset: EvalDataset, level: EvalLevel | None) -> tuple[EvalCase, ...]:
    """Return cases for a single level, or all cases when level is None."""
    if level is None:
        return dataset.cases
    return tuple(case for case in dataset.cases if case.level == level)


def load_workflow_fixture(filename: str) -> dict[str, object]:
    """Load an inline workflow definition spec from a fixture file."""
    path = WORKFLOW_FIXTURES_ROOT / filename
    if not path.is_file():
        raise EvalDatasetError(f"Workflow fixture not found: {path}")
    raw = _read_dataset_file(path)
    if not isinstance(raw, dict):
        raise EvalDatasetError(f"Workflow fixture '{filename}' root must be a mapping.")
    _validate_workflow_definition_spec(raw, context=f"Workflow fixture '{filename}'")
    return raw


def _read_dataset_file(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise EvalDatasetError(
                f"Failed to parse YAML dataset '{path}': {exc}"
            ) from exc
        return loaded if loaded is not None else {}
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvalDatasetError(
                f"Failed to parse JSON dataset '{path}': {exc}"
            ) from exc
    raise EvalDatasetError(
        f"Unsupported dataset format '{path.suffix}'. Use .yaml, .yml, or .json."
    )


def _parse_case(raw: dict[str, Any], *, index: int) -> EvalCase:
    case_id = _require_str(raw, "id", index=index)
    level = _require_level(raw, index=index)

    if level == "prompt":
        return _parse_prompt_case(raw, case_id=case_id)
    if level == "retrieval":
        return _parse_retrieval_case(raw, case_id=case_id)
    if level == "e2e":
        return _parse_e2e_case(raw, case_id=case_id)
    if level == "agent":
        return _parse_agent_case(raw, case_id=case_id)
    if level == "plugin":
        return _parse_plugin_case(raw, case_id=case_id)
    if level == "hitl":
        return _parse_hitl_case(raw, case_id=case_id)
    if level == "jobs":
        return _parse_jobs_case(raw, case_id=case_id)
    return _parse_workflow_case(raw, case_id=case_id)


def _parse_prompt_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    category = _require_str(raw, "prompt_category", case_id=case_id)
    name = _require_str(raw, "prompt_name", case_id=case_id)
    version = _require_str(raw, "prompt_version", case_id=case_id)
    variables = raw.get("prompt_variables")
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise EvalDatasetError(
            f"Case '{case_id}': prompt_variables must be a mapping when provided."
        )

    expected_contains = raw.get("expected_render_contains", [])
    if expected_contains is None:
        expected_contains = []
    if not isinstance(expected_contains, list) or not all(
        isinstance(item, str) for item in expected_contains
    ):
        raise EvalDatasetError(
            f"Case '{case_id}': expected_render_contains must be a list of strings."
        )

    expected_exact = raw.get("expected_render_exact")
    if expected_exact is not None and not isinstance(expected_exact, str):
        raise EvalDatasetError(
            f"Case '{case_id}': expected_render_exact must be a string when provided."
        )
    if not expected_contains and expected_exact is None:
        raise EvalDatasetError(
            f"Case '{case_id}': prompt cases require expected_render_contains "
            "or expected_render_exact."
        )

    return EvalCase(
        id=case_id,
        level="prompt",
        prompt_category=category,
        prompt_name=name,
        prompt_version=version,
        prompt_variables=variables,
        expected_render_contains=tuple(expected_contains),
        expected_render_exact=expected_exact,
    )


def _parse_retrieval_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    question = _require_str(raw, "question", case_id=case_id)
    document_fixture = raw.get("document_fixture")
    if document_fixture is not None and not isinstance(document_fixture, str):
        raise EvalDatasetError(
            f"Case '{case_id}': document_fixture must be a string when provided."
        )

    relevant_ids = _parse_uuid_list(raw.get("relevant_chunk_ids"), case_id=case_id)

    return EvalCase(
        id=case_id,
        level="retrieval",
        question=question,
        document_fixture=document_fixture,
        relevant_chunk_ids=relevant_ids,
    )


def _parse_e2e_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    question = _require_str(raw, "question", case_id=case_id)
    expected_answer = _require_str(raw, "expected_answer", case_id=case_id)
    match_mode = raw.get("expected_answer_match", "contains")
    if match_mode not in {"exact", "contains", "fuzzy"}:
        raise EvalDatasetError(
            f"Case '{case_id}': expected_answer_match must be exact, contains, or fuzzy."
        )

    document_fixture = raw.get("document_fixture")
    if document_fixture is not None and not isinstance(document_fixture, str):
        raise EvalDatasetError(
            f"Case '{case_id}': document_fixture must be a string when provided."
        )

    return EvalCase(
        id=case_id,
        level="e2e",
        question=question,
        expected_answer=expected_answer,
        expected_answer_match=match_mode,  # type: ignore[arg-type]
        document_fixture=document_fixture,
    )


def _parse_agent_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    goal = _require_str(raw, "goal", case_id=case_id)
    instructions = raw.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise EvalDatasetError(
            f"Case '{case_id}': instructions must be a string when provided."
        )

    expected_tool_calls = _parse_string_list(
        raw.get("expected_tool_calls", []),
        case_id=case_id,
        field_name="expected_tool_calls",
    )
    expected_outcome = raw.get("expected_outcome")
    if expected_outcome is not None and not isinstance(expected_outcome, str):
        raise EvalDatasetError(
            f"Case '{case_id}': expected_outcome must be a string when provided."
        )
    if not expected_tool_calls and expected_outcome is None:
        raise EvalDatasetError(
            f"Case '{case_id}': agent cases require expected_tool_calls "
            "and/or expected_outcome."
        )

    match_mode = raw.get("expected_outcome_match", "contains")
    if match_mode not in {"exact", "contains", "fuzzy"}:
        raise EvalDatasetError(
            f"Case '{case_id}': expected_outcome_match must be exact, contains, or fuzzy."
        )

    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        raise EvalDatasetError(
            f"Case '{case_id}': model must be a string when provided."
        )

    temperature = raw.get("temperature")
    if temperature is not None and type(temperature) not in (int, float):
        raise EvalDatasetError(
            f"Case '{case_id}': temperature must be a number when provided."
        )

    unsupported_tools = [
        tool_name for tool_name in expected_tool_calls if tool_name != "echo"
    ]
    if unsupported_tools:
        unsupported_text = ", ".join(sorted(set(unsupported_tools)))
        raise EvalDatasetError(
            f"Case '{case_id}': agent eval harness only supports echo tool calls; "
            f"unsupported expected_tool_calls: {unsupported_text}."
        )

    return EvalCase(
        id=case_id,
        level="agent",
        goal=goal,
        instructions=instructions,
        expected_tool_calls=expected_tool_calls,
        expected_outcome=expected_outcome,
        expected_outcome_match=match_mode,  # type: ignore[arg-type]
        model=model,
        temperature=float(temperature) if temperature is not None else None,
    )


def _parse_plugin_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    plugin_kind = raw.get("plugin_kind")
    if plugin_kind not in {"tool", "prompt", "workflow"}:
        raise EvalDatasetError(
            f"Case '{case_id}': plugin_kind must be tool, prompt, or workflow."
        )

    if plugin_kind == "tool":
        tool_name = _require_str(raw, "plugin_tool_name", case_id=case_id)
        arguments = raw.get("plugin_tool_arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise EvalDatasetError(
                f"Case '{case_id}': plugin_tool_arguments must be a mapping."
            )
        expected_tool_data = raw.get("expected_tool_data")
        if expected_tool_data is not None and not isinstance(expected_tool_data, dict):
            raise EvalDatasetError(
                f"Case '{case_id}': expected_tool_data must be a mapping when provided."
            )
        return EvalCase(
            id=case_id,
            level="plugin",
            plugin_kind="tool",
            plugin_tool_name=tool_name,
            plugin_tool_arguments=arguments,
            expected_tool_data=expected_tool_data,
        )

    if plugin_kind == "prompt":
        prompt_case = _parse_prompt_case(raw, case_id=case_id)
        return EvalCase(
            id=prompt_case.id,
            level="plugin",
            plugin_kind="prompt",
            prompt_category=prompt_case.prompt_category,
            prompt_name=prompt_case.prompt_name,
            prompt_version=prompt_case.prompt_version,
            prompt_variables=prompt_case.prompt_variables,
            expected_render_contains=prompt_case.expected_render_contains,
            expected_render_exact=prompt_case.expected_render_exact,
        )

    workflow_case = _parse_workflow_case(raw, case_id=case_id)
    return EvalCase(
        id=workflow_case.id,
        level="plugin",
        plugin_kind="workflow",
        workflow_definition=workflow_case.workflow_definition,
        workflow_fixture=workflow_case.workflow_fixture,
        trigger_input=workflow_case.trigger_input,
        expected_terminal_status=workflow_case.expected_terminal_status,
    )


def _parse_jobs_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    from app.ai.evaluation.jobs_scenarios import _JOBS_SCENARIOS

    job_type = _require_str(raw, "job_type", case_id=case_id)
    job_scenario = _require_str(raw, "job_scenario", case_id=case_id)
    if job_scenario not in _JOBS_SCENARIOS:
        allowed = ", ".join(sorted(_JOBS_SCENARIOS))
        raise EvalDatasetError(
            f"Case '{case_id}': job_scenario must be one of: {allowed}."
        )
    expected_type = _JOBS_SCENARIOS[job_scenario]
    if job_type != expected_type:
        raise EvalDatasetError(
            f"Case '{case_id}': job_type {job_type!r} does not match "
            f"job_scenario {job_scenario!r} (expected {expected_type!r})."
        )
    return EvalCase(
        id=case_id,
        level="jobs",
        job_type=job_type,
        job_scenario=job_scenario,  # type: ignore[arg-type]
    )


def _parse_hitl_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    surface = raw.get("hitl_surface")
    if surface not in {"agent", "workflow"}:
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_surface must be agent or workflow."
        )
    decision = raw.get("hitl_decision")
    if decision not in {"approve", "approve_with_edits", "reject"}:
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_decision must be approve, approve_with_edits, "
            "or reject."
        )

    edited_calls_raw = raw.get("hitl_edited_calls", [])
    if edited_calls_raw is None:
        edited_calls_raw = []
    if not isinstance(edited_calls_raw, list):
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_edited_calls must be a list of mappings."
        )
    edited_calls = _parse_hitl_edited_calls(edited_calls_raw, case_id=case_id)

    edited_arguments_raw = raw.get("hitl_edited_arguments", {})
    if edited_arguments_raw is None:
        edited_arguments_raw = {}
    if not isinstance(edited_arguments_raw, dict):
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_edited_arguments must be a mapping when provided."
        )
    edited_arguments = _parse_hitl_edited_arguments(
        edited_arguments_raw, case_id=case_id
    )

    _validate_hitl_edit_fields(
        case_id=case_id,
        surface=surface,
        decision=decision,
        edited_calls=edited_calls,
        edited_arguments=edited_arguments,
    )

    if surface == "agent":
        goal = _require_str(raw, "goal", case_id=case_id)
        instructions = raw.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            raise EvalDatasetError(
                f"Case '{case_id}': instructions must be a string when provided."
            )
        expected_outcome = raw.get("expected_outcome")
        if expected_outcome is not None and not isinstance(expected_outcome, str):
            raise EvalDatasetError(
                f"Case '{case_id}': expected_outcome must be a string when provided."
            )
        if decision != "reject" and expected_outcome is None:
            raise EvalDatasetError(
                f"Case '{case_id}': agent hitl approve cases require expected_outcome."
            )
        match_mode = raw.get("expected_outcome_match", "contains")
        if match_mode not in {"exact", "contains", "fuzzy"}:
            raise EvalDatasetError(
                f"Case '{case_id}': expected_outcome_match must be exact, contains, "
                "or fuzzy."
            )
        return EvalCase(
            id=case_id,
            level="hitl",
            hitl_surface="agent",
            hitl_decision=decision,  # type: ignore[arg-type]
            goal=goal,
            instructions=instructions,
            expected_outcome=expected_outcome,
            expected_outcome_match=match_mode,  # type: ignore[arg-type]
            hitl_edited_calls=edited_calls,
        )

    workflow_case = _parse_workflow_case(raw, case_id=case_id)
    return EvalCase(
        id=workflow_case.id,
        level="hitl",
        hitl_surface="workflow",
        hitl_decision=decision,  # type: ignore[arg-type]
        workflow_definition=workflow_case.workflow_definition,
        workflow_fixture=workflow_case.workflow_fixture,
        trigger_input=workflow_case.trigger_input,
        expected_terminal_status=workflow_case.expected_terminal_status,
        hitl_edited_arguments=edited_arguments,
    )


def _parse_hitl_edited_calls(
    raw: list[object], *, case_id: str
) -> tuple[dict[str, object], ...]:
    parsed: list[dict[str, object]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_calls[{index}] must be a mapping."
            )
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_calls[{index}] requires a non-empty "
                "string 'name'."
            )
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_calls[{index}] requires an "
                "'arguments' mapping."
            )
        call_id = item.get("call_id")
        if call_id is not None and not isinstance(call_id, str):
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_calls[{index}].call_id must be a "
                "string when provided."
            )
        entry: dict[str, object] = {"name": name, "arguments": arguments}
        if call_id is not None:
            entry["call_id"] = call_id
        parsed.append(entry)
    return tuple(parsed)


def _parse_hitl_edited_arguments(
    raw: dict[object, object], *, case_id: str
) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_arguments keys must be non-empty "
                "strings."
            )
        if isinstance(value, (str, int, float, bool)) or value is None:
            parsed[key] = value
            continue
        if isinstance(value, dict):
            parsed[key] = _parse_hitl_edited_arguments(value, case_id=case_id)
            continue
        if isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None for item in value
        ):
            parsed[key] = value
            continue
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_edited_arguments['{key}'] must be a scalar, "
            "mapping, or list of scalars."
        )
    return parsed


def _validate_hitl_edit_fields(
    *,
    case_id: str,
    surface: str,
    decision: str,
    edited_calls: tuple[dict[str, object], ...],
    edited_arguments: dict[str, object],
) -> None:
    has_calls = bool(edited_calls)
    has_args = bool(edited_arguments)

    if decision != "approve_with_edits":
        if has_calls:
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_calls are only allowed when "
                "hitl_decision is approve_with_edits."
            )
        if has_args:
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_arguments are only allowed when "
                "hitl_decision is approve_with_edits."
            )
        return

    if surface == "agent":
        if has_args:
            raise EvalDatasetError(
                f"Case '{case_id}': hitl_edited_arguments are not valid for agent "
                "hitl_surface; use hitl_edited_calls."
            )
        if not has_calls:
            raise EvalDatasetError(
                f"Case '{case_id}': agent approve_with_edits cases require "
                "hitl_edited_calls."
            )
        return

    if has_calls:
        raise EvalDatasetError(
            f"Case '{case_id}': hitl_edited_calls are not valid for workflow "
            "hitl_surface; use hitl_edited_arguments."
        )
    if not has_args:
        raise EvalDatasetError(
            f"Case '{case_id}': workflow approve_with_edits cases require "
            "hitl_edited_arguments."
        )


def _parse_workflow_case(raw: dict[str, Any], *, case_id: str) -> EvalCase:
    inline_definition = raw.get("workflow_definition")
    workflow_fixture = raw.get("workflow_fixture")
    if inline_definition is None and workflow_fixture is None:
        raise EvalDatasetError(
            f"Case '{case_id}': workflow cases require workflow_definition "
            "or workflow_fixture."
        )
    if inline_definition is not None and workflow_fixture is not None:
        raise EvalDatasetError(
            f"Case '{case_id}': provide workflow_definition or workflow_fixture, not both."
        )
    if inline_definition is not None and not isinstance(inline_definition, dict):
        raise EvalDatasetError(
            f"Case '{case_id}': workflow_definition must be a mapping."
        )
    if workflow_fixture is not None and not isinstance(workflow_fixture, str):
        raise EvalDatasetError(
            f"Case '{case_id}': workflow_fixture must be a string when provided."
        )

    expected_terminal_status = _require_str(
        raw, "expected_terminal_status", case_id=case_id
    )

    trigger_input = raw.get("trigger_input", {})
    if trigger_input is None:
        trigger_input = {}
    if not isinstance(trigger_input, dict):
        raise EvalDatasetError(
            f"Case '{case_id}': trigger_input must be a mapping when provided."
        )

    if inline_definition is not None:
        _validate_workflow_definition_spec(
            inline_definition, context=f"Case '{case_id}'"
        )

    return EvalCase(
        id=case_id,
        level="workflow",
        workflow_definition=inline_definition,
        workflow_fixture=workflow_fixture,
        trigger_input=trigger_input,
        expected_terminal_status=expected_terminal_status,
    )


def _validate_workflow_definition_spec(
    spec: dict[str, object], *, context: str
) -> None:
    for key in ("name", "entry_node_id", "nodes", "edges"):
        if key not in spec:
            raise EvalDatasetError(f"{context}: missing required field '{key}'.")
    nodes = spec.get("nodes")
    edges = spec.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise EvalDatasetError(f"{context}: nodes must be a non-empty list.")
    if not isinstance(edges, list):
        raise EvalDatasetError(f"{context}: edges must be a list.")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise EvalDatasetError(f"{context}: nodes[{index}] must be a mapping.")
        if "id" not in node or "type" not in node:
            raise EvalDatasetError(f"{context}: workflow nodes require id and type.")


def _parse_uuid_list(value: object, *, case_id: str) -> tuple[uuid.UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EvalDatasetError(
            f"Case '{case_id}': relevant_chunk_ids must be a list of UUID strings."
        )
    parsed: list[uuid.UUID] = []
    for item in value:
        if not isinstance(item, str):
            raise EvalDatasetError(
                f"Case '{case_id}': relevant_chunk_ids entries must be UUID strings."
            )
        try:
            parsed.append(uuid.UUID(item))
        except ValueError as exc:
            raise EvalDatasetError(
                f"Case '{case_id}': invalid UUID in relevant_chunk_ids: {item}"
            ) from exc
    return tuple(parsed)


def _parse_string_list(
    value: object, *, case_id: str, field_name: str
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvalDatasetError(
            f"Case '{case_id}': {field_name} must be a list of strings."
        )
    return tuple(value)


def _require_str(
    raw: dict[str, Any],
    key: str,
    *,
    index: int | None = None,
    case_id: str | None = None,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        if case_id is not None:
            raise EvalDatasetError(f"Case '{case_id}': missing required field '{key}'.")
        raise EvalDatasetError(
            f"Case at index {index}: missing required field '{key}'."
        )
    return value


def _require_level(raw: dict[str, Any], *, index: int) -> EvalLevel:
    value = raw.get("level")
    if value not in {
        "prompt",
        "retrieval",
        "e2e",
        "agent",
        "workflow",
        "plugin",
        "hitl",
        "jobs",
    }:
        raise EvalDatasetError(
            f"Case at index {index}: level must be prompt, retrieval, e2e, agent, "
            "workflow, plugin, hitl, or jobs."
        )
    return value  # type: ignore[return-value]
