from __future__ import annotations

from core.exceptions import InvalidStateTransitionError, PermissionDeniedError
from models.enums import APPROVAL_TRANSITIONS, FOLLOW_UP_TRANSITIONS
from models.enums import ApprovalStatus, FollowUpStatus
from models.schemas import ProjectRecordDetailDTO
from repositories.follow_up_task import FollowUpTaskRepository
from repositories.project_member import ProjectMemberRepository
from repositories.project_record import ProjectRecordRepository


def assert_approval_transition(current: ApprovalStatus, target: ApprovalStatus) -> None:
    if target not in APPROVAL_TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransitionError(
            f"기록 승인 상태를 {current.value} 에서 {target.value} 로 바꿀 수 없습니다."
        )


def assert_follow_up_transition(current: FollowUpStatus, target: FollowUpStatus) -> None:
    if target not in FOLLOW_UP_TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransitionError(
            f"후속 작업 상태를 {current.value} 에서 {target.value} 로 바꿀 수 없습니다."
        )


class ProjectRecordService:
    def __init__(
        self,
        records: ProjectRecordRepository,
        tasks: FollowUpTaskRepository,
        members: ProjectMemberRepository,
    ) -> None:
        self.records = records
        self.tasks = tasks
        self.members = members

    def get(self, project_id: int, record_id: int, requester_id: int) -> ProjectRecordDetailDTO:
        """다른 프로젝트의 기록에 접근하면 PermissionDeniedError."""
        raise NotImplementedError

    def list_by_project(
        self, project_id: int, requester_id: int, approval_status: ApprovalStatus | None = None
    ) -> list[ProjectRecordDetailDTO]:
        raise NotImplementedError

    def approve(self, project_id: int, record_id: int, approver_id: int) -> ProjectRecordDetailDTO:
        """draft -> approved. 전이 검증 후 트랜잭션으로 저장합니다."""
        raise NotImplementedError

    def discard(self, project_id: int, record_id: int, requester_id: int) -> None:
        """draft -> discarded."""
        raise NotImplementedError

    def complete_task(self, project_id: int, task_id: int, requester_id: int) -> None:
        """needs_action -> done."""
        raise NotImplementedError

    def _assert_member(self, project_id: int, user_id: int) -> None:
        if self.members.find(project_id, user_id) is None:
            raise PermissionDeniedError("이 프로젝트에 접근할 권한이 없습니다.")
