"""Generic RAG HTTP endpoint (Phase 11)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends

from app.ai.deps import get_rag_service
from app.ai.prompts.exceptions import PromptNotFoundError, PromptRenderError
from app.ai.rag.schemas import RAGResponse
from app.ai.rag.service import RAGService
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context, get_logger
from app.schemas.chat import ProviderName
from app.schemas.rag import RAGAskRequest, RAGAskResponse, RetrievedChunkMetaSchema
from app.services.chat_service import ChatServiceError, normalize_chat_error

router = APIRouter()
logger = get_logger(__name__)


def _to_response(result: RAGResponse) -> RAGAskResponse:
    return RAGAskResponse(
        answer=result.answer,
        retrieved_chunks=[
            RetrievedChunkMetaSchema(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
            )
            for chunk in result.retrieved_chunks
        ],
        truncated=result.truncated,
        model=result.model,
        provider=cast(ProviderName, result.provider),
        retrieval_latency_ms=result.retrieval_latency_ms,
        llm_latency_ms=result.llm_latency_ms,
    )


@router.post("/api/rag/ask", response_model=RAGAskResponse)
async def ask_rag(
    request: RAGAskRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rag_service: RAGService = Depends(get_rag_service),
) -> RAGAskResponse:
    assert caller.user_id is not None
    bind_context(user_id=str(caller.user_id))

    if not settings.rag_enabled:
        raise AppError(
            code="feature_disabled",
            message="RAG is not enabled on this server.",
            status_code=503,
        )

    logger.info("RAG ask accepted", route="/api/rag/ask", method="POST")

    try:
        result = await rag_service.ask(
            user_id=caller.user_id,
            question=request.question,
            prompt_template=request.prompt_template,
            instructions=request.instructions,
            top_k=request.top_k,
            temperature=request.temperature,
            provider=request.provider,
            model=request.model,
        )
    except ChatServiceError:
        raise
    except (PromptNotFoundError, PromptRenderError) as exc:
        logger.exception("RAG prompt configuration error")
        raise AppError(
            code="rag_configuration_error",
            message="RAG prompt configuration is invalid on this server.",
            status_code=500,
        ) from exc
    except ValueError as exc:
        logger.warning("RAG request failed validation", exc_info=True)
        raise AppError(
            code="rag_request_failed",
            message=str(exc),
            status_code=422,
        ) from exc
    except Exception as exc:
        normalized = normalize_chat_error(exc)
        if isinstance(normalized, ChatServiceError):
            raise normalized from exc
        raise

    return _to_response(result)
