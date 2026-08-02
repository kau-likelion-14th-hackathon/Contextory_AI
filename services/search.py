from __future__ import annotations

from models.llamaindex_models import RetrievalRequest, RetrievalResult
from models.schemas import SearchRequest, SearchResponse
from services.firebase import FirebaseService


class SearchService:
    def __init__(self, firebase: FirebaseService, retriever: "object") -> None:
        self.firebase = firebase
        self.retriever = retriever

    def search(self, request: SearchRequest, requester_id: int) -> SearchResponse:
        """프로젝트 소속 검증 후 RAG 검색을 수행하고 검색 로그를 Firestore 에 남깁니다."""
        raise NotImplementedError

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError
