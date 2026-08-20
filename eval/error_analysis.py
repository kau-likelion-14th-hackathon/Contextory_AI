"""
error_analysis.py — 실패 케이스의 '어느 단계에서 깨졌는지' 분류

순차 판정 순서 (앞에서 걸리면 뒤는 보지 않는다):
    1. Retrieval      : gold가 애초에 검색되지 않음
    2. Ranking        : 검색은 됐지만 Top-K 밖 (순위 문제)
    3. Filter         : Top-K 안에 있었는데 필터가 지움
    4. Context        : gold는 남았는데 context가 질문에 쓸모없음 (context_relevance 낮음)
    5. Interpretation : 근거는 있는데 답이 질문/변경과 연결되지 않음 (answer_relevance·completeness 낮음)
    6. Generation     : 답변 자체가 비었거나 생성되지 않음
    7. Hallucination  : 근거 없는 내용을 지어냄 (groundedness/faithfulness 낮거나 hallucination 높음)
    None              : 위 어디에도 걸리지 않음(정상)

순수 함수이며 LLM·DB에 접근하지 않는다. 점수는 judge가 매긴 값을 주입받는다.
"""

from typing import Dict, List, Optional, Sequence

from core.config import settings

STAGES = (
    "Retrieval",
    "Ranking",
    "Filter",
    "Context",
    "Interpretation",
    "Generation",
    "Hallucination",
)


def _below(score: Optional[float], threshold: float) -> bool:
    """점수가 있고 기준 미만이면 True. 점수가 없으면(판단 불가) False."""
    return score is not None and float(score) < threshold


def classify_error(
    retrieved_ids: Sequence[str],
    filtered_ids: Sequence[str],
    gold_ids: Sequence[str],
    generation_scores: Optional[Dict[str, Optional[float]]] = None,
    answer: str = "",
    k: Optional[int] = None,
    pass_threshold: Optional[float] = None,
) -> Optional[str]:
    """단일 케이스의 실패 단계를 반환한다. 정상이면 None."""
    k = k or settings.EVAL_DEFAULT_K
    threshold = pass_threshold if pass_threshold is not None else settings.EVAL_SCORE_PASS_THRESHOLD
    scores = generation_scores or {}
    gold = set(gold_ids or [])

    if gold:
        retrieved = list(retrieved_ids or [])
        if not (gold & set(retrieved)):
            return "Retrieval"
        if not (gold & set(retrieved[:k])):
            return "Ranking"
        if not (gold & set(filtered_ids or [])):
            return "Filter"

    if _below(scores.get("context_relevance"), threshold):
        return "Context"

    if _below(scores.get("answer_relevance"), threshold) or _below(scores.get("completeness"), threshold):
        return "Interpretation"

    if not str(answer or "").strip():
        # 생성 단계까지 왔는데 답이 없다 (judge를 쓰지 않는 실행에서는 answer가 비어도 정상일 수 있으므로
        # generation_scores가 하나라도 있는 경우에만 실패로 본다)
        return "Generation" if scores else None

    hallucination = scores.get("hallucination")
    if hallucination is not None and float(hallucination) > (1.0 - threshold):
        return "Hallucination"
    if _below(scores.get("groundedness"), threshold) or _below(scores.get("faithfulness"), threshold):
        return "Hallucination"

    return None


def summarize_error_stages(stages: Sequence[Optional[str]]) -> Dict[str, int]:
    """단계별 실패 건수 집계 (정상 케이스는 세지 않는다)"""
    counter: Dict[str, int] = {}
    for stage in stages:
        if not stage:
            continue
        counter[stage] = counter.get(stage, 0) + 1
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def worst_stage(stages: Sequence[Optional[str]]) -> Optional[str]:
    """가장 많이 발생한 실패 단계 (개선 우선순위 판단용)"""
    summary = summarize_error_stages(stages)
    if not summary:
        return None
    return next(iter(summary))


def stage_order() -> List[str]:
    return list(STAGES)
