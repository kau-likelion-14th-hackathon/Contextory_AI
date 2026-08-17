from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.security import verify_internal_api_key
from dependencies import get_db

# main.py의 "/health"는 프로세스 생존 확인(liveness)용이라 DB를 보지 않는다.
# 이 라우터는 DB 연동까지 확인하는 readiness 체크로 "/api/v1/health"에 등록된다.
# 응답에 DB 연결 실패 사유가 담기므로, develop의 보안 방침대로 내부 API 키를 요구한다.
router = APIRouter(prefix="/api/v1", tags=["Health"], dependencies=[Depends(verify_internal_api_key)])

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