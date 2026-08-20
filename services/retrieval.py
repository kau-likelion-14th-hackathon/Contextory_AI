"""
retrieval.py — pgvector 유사도 검색 단계 (RAG Pipeline ①②)

설계 원칙
- 이 모듈은 "검색"만 책임진다. 필터/프롬프트/신뢰도 계산은 하지 않는다.
- 임베딩 함수와 검색 함수를 전부 주입 가능하게 두어(embed_fn / search_fn),
  DB·OpenAI 없이 단위 테스트가 가능하다.
- 검색 실패(결과 없음 / 전부 threshold 미달)를 정상 결과처럼 넘기지 않고
  RetrievalOutcome.grounding_sufficient 신호로 명시해 상위 계층에 전달한다.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from llama_index.core.vector_stores.types import (
    VectorStoreQuery,
    MetadataFilters,
    MetadataFilter,
    FilterOperator,
)

from core.config import settings

# 검색 결과 chunk의 표준 형태(dict)
#   chunk_id         : 프롬프트/evidence에서 인용하는 식별자
#   source_type      : "code_review" | "repo_code"
#   source           : 출처 표기(파일 경로 또는 dataset_source)
#   text             : 프롬프트에 실을 본문
#   similarity_score : 코사인 유사도 (0.0~1.0)
RetrievedChunk = Dict[str, Any]

SOURCE_CODE_REVIEW = "code_review"
SOURCE_REPO_CODE = "repo_code"


class RetrievalError(RuntimeError):
    """Vector DB 조회 실패. 외부 인프라 오류를 '검색 결과 없음'으로 위장하지 않기 위해 분리한다."""


@dataclass
class RetrievalOutcome:
    """검색 단계의 산출물 + 근거 충분성 신호"""

    chunks: List[RetrievedChunk] = field(default_factory=list)
    top_score: float = 0.0
    above_threshold_count: int = 0
    sim_threshold: float = 0.0
    grounding_sufficient: bool = False
    reason: str = ""

    @property
    def retrieved_count(self) -> int:
        return len(self.chunks)


# ==========================================
# 1. 임베딩 (① PR Diff Embedding)
# ==========================================

# text-embedding-3-small/large의 입력 한도. 초과 시 OpenAI가 400(Invalid 'input[0]')을 반환한다.
# PR Diff 전체를 쿼리 텍스트로 쓰므로 대형 PR에서는 쉽게 이 한도를 넘긴다.
EMBEDDING_MAX_TOKENS = 8192
# 전송 형태로 바꿨을 때 한도를 넘으면 이 폭만큼 줄여 가며 다시 확인한다.
# 실측상 초과분은 수십 토큰 수준이라 몇 회 안에 들어온다.
EMBEDDING_TRUNCATE_STEP = 64


def _as_sent_to_api(text_input: str) -> str:
    """
    임베딩 API에 실제로 전송되는 형태.

    llama_index의 OpenAIEmbedding은 전송 직전에 개행을 공백으로 바꾼다
    (llama_index/embeddings/openai/base.py: `text = text.replace("\\n", " ")`).
    이 치환은 토큰 수를 바꾼다 — 코드/diff처럼 "개행+들여쓰기"가 한 토큰으로 묶이던
    자리가 공백으로 풀리면서 토큰이 늘 수 있다.

    그래서 우리가 센 토큰 수와 OpenAI가 세는 수가 어긋난다. 정확히 8192로 잘라 보내도
    400(Invalid 'input[0]')이 나는 경우가 여기서 생긴다
    (실측: 프론트 PR #42 → 자른 뒤 8,192 토큰이 전송 형태로는 8,218 토큰).
    """
    return text_input.replace("\n", " ")


def truncate_to_token_limit(text_input: str, model: Optional[str] = None, max_tokens: int = EMBEDDING_MAX_TOKENS) -> str:
    """
    임베딩 모델의 최대 입력 토큰 수를 넘지 않도록 앞부분 기준으로 자른다.

    판단 기준은 "우리가 자른 문자열"이 아니라 "실제로 전송되는 형태"다(_as_sent_to_api).
    치환으로 토큰이 늘어 한도를 넘으면 들어올 때까지 조금씩 더 줄인다.
    """
    import tiktoken  # 지연 import: 인코딩 파일 로드를 실제 임베딩 시점까지 미룬다

    try:
        encoding = tiktoken.encoding_for_model(model or settings.EMBEDDING_MODEL)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    def fits(candidate: str) -> bool:
        return len(encoding.encode(_as_sent_to_api(candidate))) <= max_tokens

    if fits(text_input):
        return text_input

    tokens = encoding.encode(text_input)
    limit = min(max_tokens, len(tokens))
    while limit > 0:
        candidate = encoding.decode(tokens[:limit])
        if fits(candidate):
            return candidate
        limit -= EMBEDDING_TRUNCATE_STEP
    return ""


def _default_embed(text_input: str) -> List[float]:
    """
    llamaindex.pipeline.get_embed_model()을 재사용해 임베딩한다.
    (인덱싱 시점과 조회 시점의 임베딩 모델을 단일 지점에서 일치시키기 위함)
    입력은 모델 토큰 한도로 잘라서 보낸다.

    Celery prefork worker(포크된 자식 프로세스)에서 이 지연 import가 처음
    실행될 때 sys.path에 프로젝트 루트가 빠져 ModuleNotFoundError가 나는
    경우가 관측됐다(부모 프로세스에서 이미 로드된 다른 top-level 패키지는
    sys.modules 캐시로 재사용되어 영향이 없었지만, 지연 import라 이 모듈만
    자식 프로세스에서 처음 import를 시도해 노출됨). CWD 상대경로('')에
    의존하지 않도록 프로젝트 루트를 절대경로로 직접 보장한다.
    """
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    from llamaindex.pipeline import get_embed_model  # 지연 import: 테스트 시 불필요한 초기화 회피

    return get_embed_model().get_text_embedding(truncate_to_token_limit(text_input))


def embed_query(text_input: str, embed_fn: Optional[Callable[[str], List[float]]] = None) -> List[float]:
    """입력 쿼리 텍스트를 임베딩 벡터로 변환한다. embed_fn 주입 시 그것을 사용한다."""
    return (embed_fn or _default_embed)(text_input)


# ==========================================
# 2. 검색 결과 정규화
# ==========================================

def make_review_chunk_id(row_id: Any) -> str:
    """
    code_review_vectors 행 id → chunk_id 규칙 (단일 정의 지점).
    평가 데이터셋의 gold chunk id도 이 함수를 통해 만들어야 런타임 결과와 맞물린다.
    """
    return f"cr-{row_id}"


def make_repo_chunk_id(node_id: Any) -> str:
    """repo_code_vectors 노드 id → chunk_id 규칙 (단일 정의 지점)"""
    return f"repo-{str(node_id)[:8]}"


def _normalize_review_row(row: Dict[str, Any]) -> RetrievedChunk:
    """code_review_vectors 한 행을 표준 chunk 형태로 변환"""
    body = row.get("review_comment") or row.get("pr_diff") or row.get("source_code") or ""
    return {
        "chunk_id": make_review_chunk_id(row.get("id")),
        "id": str(row.get("id")),
        "source_type": SOURCE_CODE_REVIEW,
        "source": row.get("dataset_source") or settings.CODE_REVIEW_TABLE_NAME,
        "text": body,
        "similarity_score": round(float(row.get("similarity_score") or 0.0), 4),
        "orig_idx": row.get("orig_idx"),
        "dataset_source": row.get("dataset_source"),
        "source_code": row.get("source_code"),
        "pr_diff": row.get("pr_diff"),
        "review_comment": row.get("review_comment"),
        "has_issue": row.get("has_issue"),
        "file_path": None,
    }


def _normalize_repo_node(node_id: str, file_path: str, content: str, score: float) -> RetrievedChunk:
    """repo_code_vectors(LlamaIndex 노드) 한 건을 표준 chunk 형태로 변환"""
    return {
        "chunk_id": make_repo_chunk_id(node_id),
        "id": str(node_id),
        "source_type": SOURCE_REPO_CODE,
        "source": file_path,
        "text": content,
        "similarity_score": round(float(score), 4),
        "file_path": file_path,
        "source_code": content,
        "pr_diff": None,
        "review_comment": None,
    }


# ==========================================
# 3. 검색 (② PGVector Similarity Search)
# ==========================================

def retrieve_contexts(
    query_text: str,
    top_k: Optional[int] = None,
    sim_threshold: Optional[float] = None,  # 하위 호환용(이 함수는 필터링하지 않는다)
    embed_fn: Optional[Callable[[str], List[float]]] = None,
    db_engine: Any = None,
) -> List[RetrievedChunk]:
    """
    PR Diff 기반 pgvector 코사인 유사도 Top-K 검색 (code_review_vectors).

    전역 리뷰 모범사례 KB는 프로젝트 격리 대상이 아니므로 repo_name 필터를 걸지 않는다.
    threshold 적용은 Context Filter 단계의 책임이므로 여기서는 자르지 않는다.
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K

    query_vector = embed_query(query_text, embed_fn=embed_fn)

    sql = text(
        f"""
        SELECT
            id,
            orig_idx,
            dataset_source,
            source_code,
            pr_diff,
            review_comment,
            has_issue,
            1 - (embedding <=> :query_vector) AS similarity_score
        FROM {settings.CODE_REVIEW_TABLE_NAME}
        ORDER BY embedding <=> :query_vector ASC
        LIMIT :top_k;
        """
    )

    # pgvector 파라미터는 "[0.1,0.2,...]" 형태의 문자열 포맷이 필요하다.
    vector_str = f"[{','.join(map(str, query_vector))}]"

    if db_engine is None:
        from core.db import engine as db_engine  # 지연 import: import 시점 DB 커넥션 생성 회피

    try:
        with db_engine.connect() as conn:
            rows = conn.execute(sql, {"query_vector": vector_str, "top_k": top_k}).mappings().all()
    except SQLAlchemyError as e:
        raise RetrievalError(f"code_review_vectors 검색 실패: {e}") from e

    return [_normalize_review_row(dict(row)) for row in rows]


def retrieve_repo_contexts(
    query_text: str,
    repo_name: str,
    top_k: Optional[int] = None,
    embed_fn: Optional[Callable[[str], List[float]]] = None,
    vector_store: Any = None,
) -> List[RetrievedChunk]:
    """
    현재 분석 대상 프로젝트의 소스코드 Context(repo_code_vectors)를
    repo_name 메타데이터로 격리해 Top-K 검색한다. (멀티 리포 혼입 방지)
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K

    query_vector = embed_query(query_text, embed_fn=embed_fn)

    if vector_store is None:
        from llamaindex.vector_store import get_vector_store  # 지연 import

        vector_store = get_vector_store(table_name=settings.REPO_CODE_TABLE_NAME)

    filters = MetadataFilters(
        filters=[MetadataFilter(key="repo_name", value=repo_name, operator=FilterOperator.EQ)]
    )
    query_obj = VectorStoreQuery(
        query_embedding=query_vector,
        similarity_top_k=top_k,
        filters=filters,
    )

    try:
        query_result = vector_store.query(query_obj)
    except Exception as e:  # PGVectorStore는 SQLAlchemyError 외 예외도 올린다
        raise RetrievalError(f"repo_code_vectors 검색 실패: {e}") from e

    results: List[RetrievedChunk] = []
    for idx, node in enumerate(query_result.nodes or []):
        score = query_result.similarities[idx] if query_result.similarities else 0.0
        results.append(
            _normalize_repo_node(
                node_id=node.node_id,
                file_path=node.metadata.get("file_path", "unknown"),
                content=node.get_content(),
                score=score,
            )
        )
    return results


# ==========================================
# 4. 검색 + 근거 충분성 신호
# ==========================================

def build_outcome(
    chunks: List[RetrievedChunk],
    sim_threshold: Optional[float] = None,
    min_evidence_count: Optional[int] = None,
) -> RetrievalOutcome:
    """검색 결과 리스트에 '근거 충분성' 판단을 붙인다. (순수 함수 — 단위 테스트 대상)"""
    if sim_threshold is None:
        sim_threshold = settings.SIM_THRESHOLD
    if min_evidence_count is None:
        min_evidence_count = settings.MIN_GROUNDING_EVIDENCE_COUNT

    ordered = sorted(chunks, key=lambda c: c.get("similarity_score", 0.0), reverse=True)
    top_score = ordered[0].get("similarity_score", 0.0) if ordered else 0.0
    above = [c for c in ordered if c.get("similarity_score", 0.0) >= sim_threshold]

    if not ordered:
        return RetrievalOutcome(
            chunks=[],
            top_score=0.0,
            above_threshold_count=0,
            sim_threshold=sim_threshold,
            grounding_sufficient=False,
            reason="검색 결과가 없습니다.",
        )

    if len(above) < min_evidence_count:
        return RetrievalOutcome(
            chunks=ordered,
            top_score=top_score,
            above_threshold_count=len(above),
            sim_threshold=sim_threshold,
            grounding_sufficient=False,
            reason=(
                f"검색된 {len(ordered)}건 중 유사도 {sim_threshold} 이상이 "
                f"{len(above)}건(최고 {top_score})으로 근거 기준({min_evidence_count}건) 미달입니다."
            ),
        )

    return RetrievalOutcome(
        chunks=ordered,
        top_score=top_score,
        above_threshold_count=len(above),
        sim_threshold=sim_threshold,
        grounding_sufficient=True,
        reason="",
    )


def retrieve_with_signals(
    query_text: str,
    repo_name: Optional[str] = None,
    top_k: Optional[int] = None,
    sim_threshold: Optional[float] = None,
    review_search_fn: Optional[Callable[..., List[RetrievedChunk]]] = None,
    repo_search_fn: Optional[Callable[..., List[RetrievedChunk]]] = None,
) -> RetrievalOutcome:
    """
    code_review_vectors + repo_code_vectors를 함께 검색해 단일 후보 목록으로 합치고,
    근거 충분성 신호를 포함한 RetrievalOutcome을 반환한다.

    두 검색 함수를 주입할 수 있어 DB 없이 파이프라인 전체를 테스트할 수 있다.
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K

    review_fn = review_search_fn or retrieve_contexts
    review_chunks = review_fn(query_text=query_text, top_k=top_k)

    repo_chunks: List[RetrievedChunk] = []
    if repo_name:
        repo_fn = repo_search_fn or retrieve_repo_contexts
        repo_chunks = repo_fn(query_text=query_text, repo_name=repo_name, top_k=top_k)

    return build_outcome(list(review_chunks) + list(repo_chunks), sim_threshold=sim_threshold)
