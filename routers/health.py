from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from dependencies import get_db

router = APIRouter(tags=["Health"])

@router.get(
    "/health", 
    summary="AI 서버 및 DB 상태 확인", 
    description="AI 서비스 서버 구동 상태 및 PostgreSQL DB 연동 상태를 체크합니다."
)
def health_check(db: Session = Depends(get_db)):
    try:
        # DB 연결 테스트 (간단한 SELECT 1 실행)
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"disconnected: {str(e)}"

    return {
        "status": "ok",
        "database": db_status
    }