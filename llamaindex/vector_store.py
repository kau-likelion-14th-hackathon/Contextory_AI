"""pgvector(PGVectorStore) 접속 지점.

파일 기반 저장소(Chroma/FAISS 등)는 쓰지 않습니다. 벡터 저장소는 pgvector 하나입니다.

비밀번호와 접속 문자열은 로그로 출력하지 않습니다. 표시가 필요하면
`describe_target()` 이 돌려주는 마스킹된 값을 씁니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

DEFAULT_TABLE_NAME = os.getenv("VECTOR_TABLE_NAME", "contextory_embeddings")


def _default_embed_dim() -> int:
    """임베딩 차원. EMBEDDING_DIM 을 먼저 보고, 없으면 EMBED_DIM 을 씁니다."""
    raw = os.getenv("EMBEDDING_DIM") or os.getenv("EMBED_DIM") or "1536"
    return int(raw)


@dataclass(frozen=True)
class PgParams:
    """pgvector 접속 파라미터. 어디서 읽었는지(source)도 함께 들고 다닙니다."""

    host: str
    port: int
    database: str
    user: str
    password: str
    source: str

    def masked(self) -> str:
        """로그에 남겨도 되는 형태. 사용자명과 비밀번호는 제외합니다."""
        return f"postgresql://{self.host}:{self.port}/{self.database}"

    def sqlalchemy_url(self, driver: str = "psycopg2") -> str:
        return (
            f"postgresql+{driver}://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}"
        )


def resolve_pg_params() -> PgParams:
    """DATABASE_URL 을 우선 쓰고, 없으면 POSTGRES_* 개별 변수를 씁니다."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        database = (parsed.path or "/").lstrip("/")
        if not database:
            raise ValueError("DATABASE_URL 에 데이터베이스 이름이 없습니다.")
        return PgParams(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=database,
            # urlparse 결과는 percent-encoding 이 남아 있으므로 되돌립니다.
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            source="DATABASE_URL",
        )

    return PgParams(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "contextory_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        source="POSTGRES_* 환경 변수",
    )


def describe_target(table_name: str | None = None) -> dict[str, Any]:
    """접속 대상을 마스킹해 돌려줍니다. 보고서·로그용입니다."""
    params = resolve_pg_params()
    return {
        "vector_store": "pgvector",
        "target": params.masked(),
        "config_source": params.source,
        "table_name": table_name or DEFAULT_TABLE_NAME,
        "embed_dim": _default_embed_dim(),
    }


def get_vector_store(
    table_name: str = DEFAULT_TABLE_NAME,
    embed_dim: int | None = None,
    perform_setup: bool = True,
    hybrid_search: bool = False,
) -> Any:
    """PGVectorStore 를 만들어 돌려줍니다.

    `perform_setup=True` 면 테이블이 없을 때 생성합니다(CREATE TABLE IF NOT EXISTS).
    기존 테이블을 지우지 않습니다.
    """
    from llama_index.vector_stores.postgres import PGVectorStore

    params = resolve_pg_params()
    dim = embed_dim if embed_dim is not None else _default_embed_dim()

    return PGVectorStore.from_params(
        host=params.host,
        port=str(params.port),
        database=params.database,
        user=params.user,
        password=params.password,
        table_name=table_name,
        embed_dim=dim,
        hybrid_search=hybrid_search,
        perform_setup=perform_setup,
    )


def healthcheck() -> tuple[bool, str]:
    """접속 가능 여부와 사유를 돌려줍니다. 접속 문자열은 노출하지 않습니다."""
    params = resolve_pg_params()
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return False, "sqlalchemy 가 설치되어 있지 않습니다."

    last = "설치된 PostgreSQL 드라이버가 없습니다."
    for driver in ("psycopg2", "psycopg"):
        try:
            engine = create_engine(params.sqlalchemy_url(driver))
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True, f"{params.masked()} 에 접속했습니다 (드라이버 {driver})."
        except Exception as exc:  # 드라이버 미설치/접속 실패 모두 여기로 옵니다.
            last = f"{type(exc).__name__}: {exc}"
    return False, f"pgvector 접속 실패 ({params.masked()}): {last}"


def count_vectors(table_name: str = DEFAULT_TABLE_NAME) -> int | None:
    """테이블에 적재된 벡터 개수. 조회에 실패하면 None 을 돌려줍니다.

    PGVectorStore 는 `table_name` 앞에 `data_` 접두사를 붙여 실제 테이블을 만듭니다.
    """
    params = resolve_pg_params()
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return None

    physical = table_name if table_name.startswith("data_") else f"data_{table_name}"
    for driver in ("psycopg2", "psycopg"):
        try:
            engine = create_engine(params.sqlalchemy_url(driver))
            with engine.connect() as conn:
                result = conn.execute(text(f'SELECT COUNT(*) FROM public."{physical}"'))
                return int(result.scalar_one())
        except Exception:
            continue
    return None
