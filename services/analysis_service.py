"""
analysis_service.py — RAG Pipeline Orchestration (①~⑥)

    ① PR Diff Embedding          (services/retrieval.py → llamaindex.pipeline.get_embed_model 재사용)
    ② PGVector Similarity Search (services/retrieval.py)
    ③ Context Filter Agent       (services/context_filter.py — Top-1 반드시 보존)
    ④ Grounded Prompt 생성        (services/prompt_builder.py — 필터 통과 Context만)
    ⑤ GPT-4o Structured Response (JSON 강제, 자유 문자열 금지)
    ⑥ Confidence 계산            (services/confidence.py — 실제 검색 신호 기반)

설계 원칙
- 각 단계의 중간 산출물을 PipelineContext 하나에 모아 추적 가능하게 유지한다.
- 검색 실패(DB 오류) / 근거 부족 / LLM 응답 파싱 실패를 각각 구분해 처리한다.
  외부 실패를 정상 응답으로 위장하지 않는다.
- retrieve/filter/generate/translate 를 전부 주입 가능하게 두어 DB·LLM 없이 테스트한다.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.config import settings
from models.schemas import (
    PRAnalysisRequest, PRAnalysisResponse, Evidence, CodeReviewComment, RoleImpact,
    AsyncAnalysisRequest, AnalysisResultPayload, AnalysisChangeItem, PullRequestFile,
    EvidenceRef, RoleImpactItem,
)
# RetrievalError(검색/DB 실패)는 이 모듈에서 잡지 않고 그대로 전파한다 → 라우터에서 502로 변환.
from services.retrieval import RetrievalOutcome, retrieve_with_signals
from services.context_filter import FilterOutcome, filter_contexts, filter_contexts_with_llm
from services.prompt_builder import (
    PRInput, ProjectInfo, SYSTEM_INSTRUCTION, build_grounded_prompt,
)
from services.confidence import ConfidenceOutcome, calculate_confidence

INSUFFICIENT_GROUNDING_SUMMARY = (
    "이번 PR을 설명할 만한 프로젝트 컨텍스트를 충분히 찾지 못했습니다. "
    "AI 초안 대신 사람이 직접 확인해 주세요."
)


class LLMResponseParseError(RuntimeError):
    """GPT 응답이 약속한 JSON 구조가 아님. 상위에서 실패로 처리해야 하며 삼키지 않는다."""


@dataclass
class PipelineContext:
    """RAG 파이프라인 한 번의 실행 추적 컨텍스트 (로깅·평가·디버깅 공용)"""

    query_text: str = ""
    retrieval: Optional[RetrievalOutcome] = None
    filtering: Optional[FilterOutcome] = None
    prompt: Optional[str] = None
    llm_raw: Optional[str] = None
    llm_output: Dict[str, Any] = field(default_factory=dict)
    confidence: Optional[ConfidenceOutcome] = None
    grounding_sufficient: bool = True
    notes: List[str] = field(default_factory=list)

    @property
    def kept(self) -> List[Dict[str, Any]]:
        return self.filtering.kept if self.filtering else []

    @property
    def filter_ratio(self) -> float:
        return self.filtering.filter_ratio if self.filtering else 0.0

    def to_log_dict(self) -> Dict[str, Any]:
        """중간 산출물 요약 (프롬프트/본문 전체는 제외하고 신호만)"""
        return {
            "retrieved_count": self.retrieval.retrieved_count if self.retrieval else 0,
            "top_score": self.retrieval.top_score if self.retrieval else 0.0,
            "above_threshold_count": self.retrieval.above_threshold_count if self.retrieval else 0,
            "kept_count": len(self.kept),
            "removed_count": len(self.filtering.removed) if self.filtering else 0,
            "filter_ratio": self.filter_ratio,
            "filter_mode": self.filtering.mode if self.filtering else None,
            "top1_preserved": self.filtering.top1_preserved if self.filtering else None,
            "grounding_sufficient": self.grounding_sufficient,
            "confidence": self.confidence.score if self.confidence else 0.0,
            "confidence_signals": self.confidence.signals if self.confidence else {},
            "prompt_chars": len(self.prompt or ""),
            "notes": list(self.notes),
        }


# ==========================================
# 0. 입력 가공
# ==========================================

def build_diff_content(files: List[PullRequestFile]) -> str:
    """PR 변경 파일 목록을 단일 Diff 문자열로 결합"""
    blocks = [f"### {f.file_path} ({f.change_type})\n{f.patch}" for f in files if f.patch]
    return "\n\n".join(blocks)


def _default_translate(title: str, description: str) -> str:
    from services.translation_service import translate_pr_to_en_query  # 지연 import

    return translate_pr_to_en_query(title, description or "")


def _make_default_filter(query_text: str) -> Callable[[List[Dict[str, Any]]], FilterOutcome]:
    """
    settings.FILTER_MODE에 따라 필터 구현을 고른다.
    LLM 모드에서도 Top-1 보존 규칙은 context_filter가 그대로 적용한다.
    """
    if settings.filter_mode == "llm":
        return lambda chunks: filter_contexts_with_llm(chunks, query_text=query_text)
    return lambda chunks: filter_contexts(chunks)


# ==========================================
# ⑤ GPT-4o Structured Response
# ==========================================

def _default_generate(system_instruction: str, prompt: str) -> str:
    """OpenAI Chat Completions(JSON mode) 호출. 원문 문자열을 그대로 반환한다."""
    from openai import OpenAI  # 지연 import: API Key 없이도 모듈 import가 가능하도록

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return response.choices[0].message.content


def parse_llm_json(raw: Optional[str]) -> Dict[str, Any]:
    """
    LLM 응답을 JSON으로 파싱한다. 실패하면 LLMResponseParseError를 던진다.
    (빈 dict를 돌려주는 '조용한 실패'는 근거 없는 응답을 정상처럼 보이게 하므로 금지)
    """
    if raw is None or not str(raw).strip():
        raise LLMResponseParseError("LLM 응답이 비어 있습니다.")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise LLMResponseParseError(f"LLM 응답 JSON 파싱 실패: {e}") from e
    if not isinstance(parsed, dict):
        raise LLMResponseParseError(f"LLM 응답이 JSON 객체가 아닙니다: {type(parsed).__name__}")
    return parsed


# ==========================================
# 파이프라인 본체
# ==========================================

def run_pipeline(
    pr: PRInput,
    repo_name: str,
    project: Optional[ProjectInfo] = None,
    retrieve_fn: Optional[Callable[..., RetrievalOutcome]] = None,
    filter_fn: Optional[Callable[..., FilterOutcome]] = None,
    generate_fn: Optional[Callable[[str, str], str]] = None,
    translate_fn: Optional[Callable[[str, str], str]] = None,
    top_k: Optional[int] = None,
) -> PipelineContext:
    """
    ①~⑥ 전체 오케스트레이션. 각 단계 산출물을 PipelineContext에 누적해 반환한다.

    예외 정책
      - Vector DB 오류 : services.retrieval.RetrievalError 그대로 전파 (외부 인프라 실패)
      - 근거 부족      : 예외가 아니라 grounding_sufficient=False 경로로 처리하고 LLM을 호출하지 않는다
      - 파싱 실패      : LLMResponseParseError 전파
    """
    ctx = PipelineContext()

    # ① 쿼리 구성 (+ 검색 정밀도용 영문 번역)
    translate = translate_fn or _default_translate
    translated = translate(pr.title, pr.body)
    ctx.query_text = f"PR Title/Summary: {translated}\nPR Diff:\n{pr.diff}"

    # ② 검색 — DB 오류는 RetrievalError로 그대로 올라간다
    retrieve = retrieve_fn or retrieve_with_signals
    ctx.retrieval = retrieve(
        query_text=ctx.query_text,
        repo_name=repo_name,
        top_k=top_k if top_k is not None else settings.RAG_TOP_K,
    )

    # ③ 필터 (제거분도 보존) — FILTER_MODE=llm 이면 LLM Context Filter Agent를 쓴다
    do_filter = filter_fn or _make_default_filter(ctx.query_text)
    ctx.filtering = do_filter(ctx.retrieval.chunks)
    if ctx.filtering.notes:
        ctx.notes.extend(ctx.filtering.notes)

    # ⑥' 근거 충분성 판단 — 부족하면 LLM을 호출하지 않고 '근거 부족' 경로로 나간다
    ctx.grounding_sufficient = bool(ctx.retrieval.grounding_sufficient)
    ctx.confidence = calculate_confidence(ctx.kept, ctx.filter_ratio)

    if not ctx.grounding_sufficient:
        ctx.notes.append(f"근거 부족: {ctx.retrieval.reason}")
        ctx.llm_output = {
            "summary": INSUFFICIENT_GROUNDING_SUMMARY,
            "needsConfirmation": True,
            "confirmationItems": [ctx.retrieval.reason or "검색된 근거가 부족합니다."],
        }
        ctx.confidence.needs_confirmation = True
        return ctx

    # ④ Grounded Prompt (필터 통과 Context만)
    ctx.prompt = build_grounded_prompt(pr=pr, filtered_contexts=ctx.kept, project=project)

    # ⑤ 구조화 출력 생성 + 파싱 (실패 시 예외 전파)
    generate = generate_fn or _default_generate
    ctx.llm_raw = generate(SYSTEM_INSTRUCTION, ctx.prompt)
    ctx.llm_output = parse_llm_json(ctx.llm_raw)

    return ctx


# ==========================================
# 응답 매핑
# ==========================================

def _needs_confirmation(ctx: PipelineContext) -> bool:
    """LLM이 스스로 표시한 확인 필요 + 검색 신호 기반 확인 필요를 OR로 합친다."""
    llm_flag = bool(ctx.llm_output.get("needsConfirmation", False))
    signal_flag = bool(ctx.confidence.needs_confirmation) if ctx.confidence else True
    return llm_flag or signal_flag or not ctx.grounding_sufficient


def _confirmation_items(ctx: PipelineContext) -> List[str]:
    items = [str(i) for i in ctx.llm_output.get("confirmationItems", []) if str(i).strip()]
    if ctx.confidence and ctx.confidence.retrieval_quality_warning:
        items.append(
            f"검색 결과의 {ctx.filter_ratio:.0%}가 필터링되었습니다. 검색 품질(인덱싱 범위·임계값) 확인이 필요합니다."
        )
    return items


def _build_evidences(ctx: PipelineContext) -> List[Evidence]:
    """동기 API용 Evidence — 실제로 프롬프트에 들어간 필터 통과 chunk 그대로"""
    return [
        Evidence(
            id=str(c.get("id", c.get("chunk_id", ""))),
            source_code=c.get("source_code"),
            pr_diff=c.get("pr_diff"),
            review_comment=c.get("review_comment"),
            similarity_score=float(c.get("similarity_score", 0.0) or 0.0),
            chunk_id=c.get("chunk_id"),
            source_type=c.get("source_type"),
            file_path=c.get("file_path"),
        )
        for c in ctx.kept
    ]


def _build_evidence_refs(ctx: PipelineContext) -> List[EvidenceRef]:
    """
    콜백용 EvidenceRef — LLM이 각 판단에 연결한 근거(설명 포함)를 우선 싣고,
    LLM이 인용하지 않은 필터 통과 chunk도 추적 가능하도록 뒤에 덧붙인다.
    """
    score_by_chunk = {c.get("chunk_id"): c.get("similarity_score") for c in ctx.kept}
    refs: List[EvidenceRef] = []
    cited: set = set()

    for item in ctx.llm_output.get("evidence", []) or []:
        if not isinstance(item, dict):
            continue
        chunk_id = _nullable_str(item.get("chunkId") or item.get("chunk_id"))
        cited.add(chunk_id)
        refs.append(
            EvidenceRef(
                chunk_id=chunk_id,
                file_path=_nullable_str(item.get("filePath") or item.get("file_path")),
                diff_location=_nullable_str(item.get("diffLocation") or item.get("diff_location")),
                description=_nullable_str(item.get("description")),
                similarity_score=score_by_chunk.get(chunk_id),
            )
        )

    for c in ctx.kept:
        if c.get("chunk_id") in cited:
            continue
        refs.append(
            EvidenceRef(
                chunk_id=c.get("chunk_id"),
                file_path=c.get("file_path"),
                diff_location=None,
                description=None,
                similarity_score=c.get("similarity_score"),
            )
        )
    return refs


# LLM이 JSON null 대신 문자열로 내보내는 값들. 그대로 두면 "null"이라는 chunk_id가 응답에 실린다.
_NULLISH_STRINGS = {"null", "none", "nil", "n/a", "na", "-", ""}


def _nullable_str(value: Any) -> Optional[str]:
    """LLM이 낸 값을 문자열 또는 None으로 정규화한다 (문자열 "null" → None)."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULLISH_STRINGS else text


