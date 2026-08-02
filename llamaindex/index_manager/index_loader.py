from __future__ import annotations

from typing import Any


class IndexLoader:
    """이미 적재된 인덱스를 불러옵니다."""

    def __init__(self, vector_store_adapter: Any) -> None:
        self.vector_store_adapter = vector_store_adapter

    def load(self, project_id: int) -> Any:
        raise NotImplementedError
