from __future__ import annotations

from models.llamaindex_models import IngestRequest, IngestResult


class IngestPipeline:
    """load -> split -> embed -> store 순서로 실행합니다."""

    def __init__(self, loader: object, splitter: object, builder: object) -> None:
        self.loader = loader
        self.splitter = splitter
        self.builder = builder

    def run(self, request: IngestRequest) -> IngestResult:
        raise NotImplementedError
