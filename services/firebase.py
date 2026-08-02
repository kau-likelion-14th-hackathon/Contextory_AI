"""Firestore 접근 서비스 (비관계형 저장소).

PostgreSQL 관계형 핵심과 중복 저장하지 않습니다.
자격 증명은 환경 변수(FIREBASE_CREDENTIALS)로만 읽습니다.
"""

from __future__ import annotations

from models.firestore_models import (
    FirestoreChunk,
    FirestoreDocument,
    FirestoreSearchLog,
)


class FirebaseService:
    def __init__(self, client: object | None = None) -> None:
        self.client = client

    # 문서
    def get_document(self, document_id: str) -> FirestoreDocument | None:
        raise NotImplementedError

    def list_documents(self, project_id: int) -> list[FirestoreDocument]:
        raise NotImplementedError

    def save_chunks(self, chunks: list[FirestoreChunk]) -> int:
        raise NotImplementedError

    # 검색 로그
    def log_search(self, log: FirestoreSearchLog) -> str:
        raise NotImplementedError
