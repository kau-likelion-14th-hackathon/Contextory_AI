from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get(
    "/health", 
    summary="AI 서버 상태 확인", 
    description="AI 서비스 서버가 정상 구동 중인지 헬스체크를 진행합니다."
)
def health_check():
    return {"status": "ok", "message": "Contextory AI Service is running"}