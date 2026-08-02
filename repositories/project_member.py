from __future__ import annotations

from models.enums import Role
from models.orm_models import ProjectMember, ProjectMemberRole
from repositories.base import BaseRepository


class ProjectMemberRepository(BaseRepository):
    def get(self, member_id: int) -> ProjectMember | None:
        raise NotImplementedError

    def find(self, project_id: int, user_id: int) -> ProjectMember | None:
        """소속 검증에 사용합니다. None 이면 그 프로젝트 접근 권한이 없습니다."""
        raise NotImplementedError

    def list_by_project(self, project_id: int) -> list[ProjectMember]:
        raise NotImplementedError

    def list_roles(self, member_id: int) -> list[ProjectMemberRole]:
        raise NotImplementedError

    def add_role(self, member_id: int, role: Role) -> ProjectMemberRole:
        raise NotImplementedError
