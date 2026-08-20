"""오류 단계 분류 테스트 — Retrieval → Ranking → Filter → Context → Interpretation → Generation → Hallucination 순차 판정"""

from eval.error_analysis import STAGES, classify_error, summarize_error_stages, worst_stage

GOLD = ["g1"]
GOOD_SCORES = {
    "groundedness": 0.9, "faithfulness": 0.9, "completeness": 0.9,
    "answer_relevance": 0.9, "context_relevance": 0.9, "hallucination": 0.05,
}


def test_gold_never_retrieved_is_retrieval_error():
    assert classify_error(retrieved_ids=["x", "y"], filtered_ids=["x"], gold_ids=GOLD) == "Retrieval"


def test_gold_retrieved_but_outside_top_k_is_ranking_error():
    retrieved = ["a", "b", "c", "d", "e", "g1"]

    assert classify_error(retrieved_ids=retrieved, filtered_ids=retrieved, gold_ids=GOLD, k=5) == "Ranking"


def test_gold_deleted_by_filter_is_filter_error():
    assert classify_error(retrieved_ids=["g1", "x"], filtered_ids=["x"], gold_ids=GOLD, k=5) == "Filter"


def test_low_context_relevance_is_context_error():
    scores = {**GOOD_SCORES, "context_relevance": 0.2}

    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=scores, answer="답변") == "Context"


def test_low_answer_relevance_is_interpretation_error():
    scores = {**GOOD_SCORES, "answer_relevance": 0.1}

    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=scores, answer="답변") == "Interpretation"


def test_low_completeness_is_interpretation_error():
    scores = {**GOOD_SCORES, "completeness": 0.1}

    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=scores, answer="답변") == "Interpretation"


def test_empty_answer_is_generation_error():
    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=GOOD_SCORES, answer="") == "Generation"


def test_ungrounded_answer_is_hallucination():
    scores = {**GOOD_SCORES, "groundedness": 0.1, "faithfulness": 0.1, "hallucination": 0.9}

    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=scores, answer="지어낸 답") == "Hallucination"


def test_healthy_case_returns_none():
    assert classify_error(["g1"], ["g1"], GOLD, generation_scores=GOOD_SCORES, answer="답변") is None


def test_retrieval_error_wins_over_generation_scores():
    """순차 판정: 앞 단계에서 걸리면 뒤 단계 점수는 보지 않는다."""
    bad_scores = {"groundedness": 0.0, "context_relevance": 0.0}

    assert classify_error(["x"], ["x"], GOLD, generation_scores=bad_scores, answer="") == "Retrieval"


def test_missing_scores_are_not_treated_as_failure():
    assert classify_error(["g1"], ["g1"], GOLD, generation_scores={}, answer="답변") is None


def test_summarize_and_worst_stage():
    stages = ["Retrieval", "Filter", "Retrieval", None]

    assert summarize_error_stages(stages) == {"Retrieval": 2, "Filter": 1}
    assert worst_stage(stages) == "Retrieval"
    assert worst_stage([None, None]) is None


def test_stage_order_matches_spec():
    assert STAGES == (
        "Retrieval", "Ranking", "Filter", "Context", "Interpretation", "Generation", "Hallucination",
    )
