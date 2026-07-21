"""Evaluation framework public exports."""

from app.ai.evaluation.datasets import (
    EvalCase,
    EvalDataset,
    EvalDatasetError,
    load_dataset,
)
from app.ai.evaluation.metrics import (
    TARGET_RAG_RESPONSE_MS,
    TARGET_RETRIEVAL_MS,
    TARGET_VECTOR_SEARCH_MS,
    answer_matches,
    faithfulness_score,
    hallucination_detected,
    latency_within_target,
    precision,
    recall,
)
from app.ai.evaluation.report import EvalCaseResult, EvalRunReport
from app.ai.evaluation.runners import (
    EndToEndEvalRunner,
    PromptEvalRunner,
    RetrievalEvalRunner,
)

__all__ = [
    "EvalCase",
    "EvalCaseResult",
    "EvalDataset",
    "EvalDatasetError",
    "EvalRunReport",
    "EndToEndEvalRunner",
    "PromptEvalRunner",
    "RetrievalEvalRunner",
    "TARGET_RAG_RESPONSE_MS",
    "TARGET_RETRIEVAL_MS",
    "TARGET_VECTOR_SEARCH_MS",
    "answer_matches",
    "faithfulness_score",
    "hallucination_detected",
    "latency_within_target",
    "load_dataset",
    "precision",
    "recall",
]
