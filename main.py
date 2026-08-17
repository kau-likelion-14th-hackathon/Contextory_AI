from contextlib import asynccontextmanager

from fastapi import FastAPI
from routers import analysis, indexing, internal_analysis
from services.job_store import init_job_store_table


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ai_analysis_jobs 테이블이 없으면 생성한다 (Alembic 미도입 저장소이므로 DDL 직접 실행).
    # CREATE TABLE/INDEX IF NOT EXISTS를 사용해 멀티 워커 기동 시 동시 호출에도 안전하다.
    init_job_store_table()
    yield


app = FastAPI(
    title="Contextory AI Workers API",
    description="PR 분석 및 pgvector RAG 코드 인덱싱을 담당하는 FastAPI 서버입니다.",
    version="1.0.0",
    lifespan=lifespan,
)


# 루트 엔드포인트 (http://127.0.0.1:8000/ 접속 시)
@app.get("/")
def read_root():
    return {"message": "Contextory AI Workers API is Running!"}


# 헬스 체크 엔드포인트
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Contextory AI Engine"}


# 라우터 등록
app.include_router(analysis.router)
app.include_router(indexing.router)
app.include_router(internal_analysis.router)