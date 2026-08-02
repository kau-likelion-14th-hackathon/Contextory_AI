from __future__ import annotations

from fastapi import APIRouter, Depends

from dependencies import get_chat_service, get_current_user_id
from models.schemas import ChatMessageDTO, ChatRequest, ChatResponse
from services.chat import ChatService

router = APIRouter(prefix="/chats", tags=["Chat"])


@router.post("", response_model=ChatResponse)
def ask(
    body: ChatRequest,
    user_id: int = Depends(get_current_user_id),
    svc: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    return svc.ask(body, user_id)


@router.get("/{chat_id}/messages", response_model=list[ChatMessageDTO])
def list_messages(
    chat_id: str,
    user_id: int = Depends(get_current_user_id),
    svc: ChatService = Depends(get_chat_service),
) -> list[ChatMessageDTO]:
    return svc.list_messages(chat_id, user_id)
