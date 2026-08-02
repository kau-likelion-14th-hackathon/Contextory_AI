from __future__ import annotations

from typing import Any

from core.constants import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE


class TextSplitter:
    """문서를 청크로 나눕니다.

    코드 diff 는 문장 경계가 의미를 갖지 않으므로 토큰 기준 분할을 사용합니다.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, documents: list[Any]) -> list[Any]:
        raise NotImplementedError
