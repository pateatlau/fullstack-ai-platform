"""Centralized API error types and FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.core.config import get_settings
from app.core.cors import apply_cors_headers
from app.core.logging import get_logger
from app.core.security import AuthError
from app.middleware.correlation_id import REQUEST_ID_HEADER, get_request_id
from app.schemas.chat import ErrorDetail, ErrorResponseSchema
from app.services.chat_service import ChatServiceError
from app.services.document_service import DocumentServiceError
from app.services.knowledge_service import KnowledgeServiceError

logger = get_logger(__name__)

DATABASE_ERROR_MESSAGE = "The database is temporarily unavailable."
INTERNAL_ERROR_MESSAGE = "Unexpected server error."


class AppError(Exception):
    """Base application error with a stable API envelope."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


RATE_LIMIT_MESSAGE = "Too many requests. Please retry shortly."


class RateLimitExceededError(ChatServiceError, AppError):
    """HTTP rate-limit exceeded (middleware or explicit raise)."""

    def __init__(
        self,
        *,
        retry_after_seconds: int | None = None,
        message: str = RATE_LIMIT_MESSAGE,
    ) -> None:
        AppError.__init__(
            self,
            code="rate_limit_exceeded",
            message=message,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the standard ``{ error: { code, message, request_id } }`` response."""
    request_id = get_request_id()
    payload = ErrorResponseSchema(
        error=ErrorDetail(code=code, message=message, request_id=request_id)
    )
    response = JSONResponse(status_code=status_code, content=payload.model_dump())
    if request_id is not None:
        response.headers[REQUEST_ID_HEADER] = request_id
    return apply_cors_headers(response)


def rate_limit_error_response(retry_after_seconds: int) -> JSONResponse:
    """Build a 429 rate-limit response with ``Retry-After`` (seconds)."""
    response = error_response(429, "rate_limit_exceeded", RATE_LIMIT_MESSAGE)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def _format_validation_errors(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        prefix = f"{location}: " if location else ""
        messages.append(f"{prefix}{error['msg']}")
    return "; ".join(messages) or "Request validation failed."


def _handle_app_error(exc: AppError | AuthError | ChatServiceError) -> JSONResponse:
    response = error_response(exc.status_code, exc.code, exc.message)
    if isinstance(exc, RateLimitExceededError) and exc.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    return response


def _document_service_status_code(code: str) -> int:
    if code == "document_too_large":
        return 413
    if code == "unsupported_document_type":
        return 422
    return 422


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _handle_app_error(exc)

    @app.exception_handler(ChatServiceError)
    async def handle_chat_service_error(
        _: Request, exc: ChatServiceError
    ) -> JSONResponse:
        return _handle_app_error(exc)

    @app.exception_handler(AuthError)
    async def handle_auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return _handle_app_error(exc)

    @app.exception_handler(DocumentServiceError)
    async def handle_document_service_error(
        _: Request, exc: DocumentServiceError
    ) -> JSONResponse:
        code = (
            "validation_error" if exc.code == "unsupported_document_type" else exc.code
        )
        return error_response(
            _document_service_status_code(exc.code),
            code,
            exc.message,
        )

    @app.exception_handler(KnowledgeServiceError)
    async def handle_knowledge_service_error(
        _: Request, exc: KnowledgeServiceError
    ) -> JSONResponse:
        if exc.code == "document_not_found":
            return error_response(404, exc.code, exc.message)
        return error_response(500, "ingestion_failed", "Document ingestion failed.")

    @app.exception_handler(RateLimitExceededError)
    async def handle_rate_limit_error(
        _: Request, exc: RateLimitExceededError
    ) -> JSONResponse:
        retry_after = (
            exc.retry_after_seconds if exc.retry_after_seconds is not None else 60
        )
        return rate_limit_error_response(retry_after)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "validation_error", _format_validation_errors(exc))

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    @app.exception_handler(DBAPIError)
    async def handle_database_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Database error")
        return error_response(503, "database_error", DATABASE_ERROR_MESSAGE)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        settings = get_settings()
        logger.exception("Unhandled server error")
        message = INTERNAL_ERROR_MESSAGE
        if settings.is_development:
            message = f"{INTERNAL_ERROR_MESSAGE} ({exc.__class__.__name__})"
        return error_response(500, "internal_error", message)
