"""Context Filter 단위 테스트 — Top-1 보존 규칙과 FILTER_MODE=off 동작 검증"""

from services.context_filter import filter_contexts


def _chunk(chunk_id: str, score: float):
    return {"chunk_id": chunk_id, "similarity_score": score, "text": f"body of {chunk_id}"}


def test_top1_preserved_even_below_threshold():
    """Top-1이 threshold 미달이어도 필터 후 반드시 남는다."""
    chunks = [_chunk("c1", 0.31), _chunk("c2", 0.20), _chunk("c3", 0.10)]

    outcome = filter_contexts(chunks, sim_threshold=0.9, filter_mode="on")

    assert [c["chunk_id"] for c in outcome.kept] == ["c1"]
    assert outcome.top1_preserved is True
    assert [c["chunk_id"] for c in outcome.removed] == ["c2", "c3"]
    assert outcome.filter_ratio == round(2 / 3, 4)


def test_top1_is_highest_score_not_input_order():
    """입력 순서가 뒤죽박죽이어도 '최고 유사도' chunk가 Top-1로 보존된다."""
    chunks = [_chunk("low", 0.10), _chunk("high", 0.44), _chunk("mid", 0.20)]

    outcome = filter_contexts(chunks, sim_threshold=0.95, filter_mode="on")

    assert [c["chunk_id"] for c in outcome.kept] == ["high"]


def test_filter_mode_off_removes_nothing():
    """FILTER_MODE=off 면 아무것도 제거하지 않는다."""
    chunks = [_chunk("c1", 0.9), _chunk("c2", 0.1), _chunk("c3", 0.0)]

    outcome = filter_contexts(chunks, sim_threshold=0.5, filter_mode="off")

    assert len(outcome.kept) == 3
    assert outcome.removed == []
    assert outcome.filter_ratio == 0.0
    assert outcome.mode == "off"


def test_removed_chunks_are_preserved_for_evaluation():
    """제거된 chunk를 버리지 않고 removed로 돌려준다."""
    chunks = [_chunk("c1", 0.9), _chunk("c2", 0.8), _chunk("c3", 0.1)]

    outcome = filter_contexts(chunks, sim_threshold=0.5, filter_mode="on")

    assert [c["chunk_id"] for c in outcome.kept] == ["c1", "c2"]
    assert [c["chunk_id"] for c in outcome.removed] == ["c3"]
    assert outcome.retrieved_count == 3


def test_empty_input():
    outcome = filter_contexts([], sim_threshold=0.5)

    assert outcome.kept == []
    assert outcome.removed == []
    assert outcome.filter_ratio == 0.0
    assert outcome.top1_preserved is False


def test_custom_relevance_fn_cannot_break_top1_rule():
    """관련성 판단 함수를 주입해 전부 무관하다고 해도 Top-1은 남는다."""
    chunks = [_chunk("c1", 0.99), _chunk("c2", 0.98)]

    outcome = filter_contexts(chunks, sim_threshold=0.5, relevance_fn=lambda chunk, threshold: False)

    assert [c["chunk_id"] for c in outcome.kept] == ["c1"]
    assert outcome.top1_preserved is True
