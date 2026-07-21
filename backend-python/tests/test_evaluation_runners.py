"""Unit and integration tests for evaluation runners."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.evaluation.datasets import EvalCase, load_dataset
from app.ai.evaluation.report import EvalRunReport, write_json_report
from app.ai.evaluation.runners import (
    EndToEndEvalRunner,
    PromptEvalRunner,
    RetrievalEvalRunner,
    pgvector_available,
)
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings
from tests.test_rag_service import _pgvector_available

DATASET = Path(__file__).resolve().parent / "data" / "evaluation" / "sample.yaml"


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "llm_provider": "openai",
        "openai_model": "gpt-4o-mini",
        "default_temperature": 0.7,
        "rag_top_k": 5,
        "rag_context_max_chars": 8000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _prompt_case(case_id: str) -> EvalCase:
    dataset = load_dataset(DATASET)
    case = next(item for item in dataset.cases if item.id == case_id)
    assert case.level == "prompt"
    return case


def _retrieval_case(case_id: str) -> EvalCase:
    dataset = load_dataset(DATASET)
    case = next(item for item in dataset.cases if item.id == case_id)
    assert case.level == "retrieval"
    return case


def _e2e_case(case_id: str) -> EvalCase:
    dataset = load_dataset(DATASET)
    case = next(item for item in dataset.cases if item.id == case_id)
    assert case.level == "e2e"
    return case


def test_prompt_eval_runner_pass_and_fail() -> None:
    manager = create_prompt_manager()
    runner = PromptEvalRunner(prompt_manager=manager)

    passed = runner.run_case(_prompt_case("rag_answer_renders"))
    assert passed.passed is True
    assert passed.level == "prompt"
    assert passed.latency_ms >= 0

    failing_case = EvalCase(
        id="bad_prompt",
        level="prompt",
        prompt_category="chat",
        prompt_name="summarize_system",
        prompt_version="1",
        prompt_variables={},
        expected_render_contains=("definitely-not-present",),
    )
    failed = runner.run_case(failing_case)
    assert failed.passed is False


@pytest.mark.anyio
async def test_retrieval_eval_runner_with_mocked_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_id = uuid.uuid4()
    settings = _settings()
    session = AsyncMock()
    runner = RetrievalEvalRunner(session=session, settings=settings)

    case = EvalCase(
        id="mock_retrieval",
        level="retrieval",
        question="fixture question",
        relevant_chunk_ids=(chunk_id,),
    )

    async def fake_create_user(self: RetrievalEvalRunner) -> uuid.UUID:
        return uuid.uuid4()

    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=[
            ScoredChunk(
                chunk_id=chunk_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="body",
                metadata={},
                score=0.9,
            )
        ]
    )

    def fake_retriever(self: RetrievalEvalRunner) -> MagicMock:
        return retriever

    monkeypatch.setattr(RetrievalEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(RetrievalEvalRunner, "_retriever", fake_retriever)

    result = await runner.run_case(case)

    assert result.passed is True
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.retrieved_count == 1


@pytest.mark.anyio
async def test_retrieval_eval_runner_rolls_back_session_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    session = AsyncMock()
    runner = RetrievalEvalRunner(session=session, settings=settings)
    case = EvalCase(
        id="failing_retrieval",
        level="retrieval",
        question="fixture question",
    )

    async def failing_create_user(self: RetrievalEvalRunner) -> uuid.UUID:
        raise RuntimeError("create user failed")

    monkeypatch.setattr(RetrievalEvalRunner, "_create_user", failing_create_user)

    result = await runner.run_case(case)

    assert result.passed is False
    assert result.error == "create user failed"
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_end_to_end_eval_runner_rolls_back_session_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    session = AsyncMock()
    runner = EndToEndEvalRunner(
        session=session,
        settings=settings,
        prompt_manager=create_prompt_manager(),
    )
    case = EvalCase(
        id="failing_e2e",
        level="e2e",
        question="What is here?",
        expected_answer="plain text fixture content",
    )

    async def failing_create(*_args: object, **_kwargs: object) -> MagicMock:
        raise RuntimeError("create user failed")

    monkeypatch.setattr("app.ai.evaluation.runners.SqlUserStore.create", failing_create)

    result = await runner.run_case(case)

    assert result.passed is False
    assert result.error == "create user failed"
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_end_to_end_eval_runner_with_mocked_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    settings = _settings()
    session = AsyncMock()
    prompt_manager = create_prompt_manager()

    runner = EndToEndEvalRunner(
        session=session,
        settings=settings,
        prompt_manager=prompt_manager,
    )

    case = EvalCase(
        id="mock_e2e",
        level="e2e",
        question="What is here?",
        expected_answer="plain text fixture content",
        expected_answer_match="contains",
    )

    async def fake_create(*_args: object, **_kwargs: object) -> MagicMock:
        return MagicMock(id=user_id)

    monkeypatch.setattr("app.ai.evaluation.runners.SqlUserStore.create", fake_create)

    rag_response = MagicMock()
    rag_response.answer = "It contains plain text fixture content."
    rag_service = MagicMock()
    rag_service.ask = AsyncMock(return_value=rag_response)

    def fake_rag_service(self: EndToEndEvalRunner, _llm: object) -> MagicMock:
        return rag_service

    monkeypatch.setattr(EndToEndEvalRunner, "_rag_service", fake_rag_service)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._extract_context",
        lambda _messages: "Plain text fixture content.",
    )

    result = await runner.run_case(case)

    assert result.passed is True
    assert result.correctness is True
    assert result.latency_ms >= 0


def test_report_json_serialization(tmp_path: Path) -> None:
    report = EvalRunReport(
        dataset_path="tests/data/evaluation/sample.yaml",
        settings_snapshot={"rag_top_k": 5},
        results=[],
    )
    output = tmp_path / "eval-report.json"
    write_json_report(report, output)

    payload = output.read_text(encoding="utf-8")
    assert '"schema_version": 1' in payload
    assert '"dataset_path"' in payload
    assert '"settings_snapshot"' in payload


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.mark.anyio
async def test_retrieval_eval_integration_on_fixture(pgvector_session) -> None:
    settings = _settings()
    runner = RetrievalEvalRunner(session=pgvector_session, settings=settings)
    result = await runner.run_case(_retrieval_case("retrieval_finds_fixture"))

    assert result.passed is True
    assert result.recall == 1.0
    assert result.precision == 1.0
    assert (result.retrieved_count or 0) > 0


@pytest.mark.anyio
async def test_retrieval_eval_empty_corpus_case(pgvector_session) -> None:
    settings = _settings()
    runner = RetrievalEvalRunner(session=pgvector_session, settings=settings)
    result = await runner.run_case(_retrieval_case("empty_corpus_retrieval"))

    assert result.passed is True
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.retrieved_count == 0


@pytest.mark.anyio
async def test_e2e_eval_integration_on_fixture(pgvector_session) -> None:
    settings = _settings()
    runner = EndToEndEvalRunner(
        session=pgvector_session,
        settings=settings,
        prompt_manager=create_prompt_manager(),
    )
    result = await runner.run_case(_e2e_case("e2e_fixture_answer"))

    assert result.passed is True
    assert result.correctness is True


@pytest.mark.anyio
async def test_pgvector_available_helper(pgvector_session) -> None:
    assert await pgvector_available(pgvector_session) is True


@pytest.mark.anyio
async def test_pgvector_available_false_when_query_fails() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=RuntimeError("db down"))
    assert await pgvector_available(session) is False
