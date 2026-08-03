from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.exceptions import ContextoryError
from core.logging import setup_logging
from routers import analysis, documents, health, indexing, pr, search, users

setup_logging()

app = FastAPI(
    title="Contextory AI Workers API",
    description="PR 분석 및 pgvector RAG 코드 인덱싱을 담당하는 FastAPI 서버입니다.",
    version="1.0.0",
)

# 라우터 등록
app.include_router(health.router)
app.include_router(users.router)
app.include_router(pr.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(analysis.router)
app.include_router(indexing.router)


@app.exception_handler(ContextoryError)
def handle_contextory_error(request: Request, exc: ContextoryError) -> JSONResponse:
    """도메인 예외를 HTTP 응답으로 변환합니다."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.get("/")
def read_root():
    return {"message": "Welcome to Contextory AI API"}


@app.get("/health")
def health_check():
    """서버 작동 상태 검증 기본 헬스 체크 엔드포인트입니다."""
    return {"status": "ok", "service": "Contextory AI Engine"}