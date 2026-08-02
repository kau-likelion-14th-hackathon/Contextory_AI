"""Firestore 문서 모델 (비관계형).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from core.constants import FIRESTORE_DOCUMENTS, FIRESTORE_SEARCH_LOGS


class FirestoreDocument(BaseModel):
    """collection: documents"""

    collection: str = Field(default=FIRESTORE_DOCUMENTS, exclude=True)

    document_id: str
    project_id: int
    title: str
    content: str
    source_uri: str | None = None
    created_at: datetime | None = None


class FirestoreChunk(BaseModel):
    """collection: documents/{document_id}/chunks"""

    chunk_id: str
    document_id: str
    project_id: int
    text: str
    order: int
    metadata: dict = Field(default_factory=dict)


class FirestoreSearchLog(BaseModel):
    """collection: search_logs"""

    collection: str = Field(default=FIRESTORE_SEARCH_LOGS, exclude=True)

    log_id: str
    project_id: int
    user_id: int | None = None
    query: str
    hit_count: int = 0
    created_at: datetime | None = None
