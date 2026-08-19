"""
metrics/generation.py — 생성 단계 지표 (judge 주입식)

이 모듈은 LLM을 직접 호출하지 않는다. judge(callable)를 주입받아 점수만 계산한다.
따라서 LLM/DB 없이도 (Offline Keyword Judge 또는 가짜 judge로) 전부 단위 테스트할 수 있다.

judge 인터페이스 (eval/judge.py 의 두 구현이 동일하게 따른다):
    judge(question=..., context=..., answer=..., criterion=..., reference=...) -> dict
        {"criterion": str, "score": float|None, "reason": str, "error": str|None}
"""

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.config import settings

# 지표명 → judge criterion
GENERATION_CRITERIA = (
    "groundedness",
    "faithfulness",
    "completeness",
    "answer_relevance",
    "context_relevance",
    "hallucination",
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_./#-]{1,}|[가-힣]{2,}|\d+")


def _tokens(text: Optional[str]) -> List[str]:
    return [t.lower() for t in _TOKEN_PATTERN.findall(text or "")]


def evaluate_generation(
    question: str,
    context: str,
    answer: str,
    judge: Callable[..., Dict[str, Any]],
    criteria: Sequence[str] = GENERATION_CRITERIA,
    reference: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    주입된 judge로 생성 품질 지표를 계산한다.
    반환: {"scores": {criterion: float|None}, "details": {criterion: judge 원본 결과}}
    """
    scores: Dict[str, Optional[float]] = {}
    details: Dict[str, Any] = {}

    for criterion in criteria:
        result = judge(
            question=question,
            context=context,
            answer=answer,
            criterion=criterion,
            reference=reference,
        )
        details[criterion] = result
        raw_score = result.get("score")
        scores[criterion] = None if raw_score is None else float(raw_score)

    return {"scores": scores, "details": details}


def hallucination_rate(
    scores_per_case: Sequence[Dict[str, Optional[float]]],
    pass_threshold: Optional[float] = None,
) -> float:
    """
    Hallucination Rate = groundedness(없으면 faithfulness)가 기준 미만인 케이스 비율.
    judge가 hallucination criterion 점수를 직접 준 경우 그 값을 우선 사용한다.
    """
    threshold = pass_threshold if pass_threshold is not None else settings.EVAL_SCORE_PASS_THRESHOLD
    judged: List[bool] = []

    for scores in scores_per_case:
        if scores.get("hallucination") is not None:
            judged.append(float(scores["hallucination"]) >= threshold)
            continue
        base = scores.get("groundedness")
        if base is None:
            base = scores.get("faithfulness")
        if base is None:
            continue
        judged.append(float(base) < threshold)

    if not judged:
        return 0.0
    return round(len([j for j in judged if j]) / len(judged), 4)


def exact_match(prediction: str, reference: str) -> float:
    """EM — 공백/대소문자 정규화 후 완전 일치 여부"""
    norm = lambda s: re.sub(r"\s+", " ", (s or "")).strip().lower()
    return 1.0 if norm(prediction) == norm(reference) and norm(reference) else 0.0


def token_f1(prediction: str, reference: str) -> float:
    """F1 — 토큰 단위 겹침 기반 (보조 지표)"""
    pred_tokens = _tokens(prediction)
    ref_tokens = _tokens(reference)
    if not pred_tokens or not ref_tokens:
        return 0.0

    common = Counter(pred_tokens) & Counter(ref_tokens)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0

    precision = overlap / len(pred_tokens)
    recall = overlap / len(ref_tokens)
    return round(2 * precision * recall / (precision + recall), 4)


def average_scores(scores_per_case: Sequence[Dict[str, Optional[float]]]) -> Dict[str, float]:
    """케이스별 점수 dict들을 criterion별 평균으로 집계한다(None은 제외)."""
    if not scores_per_case:
        return {}
    keys = sorted({k for s in scores_per_case for k in s})
    aggregated: Dict[str, float] = {}
    for key in keys:
        values = [float(s[key]) for s in scores_per_case if s.get(key) is not None]
        aggregated[key] = round(sum(values) / len(values), 4) if values else 0.0
    return aggregated
