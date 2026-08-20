# Contextory AI Workers API
# psycopg2-binary / tiktoken 등 휠 배포 패키지를 쓰므로 slim 이미지로 충분하다.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성 레이어를 소스와 분리해 캐시 적중률을 높인다.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# .env는 이미지에 넣지 않는다(.dockerignore). 실행 시 환경변수 또는 시크릿으로 주입한다.
EXPOSE 8000

# liveness 확인용 엔드포인트(/health)는 DB를 보지 않으므로 컨테이너 헬스체크에 적합하다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
