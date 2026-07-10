from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequestSchema, ChatResponseSchema
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/api/chat", response_model=ChatResponseSchema)
async def create_chat(request: ChatRequestSchema) -> ChatResponseSchema:
    service = ChatService()
    return await service.complete_chat(request)


@router.post("/api/chat/stream")
async def create_chat_stream(
    request: ChatRequestSchema, http_request: Request
) -> StreamingResponse:
    service = ChatService()
    return StreamingResponse(
        service.stream_chat(request, http_request),
        media_type="text/event-stream",
    )
