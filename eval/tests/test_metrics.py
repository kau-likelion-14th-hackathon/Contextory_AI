"""검색/필터 지표 수치 검증 — 손으로 계산 가능한 소규모 입력 사용"""

import pytest

from eval.metrics.filtering import (
    compute_false_deletion, compute_filter_metrics, compute_filter_ratio,
    compute_gold_retained, compute_recall_delta_at_k,
)
from eval.metrics.retrieval import (
    average_metrics, compute_hit_rate, compute_mrr, compute_precision_at_k,
    compute_recall_at_k, compute_retrieval_failure_rate, compute_retrieval_metrics,
    is_retrieval_failure,
)

# 5개 검색, 그중 gold 2개(2번째·4번째)
RETRIEVED = ["c1", "c2", "c3", "c4", "c5"]
GOLD = ["c2", "c4"]


def test_precision_at_k_hand_calculation():
    # Top-5 안의 gold 2개 / Top-5 chunk 5개 = 0.4
    assert compute_precision_at_k(RETRIEVED, GOLD, k=5) == pytest.approx(0.4)
    # Top-3 안의 gold는 c2 하나 / 3 = 0.3333
    assert compute_precision_at_k(RETRIEVED, GOLD, k=3) == pytest.approx(1 / 3)


def test_recall_at_k_hand_calculation():
    assert compute_recall_at_k(RETRIEVED, GOLD, k=5) == pytest.approx(1.0)
    assert compute_recall_at_k(RETRIEVED, GOLD, k=3) == pytest.approx(0.5)
    assert compute_recall_at_k(RETRIEVED, GOLD, k=1) == pytest.approx(0.0)


def test_mrr_is_reciprocal_of_first_gold_rank():
    # 첫 gold(c2)가 2위 → 1/2
    assert compute_mrr(RETRIEVED, GOLD) == pytest.approx(0.5)
    assert compute_mrr(["c4", "c1"], GOLD) == pytest.approx(1.0)
    assert compute_mrr(["c1", "c3"], GOLD) == pytest.approx(0.0)


def test_hit_rate():
    assert compute_hit_rate(RETRIEVED, GOLD, k=5) == 1.0
    assert compute_hit_rate(RETRIEVED, GOLD, k=1) == 0.0


def test_empty_and_edge_cases():
    assert compute_precision_at_k([], GOLD, k=5) == 0.0
    assert compute_recall_at_k(RETRIEVED, [], k=5) == 0.0
    assert compute_precision_at_k(RETRIEVED, GOLD, k=0) == 0.0


def test_retrieval_failure_detection():
    assert is_retrieval_failure([], None) is True
    assert is_retrieval_failure(["c1"], [0.2, 0.1], sim_threshold=0.5) is True
    assert is_retrieval_failure(["c1"], [0.6, 0.1], sim_threshold=0.5) is False
    assert compute_retrieval_failure_rate([True, False, False, False]) == pytest.approx(0.25)


def test_compute_retrieval_metrics_bundle():
    metrics = compute_retrieval_metrics(RETRIEVED, GOLD, k=5, scores=[0.9, 0.8, 0.4, 0.3, 0.1], sim_threshold=0.5)

    assert metrics["precision_at_k"] == pytest.approx(0.4)
    assert metrics["recall_at_k"] == pytest.approx(1.0)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert metrics["hit_rate"] == 1.0
    assert metrics["retrieval_failure"] == 0.0


def test_average_metrics():
    averaged = average_metrics([{"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 1.0}])

    assert averaged == {"a": 0.5, "b": 0.5}


# ==========================================
# 필터 지표
# ==========================================

FILTERED_OK = ["c1", "c2", "c4"]      # gold 둘 다 유지
FILTERED_BAD = ["c1", "c2"]           # gold c4를 삭제


def test_filter_ratio_hand_calculation():
    # 5개 중 2개 유지 → 3개 제거 → 0.6
    assert compute_filter_ratio(RETRIEVED, FILTERED_BAD) == pytest.approx(0.6)
    assert compute_filter_ratio(RETRIEVED, RETRIEVED) == 0.0
    assert compute_filter_ratio([], []) == 0.0


def test_gold_retained_and_false_deletion():
    assert compute_gold_retained(FILTERED_OK, GOLD) == pytest.approx(1.0)
    assert compute_false_deletion(RETRIEVED, FILTERED_OK, GOLD) == pytest.approx(0.0)

    # gold 2개 중 1개(c4) 삭제 → 0.5
    assert compute_gold_retained(FILTERED_BAD, GOLD) == pytest.approx(0.5)
    assert compute_false_deletion(RETRIEVED, FILTERED_BAD, GOLD) == pytest.approx(0.5)


def test_false_deletion_ignores_never_retrieved_gold():
    """애초에 검색되지 않은 gold는 필터 잘못이 아니다."""
    assert compute_false_deletion(["c1"], ["c1"], ["c2", "c4"]) == 0.0


def test_recall_delta_is_negative_when_filter_drops_gold():
    # 필터 전 recall 1.0 → 후 0.5 → -0.5
    assert compute_recall_delta_at_k(RETRIEVED, FILTERED_BAD, GOLD, k=5) == pytest.approx(-0.5)
    assert compute_recall_delta_at_k(RETRIEVED, FILTERED_OK, GOLD, k=5) == pytest.approx(0.0)


def test_compute_filter_metrics_bundle():
    metrics = compute_filter_metrics(RETRIEVED, FILTERED_BAD, GOLD, k=5)

    assert metrics["filter_ratio"] == pytest.approx(0.6)
    assert metrics["gold_retained"] == pytest.approx(0.5)
    assert metrics["false_deletion"] == pytest.approx(0.5)
    assert metrics["recall_delta_at_k"] == pytest.approx(-0.5)
    assert metrics["deleted_gold_count"] == 1.0
    assert metrics["retrieved_gold_count"] == 2.0
