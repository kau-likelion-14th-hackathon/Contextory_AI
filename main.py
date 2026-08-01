from fastapi import FastAPI
from routers import health

app = FastAPI(title="Contextory AI Service")

# 라우터 연결
app.include_router(health.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Contextory AI API"}