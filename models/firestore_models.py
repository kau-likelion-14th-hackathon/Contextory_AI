"""Firestore 문서 모델 (비관계형).

PostgreSQL 관계형 핵심과 중복 저장하지 않습니다.
Firestore 담당: 문서 원문/청크, 채팅, 검색 로그.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from core.constants import (
    FIRESTORE_CHATS,
    FIRESTORE_DOCUMENTS,
    FIRESTORE_MESSAGES,
    FIRESTORE_SEARCH_LOGS,
)


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


class FirestoreChat(BaseModel):
    """collection: chats"""

    collection: str = Field(default=FIRESTORE_CHATS, exclude=True)

    chat_id: str
    project_id: int
    user_id: int
    title: str | None = None
    created_at: datetime | None = None


class FirestoreMessage(BaseModel):
    """collection: chats/{chat_id}/messages"""

    collection: str = Field(default=FIRESTORE_MESSAGES, exclude=True)

    message_id: str
    chat_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime | None = None


class FirestoreSearchLog(BaseModel):
    """collection: search_logs"""

    collection: str = Field(default=FIRESTORE_SEARCH_LOGS, exclude=True)

    log_id: str
    project_id: int
    user_id: int | None = None
    query: str
    hit_count: int = 0
    created_at: datetime | None = None
