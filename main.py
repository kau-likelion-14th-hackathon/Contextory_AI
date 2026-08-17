from fastapi import FastAPI
from routers import analysis, health, indexing, internal_analysis

app = FastAPI(
    title="Contextory AI Workers API",
    description="PR 분석 및 pgvector RAG 코드 인덱싱을 담당하는 FastAPI 서버입니다.",
    version="1.0.0",
)


# 루트 엔드포인트 (http://127.0.0.1:8000/ 접속 시)
@app.get("/")
def read_root():
    return {"message": "Contextory AI Workers API is Running!"}


# 헬스 체크 엔드포인트 (liveness — DB 없이도 200)
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Contextory AI Engine"}


# 라우터 등록
app.include_router(analysis.router)
app.include_router(indexing.router)
app.include_router(internal_analysis.router)
# DB 연동까지 확인하는 readiness 체크 (GET /api/v1/health)
app.include_router(health.router)