from fastapi import FastAPI
from routers import analysis, indexing

app = FastAPI(
    title="Contextory AI Workers API",
    description="PR 분석 및 pgvector RAG 코드 인덱싱을 담당하는 FastAPI 서버입니다.",
    version="1.0.0"
)

# 헬스 체크 엔드포인트
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Contextory AI Engine"}

# 라우터 등록
app.include_router(analysis.router)
app.include_router(indexing.router)