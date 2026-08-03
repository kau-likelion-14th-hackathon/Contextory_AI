from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.exceptions import ContextoryError
from core.logging import setup_logging
from routers import analysis, documents, health, pr, search, users

setup_logging()

app = FastAPI(title="Contextory AI Service")

app.include_router(health.router)
app.include_router(users.router)
app.include_router(pr.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(analysis.router)


@app.exception_handler(ContextoryError)
def handle_contextory_error(request: Request, exc: ContextoryError) -> JSONResponse:
    """도메인 예외를 HTTP 응답으로 변환합니다."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_response())


@app.get("/")
def read_root():
    return {"message": "Welcome to Contextory AI API"}
