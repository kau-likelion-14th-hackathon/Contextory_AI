"""인덱싱/RAG 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, DEFAULT_TOP_K


class IngestRequest(BaseModel):
    project_id: int
    document_ids: list[str] = Field(default_factory=list)
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP


class IngestResult(BaseModel):
    project_id: int
    indexed_documents: int = 0
    indexed_nodes: int = 0
    skipped: list[str] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    project_id: int
    query: str
    top_k: int = DEFAULT_TOP_K
    filters: dict = Field(default_factory=dict)


class RetrievedNode(BaseModel):
    node_id: str
    text: str
    score: float | None = None
    metadata: dict = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    query: str
    nodes: list[RetrievedNode] = Field(default_factory=list)
