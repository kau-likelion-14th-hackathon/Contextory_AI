# core/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config import settings

# SQLAlchemy Engine 생성
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # 연결 유효성 자동 체크
)

# DB 세션 팩토리 생성
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# ORM 모델 Base 클래스
Base = declarative_base()