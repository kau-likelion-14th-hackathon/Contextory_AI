from __future__ import annotations

from models.enums import GithubConnectionStatus
from models.orm_models import GithubConnection
from repositories.base import BaseRepository


class GithubConnectionRepository(BaseRepository):
    def get_by_project(self, project_id: int) -> GithubConnection | None:
        """프로젝트당 1개 (MVP)."""
        raise NotImplementedError

    def set_status(self, connection_id: int, status: GithubConnectionStatus) -> None:
        raise NotImplementedError
