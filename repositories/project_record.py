from __future__ import annotations

from models.enums import ApprovalStatus
from models.orm_models import ProjectRecord, RecordIssue, RoleImpact
from models.schemas import AnalysisResultDTO
from repositories.base import BaseRepository


class ProjectRecordRepository(BaseRepository):
    def get(self, record_id: int) -> ProjectRecord | None:
        raise NotImplementedError

    def list_by_project(
        self, project_id: int, approval_status: ApprovalStatus | None = None
    ) -> list[ProjectRecord]:
        raise NotImplementedError

    def create_from_analysis(
        self, project_id: int, pull_request_id: int | None, result: AnalysisResultDTO
    ) -> ProjectRecord:
        """분석 결과를 draft 상태 기록으로 저장합니다.

        기록 본문과 issues / role_impacts / follow_up_tasks 를 함께 만듭니다.
        커밋은 호출하는 Service 가 담당합니다.
        """
        raise NotImplementedError

    def set_approval(
        self, record_id: int, status: ApprovalStatus, approver_id: int | None
    ) -> None:
        """상태 전이 검증은 Service 계층에서 수행한 뒤 호출합니다."""
        raise NotImplementedError

    def list_issues(self, record_id: int) -> list[RecordIssue]:
        raise NotImplementedError

    def list_role_impacts(self, record_id: int) -> list[RoleImpact]:
        raise NotImplementedError
