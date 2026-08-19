"""리포트 테스트 — Fake Confidence 판정 규칙표와 Filter OFF/ON 비교표 출력 검증"""

import json

from eval.report import (
    VERDICT_FAKE, VERDICT_IMPROVED, VERDICT_NO_GAIN, build_report, judge_fake_confidence,
    to_json_report, to_text_report,
)


def _aggregate(precision, confidence, faithfulness, filter_ratio=0.0, false_deletion=0.0, recall_delta=0.0):
    return {
        "k": 5,
        "case_count": 3,
        "ground_truth_case_count": 3,
        "error_case_count": 0,
        "retrieval": {"precision_at_k": precision, "recall_at_k": 1.0, "mrr": 1.0, "hit_rate": 1.0},
        "context": {"precision_at_k": precision, "recall_at_k": 1.0 + recall_delta},
        "filtering": {
            "filter_ratio": filter_ratio,
            "false_deletion": false_deletion,
            "recall_delta_at_k": recall_delta,
            "gold_retained": 1.0 - false_deletion,
        },
        "generation": {"faithfulness": faithfulness, "groundedness": faithfulness},
        "signals": {"top_score": 0.9, "filter_ratio": filter_ratio},
        "confidence": confidence,
        "error_stages": {},
        "retrieval_failure_rate": 0.0,
        "hallucination_rate": 0.0,
    }


def test_normal_improvement_verdict():
    off = _aggregate(precision=0.4, confidence=0.60, faithfulness=0.80)
    on = _aggregate(precision=1.0, confidence=0.75, faithfulness=0.90, filter_ratio=0.6)

    verdict = judge_fake_confidence(off, on)

    assert verdict["verdict"] == VERDICT_IMPROVED
    assert verdict["deltas"]["confidence"] == 0.15
    assert verdict["warnings"] == []


def test_fake_confidence_when_gold_is_deleted():
    """Confidence↑ 인데 false_deletion>0 / recall_delta<0 이면 Fake Confidence."""
    off = _aggregate(precision=0.4, confidence=0.60, faithfulness=0.80)
    on = _aggregate(
        precision=1.0, confidence=0.85, faithfulness=0.90,
        filter_ratio=0.6, false_deletion=0.33, recall_delta=-0.33,
    )

    verdict = judge_fake_confidence(off, on)

    assert verdict["verdict"] == VERDICT_FAKE
    assert any("threshold 하향" in reason for reason in verdict["reasons"])
    assert any("false_deletion" in reason for reason in verdict["reasons"])
    assert any("recall_delta" in reason for reason in verdict["reasons"])


def test_high_filter_ratio_warns_regardless_of_confidence():
    off = _aggregate(precision=0.4, confidence=0.60, faithfulness=0.80)
    on = _aggregate(precision=1.0, confidence=0.50, faithfulness=0.80, filter_ratio=0.9)

    verdict = judge_fake_confidence(off, on, filter_ratio_warn_threshold=0.8)

    assert verdict["verdict"] == VERDICT_NO_GAIN          # Confidence가 안 올랐으니 개선 아님
    assert any("검색 품질 확인 필요" in w for w in verdict["warnings"])


def test_no_confidence_gain_is_not_improvement():
    off = _aggregate(precision=0.4, confidence=0.70, faithfulness=0.80)
    on = _aggregate(precision=1.0, confidence=0.65, faithfulness=0.85, filter_ratio=0.5)

    assert judge_fake_confidence(off, on)["verdict"] == VERDICT_NO_GAIN


def test_missing_measurements_are_reported_not_assumed():
    off = {"confidence": 0.5}
    on = {"confidence": 0.7}

    verdict = judge_fake_confidence(off, on)

    assert verdict["verdict"] == VERDICT_IMPROVED
    assert any("미측정" in reason for reason in verdict["reasons"])


def test_text_report_contains_comparison_table_and_verdict():
    report = build_report({
        "filter_off": {"mode": "filter_off", "aggregate": _aggregate(0.4, 0.60, 0.80), "cases": []},
        "filter_on": {
            "mode": "filter_on",
            "aggregate": _aggregate(1.0, 0.85, 0.90, filter_ratio=0.6, false_deletion=0.33, recall_delta=-0.33),
            "cases": [],
        },
    })
    text = to_text_report(report)

    assert "Filter OFF vs ON 비교" in text
    assert "filter_off" in text and "filter_on" in text
    assert VERDICT_FAKE in text
    assert "오류 단계 분포" in text
    assert report["fake_confidence"]["verdict"] == VERDICT_FAKE


def test_three_way_report_judges_every_run_against_baseline():
    report = build_report({
        "filter_off": {"mode": "filter_off", "aggregate": _aggregate(0.4, 0.60, 0.80), "cases": []},
        "filter_on": {"mode": "filter_on", "aggregate": _aggregate(1.0, 0.75, 0.85, filter_ratio=0.6), "cases": []},
        "filter_on_strict": {
            "mode": "filter_on",
            "aggregate": _aggregate(1.0, 0.90, 0.85, filter_ratio=0.85, false_deletion=0.5, recall_delta=-0.5),
            "cases": [],
        },
    })

    assert report["verdicts"]["filter_on"]["verdict"] == VERDICT_IMPROVED
    assert report["verdicts"]["filter_on_strict"]["verdict"] == VERDICT_FAKE
    assert report["verdicts"]["filter_on_strict"]["warnings"]


def test_json_report_is_valid_json():
    report = build_report({"filter_off": {"mode": "filter_off", "aggregate": _aggregate(0.4, 0.6, 0.8), "cases": []}})

    parsed = json.loads(to_json_report(report))

    assert parsed["runs"]["filter_off"]["confidence"] == 0.6
