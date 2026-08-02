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

    # Google API Config
    GOOGLE_API_KEY: str = ""
    GOOGLE_SEARCH_ENGINE_ID: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def database_url(self) -> str:
        """SQLAlchemy 용 PostgreSQL 접속 URL을 생성합니다."""
        # 비밀번호 특수문자 이스케이프 처리
        password = quote_plus(self.POSTGRES_PASSWORD)

        # psycopg2 드라이버 명시 (+psycopg2 추가)
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


# 싱글톤 패턴으로 settings 객체 생성
settings = Settings()