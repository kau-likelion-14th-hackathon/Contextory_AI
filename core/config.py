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

    # DB 커넥션 풀 크기. job_store(CRUD)와 LlamaIndex 인덱싱/조회가 같은 엔진을 공유하므로,
    # 동시 요청이 몰릴 때 풀 고갈로 커넥션 체크아웃이 대기(기본 30초 타임아웃)하지 않도록
    # SQLAlchemy 기본값(5 + overflow 10)보다 넉넉하게 잡는다.
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # OpenAI Config
    OPENAI_API_KEY: str = ""
    EMBED_DIM: int = 1536

    # 🚀 [추가] RAG & LLM Config
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    LLM_MODEL: str = "gpt-4o"
    RAG_TOP_K: int = 5            # pgvector Top-K 검색 수
    SIM_THRESHOLD: float = 0.5    # Context Filter 유사도 임계값
    # Context Filter 동작 모드.
    #   on  : 유사도 임계값(SIM_THRESHOLD) 기반 필터
    #   off : 검색 결과를 그대로 통과 (필터 기여도 A/B 측정용)
    #   llm : LLM Context Filter Agent가 무관 chunk를 판단해 제거 (임계값은 fallback으로만 사용)
    FILTER_MODE: str = "on"
    # LLM Context Filter Agent / 검색 쿼리 번역에 쓰는 보조 모델 (본 분석 모델과 분리)
    CONTEXT_FILTER_MODEL: str = "gpt-4o-mini"
    TRANSLATION_MODEL: str = "gpt-4o-mini"
    # LLM 필터에 넘길 chunk 본문 최대 길이 (토큰 비용 보호)
    CONTEXT_FILTER_SNIPPET_CHARS: int = 600

    # 🚀 [추가] Confidence Config
    # Confidence는 LLM 자기평가가 아니라 "필터를 통과한 검색 신호"만으로 계산한다.
    # 아래 가중치는 합이 1이 아니어도 되며, confidence.py에서 합으로 정규화한다.
    STRONG_EVIDENCE_THRESHOLD: float = 0.75      # 이 유사도 이상을 "강한 근거"로 센다
    CONFIDENCE_W_TOP_SCORE: float = 0.5          # 신호1: 필터 이후 Top Similarity
    CONFIDENCE_W_EVIDENCE_COUNT: float = 0.2     # 신호2: Evidence 수(목표 개수 대비)
    CONFIDENCE_W_STRONG_EVIDENCE: float = 0.2    # 신호3: Strong Evidence 비율
    CONFIDENCE_W_FILTER_RETENTION: float = 0.1   # 신호4: (1 - filter_ratio) 잔존율
    CONFIDENCE_EVIDENCE_TARGET_COUNT: int = 3    # 신호2 포화 기준 개수
    CONFIDENCE_CONFIRM_THRESHOLD: float = 0.6    # 이 미만이면 needs_confirmation=True
    FILTER_RATIO_WARN_THRESHOLD: float = 0.8     # 이 이상 필터링되면 "검색 품질 확인 필요" 경고

    # 🚀 [추가] 근거 부족(답변 거부) 판단 기준
    # 검색 결과가 0건이거나, threshold 이상 chunk가 이 개수 미만이면 "근거 부족" 경로로 처리한다.
    MIN_GROUNDING_EVIDENCE_COUNT: int = 1

    # 🚀 [추가] Evaluation Config (eval/ 에서만 사용, 런타임 경로에는 영향 없음)
    EVAL_DEFAULT_K: int = 5
    # Judge 점수가 이 값 미만이면 해당 항목을 '실패'로 보고 오류 단계 분류 대상에 넣는다.
    EVAL_SCORE_PASS_THRESHOLD: float = 0.6

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

    # 🚀 [추가] 비동기 분석 작업(ai_analysis_jobs) 관리 정책
    # JOB_TIMEOUT_MINUTES: PROCESSING 상태가 이 시간을 넘기면 좀비 작업으로 보고 FAILED 처리한다.
    #   (서버가 분석 도중 강제 종료되면 해당 행이 영원히 PROCESSING으로 남기 때문)
    JOB_TIMEOUT_MINUTES: int = 30
    # JOB_RETENTION_DAYS: 종료(COMPLETED/FAILED)된 작업 행의 보존 기간. 0이면 자동 삭제를 하지 않는다.
    JOB_RETENTION_DAYS: int = 0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def filter_mode(self) -> str:
        """FILTER_MODE 문자열을 정규화하는 단일 지점 ("on" | "off" | "llm")"""
        mode = str(self.FILTER_MODE).strip().lower()
        if mode in ("off", "false", "0", "none"):
            return "off"
        if mode == "llm":
            return "llm"
        return "on"

    @property
    def filter_enabled(self) -> bool:
        """필터가 동작하는지 여부 (off가 아니면 True)"""
        return self.filter_mode != "off"

    @property
    def database_url(self) -> str:
        """SQLAlchemy 용 PostgreSQL 접속 URL 생성"""
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()