def _nullable_int(value: Any) -> Optional[int]:
    """LLM이 낸 값을 정수 또는 None으로 정규화한다 ("42" → 42, "null"/"미정" → None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _nullable_str(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _risk_score(output: Dict[str, Any]) -> int:
    """riskScore를 0~100 정수로 정규화한다. 값이 없거나 해석 불가면 0."""
    raw = output.get("riskScore", output.get("risk_score"))
    parsed = _nullable_int(raw)
    if parsed is None:
        return 0
    return max(0, min(parsed, 100))


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _role_impacts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    parsed = []
    for item in value:
        if not isinstance(item, dict) or not item.get("role"):
            continue
        parsed.append(
            {
                "role": str(item.get("role")),
                "impact": str(item.get("impact", "")),
                "evidence_ids": _str_list(item.get("evidenceIds") or item.get("evidence_ids")),
            }
        )
    return parsed


# ==========================================
# 진입점 1: 동기 API (POST /api/v1/analyze/pr)
# ==========================================

def analyze_pr_pipeline(
    request: PRAnalysisRequest,
    project: Optional[ProjectInfo] = None,
    **injected: Any,
) -> PRAnalysisResponse:
    """동기 분석 — 기존 PRAnalysisResponse 계약(하위 호환)에 기록 초안 필드를 추가해 반환한다."""
    pr = PRInput(
        title=request.title,
        body=getattr(request, "description", "") or "",
        changed_files=[],
        diff=request.diff_content,
    )
    ctx = run_pipeline(pr=pr, repo_name=request.repo_name, project=project, **injected)
    out = ctx.llm_output

    reviews = [
        CodeReviewComment(
            file_path=_nullable_str(r.get("file_path") or r.get("filePath")),
            line_number=_nullable_int(r.get("line_number") or r.get("lineNumber")),
            comment=str(r.get("comment", "")),
        )
        for r in out.get("reviews", []) or []
        if isinstance(r, dict) and str(r.get("comment", "")).strip()
    ]

    return PRAnalysisResponse(
        pr_id=request.pr_id,
        summary=str(out.get("summary", "")) or INSUFFICIENT_GROUNDING_SUMMARY,
        risk_score=_risk_score(out),
        reviews=reviews,
        evidences=_build_evidences(ctx),
        confidence=ctx.confidence.score if ctx.confidence else 0.0,
        needs_confirmation=_needs_confirmation(ctx),
        filter_ratio=ctx.filter_ratio,
        purpose=out.get("purpose"),
        change_reason=out.get("changeReason"),
        before=out.get("before"),
        after=out.get("after"),
        related_features=_str_list(out.get("relatedFeatures")),
        affected_roles=_str_list(out.get("affectedRoles")),
        role_impacts=[RoleImpact(**ri) for ri in _role_impacts(out.get("roleImpacts"))],
        follow_up_tasks=_str_list(out.get("followUpTasks")),
        confirmation_items=_confirmation_items(ctx),
        retrieval_quality_warning=bool(ctx.confidence.retrieval_quality_warning) if ctx.confidence else False,
        grounding_sufficient=ctx.grounding_sufficient,
    )


# ==========================================
# 진입점 2: 비동기 내부 API (POST /internal/v1/analyses → 콜백)
# ==========================================

def analyze_pr_for_callback(
    request: AsyncAnalysisRequest,
    project: Optional[ProjectInfo] = None,
    **injected: Any,
) -> AnalysisResultPayload:
    """비동기 분석 — Backend 콜백 result 계약(camelCase)에 맞춰 반환한다."""
    files = request.pull_request.files
    pr = PRInput(
        title=request.pull_request.title,
        body=request.pull_request.body or "",
        changed_files=[f"{f.file_path} ({f.change_type}, +{f.additions}/-{f.deletions})" for f in files],
        diff=build_diff_content(files),
        commits=[request.pull_request.head_sha] if request.pull_request.head_sha else [],
    )
    project = project or ProjectInfo(name=request.repository_full_name, language=request.language)

    ctx = run_pipeline(pr=pr, repo_name=request.repository_full_name, project=project, **injected)
    out = ctx.llm_output

    changes = [
        AnalysisChangeItem(
            file_path=_nullable_str(c.get("filePath") or c.get("file_path")) or "unknown",
            description=str(c.get("description", "")),
        )
        for c in out.get("changes", []) or []
        if isinstance(c, dict)
    ]

    return AnalysisResultPayload(
        summary=str(out.get("summary", "")) or INSUFFICIENT_GROUNDING_SUMMARY,
        changes=changes,
        impacts=_str_list(out.get("impacts")),
        risks=_str_list(out.get("risks")),
        recommendations=_str_list(out.get("recommendations")),
        purpose=out.get("purpose"),
        change_reason=out.get("changeReason"),
        before=out.get("before"),
        after=out.get("after"),
        related_features=_str_list(out.get("relatedFeatures")),
        affected_roles=_str_list(out.get("affectedRoles")),
        role_impacts=[RoleImpactItem(**ri) for ri in _role_impacts(out.get("roleImpacts"))],
        follow_up_tasks=_str_list(out.get("followUpTasks")),
        needs_confirmation=_needs_confirmation(ctx),
        confirmation_items=_confirmation_items(ctx),
        evidence=_build_evidence_refs(ctx),
        confidence=ctx.confidence.score if ctx.confidence else 0.0,
        retrieval_quality_warning=bool(ctx.confidence.retrieval_quality_warning) if ctx.confidence else False,
    )
