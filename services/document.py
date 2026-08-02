from __future__ import annotations

from models.llamaindex_models import IngestRequest, IngestResult
from models.schemas import DocumentDTO
from services.firebase import FirebaseService


class DocumentService:
    """문서 원문/청크는 Firestore, 임베딩은 pgvector 로 나눠 저장합니다."""

    def __init__(self, firebase: FirebaseService) -> None:
        self.firebase = firebase

    def get(self, project_id: int, document_id: str, requester_id: int) -> DocumentDTO:
        raise NotImplementedError

    def list_by_project(self, project_id: int, requester_id: int) -> list[DocumentDTO]:
        raise NotImplementedError

    def ingest(self, request: IngestRequest) -> IngestResult:
        """문서를 청크로 나눠 인덱싱합니다. 오래 걸리면 Celery 태스크로 넘깁니다."""
        raise NotImplementedError
