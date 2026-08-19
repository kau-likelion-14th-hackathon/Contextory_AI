"""
confidence.py — Confidence 계산 (RAG Pipeline ⑥)

설계 원칙
- LLM 자기 평가("얼마나 자신 있어?")를 쓰지 않는다. 실제 검색 신호만 조합한다.
- 필터를 '통과한' Evidence만 입력으로 받는다.
- 사용 신호 4가지: Top Similarity / Evidence 수 / Strong Evidence 수 / Filter 잔존율(1-filter_ratio)
- 가중치·기준값은 전부 core/config.py 에서 온다. 계산식은 _weighted_score() 하나에 격리해
  나중에 통째로 교체할 수 있게 한다.

주의(Fake Confidence)
  필터가 정답 Context를 지우면 남은 chunk의 평균 유사도가 올라 Confidence가 상승할 수 있다.
  따라서 Confidence 단독으로 필터 개선을 판정하면 안 되고, eval/ 의 recall_delta·false_deletion과
  함께 봐야 한다. (판정 규칙은 eval/report.py 의 judge_fake_confidence 참고)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.config import settings


@dataclass
class ConfidenceOutcome:
    score: float = 0.0
    needs_confirmation: bool = True
    retrieval_quality_warning: bool = False
    signals: Dict[str, Any] = field(default_factory=dict)


def _weighted_score(
    top_score: float,
    evidence_ratio: float,
    strong_ratio: float,
    retention: float,
    weights: Dict[str, float],
) -> float:
    """
    Confidence 계산식 (교체 지점).
    가중치 합이 1이 아니어도 되도록 합으로 정규화한다.
    """
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0

    weighted = (
        weights["top_score"] * top_score
        + weights["evidence_count"] * evidence_ratio
        + weights["strong_evidence"] * strong_ratio
        + weights["filter_retention"] * retention
    )
    return weighted / total_weight


def compute_confidence(
    evidence_scores: Sequence[float],
    filter_ratio: float = 0.0,
    strong_threshold: Optional[float] = None,
    evidence_target_count: Optional[int] = None,
    confirm_threshold: Optional[float] = None,
    filter_ratio_warn_threshold: Optional[float] = None,
    valid_evidence_threshold: Optional[float] = None,
    weights: Optional[Dict[str, float]] = None,
) -> ConfidenceOutcome:
    """
    필터를 통과한 Evidence들의 similarity score + filter_ratio 로 Confidence를 계산한다.
    (순수 함수 — DB·LLM 의존 없음)

    '근거 수' 신호는 유사도 기준(valid_evidence_threshold, 기본 SIM_THRESHOLD) 이상인 것만 센다.
    Top-1 보존 규칙 때문에 기준 미달 chunk가 남을 수 있는데, 그것까지 근거 수로 세면
    근거가 없는데도 Confidence가 올라가기 때문이다.
    """
    strong_threshold = strong_threshold if strong_threshold is not None else settings.STRONG_EVIDENCE_THRESHOLD
    valid_threshold = valid_evidence_threshold if valid_evidence_threshold is not None else settings.SIM_THRESHOLD
    evidence_target_count = evidence_target_count or settings.CONFIDENCE_EVIDENCE_TARGET_COUNT
    confirm_threshold = confirm_threshold if confirm_threshold is not None else settings.CONFIDENCE_CONFIRM_THRESHOLD
    warn_threshold = (
        filter_ratio_warn_threshold
        if filter_ratio_warn_threshold is not None
        else settings.FILTER_RATIO_WARN_THRESHOLD
    )
    weights = weights or {
        "top_score": settings.CONFIDENCE_W_TOP_SCORE,
        "evidence_count": settings.CONFIDENCE_W_EVIDENCE_COUNT,
        "strong_evidence": settings.CONFIDENCE_W_STRONG_EVIDENCE,
        "filter_retention": settings.CONFIDENCE_W_FILTER_RETENTION,
    }

    scores = [float(s) for s in evidence_scores if s is not None]
    warning = filter_ratio >= warn_threshold

    if not scores:
        return ConfidenceOutcome(
            score=0.0,
            needs_confirmation=True,
            retrieval_quality_warning=warning,
            signals={
                "top_score": 0.0,
                "evidence_count": 0,
                "strong_evidence_count": 0,
                "filter_ratio": round(filter_ratio, 4),
                "reason": "필터를 통과한 Evidence가 없습니다.",
            },
        )

    top_score = max(scores)
    evidence_count = len([s for s in scores if s >= valid_threshold])
    strong_count = len([s for s in scores if s >= strong_threshold])

    evidence_ratio = min(evidence_count / evidence_target_count, 1.0) if evidence_target_count else 0.0
    strong_ratio = min(strong_count / evidence_target_count, 1.0) if evidence_target_count else 0.0
    retention = max(0.0, 1.0 - filter_ratio)

    score = _weighted_score(
        top_score=max(0.0, min(top_score, 1.0)),
        evidence_ratio=evidence_ratio,
        strong_ratio=strong_ratio,
        retention=retention,
        weights=weights,
    )
    score = round(max(0.0, min(score, 1.0)), 4)

    return ConfidenceOutcome(
        score=score,
        needs_confirmation=(score < confirm_threshold) or warning,
        retrieval_quality_warning=warning,
        signals={
            "top_score": round(top_score, 4),
            "evidence_count": evidence_count,
            "kept_chunk_count": len(scores),
            "strong_evidence_count": strong_count,
            "strong_threshold": strong_threshold,
            "valid_evidence_threshold": valid_threshold,
            "filter_ratio": round(filter_ratio, 4),
            "filter_retention": round(retention, 4),
            "weights": dict(weights),
        },
    )


def calculate_confidence(filtered_contexts: List[Dict[str, Any]], filter_ratio: float) -> ConfidenceOutcome:
    """chunk dict 리스트를 그대로 받는 편의 래퍼 (파이프라인에서 사용)"""
    return compute_confidence(
        [c.get("similarity_score", 0.0) for c in filtered_contexts],
        filter_ratio=filter_ratio,
    )
