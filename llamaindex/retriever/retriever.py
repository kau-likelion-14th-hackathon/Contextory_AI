from __future__ import annotations

from models.llamaindex_models import RetrievalRequest, RetrievalResult


class ProjectRetriever:
    """프로젝트 범위로 한정해 검색합니다.

    다른 프로젝트의 문서가 섞이지 않도록 project_id 필터를 반드시 적용합니다.
    """

    def __init__(self, index_loader: object) -> None:
        self.index_loader = index_loader

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError
