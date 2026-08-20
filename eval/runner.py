"""
runner.py — 평가 실행기 (callback 주입식)

핵심 원칙: eval/ 은 services/ 를 절대 import하지 않는다.
    retrieve_fn / filter_fn / generate_fn / confidence_fn / judge 를 전부 주입받아 실행하므로,
    LLM·Vector DB·서비스 서버 없이 가짜 callback만으로 전체 평가가 돌아간다.

주입 인터페이스
    retrieve_fn(query: str) -> List[dict]      # dict에 chunk_id, similarity_score, text
    filter_fn(chunks: List[dict]) -> List[dict] | (kept, removed)   # None이면 필터 OFF
    generate_fn(query: str, contexts: List[dict]) -> str | dict     # dict면 {"answer":..., "confidence":...}
    confidence_fn(contexts: List[dict], filter_ratio: float) -> float
    judge(question=, context=, answer=, criterion=, reference=) -> dict
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.config import settings
from eval.datasets.ground_truth import GroundTruthCase, as_case
from eval.error_analysis import classify_error
from eval.metrics.filtering import compute_filter_metrics
from eval.metrics.generation import (
    GENERATION_CRITERIA, average_scores, evaluate_generation, exact_match, hallucination_rate, token_f1,
)
from eval.metrics.retrieval import (
    average_metrics, compute_retrieval_failure_rate, compute_retrieval_metrics, is_retrieval_failure,
)
from eval.metrics.retrieval_signals import aggregate_signals, summarize_signals

MODE_FILTER_OFF = "filter_off"
MODE_FILTER_ON = "filter_on"


@dataclass
class CaseResult:
    case_id: str
    query: str
    has_ground_truth: bool = False
    retrieved_ids: List[str] = field(default_factory=list)
    filtered_ids: List[str] = field(default_factory=list)
    removed_ids: List[str] = field(default_factory=list)
    gold_ids: List[str] = field(default_factory=list)
    filter_ratio: float = 0.0
    confidence: Optional[float] = None
    answer: str = ""
    retrieval_metrics: Dict[str, float] = field(default_factory=dict)   # 검색 단계 원본(retrieved) 기준
    context_metrics: Dict[str, float] = field(default_factory=dict)     # 필터 후 최종 컨텍스트(filtered) 기준
    filtering_metrics: Dict[str, float] = field(default_factory=dict)
    generation_scores: Dict[str, Optional[float]] = field(default_factory=dict)
    signals: Dict[str, Any] = field(default_factory=dict)
    exact_match: Optional[float] = None
    token_f1: Optional[float] = None
    error_stage: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RunResult:
    mode: str
    cases: List[CaseResult] = field(default_factory=list)
    aggregate: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "aggregate": self.aggregate, "cases": [asdict(c) for c in self.cases]}


def _chunk_id(chunk: Dict[str, Any]) -> str:
    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def _context_text(chunks: Sequence[Dict[str, Any]]) -> str:
    return "\n\n".join(str(c.get("text") or c.get("source_code") or c.get("review_comment") or "") for c in chunks)


def _apply_filter(
    chunks: List[Dict[str, Any]],
    filter_fn: Optional[Callable],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """filter_fn 결과를 (kept, removed)로 정규화한다. None이면 필터 OFF."""
    if filter_fn is None:
        return list(chunks), []

    result = filter_fn(chunks)
    if isinstance(result, tuple) and len(result) == 2:
        kept, removed = list(result[0]), list(result[1])
        return kept, removed

    kept = list(result)
    kept_ids = {_chunk_id(c) for c in kept}
    removed = [c for c in chunks if _chunk_id(c) not in kept_ids]
    return kept, removed


def run_evaluation(
    cases: Sequence[Any],
    retrieve_fn: Callable[[str], List[Dict[str, Any]]],
    filter_fn: Optional[Callable] = None,
    generate_fn: Optional[Callable] = None,
    judge: Optional[Callable] = None,
    confidence_fn: Optional[Callable] = None,
    k: Optional[int] = None,
    sim_threshold: Optional[float] = None,
    criteria: Sequence[str] = GENERATION_CRITERIA,
    mode: Optional[str] = None,
) -> RunResult:
    """
    평가 1회 실행.
      - Ground Truth(gold_chunks)가 있으면 retrieval/filtering 지표를 계산한다.
      - 없으면 Reference-Free 로 judge 기반 생성 지표만 계산한다.
      - filter_fn=None 이면 필터 OFF 실행이다.
    """
    k = k or settings.EVAL_DEFAULT_K
    sim_threshold = settings.SIM_THRESHOLD if sim_threshold is None else sim_threshold
    mode = mode or (MODE_FILTER_OFF if filter_fn is None else MODE_FILTER_ON)

    results: List[CaseResult] = []

    for raw_case in cases:
        case: GroundTruthCase = as_case(raw_case)
        result = CaseResult(
            case_id=case.case_id,
            query=case.query,
            gold_ids=list(case.gold_chunks),
            has_ground_truth=bool(case.gold_chunks),
        )

        try:
            retrieved = list(retrieve_fn(case.query))
            kept, removed = _apply_filter(retrieved, filter_fn)

            result.retrieved_ids = [_chunk_id(c) for c in retrieved]
            result.filtered_ids = [_chunk_id(c) for c in kept]
            result.removed_ids = [_chunk_id(c) for c in removed]
            result.filter_ratio = round(len(removed) / len(retrieved), 4) if retrieved else 0.0

            scores = [float(c.get("similarity_score", 0.0) or 0.0) for c in kept]
            confidence = confidence_fn(kept, result.filter_ratio) if confidence_fn else None
            result.confidence = None if confidence is None else round(float(confidence), 4)
            result.signals = summarize_signals(scores, result.filter_ratio, confidence=result.confidence)

            if case.gold_chunks:
                result.retrieval_metrics = compute_retrieval_metrics(
                    retrieved_ids=result.retrieved_ids,
                    gold_ids=result.gold_ids,
                    k=k,
                    scores=[float(c.get("similarity_score", 0.0) or 0.0) for c in retrieved],
                    sim_threshold=sim_threshold,
                )
                # 필터 후 최종 컨텍스트 기준 지표 — 필터 ON/OFF의 정밀도 차이는 여기서 드러난다.
                result.context_metrics = compute_retrieval_metrics(
                    retrieved_ids=result.filtered_ids,
                    gold_ids=result.gold_ids,
                    k=k,
                    scores=scores,
                    sim_threshold=sim_threshold,
                )
                result.filtering_metrics = compute_filter_metrics(
                    retrieved_ids=result.retrieved_ids,
                    filtered_ids=result.filtered_ids,
                    gold_ids=result.gold_ids,
                    k=k,
                )
            else:
                result.retrieval_metrics = {
                    "retrieval_failure": 1.0
                    if is_retrieval_failure(
                        result.retrieved_ids,
                        [float(c.get("similarity_score", 0.0) or 0.0) for c in retrieved],
                        sim_threshold,
                    )
                    else 0.0
                }

            if generate_fn is not None:
                generated = generate_fn(case.query, kept)
                if isinstance(generated, dict):
                    result.answer = str(generated.get("answer", ""))
                    if generated.get("confidence") is not None:
                        result.confidence = round(float(generated["confidence"]), 4)
                        result.signals["confidence"] = result.confidence
                else:
                    result.answer = str(generated)

            if judge is not None and result.answer:
                judged = evaluate_generation(
                    question=case.query,
                    context=_context_text(kept),
                    answer=result.answer,
                    judge=judge,
                    criteria=criteria,
                    reference=case.as_reference(),
                )
                result.generation_scores = judged["scores"]

            if case.reference_answer and result.answer:
                result.exact_match = exact_match(result.answer, case.reference_answer)
                result.token_f1 = token_f1(result.answer, case.reference_answer)

            result.error_stage = classify_error(
                retrieved_ids=result.retrieved_ids,
                filtered_ids=result.filtered_ids,
                gold_ids=result.gold_ids,
                generation_scores=result.generation_scores,
                answer=result.answer,
                k=k,
            )
        except Exception as e:  # 케이스 단위 실패는 기록하고 다음 케이스로 (조용히 성공 처리하지 않는다)
            result.error = f"{type(e).__name__}: {e}"
            result.error_stage = "Execution"

        results.append(result)

    return RunResult(mode=mode, cases=results, aggregate=_aggregate(results, k=k))


def _aggregate(results: List[CaseResult], k: int) -> Dict[str, Any]:
    with_gt = [r for r in results if r.has_ground_truth and not r.error]
    generation_scores = [r.generation_scores for r in results if r.generation_scores]

    error_stages: Dict[str, int] = {}
    for r in results:
        if r.error_stage:
            error_stages[r.error_stage] = error_stages.get(r.error_stage, 0) + 1

    aggregate: Dict[str, Any] = {
        "case_count": len(results),
        "k": k,
        "ground_truth_case_count": len(with_gt),
        "error_case_count": len([r for r in results if r.error]),
        "retrieval": average_metrics([r.retrieval_metrics for r in results if r.retrieval_metrics]),
        "context": average_metrics([r.context_metrics for r in results if r.context_metrics]),
        "filtering": average_metrics([r.filtering_metrics for r in with_gt if r.filtering_metrics]),
        "generation": average_scores(generation_scores),
        "signals": aggregate_signals([r.signals for r in results if r.signals]),
        "retrieval_failure_rate": compute_retrieval_failure_rate(
            [bool(r.retrieval_metrics.get("retrieval_failure")) for r in results if r.retrieval_metrics]
        ),
        "hallucination_rate": hallucination_rate(generation_scores),
        "error_stages": error_stages,
    }

    em_values = [r.exact_match for r in results if r.exact_match is not None]
    f1_values = [r.token_f1 for r in results if r.token_f1 is not None]
    if em_values:
        aggregate["exact_match"] = round(sum(em_values) / len(em_values), 4)
    if f1_values:
        aggregate["token_f1"] = round(sum(f1_values) / len(f1_values), 4)

    confidences = [r.confidence for r in results if r.confidence is not None]
    if confidences:
        aggregate["confidence"] = round(sum(confidences) / len(confidences), 4)

    return aggregate


def run_comparison(
    cases: Sequence[Any],
    retrieve_fn: Callable[[str], List[Dict[str, Any]]],
    filter_fn: Callable,
    generate_fn: Optional[Callable] = None,
    judge: Optional[Callable] = None,
    confidence_fn: Optional[Callable] = None,
    k: Optional[int] = None,
    sim_threshold: Optional[float] = None,
    criteria: Sequence[str] = GENERATION_CRITERIA,
) -> Dict[str, RunResult]:
    """Filter OFF(filter_fn=None) vs ON(filter_fn 주입) 비교 실행."""
    common = dict(
        cases=cases,
        retrieve_fn=retrieve_fn,
        generate_fn=generate_fn,
        judge=judge,
        confidence_fn=confidence_fn,
        k=k,
        sim_threshold=sim_threshold,
        criteria=criteria,
    )
    return {
        MODE_FILTER_OFF: run_evaluation(filter_fn=None, mode=MODE_FILTER_OFF, **common),
        MODE_FILTER_ON: run_evaluation(filter_fn=filter_fn, mode=MODE_FILTER_ON, **common),
    }


def evaluate_reference_free(
    questions: Sequence[Any],
    retrieve_fn: Callable,
    filter_fn: Optional[Callable] = None,
    generate_fn: Optional[Callable] = None,
    judge: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Ground Truth 없이 judge 기반 지표만 계산하는 Reference-Free 실행 (하위 호환 진입점).
    """
    run = run_evaluation(
        cases=questions,
        retrieve_fn=retrieve_fn,
        filter_fn=filter_fn,
        generate_fn=generate_fn,
        judge=judge,
    )
    return run.to_dict()
