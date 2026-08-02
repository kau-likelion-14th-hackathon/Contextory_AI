from __future__ import annotations

from models.schemas import ProjectMemberDTO, UserDTO
from repositories.project_member import ProjectMemberRepository
from repositories.user import UserRepository


class UserService:
    def __init__(self, users: UserRepository, members: ProjectMemberRepository) -> None:
        self.users = users
        self.members = members

    def get(self, user_id: int) -> UserDTO:
        raise NotImplementedError

    def list_project_members(self, project_id: int, requester_id: int) -> list[ProjectMemberDTO]:
        """요청자가 해당 프로젝트 멤버인지 먼저 검증합니다."""
        raise NotImplementedError

    def update_roles(
        self, project_id: int, member_id: int, roles: list, requester_id: int
    ) -> ProjectMemberDTO:
        """관리자(is_admin)만 수행할 수 있습니다. 권한 검증 위치."""
        raise NotImplementedError
