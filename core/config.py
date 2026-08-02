"""환경 설정. 값은 환경 변수에서만 읽습니다. 시크릿을 코드에 쓰지 않습니다."""

from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    """애플리케이션 설정.

    pydantic-settings 를 쓰지 않고 표준 라이브러리만 사용합니다.
    의존성을 늘리지 않기 위해서이며, 필요하면 나중에 교체할 수 있습니다.
    """

    app_name: str = "Contextory AI Service"

    # PostgreSQL (pgvector 포함). Spring Boot 백엔드와 같은 DB 를 공유합니다.
    database_url: str | None = os.getenv("DATABASE_URL")

    # LLM / 임베딩
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "1536"))

    # Firebase
    firebase_credentials: str | None = os.getenv("FIREBASE_CREDENTIALS")
    firebase_project_id: str | None = os.getenv("FIREBASE_PROJECT_ID")

    # Celery
    celery_broker_url: str | None = os.getenv("CELERY_BROKER_URL")
    celery_result_backend: str | None = os.getenv("CELERY_RESULT_BACKEND")

    # 벡터 스토어
    vector_table_name: str = os.getenv("VECTOR_TABLE_NAME", "contextory_embeddings")

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL 이 설정되어 있지 않습니다.")
        return self.database_url

    def require_openai_api_key(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY 가 설정되어 있지 않습니다.")
        return self.openai_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
