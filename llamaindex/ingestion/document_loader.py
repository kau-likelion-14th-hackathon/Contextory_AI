from __future__ import annotations

from typing import Any


class DocumentLoader:
    """Firestore 또는 Storage 에서 원문을 읽어 LlamaIndex Document 로 만듭니다.

    코드와 식별자는 원문을 유지합니다.
    """

    def load_by_ids(self, project_id: int, document_ids: list[str]) -> list[Any]:
        raise NotImplementedError

    def load_all(self, project_id: int) -> list[Any]:
        raise NotImplementedError
