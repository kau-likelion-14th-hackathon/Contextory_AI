"""
metrics/retrieval.py — 검색 단계 지표 (순수 함수)

입력은 (검색된 chunk_id 리스트, gold chunk_id 리스트, K) 수준의 원시 데이터만 받는다.
services/ 를 import하지 않으며 DB·LLM에 의존하지 않는다.
"""

from typing import Dict, List, Optional, Sequence

Ids = Sequence[str]


def compute_precision_at_k(retrieved_ids: Ids, gold_ids: Ids, k: int = 5) -> float:
    """
    Precision@K = (Top-K 중 gold인 것) / (Top-K에 실제로 있는 chunk 수)

    분모를 상수 K가 아니라 실제 chunk 수로 두는 이유: 필터 ON/OFF 비교 시
    "남긴 것 중 얼마나 맞는가"를 봐야 필터의 정밀도 개선이 수치로 드러난다.
    (검색 결과가 K개보다 적을 때 과소평가되는 문제도 함께 피한다)
    """
    if k <= 0:
        return 0.0
    top_k = list(retrieved_ids)[:k]
    if not top_k:
        return 0.0
    gold = set(gold_ids)
    hits = len([r for r in top_k if r in gold])
    return hits / len(top_k)


def compute_recall_at_k(retrieved_ids: Ids, gold_ids: Ids, k: int = 5) -> float:
    """Recall@K = (Top-K에 포함된 gold 수) / (전체 gold 수)"""
    gold = set(gold_ids)
    if not gold:
        return 0.0
    top_k = set(list(retrieved_ids)[:k])
    return len(top_k & gold) / len(gold)


def compute_mrr(retrieved_ids: Ids, gold_ids: Ids, k: Optional[int] = None) -> float:
    """MRR = 첫 번째 gold chunk 순위의 역수 (없으면 0)"""
    gold = set(gold_ids)
    candidates = list(retrieved_ids)[:k] if k else list(retrieved_ids)
    for rank, chunk_id in enumerate(candidates, 1):
        if chunk_id in gold:
            return 1.0 / rank
    return 0.0


def compute_hit_rate(retrieved_ids: Ids, gold_ids: Ids, k: int = 5) -> float:
    """Hit Rate = Top-K에 gold가 1개라도 있으면 1.0"""
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return 1.0 if set(list(retrieved_ids)[:k]) & gold else 0.0


def is_retrieval_failure(
    retrieved_ids: Ids,
    scores: Optional[Sequence[float]] = None,
    sim_threshold: float = 0.0,
) -> bool:
    """
    단일 케이스의 검색 실패 여부.
    - 검색 결과가 비었거나
    - 점수가 주어졌는데 전부 threshold 미달이면 실패로 본다.
    """
    if not list(retrieved_ids):
        return True
    if scores is None:
        return False
    return not any(float(s) >= sim_threshold for s in scores)


def compute_retrieval_failure_rate(failure_flags: Sequence[bool]) -> float:
    """Retrieval Failure Rate = 검색 실패 케이스 비율"""
    flags = list(failure_flags)
    if not flags:
        return 0.0
    return len([f for f in flags if f]) / len(flags)


def compute_retrieval_metrics(
    retrieved_ids: Ids,
    gold_ids: Ids,
    k: int = 5,
    scores: Optional[Sequence[float]] = None,
    sim_threshold: float = 0.0,
) -> Dict[str, float]:
    """단일 케이스의 검색 지표 묶음"""
    return {
        "precision_at_k": compute_precision_at_k(retrieved_ids, gold_ids, k),
        "recall_at_k": compute_recall_at_k(retrieved_ids, gold_ids, k),
        "mrr": compute_mrr(retrieved_ids, gold_ids),
        "hit_rate": compute_hit_rate(retrieved_ids, gold_ids, k),
        "retrieval_failure": 1.0 if is_retrieval_failure(retrieved_ids, scores, sim_threshold) else 0.0,
    }


def average_metrics(metric_dicts: List[Dict[str, float]]) -> Dict[str, float]:
    """케이스별 지표 dict 리스트를 키별 평균으로 집계한다."""
    if not metric_dicts:
        return {}
    keys = sorted({key for d in metric_dicts for key in d})
    aggregated = {}
    for key in keys:
        values = [float(d[key]) for d in metric_dicts if d.get(key) is not None]
        aggregated[key] = round(sum(values) / len(values), 4) if values else 0.0
    return aggregated
