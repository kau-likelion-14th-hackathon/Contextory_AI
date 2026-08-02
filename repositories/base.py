"""Repository 공통 베이스. 세션을 주입받아 데이터 접근만 담당합니다.

커밋/롤백 등 트랜잭션 경계는 Service 계층이 관리합니다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
