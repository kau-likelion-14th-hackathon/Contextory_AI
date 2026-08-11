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