from __future__ import annotations

from models.orm_models import Project
from repositories.base import BaseRepository


class ProjectRepository(BaseRepository):
    def get(self, project_id: int) -> Project | None:
        raise NotImplementedError

    def list_for_user(self, user_id: int) -> list[Project]:
        raise NotImplementedError
