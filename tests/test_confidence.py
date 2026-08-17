"""Confidence 단위 테스트 — 검색 신호 기반 계산과 경고 플래그 검증"""

from services.confidence import calculate_confidence, compute_confidence

WEIGHTS = {"top_score": 0.5, "evidence_count": 0.2, "strong_evidence": 0.2, "filter_retention": 0.1}


def test_no_evidence_gives_zero_and_needs_confirmation():
    outcome = compute_confidence([], filter_ratio=0.0)

    assert outcome.score == 0.0
    assert outcome.needs_confirmation is True
    assert outcome.signals["evidence_count"] == 0


def test_weighted_formula_matches_hand_calculation():
    """0.9/0.8/0.6 세 근거, strong 기준 0.75, 목표 개수 3, filter_ratio 0.0"""
    outcome = compute_confidence(
        [0.9, 0.8, 0.6],
        filter_ratio=0.0,
        strong_threshold=0.75,
        evidence_target_count=3,
        valid_evidence_threshold=0.5,
        weights=WEIGHTS,
    )

    # top=0.9, evidence_ratio=3/3=1.0, strong_ratio=2/3, retention=1.0
    expected = (0.5 * 0.9 + 0.2 * 1.0 + 0.2 * (2 / 3) + 0.1 * 1.0) / 1.0
    assert outcome.score == round(expected, 4)
    assert outcome.signals["strong_evidence_count"] == 2


def test_evidence_below_threshold_is_not_counted_as_evidence():
    """Top-1 보존으로 남은 저유사도 chunk는 '근거 수' 신호로 세지 않는다."""
    strong = compute_confidence([0.9, 0.8], filter_ratio=0.0, valid_evidence_threshold=0.5, weights=WEIGHTS)
    padded = compute_confidence([0.9, 0.8, 0.1, 0.05], filter_ratio=0.0, valid_evidence_threshold=0.5, weights=WEIGHTS)

    assert padded.signals["evidence_count"] == strong.signals["evidence_count"] == 2
    assert padded.signals["kept_chunk_count"] == 4
    assert padded.score == strong.score


def test_high_filter_ratio_raises_retrieval_quality_warning():
    outcome = compute_confidence([0.95], filter_ratio=0.9, filter_ratio_warn_threshold=0.8)

    assert outcome.retrieval_quality_warning is True
    assert outcome.needs_confirmation is True


def test_low_score_sets_needs_confirmation():
    outcome = compute_confidence([0.4], filter_ratio=0.0, confirm_threshold=0.6, valid_evidence_threshold=0.5)

    assert outcome.score < 0.6
    assert outcome.needs_confirmation is True


def test_weights_are_normalized_by_their_sum():
    """가중치 합이 1이 아니어도 0~1 범위를 유지한다."""
    outcome = compute_confidence(
        [1.0, 1.0, 1.0],
        filter_ratio=0.0,
        weights={"top_score": 5, "evidence_count": 2, "strong_evidence": 2, "filter_retention": 1},
    )

    assert outcome.score == 1.0


def test_calculate_confidence_accepts_chunk_dicts():
    outcome = calculate_confidence([{"similarity_score": 0.9}, {"similarity_score": 0.7}], filter_ratio=0.5)

    assert 0.0 < outcome.score <= 1.0
    assert outcome.signals["filter_ratio"] == 0.5
