"""
report.py — 평가 결과 리포트 (Text / JSON)

포함 내용
    1. Filter OFF vs ON 비교표
    2. Fake Confidence 판정 (아래 규칙표)
    3. 오류 단계 분포

Fake Confidence 판정 규칙
    Confidence↑ AND Precision↑ AND Faithfulness↑ AND recall_delta ≥ 0 AND false_deletion = 0
        → 정상 개선
    Confidence↑ BUT (recall_delta < 0 OR false_deletion > 0)
        → ⚠ Fake Confidence (필터 threshold 하향 권고)
    filter_ratio > 경고 기준
        → Confidence와 무관하게 "⚠ 검색 품질 확인 필요"
"""

import json
from typing import Any, Dict, List, Optional

from core.config import settings

VERDICT_IMPROVED = "정상 개선"
VERDICT_FAKE = "⚠ Fake Confidence"
VERDICT_NO_GAIN = "개선 없음/판정 보류"


def _as_dict(run: Any) -> Dict[str, Any]:
    """RunResult(dataclass) / dict 어느 쪽이 와도 dict로 통일한다."""
    if isinstance(run, dict):
        return run
    if hasattr(run, "to_dict"):
        return run.to_dict()
    return {"mode": getattr(run, "mode", "unknown"), "aggregate": getattr(run, "aggregate", {}), "cases": []}


def _get(aggregate: Dict[str, Any], group: str, key: str) -> Optional[float]:
    value = (aggregate.get(group) or {}).get(key)
    return None if value is None else float(value)


def _post_filter(aggregate: Dict[str, Any], key: str) -> Optional[float]:
    """필터 후(최종 컨텍스트) 기준 검색 지표. 없으면 검색 단계 원본 값으로 대체한다."""
    value = _get(aggregate, "context", key)
    return value if value is not None else _get(aggregate, "retrieval", key)


def _quality_score(aggregate: Dict[str, Any]) -> Optional[float]:
    """Faithfulness 우선, 없으면 Groundedness를 생성 품질 대리 지표로 쓴다."""
    for key in ("faithfulness", "groundedness"):
        value = _get(aggregate, "generation", key)
        if value is not None:
            return value
    return None


