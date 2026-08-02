"""SQLAlchemy engine / session / Base.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings


class Base(DeclarativeBase):
    """ORM 매핑 베이스. DDL 생성 용도로 쓰지 않습니다."""


_engine: Any = None
_SessionLocal: Any = None


def get_engine() -> Any:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.require_database_url(), pool_pre_ping=True)
    return _engine


def get_session_factory() -> Any:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """요청당 세션. 커밋/롤백 경계는 Service 계층이 담당합니다."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
