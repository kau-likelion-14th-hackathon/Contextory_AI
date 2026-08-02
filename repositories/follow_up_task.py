from __future__ import annotations

from models.enums import FollowUpStatus, Role
from models.orm_models import FollowUpTask
from repositories.base import BaseRepository


class FollowUpTaskRepository(BaseRepository):
    def get(self, task_id: int) -> FollowUpTask | None:
        raise NotImplementedError

    def list_by_record(self, record_id: int) -> list[FollowUpTask]:
        raise NotImplementedError

    def list_by_role(self, project_id: int, role: Role) -> list[FollowUpTask]:
        raise NotImplementedError

    def set_status(self, task_id: int, status: FollowUpStatus) -> None:
        """상태 전이 검증은 Service 계층에서 수행한 뒤 호출합니다."""
        raise NotImplementedError

    def assign(self, task_id: int, member_id: int | None) -> None:
        raise NotImplementedError
