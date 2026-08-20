"""
metrics/retrieval_signals.py — Confidence의 재료가 되는 '검색 신호' 요약 (순수 함수)

Confidence 계산식 자체는 런타임(services/confidence.py)의 책임이고, eval은 그 값을
callback으로 주입받는다. 이 모듈은 신호(Top Score / Evidence 수 / Strong Evidence 수 /
Filter Ratio)를 케이스별로 요약·집계해서, Confidence가 오를 때 그게 어떤 신호 때문인지
(그리고 Fake Confidence 인지) 설명 가능하게 만드는 용도다.
"""

from typing import Any, Dict, List, Optional, Sequence

from core.config import settings


def summarize_signals(
    scores: Sequence[float],
    filter_ratio: float = 0.0,
    strong_threshold: Optional[float] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """필터 통과 chunk들의 유사도 점수로 검색 신호를 요약한다."""
    threshold = strong_threshold if strong_threshold is not None else settings.STRONG_EVIDENCE_THRESHOLD
    values = [float(s) for s in scores if s is not None]

    return {
        "top_score": round(max(values), 4) if values else 0.0,
        "mean_score": round(sum(values) / len(values), 4) if values else 0.0,
        "evidence_count": float(len(values)),
        "strong_evidence_count": float(len([v for v in values if v >= threshold])),
        "filter_ratio": round(float(filter_ratio), 4),
        "confidence": None if confidence is None else round(float(confidence), 4),
    }


def aggregate_signals(signal_dicts: List[Dict[str, Any]]) -> Dict[str, float]:
    """케이스별 신호 요약을 키별 평균으로 집계한다(None 제외)."""
    if not signal_dicts:
        return {}
    keys = sorted({k for d in signal_dicts for k in d})
    aggregated: Dict[str, float] = {}
    for key in keys:
        values = [float(d[key]) for d in signal_dicts if d.get(key) is not None]
        aggregated[key] = round(sum(values) / len(values), 4) if values else 0.0
    return aggregated


def retrieval_quality_warning(filter_ratio: float, warn_threshold: Optional[float] = None) -> bool:
    """filter_ratio가 경고 기준을 넘으면 Confidence와 무관하게 '검색 품질 확인 필요'."""
    threshold = warn_threshold if warn_threshold is not None else settings.FILTER_RATIO_WARN_THRESHOLD
    return float(filter_ratio) >= threshold