def judge_fake_confidence(
    off_aggregate: Dict[str, Any],
    on_aggregate: Dict[str, Any],
    filter_ratio_warn_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Filter OFF/ON 집계값을 비교해 Fake Confidence 여부를 판정한다."""
    warn_threshold = (
        filter_ratio_warn_threshold
        if filter_ratio_warn_threshold is not None
        else settings.FILTER_RATIO_WARN_THRESHOLD
    )

    off_conf = off_aggregate.get("confidence")
    on_conf = on_aggregate.get("confidence")
    confidence_delta = None if (off_conf is None or on_conf is None) else round(float(on_conf) - float(off_conf), 4)

    # 필터 효과 판정이므로 Precision은 '필터 후 최종 컨텍스트' 기준으로 본다.
    off_precision = _post_filter(off_aggregate, "precision_at_k")
    on_precision = _post_filter(on_aggregate, "precision_at_k")
    precision_delta = None if (off_precision is None or on_precision is None) else round(on_precision - off_precision, 4)

    off_quality = _quality_score(off_aggregate)
    on_quality = _quality_score(on_aggregate)
    quality_delta = None if (off_quality is None or on_quality is None) else round(on_quality - off_quality, 4)

    recall_delta = _get(on_aggregate, "filtering", "recall_delta_at_k")
    false_deletion = _get(on_aggregate, "filtering", "false_deletion")
    filter_ratio = _get(on_aggregate, "filtering", "filter_ratio")
    if filter_ratio is None:
        filter_ratio = _get(on_aggregate, "signals", "filter_ratio")

    reasons: List[str] = []
    warnings: List[str] = []

    confidence_up = confidence_delta is not None and confidence_delta > 0
    information_lost = (recall_delta is not None and recall_delta < 0) or (
        false_deletion is not None and false_deletion > 0
    )

    if information_lost:
        if recall_delta is not None and recall_delta < 0:
            reasons.append(f"recall_delta@K = {recall_delta} (<0): 필터가 정보를 잃었습니다.")
        if false_deletion is not None and false_deletion > 0:
            reasons.append(f"false_deletion = {false_deletion} (>0): 필터가 gold chunk를 삭제했습니다.")

    if confidence_up and information_lost:
        verdict = VERDICT_FAKE
        reasons.append("Confidence는 올랐지만 정보 손실이 동반되었습니다. 필터 threshold 하향을 권고합니다.")
    elif (
        confidence_up
        and (precision_delta is None or precision_delta >= 0)
        and (quality_delta is None or quality_delta >= 0)
        and (recall_delta is None or recall_delta >= 0)
        and (false_deletion in (None, 0.0))
    ):
        verdict = VERDICT_IMPROVED
        reasons.append("Confidence 상승에 정보 손실이 없습니다.")
        if precision_delta is None:
            reasons.append("Precision 미측정(Ground Truth 없음) — 검색 개선 여부는 확인되지 않았습니다.")
        if quality_delta is None:
            reasons.append("Faithfulness/Groundedness 미측정 — 생성 품질 개선 여부는 확인되지 않았습니다.")
    else:
        verdict = VERDICT_NO_GAIN
        if not confidence_up:
            reasons.append("Confidence 상승이 없어 개선으로 판정하지 않습니다.")
        if information_lost:
            reasons.append("정보 손실이 감지되어 개선으로 판정하지 않습니다.")

    if filter_ratio is not None and filter_ratio > warn_threshold:
        warnings.append(
            f"⚠ 검색 품질 확인 필요: filter_ratio {filter_ratio} > 경고 기준 {warn_threshold} "
            "(Confidence와 무관하게 검색·인덱싱 범위를 점검하세요)"
        )

    return {
        "verdict": verdict,
        "reasons": reasons,
        "warnings": warnings,
        "deltas": {
            "confidence": confidence_delta,
            "precision_at_k": precision_delta,
            "generation_quality": quality_delta,
            "recall_delta_at_k": recall_delta,
            "false_deletion": false_deletion,
            "filter_ratio": filter_ratio,
        },
    }


def build_report(
    comparison: Dict[str, Any],
    title: str = "Contextory RAG Evaluation",
    baseline: str = "filter_off",
) -> Dict[str, Any]:
    """
    run_comparison() 결과(dict of RunResult)를 리포트 구조로 변환한다.
    filter_off / filter_on 외에 임계값이 다른 실행(filter_on_strict 등)을 더 넣어도 되고,
    baseline 대비 모든 실행에 대해 Fake Confidence 판정을 만든다.
    """
    runs = {mode: _as_dict(run) for mode, run in comparison.items()}
    aggregates = {mode: run.get("aggregate", {}) for mode, run in runs.items()}

    report: Dict[str, Any] = {
        "title": title,
        "baseline": baseline,
        "config": {
            "k": next((agg.get("k") for agg in aggregates.values() if agg.get("k")), settings.EVAL_DEFAULT_K),
            "sim_threshold": settings.SIM_THRESHOLD,
            "strong_evidence_threshold": settings.STRONG_EVIDENCE_THRESHOLD,
            "filter_ratio_warn_threshold": settings.FILTER_RATIO_WARN_THRESHOLD,
        },
        "runs": aggregates,
        "error_stages": {mode: (agg or {}).get("error_stages", {}) for mode, agg in aggregates.items()},
        "verdicts": {},
    }

    baseline_aggregate = aggregates.get(baseline)
    if baseline_aggregate is not None:
        for mode, aggregate in aggregates.items():
            if mode == baseline:
                continue
            report["verdicts"][mode] = judge_fake_confidence(baseline_aggregate, aggregate)

    # 하위 호환: 단일 OFF/ON 비교의 판정을 최상위 키로도 노출한다.
    if "filter_on" in report["verdicts"]:
        report["fake_confidence"] = report["verdicts"]["filter_on"]

    return report


# ==========================================
# 출력
# ==========================================

_COMPARISON_COLUMNS = [
    # Precision/Recall은 '필터 후 최종 컨텍스트' 기준, MRR/HitRate는 '검색 단계 원본' 기준이다.
    ("Precision@K(post)", lambda agg: _post_filter(agg, "precision_at_k")),
    ("Recall@K(post)", lambda agg: _post_filter(agg, "recall_at_k")),
    ("Recall@K(retr)", lambda agg: _get(agg, "retrieval", "recall_at_k")),
    ("MRR(retr)", lambda agg: _get(agg, "retrieval", "mrr")),
    ("HitRate(retr)", lambda agg: _get(agg, "retrieval", "hit_rate")),
    ("FilterRatio", lambda agg: _get(agg, "filtering", "filter_ratio")),
    ("FalseDeletion", lambda agg: _get(agg, "filtering", "false_deletion")),
    ("RecallDelta@K", lambda agg: _get(agg, "filtering", "recall_delta_at_k")),
    ("GoldRetained", lambda agg: _get(agg, "filtering", "gold_retained")),
    ("Confidence", lambda agg: agg.get("confidence")),
    ("Faithfulness", _quality_score),
]


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{float(value):.3f}"


def to_text_report(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"=== {report.get('title', 'Evaluation Report')} ===",
        f"K={report['config']['k']} / SIM_THRESHOLD={report['config']['sim_threshold']} / "
        f"STRONG_EVIDENCE={report['config']['strong_evidence_threshold']} / "
        f"FILTER_RATIO_WARN={report['config']['filter_ratio_warn_threshold']}",
        "",
        "[Filter OFF vs ON 비교]",
    ]

    header = ["구분"] + [name for name, _ in _COMPARISON_COLUMNS] + ["판정"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    baseline = report.get("baseline", "filter_off")
    for mode, aggregate in report.get("runs", {}).items():
        verdict = report.get("verdicts", {}).get(mode)
        label = "기준선" if mode == baseline else (verdict or {}).get("verdict", "-")
        row = [mode] + [_fmt(getter(aggregate)) for _, getter in _COMPARISON_COLUMNS] + [label]
        lines.append("| " + " | ".join(row) + " |")

    for mode, verdict in report.get("verdicts", {}).items():
        lines += ["", f"[Fake Confidence 판정: {baseline} → {mode}] {verdict['verdict']}"]
        for reason in verdict["reasons"]:
            lines.append(f"  - {reason}")
        for warning in verdict["warnings"]:
            lines.append(f"  {warning}")
        deltas = verdict["deltas"]
        lines.append(
            "  Δ confidence={c} / Δ precision@K={p} / Δ generation={g} / recall_delta@K={r} / false_deletion={f}".format(
                c=deltas["confidence"], p=deltas["precision_at_k"], g=deltas["generation_quality"],
                r=deltas["recall_delta_at_k"], f=deltas["false_deletion"],
            )
        )

    lines += ["", "[오류 단계 분포]"]
    for mode, stages in report.get("error_stages", {}).items():
        lines.append(f"  - {mode}: {stages if stages else '(실패 없음)'}")

    for mode, aggregate in report.get("runs", {}).items():
        lines.append("")
        lines.append(f"[{mode} 상세]")
        lines.append(f"  케이스 {aggregate.get('case_count', 0)}건 / GT {aggregate.get('ground_truth_case_count', 0)}건 "
                     f"/ 실행 오류 {aggregate.get('error_case_count', 0)}건")
        lines.append(f"  retrieval_failure_rate={aggregate.get('retrieval_failure_rate')} / "
                     f"hallucination_rate={aggregate.get('hallucination_rate')}")
        if aggregate.get("generation"):
            lines.append(f"  generation={aggregate['generation']}")
        if aggregate.get("signals"):
            lines.append(f"  signals={aggregate['signals']}")

    return "\n".join(lines)


def to_json_report(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)
