from __future__ import annotations

from models.enums import PrAnalysisStatus
from models.orm_models import PullRequest, PullRequestFile
from repositories.base import BaseRepository


class PullRequestRepository(BaseRepository):
    def get(self, pr_id: int) -> PullRequest | None:
        raise NotImplementedError

    def get_by_number(self, project_id: int, pr_number: int) -> PullRequest | None:
        raise NotImplementedError

    def list_by_project(
        self, project_id: int, status: PrAnalysisStatus | None = None
    ) -> list[PullRequest]:
        raise NotImplementedError

    def list_files(self, pr_id: int) -> list[PullRequestFile]:
        raise NotImplementedError

    def set_status(self, pr_id: int, status: PrAnalysisStatus) -> None:
        """상태 전이 검증은 Service 계층에서 수행한 뒤 호출합니다."""
        raise NotImplementedError
