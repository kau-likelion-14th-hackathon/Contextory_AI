"""
context_filter.py — Context Filter Agent (RAG Pipeline ③)

설계 원칙
- Retrieval과 Prompt 사이의 독립 단계로 둔다. 그래야 filter_ratio / gold_retained /
  false_deletion / recall_delta 를 필터 단독으로 측정할 수 있다.
- 제거된 chunk를 버리지 않고 removed로 함께 반환한다. (평가·로깅에 필요)
- Top-1(최고 유사도) chunk는 어떤 설정에서도 보존한다. threshold 미달이어도 남긴다.

동작 모드 (settings.FILTER_MODE)
    on  : 유사도 임계값 기반 (기본)
    off : 전량 통과
    llm : LLM Context Filter Agent가 관련성을 판단 (1회 배치 호출, 실패 시 임계값 필터로 폴백)
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.config import settings

FilteredChunk = Dict[str, Any]


@dataclass
class FilterOutcome:
    """필터 단계 산출물. 유지/제거 목록을 모두 보존한다."""

    kept: List[FilteredChunk] = field(default_factory=list)
    removed: List[FilteredChunk] = field(default_factory=list)
    filter_ratio: float = 0.0
    top1_preserved: bool = False
    mode: str = "on"
    sim_threshold: float = 0.0
    notes: List[str] = field(default_factory=list)   # 폴백 발생 등 판단 경위 기록

    @property
    def retrieved_count(self) -> int:
        return len(self.kept) + len(self.removed)

    @property
    def information_loss_ratio(self) -> float:
        """
        '쓸 만한 근거를 잃은 비율' — filter_ratio 와 다르다.

        filter_ratio 는 버린 비율 전부를 센다. 그런데 임계값 미달 후보를 버리는 건
        필터가 제 일을 한 것이지 손실이 아니다. 검색이 후보를 더 많이 물어올수록
        filter_ratio 가 올라가서, 근거가 좋아졌는데 Confidence 가 깎이는 역전이 생긴다
        (실측: 프론트 PR #48 에서 근거가 실제 프로젝트 파일로 바뀌었는데 0.6107 → 0.58).

        그래서 손실은 '임계값을 넘겼는데도 버려진 것'만 센다.
        임계값 필터(mode="on")에서는 항상 0이고, LLM 필터가 높은 유사도 chunk 를
        관련 없다고 버렸을 때만 값이 생긴다 — 그게 진짜 정보 손실이다.
        """
        def above(chunks: List[FilteredChunk]) -> int:
            return sum(
                1 for c in chunks
                if float(c.get("similarity_score", 0.0) or 0.0) >= self.sim_threshold
            )

        candidates = above(self.kept) + above(self.removed)
        if not candidates:
            return 0.0
        return above(self.removed) / candidates


def _default_relevance_fn(chunk: FilteredChunk, sim_threshold: float) -> bool:
    """기본 관련성 판단: 유사도 임계값 이상이면 관련 있음."""
    return float(chunk.get("similarity_score", 0.0) or 0.0) >= sim_threshold


def filter_contexts(
    contexts: List[FilteredChunk],
    sim_threshold: Optional[float] = None,
    filter_mode: Optional[str] = None,
    relevance_fn: Optional[Callable[[FilteredChunk, float], bool]] = None,
) -> FilterOutcome:
    """
    검색 결과에서 무관한 chunk를 제거한다.

    - filter_mode="off" 이면 아무것도 제거하지 않는다(전체 통과).
    - Top-1(최고 유사도)은 threshold 미달이어도 반드시 kept에 남는다.
    - relevance_fn을 주입하면 임계값 대신 임의의 판단 로직(LLM Agent 등)으로 교체할 수 있다.
    """
    if sim_threshold is None:
        sim_threshold = settings.SIM_THRESHOLD
    mode = (filter_mode if filter_mode is not None else settings.FILTER_MODE)
    enabled = str(mode).strip().lower() not in ("off", "false", "0", "none")

    if not contexts:
        return FilterOutcome(
            kept=[], removed=[], filter_ratio=0.0, top1_preserved=False,
            mode="on" if enabled else "off", sim_threshold=sim_threshold,
        )

    # 유사도 내림차순 정렬 후 첫 번째가 Top-1
    ordered = sorted(contexts, key=lambda c: float(c.get("similarity_score", 0.0) or 0.0), reverse=True)
    top1 = ordered[0]

    if not enabled:
        return FilterOutcome(
            kept=list(ordered), removed=[], filter_ratio=0.0, top1_preserved=True,
            mode="off", sim_threshold=sim_threshold,
        )

    judge = relevance_fn or _default_relevance_fn

    kept: List[FilteredChunk] = []
    removed: List[FilteredChunk] = []
    for idx, chunk in enumerate(ordered):
        # idx == 0 은 Top-1 → 판단 결과와 무관하게 무조건 보존한다.
        if idx == 0 or judge(chunk, sim_threshold):
            kept.append(chunk)
        else:
            removed.append(chunk)

    total = len(ordered)
    filter_ratio = len(removed) / total if total else 0.0

    return FilterOutcome(
        kept=kept,
        removed=removed,
        filter_ratio=round(filter_ratio, 4),
        top1_preserved=any(c is top1 for c in kept),
        mode="on",
        sim_threshold=sim_threshold,
    )


# ==========================================
# LLM Context Filter Agent (FILTER_MODE=llm)
# ==========================================

LLM_FILTER_SYSTEM_INSTRUCTION = (
    "너는 RAG 파이프라인의 Context Filter다. 주어진 PR 분석 질의와 각 컨텍스트를 비교해 "
    "질의를 설명하는 데 실제로 쓸모 있는 것만 남긴다. 애매하면 남긴다(근거를 잃는 것이 더 위험하다). "
    "반드시 지정된 JSON 객체 하나만 출력한다."
)

LLM_FILTER_PROMPT_TEMPLATE = """<질의>
{query}
</질의>

<컨텍스트 목록>
{context_items}
</컨텍스트 목록>

<판단 기준>
- 이 질의(PR 변경)를 설명·보강하는 데 쓸 수 있으면 keep=true.
- 주제가 전혀 다르거나(예: 문서 오타, 무관한 스타일 지적) 판단에 기여하지 못하면 keep=false.
- 확신이 없으면 keep=true 로 둔다.
</판단 기준>

<출력>
{{"decisions": [{{"chunk_id": "...", "keep": true, "reason": "한 문장"}}]}}
</출력>
"""


def _chunk_id(chunk: FilteredChunk) -> str:
    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def _default_llm_client() -> Any:
    from openai import OpenAI  # 지연 import: API Key 없이도 모듈 import가 가능하도록

    return OpenAI(api_key=settings.OPENAI_API_KEY)


def build_llm_relevance_map(
    contexts: List[FilteredChunk],
    query_text: str,
    client: Any = None,
    model: Optional[str] = None,
    snippet_chars: Optional[int] = None,
) -> Dict[str, bool]:
    """
    LLM에 한 번만 배치 질의해 chunk_id → keep 여부 map을 만든다.
    호출/파싱에 실패하면 예외를 올려 상위(filter_contexts_with_llm)가 임계값 필터로 폴백하게 한다.
    """
    limit = snippet_chars or settings.CONTEXT_FILTER_SNIPPET_CHARS
    items = "\n".join(
        f"- chunk_id: {_chunk_id(c)} | 유사도: {float(c.get('similarity_score', 0.0) or 0.0):.4f}\n"
        f"  내용: {str(c.get('text') or c.get('source_code') or c.get('review_comment') or '')[:limit]}"
        for c in contexts
    )

    response = (client or _default_llm_client()).chat.completions.create(
        model=model or settings.CONTEXT_FILTER_MODEL,
        messages=[
            {"role": "system", "content": LLM_FILTER_SYSTEM_INSTRUCTION},
            {"role": "user", "content": LLM_FILTER_PROMPT_TEMPLATE.format(query=query_text, context_items=items)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    parsed = json.loads(response.choices[0].message.content)
    decisions = parsed.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError(f"LLM 필터 응답에 decisions 배열이 없습니다: {parsed}")

    return {
        str(d.get("chunk_id")): bool(d.get("keep", True))
        for d in decisions
        if isinstance(d, dict) and d.get("chunk_id") is not None
    }


def filter_contexts_with_llm(
    contexts: List[FilteredChunk],
    query_text: str,
    sim_threshold: Optional[float] = None,
    client: Any = None,
    model: Optional[str] = None,
) -> FilterOutcome:
    """
    LLM Context Filter Agent로 무관 chunk를 제거한다.

    - Top-1 보존 규칙은 filter_contexts()가 그대로 적용하므로 LLM이 지우라고 해도 남는다.
    - LLM이 판단하지 못한 chunk(응답 누락)는 임계값 기준으로 판단한다.
    - 호출/파싱 실패 시 임계값 필터로 폴백하고 그 사실을 notes에 남긴다(조용히 전량 통과시키지 않는다).
    """
    if sim_threshold is None:
        sim_threshold = settings.SIM_THRESHOLD

    if not contexts:
        return FilterOutcome(mode="llm", sim_threshold=sim_threshold)

    try:
        decisions = build_llm_relevance_map(contexts, query_text, client=client, model=model)
    except Exception as e:
        outcome = filter_contexts(contexts, sim_threshold=sim_threshold, filter_mode="on")
        outcome.mode = "llm_fallback"
        outcome.notes.append(f"LLM 필터 실패로 임계값 필터로 폴백했습니다: {type(e).__name__}: {e}")
        return outcome

    def relevance_fn(chunk: FilteredChunk, threshold: float) -> bool:
        decision = decisions.get(_chunk_id(chunk))
        if decision is None:
            return _default_relevance_fn(chunk, threshold)   # 판단 누락분은 임계값으로
        return decision

    outcome = filter_contexts(
        contexts, sim_threshold=sim_threshold, filter_mode="on", relevance_fn=relevance_fn
    )
    outcome.mode = "llm"
    undecided = [c for c in contexts if _chunk_id(c) not in decisions]
    if undecided:
        outcome.notes.append(f"LLM이 판단하지 않은 {len(undecided)}건은 임계값 기준으로 처리했습니다.")
    return outcome
