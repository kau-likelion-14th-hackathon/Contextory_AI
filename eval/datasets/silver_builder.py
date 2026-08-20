"""
datasets/silver_builder.py — Silver(약지도) Ground Truth 생성

사람이 라벨링한 Gold 데이터가 부족할 때, 실제 파이프라인의 검색 결과로부터
"임시 정답(silver)"을 만들어 회귀 감시용 데이터셋으로 쓴다.

silver 라벨 규칙 (기본)
    - 검색 결과 중 similarity_score >= silver_threshold 인 chunk를 gold로 채택
    - 상위 max_gold개까지만 채택
    - 최소 1개(Top-1)는 반드시 포함 → 런타임의 'Top-1 보존' 규칙과 같은 가정을 유지한다

주의
    silver는 검색기 자신의 출력에서 나오므로 검색기 편향이 그대로 들어간다.
    절대 성능 수치의 근거로 쓰지 말고, 변경 전후 회귀 비교용으로만 쓴다.
    이 모듈도 services/ 를 import하지 않는다 (retrieve_fn 주입식).
"""

from typing import Any, Callable, Dict, List, Optional, Sequence

from core.config import settings
from eval.datasets.ground_truth import GroundTruthCase


def _chunk_id(chunk: Dict[str, Any]) -> str:
    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def build_silver_case(
    case_id: str,
    query: str,
    retrieved: Sequence[Dict[str, Any]],
    silver_threshold: Optional[float] = None,
    max_gold: int = 3,
    reference_keywords: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> GroundTruthCase:
    """검색 결과 하나로부터 silver 케이스 1건을 만든다."""
    threshold = silver_threshold if silver_threshold is not None else settings.STRONG_EVIDENCE_THRESHOLD

    ordered = sorted(retrieved, key=lambda c: float(c.get("similarity_score", 0.0) or 0.0), reverse=True)
    gold = [_chunk_id(c) for c in ordered if float(c.get("similarity_score", 0.0) or 0.0) >= threshold][:max_gold]

    # Top-1은 threshold 미달이어도 포함한다(런타임 Top-1 보존 규칙과 동일한 가정).
    if ordered and not gold:
        gold = [_chunk_id(ordered[0])]

    meta = dict(metadata or {})
    meta.update({
        "label_type": "silver",
        "silver_threshold": threshold,
        "retrieved_count": len(ordered),
    })

    return GroundTruthCase(
        case_id=case_id,
        query=query,
        gold_chunks=[g for g in gold if g],
        reference_answer=None,
        reference_keywords=list(reference_keywords or []),
        metadata=meta,
    )


def build_silver_dataset(
    queries: Sequence[Dict[str, Any]],
    retrieve_fn: Callable[[str], List[Dict[str, Any]]],
    silver_threshold: Optional[float] = None,
    max_gold: int = 3,
) -> List[GroundTruthCase]:
    """
    질의 목록과 retrieve_fn(주입)으로 silver 데이터셋을 만든다.
    queries 각 항목: {"case_id": str, "query": str, "reference_keywords": [...], "metadata": {...}}
    """
    cases: List[GroundTruthCase] = []
    for idx, item in enumerate(queries):
        query = str(item.get("query") or item.get("question") or "")
        if not query:
            continue
        retrieved = list(retrieve_fn(query))
        cases.append(
            build_silver_case(
                case_id=str(item.get("case_id") or f"silver-{idx}"),
                query=query,
                retrieved=retrieved,
                silver_threshold=silver_threshold,
                max_gold=max_gold,
                reference_keywords=item.get("reference_keywords"),
                metadata=item.get("metadata"),
            )
        )
    return cases
