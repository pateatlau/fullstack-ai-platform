"""Load and validate evaluation case datasets from YAML or JSON."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

EvalLevel = Literal["prompt", "retrieval", "e2e"]
AnswerMatchMode = Literal["exact", "contains", "fuzzy"]


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
    return _parse_e2e_case(raw, case_id=case_id)


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
    if value not in {"prompt", "retrieval", "e2e"}:
        raise EvalDatasetError(
            f"Case at index {index}: level must be prompt, retrieval, or e2e."
        )
    return value  # type: ignore[return-value]
