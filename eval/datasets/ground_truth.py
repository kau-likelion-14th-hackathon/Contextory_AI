"""
datasets/ground_truth.py — 평가 케이스 정의

한 케이스 = 질문(PR) + gold chunk 목록 + 기준 답변(선택).
gold_chunks가 비어 있으면 Reference-Free 평가 케이스로 취급한다.

JSONL 한 줄 예시:
{"case_id": "pr-101", "query": "JWT 로그인 도입 PR", "gold_chunks": ["cr-1", "repo-a1b2"],
 "reference_answer": "세션 인증에서 JWT 인증으로 전환한다", "reference_keywords": ["JWT", "AuthService"],
 "metadata": {"repo": "org/contextory"}}
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GroundTruthCase:
    case_id: str
    query: str
    gold_chunks: List[str] = field(default_factory=list)
    reference_answer: Optional[str] = None
    reference_keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_ground_truth(self) -> bool:
        return bool(self.gold_chunks)

    def as_reference(self) -> Dict[str, Any]:
        """judge에 넘길 reference 형태"""
        return {
            "reference_answer": self.reference_answer,
            "reference_keywords": list(self.reference_keywords),
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def as_case(raw: Any) -> GroundTruthCase:
    """dict / GroundTruthCase 어느 쪽이 와도 GroundTruthCase로 정규화한다."""
    if isinstance(raw, GroundTruthCase):
        return raw
    if not isinstance(raw, dict):
        raise TypeError(f"평가 케이스는 dict 또는 GroundTruthCase여야 합니다: {type(raw).__name__}")

    # query 키는 question/query 둘 다 허용 (기존 runner 호출부 호환)
    query = raw.get("query") or raw.get("question") or ""
    if not query:
        raise ValueError(f"평가 케이스에 query가 없습니다: {raw}")

    return GroundTruthCase(
        case_id=str(raw.get("case_id") or raw.get("id") or query[:32]),
        query=str(query),
        gold_chunks=[str(g) for g in (raw.get("gold_chunks") or raw.get("gold_ids") or [])],
        reference_answer=raw.get("reference_answer"),
        reference_keywords=[str(k) for k in (raw.get("reference_keywords") or [])],
        metadata=raw.get("metadata") or {},
    )
