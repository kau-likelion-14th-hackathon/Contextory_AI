"""벡터 스토어 추상화."""

from __future__ import annotations

from typing import Any, Protocol


class VectorStoreAdapter(Protocol):
    """벡터 스토어 교체 지점. 구현체는 이 프로토콜을 만족해야 합니다."""

    def get_store(self) -> Any:
        """LlamaIndex 가 사용할 vector store 객체를 돌려줍니다."""
        ...

    def healthcheck(self) -> bool:
        ...


class PgVectorAdapter:
    """PostgreSQL + pgvector 어댑터.

    DATABASE_URL 은 Spring Boot 백엔드와 같은 DB 를 가리킵니다.
    임베딩 테이블은 AI 파트 전용이며, 관계형 핵심 테이블과 분리합니다.
    """

    def __init__(self, table_name: str | None = None, embed_dim: int | None = None) -> None:
        self.table_name = table_name
        self.embed_dim = embed_dim

    def get_store(self) -> Any:
        """llama_index.vector_stores.postgres.PGVectorStore.from_params(...) 를 반환합니다."""
        raise NotImplementedError

    def healthcheck(self) -> bool:
        raise NotImplementedError


def get_vector_store_adapter() -> VectorStoreAdapter:
    """설정에 따라 어댑터를 고릅니다. 교체 시 이 함수만 바꿉니다."""
    raise NotImplementedError
