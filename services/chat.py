from __future__ import annotations

from models.schemas import ChatRequest, ChatResponse
from services.firebase import FirebaseService
from services.llm_client import LlmClient
from services.search import SearchService


class ChatService:
    """채팅 이력은 Firestore, 답변 생성은 RAG + LLM 으로 처리합니다."""

    def __init__(
        self, firebase: FirebaseService, search: SearchService, llm: LlmClient
    ) -> None:
        self.firebase = firebase
        self.search = search
        self.llm = llm

    def ask(self, request: ChatRequest, user_id: int) -> ChatResponse:
        """1) 소속 검증 2) RAG 검색 3) LLM 답변 4) Firestore 에 메시지 저장."""
        raise NotImplementedError

    def list_messages(self, chat_id: str, user_id: int) -> list:
        raise NotImplementedError
