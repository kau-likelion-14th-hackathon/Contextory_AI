from typing import List, Dict, Any, Tuple

def calculate_confidence(filtered_contexts: List[Dict[str, Any]], filter_ratio: float) -> Tuple[float, bool]:
    """
    검색 score, evidence 수, filter_ratio 기반 신뢰도 계산
    Returns: (confidence_score, needs_confirmation)
    """
    if not filtered_contexts:
        return 0.2, True

    top_score = filtered_contexts[0].get("similarity_score", 0.0)
    evidence_count = len(filtered_contexts)

    # 기초 신뢰도 산출
    confidence = (top_score * 0.7) + (min(evidence_count / 5.0, 1.0) * 0.3)

    # Over-confidence / Fake confidence 예방 페널티
    needs_confirmation = False
    if filter_ratio >= 0.8:
        confidence *= 0.85
        needs_confirmation = True

    if confidence < 0.6:
        needs_confirmation = True

    return round(confidence, 2), needs_confirmation
