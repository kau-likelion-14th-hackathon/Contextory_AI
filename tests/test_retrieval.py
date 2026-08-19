"""Retrieval 단위 테스트 — 근거 충분성 신호와 DB 오류 구분 (DB·OpenAI 호출 없음)"""

import pytest
from sqlalchemy.exc import OperationalError

from services.retrieval import (
    RetrievalError, build_outcome, retrieve_contexts, retrieve_with_signals,
)


def _chunk(chunk_id: str, score: float):
    return {"chunk_id": chunk_id, "similarity_score": score, "text": chunk_id}


def test_empty_result_is_not_grounded():
    outcome = build_outcome([], sim_threshold=0.5)

    assert outcome.grounding_sufficient is False
    assert "검색 결과가 없습니다" in outcome.reason


def test_all_below_threshold_is_not_grounded():
    outcome = build_outcome([_chunk("c1", 0.31), _chunk("c2", 0.2)], sim_threshold=0.5, min_evidence_count=1)

    assert outcome.grounding_sufficient is False
    assert outcome.above_threshold_count == 0
    assert outcome.top_score == 0.31
    assert outcome.chunks  # 근거 부족이어도 검색 결과 자체는 버리지 않는다


def test_sufficient_grounding():
    outcome = build_outcome([_chunk("c1", 0.8), _chunk("c2", 0.2)], sim_threshold=0.5, min_evidence_count=1)

    assert outcome.grounding_sufficient is True
    assert outcome.above_threshold_count == 1
    assert outcome.retrieved_count == 2


def test_chunks_are_sorted_by_score_desc():
    outcome = build_outcome([_chunk("low", 0.1), _chunk("high", 0.9)], sim_threshold=0.5)

    assert [c["chunk_id"] for c in outcome.chunks] == ["high", "low"]


def test_retrieve_with_signals_merges_both_sources():
    review = [_chunk("cr-1", 0.6)]
    repo = [_chunk("repo-1", 0.8)]

    outcome = retrieve_with_signals(
        query_text="q",
        repo_name="org/repo",
        top_k=5,
        review_search_fn=lambda **kwargs: review,
        repo_search_fn=lambda **kwargs: repo,
    )

    assert [c["chunk_id"] for c in outcome.chunks] == ["repo-1", "cr-1"]
    assert outcome.grounding_sufficient is True


def test_repo_search_skipped_when_no_repo_name():
    called = {"repo": False}

    def repo_fn(**kwargs):
        called["repo"] = True
        return []

    outcome = retrieve_with_signals(
        query_text="q",
        repo_name=None,
        review_search_fn=lambda **kwargs: [_chunk("cr-1", 0.9)],
        repo_search_fn=repo_fn,
    )

    assert called["repo"] is False
    assert outcome.retrieved_count == 1


def test_db_error_is_raised_as_retrieval_error():
    """DB 오류를 '검색 결과 없음'으로 위장하지 않는다."""

    class _FailingEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    with pytest.raises(RetrievalError):
        retrieve_contexts(
            query_text="q",
            top_k=3,
            embed_fn=lambda text: [0.0, 0.1],
            db_engine=_FailingEngine(),
        )


# ==========================================
# 임베딩 입력 자르기 — 전송 형태 기준으로 판단해야 한다
# ==========================================

def test_truncate_counts_tokens_as_sent_to_api():
    """
    llama_index의 OpenAIEmbedding은 전송 직전에 개행을 공백으로 바꾼다.
    그 치환으로 토큰이 늘어나면, 우리가 8192로 정확히 잘라 보내도 OpenAI가 400을 낸다
    (실측: 프론트 PR #42 → 자른 뒤 8,192 토큰이 전송 형태로는 8,218 토큰).
    그래서 판단 기준은 자른 문자열이 아니라 '실제로 전송되는 형태'여야 한다.
    """
    import tiktoken

    from services.retrieval import _as_sent_to_api, truncate_to_token_limit

    encoding = tiktoken.get_encoding("cl100k_base")
    limit = 200
    # 개행 + 들여쓰기가 반복되는 코드/diff 형태 (치환 시 토큰 수가 변한다)
    text = "\n".join("    const value = useQuery({ queryKey: [pullRequest] });" for _ in range(400))

    out = truncate_to_token_limit(text, max_tokens=limit)

    assert len(encoding.encode(_as_sent_to_api(out))) <= limit


def test_truncate_keeps_short_input_untouched():
    from services.retrieval import truncate_to_token_limit

    text = "short query"
    assert truncate_to_token_limit(text, max_tokens=100) == text
