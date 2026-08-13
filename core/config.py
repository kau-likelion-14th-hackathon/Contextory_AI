from urllib.parse import quote_plus
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Config
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # PostgreSQL Config
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "contextory_db"

    # OpenAI Config
    OPENAI_API_KEY: str = ""
    EMBED_DIM: int = 1536

    # 🚀 [추가] RAG & LLM Config
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_MODEL: str = "gpt-4o"
    RAG_TOP_K: int = 5            # pgvector Top-K 검색 수
    SIM_THRESHOLD: float = 0.5    # Context Filter 유사도 임계값

    # Google API Config
    GOOGLE_API_KEY: str = ""
    GOOGLE_SEARCH_ENGINE_ID: str = ""

    # 🚀 [추가] Vector Table 이름 (적재/조회 경로 일원화를 위한 단일 관리 지점)
    # repo_code_vectors: LlamaIndex PGVectorStore table_name 파라미터 값.
    #   테이블은 LlamaIndex 규약에 따라 "data_"가붙은 data_repo_code_vectors 이다.
    REPO_CODE_TABLE_NAME: str = "repo_code_vectors"
    # code_review_vectors: raw SQL(scripts/index_to_pg.py)로 적재되는 테이블명 그대로 사용.
    CODE_REVIEW_TABLE_NAME: str = "code_review_vectors"

    # 🚀 [추가] 내부 API(Backend<->AI) 공유 비밀키. X-Internal-Api-Key 헤더 검증 및
    # 콜백(FastAPI -> Backend) 전송 시 동일 헤더로 사용한다. 콜백 대상 URL은 요청(callbackUrl)에서 받는다.
    INTERNAL_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def database_url(self) -> str:
        """SQLAlchemy 용 PostgreSQL 접속 URL 생성"""
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()