from __future__ import annotations

from models.orm_models import User
from repositories.base import BaseRepository


class UserRepository(BaseRepository):
    def get(self, user_id: int) -> User | None:
        raise NotImplementedError

    def get_by_github_id(self, github_id: str) -> User | None:
        raise NotImplementedError

    def create(self, github_id: str, username: str, email: str | None = None) -> User:
        raise NotImplementedError
