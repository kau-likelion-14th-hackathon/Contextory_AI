# dependencies.py
from typing import Generator
from core.db import SessionLocal

def get_db() -> Generator:
    """
    FastAPI 요청(Request)마다 독립된 SQLAlchemy DB 세션을 생성하고,
    요청이 종료되면 세션을 자동으로 반환(close)합니다.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()