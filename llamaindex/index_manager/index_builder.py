from __future__ import annotations

from typing import Any

from models.llamaindex_models import IngestResult


class IndexBuilder:
    """문서를 인덱싱해 벡터 스토어에 적재합니다."""

    def __init__(self, vector_store_adapter: Any) -> None:
        self.vector_store_adapter = vector_store_adapter

    def build(self, project_id: int, documents: list[Any]) -> IngestResult:
        raise NotImplementedError

    def rebuild(self, project_id: int) -> IngestResult:
        """기존 인덱스를 지우고 다시 만듭니다."""
        raise NotImplementedError
