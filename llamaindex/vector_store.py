"""pgvector(PGVectorStore) 접속 지점.

파일 기반 저장소(Chroma/FAISS 등)는 쓰지 않으며, 벡터 저장소는 pgvector를 사용합니다.
비밀번호와 접속 문자열은 로그 출력 시 마스킹 처리(`describe_target()`)를 거칩니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse
from dotenv import load_dotenv

load_dotenv()

# 기본 테이블명을 'code_embeddings'로 유지합니다.
DEFAULT_TABLE_NAME = os.getenv("VECTOR_TABLE_NAME", "code_embeddings")


def _default_embed_dim() -> int:
    """임베딩 차원. EMBEDDING_DIM 을 먼저 보고, 없으면 EMBED_DIM 을 씁니다."""
    raw = os.getenv("EMBEDDING_DIM") or os.getenv("EMBED_DIM") or "1536"
    return int(raw)


@dataclass(frozen=True)
class PgParams:
    """pgvector 접속 파라미터. 어디서 읽었는지(source)도 함께 유지합니다."""

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
        """특수문자 패스워드 인코딩 및 UTF-8 클라이언트 인코딩이 반영된 접속 URL을 생성합니다."""
        return (
            f"postgresql+{driver}://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}?client_encoding=utf8"
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
    """PGVectorStore 를 생성하여 반환합니다.

    `perform_setup=True` 옵션으로 테이블이 없을 때 자동 생성합니다(CREATE TABLE IF NOT EXISTS).
    connection_string을 명시하여 UTF-8 인코딩 및 특수문자 패스워드 방지를 보장합니다.
    """
    from llama_index.vector_stores.postgres import PGVectorStore

    params = resolve_pg_params()
    dim = embed_dim if embed_dim is not None else _default_embed_dim()
    connection_string = params.sqlalchemy_url("psycopg2")

    return PGVectorStore.from_params(
        connection_string=connection_string,
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
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return False, f"pgvector 접속 실패 ({params.masked()}): {last}"


def count_vectors(table_name: str = DEFAULT_TABLE_NAME) -> int | None:
    """테이블에 적재된 벡터 개수를 조회합니다. 조회 실패 시 None 을 반환합니다.

    PGVectorStore 는 `table_name` 앞에 `data_` 접두사를 붙여 실제 테이블을 생성합니다.
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