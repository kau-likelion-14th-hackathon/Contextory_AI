"""
metrics/filtering.py — Context Filter 단독 지표 (순수 함수)

품질 문제가 검색/필터/생성 중 어디서 생겼는지 분리하기 위해, 필터만 따로 측정한다.

  filter_ratio   = (제거된 chunk 수) / (검색된 chunk 수)
  gold_retained  = 필터 후에도 남은 gold chunk 비율
  false_deletion = 필터가 삭제한 gold chunk 비율 (0이어야 정상)
  recall_delta@K = (필터 후 Recall@K) − (필터 전 Recall@K), 음수면 필터가 정보를 잃음
"""

from typing import Dict, Sequence

from eval.metrics.retrieval import compute_recall_at_k

Ids = Sequence[str]


def compute_filter_ratio(retrieved_ids: Ids, filtered_ids: Ids) -> float:
    retrieved = list(retrieved_ids)
    if not retrieved:
        return 0.0
    removed = len([r for r in retrieved if r not in set(filtered_ids)])
    return removed / len(retrieved)


def compute_gold_retained(filtered_ids: Ids, gold_ids: Ids) -> float:
    """필터 후 남은 gold 비율. gold가 없으면 잃을 것도 없으므로 1.0."""
    gold = set(gold_ids)
    if not gold:
        return 1.0
    return len(gold & set(filtered_ids)) / len(gold)


def compute_false_deletion(retrieved_ids: Ids, filtered_ids: Ids, gold_ids: Ids) -> float:
    """
    필터가 삭제한 gold 비율.
    분모는 '검색되어 필터 입력으로 들어온 gold 수'다. 애초에 검색되지 않은 gold는
    필터의 잘못이 아니므로 제외한다(검색 단계의 Recall이 잡는다).
    """
    gold = set(gold_ids)
    retrieved_gold = [g for g in set(retrieved_ids) if g in gold]
    if not retrieved_gold:
        return 0.0
    deleted = [g for g in retrieved_gold if g not in set(filtered_ids)]
    return len(deleted) / len(retrieved_gold)


def compute_recall_delta_at_k(retrieved_ids: Ids, filtered_ids: Ids, gold_ids: Ids, k: int = 5) -> float:
    """필터 후 Recall@K − 필터 전 Recall@K"""
    before = compute_recall_at_k(retrieved_ids, gold_ids, k)
    after = compute_recall_at_k(filtered_ids, gold_ids, k)
    return after - before


def compute_filter_metrics(
    retrieved_ids: Ids,
    filtered_ids: Ids,
    gold_ids: Ids,
    k: int = 5,
) -> Dict[str, float]:
    """단일 케이스의 필터 지표 묶음"""
    gold = set(gold_ids)
    retrieved_gold = [g for g in set(retrieved_ids) if g in gold]
    deleted_gold = [g for g in retrieved_gold if g not in set(filtered_ids)]

    return {
        "filter_ratio": round(compute_filter_ratio(retrieved_ids, filtered_ids), 4),
        "gold_retained": round(compute_gold_retained(filtered_ids, gold_ids), 4),
        "false_deletion": round(compute_false_deletion(retrieved_ids, filtered_ids, gold_ids), 4),
        "recall_delta_at_k": round(compute_recall_delta_at_k(retrieved_ids, filtered_ids, gold_ids, k), 4),
        # 원인 추적용 절대 개수 (비율만으로는 규모를 알 수 없어 함께 남긴다)
        "deleted_gold_count": float(len(deleted_gold)),
        "retrieved_gold_count": float(len(retrieved_gold)),
    }
