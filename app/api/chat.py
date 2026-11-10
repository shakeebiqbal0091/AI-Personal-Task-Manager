from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.worker.agent import (
    ProductivityWorker,
    WorkerConfigurationError,
    WorkerSettings,
)
from app.worker.context import ConversationContextError
from app.worker.conversations import ConversationCapacityError, ConversationStore
from app.worker.execution import WorkerExecutionError

router = APIRouter(tags=["chat"])


class ChatRuntime:
    def __init__(
        self, worker: ProductivityWorker, conversations: ConversationStore
    ) -> None:
        self.worker = worker
        self.conversations = conversations

    async def close(self) -> None:
        await self.worker.close()


def get_chat_runtime(request: Request) -> ChatRuntime:
    runtime = getattr(request.app.state, "chat_runtime", None)
    if runtime is not None:
        return runtime

    try:
        settings = WorkerSettings.from_environment()
    except WorkerConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat is unavailable because the AI provider is not configured.",
        ) from exc

    runtime = ChatRuntime(
        ProductivityWorker(settings),
        ConversationStore(max_messages=settings.context_max_messages),
    )
    request.app.state.chat_runtime = runtime
    return runtime


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: Annotated[Session, Depends(get_db)],
    runtime: Annotated[ChatRuntime, Depends(get_chat_runtime)],
) -> ChatResponse:
    try:
        conversation, conversation_lock = runtime.conversations.get_or_create(
            payload.conversation_id
        )
    except ConversationCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    try:
        async with conversation_lock:
            response = await runtime.worker.respond(
                payload.message,
                conversation,
                session=session,
            )
    except ConversationContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except WorkerExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return ChatResponse(conversation_id=payload.conversation_id, response=response)
