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
    EvidenceRef, RoleImpactItem, FollowUpTask, FollowUpTaskItem,
)
# RetrievalError(검색/DB 실패)는 이 모듈에서 잡지 않고 그대로 전파한다 → 라우터에서 502로 변환.
from services.retrieval import RetrievalOutcome, retrieve_with_signals
from services.context_filter import FilterOutcome, filter_contexts, filter_contexts_with_llm
from services.prompt_builder import (
    ALLOWED_BASIS, ALLOWED_ROLES, EVIDENCE_SOURCE_CONTEXT, EVIDENCE_SOURCE_DIFF, PRInput,
    ProjectInfo, RecordDraftOutput, build_system_prompt, build_user_prompt,
)
from services.confidence import ConfidenceOutcome, calculate_confidence

INSUFFICIENT_GROUNDING_SUMMARY = (
    "이번 PR을 설명할 만한 프로젝트 컨텍스트를 충분히 찾지 못했습니다. "
    "AI 초안 대신 사람이 직접 확인해 주세요."
)
EMPTY_DIFF_SUMMARY = "코드 diff가 비어 있어 변경 내용을 분석할 수 없습니다."


class LLMResponseParseError(RuntimeError):
    """GPT 응답이 약속한 JSON 구조가 아님. 상위에서 실패로 처리해야 하며 삼키지 않는다."""


@dataclass
class PipelineContext:
    """RAG 파이프라인 한 번의 실행 추적 컨텍스트 (로깅·평가·디버깅 공용)"""

    query_text: str = ""
    retrieval: Optional[RetrievalOutcome] = None
    filtering: Optional[FilterOutcome] = None
    system_prompt: Optional[str] = None
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


def _lookup_project(repo_name: str, language: Optional[str] = None) -> Optional[ProjectInfo]:
    """
    project.yml 에서 저장소 이름으로 프로젝트 메타를 찾는다.
    파일이 없거나 항목이 없으면 None — 프롬프트에 "(정보 없음)"으로 표기되고 역할 판단을 하지 않는다.
    레지스트리 파일 오류가 분석 전체를 막지 않도록 예외는 삼키고 로그만 남긴다.
    """
    try:
        from services.project_registry import get_project_info  # 지연 import

        project = get_project_info(repo_name)
    except Exception as e:  # YAML 파싱 오류 등
        print(f"[Project Registry] 프로젝트 메타 조회 실패 repo={repo_name}: {type(e).__name__}: {e}")
        return None

    if project is not None and language:
        project.language = language
    return project


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

def _default_generate(system_prompt: str, user_prompt: str) -> str:
    """
    OpenAI Structured Output 호출.

    RecordDraftOutput(Pydantic) 으로 JSON 스키마를 강제하고 그대로 파싱하므로
    자유 텍스트 후처리 파싱이 필요 없다. 반환값은 정규화된 JSON 문자열이며,
    이후 파이프라인은 주입된 generate_fn과 동일한 인터페이스(문자열)를 그대로 쓴다.
    """
    from openai import OpenAI  # 지연 import: API Key 없이도 모듈 import가 가능하도록

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # SDK 버전에 따라 parse()가 client.chat.completions / client.beta.chat.completions 에 있다.
    completions = client.chat.completions
    if not hasattr(completions, "parse"):
        completions = client.beta.chat.completions

    response = completions.parse(
        model=settings.LLM_MODEL,
        messages=messages,
        response_format=RecordDraftOutput,
        temperature=0.2,
    )

    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise LLMResponseParseError(f"LLM이 응답을 거부했습니다: {message.refusal}")
    if message.parsed is None:
        raise LLMResponseParseError("LLM 응답을 출력 스키마로 파싱하지 못했습니다.")

    return message.parsed.model_dump_json()


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

    # ⓪ diff가 비어 있으면 분석할 대상이 없다 — LLM을 호출하지 않고 확인 필요 경로로 처리한다.
    if not pr.has_diff:
        ctx.grounding_sufficient = False
        ctx.notes.append("코드 diff가 비어 있어 분석을 진행하지 않았습니다.")
        ctx.llm_output = {
            "summary": EMPTY_DIFF_SUMMARY,
            "needsConfirmation": ["코드 diff가 비어 있음 — PR에 실제 변경이 있는지 확인 필요"],
        }
        ctx.confidence = calculate_confidence([], 0.0)
        return ctx

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
            "needsConfirmation": [ctx.retrieval.reason or "검색된 근거가 부족합니다."],
        }
        ctx.confidence.needs_confirmation = True
        return ctx

    # ④ Grounded Prompt (필터 통과 Context만)
    ctx.system_prompt = build_system_prompt(project)
    ctx.prompt = build_user_prompt(pr=pr, filtered_contexts=ctx.kept, project=project)

    # ⑤ 구조화 출력 생성 + 파싱 (실패 시 예외 전파)
    generate = generate_fn or _default_generate
    ctx.llm_raw = generate(ctx.system_prompt, ctx.prompt)
    ctx.llm_output = parse_llm_json(ctx.llm_raw)

    return ctx


# ==========================================
# 응답 매핑
# ==========================================

def _confirmation_items(ctx: PipelineContext) -> List[str]:
    """
    확인 필요 사항(needsConfirmation) 목록.
    LLM이 적은 항목 + 검색 신호로 판단한 항목을 합친다.
    """
    raw = ctx.llm_output.get("needsConfirmation")
    if isinstance(raw, bool):        # 구버전 출력(bool) 하위 호환
        raw = []
    items = [str(i).strip() for i in (raw or []) if str(i).strip()]

    if ctx.confidence and ctx.confidence.retrieval_quality_warning:
        items.append(
            f"검색 결과의 {ctx.filter_ratio:.0%}가 필터링되었습니다. 검색 품질(인덱싱 범위·임계값) 확인이 필요합니다."
        )
    if not ctx.grounding_sufficient and not items:
        items.append("검색된 근거가 부족합니다 — 사람이 직접 확인 필요")
    return items


def _needs_confirmation(ctx: PipelineContext) -> bool:
    """동기 API의 bool 필드용 — 확인 항목이 있거나 검색 신호가 나쁘면 True (하위 호환 유지)"""
    signal_flag = bool(ctx.confidence.needs_confirmation) if ctx.confidence else True
    return bool(_confirmation_items(ctx)) or signal_flag or not ctx.grounding_sufficient


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
    분석 근거(evidence) 목록.

    LLM이 각 판단에 연결한 근거(id/source/location/description)를 그대로 싣고,
    location이 검색 chunk를 가리키면 유사도를 붙여 추적 가능하게 한다.
    LLM이 인용하지 않은 필터 통과 chunk도 뒤에 덧붙여 "무엇을 보여줬는지"를 남긴다.
    """
    kept_by_chunk = {str(c.get("chunk_id")): c for c in ctx.kept}
    refs: List[EvidenceRef] = []
    used_ids: set = set()
    cited_chunks: set = set()

    for idx, item in enumerate(ctx.llm_output.get("evidence", []) or [], 1):
        if not isinstance(item, dict):
            continue
        location = _nullable_str(item.get("location"))
        source = _nullable_str(item.get("source")) or (
            EVIDENCE_SOURCE_CONTEXT if location in kept_by_chunk else EVIDENCE_SOURCE_DIFF
        )
        chunk = kept_by_chunk.get(location or "")
        if chunk is not None:
            cited_chunks.add(str(chunk.get("chunk_id")))

        evidence_id = _nullable_str(item.get("id")) or f"e{idx}"
        used_ids.add(evidence_id)
        refs.append(
            EvidenceRef(
                id=evidence_id,
                source=source,
                location=location,
                description=_nullable_str(item.get("description")),
                chunk_id=str(chunk.get("chunk_id")) if chunk is not None else None,
                similarity_score=chunk.get("similarity_score") if chunk is not None else None,
            )
        )

    # LLM이 인용하지 않은 컨텍스트도 근거 목록에 남긴다(무엇을 보여줬는지 추적).
    for offset, c in enumerate(ctx.kept, 1):
        chunk_id = str(c.get("chunk_id"))
        if chunk_id in cited_chunks:
            continue
        candidate = f"c{offset}"
        while candidate in used_ids:
            offset += len(ctx.kept)
            candidate = f"c{offset}"
        used_ids.add(candidate)
        refs.append(
            EvidenceRef(
                id=candidate,
                source=EVIDENCE_SOURCE_CONTEXT,
                location=c.get("file_path") or chunk_id,
                description=None,
                chunk_id=chunk_id,
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


def _positive_int(value: Any) -> Optional[int]:
    """1 이상의 정수만 값으로 인정한다 (0/음수/해석 불가는 '특정 불가' → None)."""
    parsed = _nullable_int(value)
    return parsed if parsed is not None and parsed > 0 else None


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


def _allowed_roles(value: Any) -> List[str]:
    """
    affectedRoles를 허용 역할 목록으로 제한한다.
    프롬프트 규칙 7("근거 없는 역할을 만들지 않는다")을 코드에서도 강제해,
    LLM이 만들어낸 임의 역할(예: "개발자")이 응답에 새는 것을 막는다.
    """
    roles = []
    for role in _str_list(value):
        name = role.strip()
        if name in ALLOWED_ROLES and name not in roles:
            roles.append(name)
    return roles


def _follow_up_tasks(value: Any) -> List[Dict[str, Any]]:
    """
    followUpTasks 정규화 — `{role, task, evidenceRefs}` 형태.

    - role은 허용 역할 7종만 인정하고, 그 외/빈 값은 None(담당 미정)으로 둔다.
      작업 자체는 버리지 않는다 — 담당을 특정 못 했다고 해야 할 일이 사라지는 건 아니다.
    - 구버전 출력(string[])도 받아들인다.
    """
    if not isinstance(value, list):
        return []

    tasks: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):                       # 구버전 string[] 하위 호환
            text = item.strip()
            if text:
                tasks.append({"role": None, "task": text, "evidence_refs": []})
            continue
        if not isinstance(item, dict):
            continue

        text = str(item.get("task", "")).strip()
        if not text:
            continue
        role = _nullable_str(item.get("role"))
        tasks.append(
            {
                "role": role if role in ALLOWED_ROLES else None,
                "task": text,
                "evidence_refs": _str_list(
                    item.get("evidenceRefs") or item.get("evidence_refs")
                ),
            }
        )
    return tasks


def _role_impacts(value: Any) -> List[Dict[str, Any]]:
    """roleImpacts 정규화 — 허용 역할만, basis는 허용 값만 남긴다."""
    if not isinstance(value, list):
        return []

    parsed = []
    for item in value:
        if not isinstance(item, dict) or not item.get("role"):
            continue
        role = str(item.get("role")).strip()
        if role not in ALLOWED_ROLES:
            continue
        basis = _nullable_str(item.get("basis"))
        parsed.append(
            {
                "role": role,
                "impact": str(item.get("impact", "")),
                "basis": basis if basis in ALLOWED_BASIS else None,
                "evidence_refs": _str_list(
                    item.get("evidenceRefs") or item.get("evidence_refs") or item.get("evidenceIds")
                ),
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
    project = project or _lookup_project(request.repo_name)
    ctx = run_pipeline(pr=pr, repo_name=request.repo_name, project=project, **injected)
    out = ctx.llm_output

    # 구조화 출력은 strict 모드라 null 대신 빈 문자열/0 이 오므로 그것도 "특정 불가"로 정규화한다.
    reviews = [
        CodeReviewComment(
            file_path=_nullable_str(r.get("file_path") or r.get("filePath")),
            line_number=_positive_int(r.get("line_number") or r.get("lineNumber")),
            comment=str(r.get("comment", "")).strip(),
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
        affected_roles=_allowed_roles(out.get("affectedRoles")),
        role_impacts=[RoleImpact(**ri) for ri in _role_impacts(out.get("roleImpacts"))],
        follow_up_tasks=[FollowUpTask(**task) for task in _follow_up_tasks(out.get("followUpTasks"))],
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
    # 요청에 project가 넘어오면 그것이 우선, 없으면 레지스트리(project.yml)에서 찾는다.
    project = project or _lookup_project(request.repository_full_name, language=request.language)
    if project is None:
        project = ProjectInfo(name=request.repository_full_name, language=request.language)

    ctx = run_pipeline(pr=pr, repo_name=request.repository_full_name, project=project, **injected)
    out = ctx.llm_output

    role_impacts = _role_impacts(out.get("roleImpacts"))
    evidence = _build_evidence_refs(ctx)
    follow_up_tasks = _follow_up_tasks(out.get("followUpTasks"))

    # 기존 콜백 계약 필드(changes/impacts/recommendations)는 새 출력 스키마에 대응 항목이 없다.
    # LLM에 같은 내용을 두 번 만들게 하지 않고, 새 스키마를 기존 필드로 투영(projection)한다.
    #   changes         ← evidence 중 현재 PR diff 근거
    #   impacts         ← 역할별 영향
    #   recommendations ← 후속 작업
    #   risks           ← 대응 항목 없음(빈 배열). LLM이 값을 내면 그대로 통과시킨다.
    changes = [
        AnalysisChangeItem(file_path=e.location or "unknown", description=e.description or "")
        for e in evidence
        if e.source == EVIDENCE_SOURCE_DIFF and (e.location or e.description)
    ] or [
        AnalysisChangeItem(
            file_path=_nullable_str(c.get("filePath") or c.get("file_path")) or "unknown",
            description=str(c.get("description", "")),
        )
        for c in out.get("changes", []) or []
        if isinstance(c, dict)
    ]
    impacts = _str_list(out.get("impacts")) or [
        f"{ri['role']}: {ri['impact']}" for ri in role_impacts if ri.get("impact")
    ]
    recommendations = _str_list(out.get("recommendations")) or [
        f"{task['role']}: {task['task']}" if task.get("role") else task["task"]
        for task in follow_up_tasks
    ]

    return AnalysisResultPayload(
        summary=str(out.get("summary", "")) or INSUFFICIENT_GROUNDING_SUMMARY,
        changes=changes,
        impacts=impacts,
        risks=_str_list(out.get("risks")),
        recommendations=recommendations,
        purpose=out.get("purpose"),
        change_reason=out.get("changeReason"),
        before=out.get("before"),
        after=out.get("after"),
        related_features=_str_list(out.get("relatedFeatures")),
        affected_roles=_allowed_roles(out.get("affectedRoles")),
        role_impacts=[RoleImpactItem(**ri) for ri in role_impacts],
        follow_up_tasks=[FollowUpTaskItem(**task) for task in follow_up_tasks],
        needs_confirmation=_confirmation_items(ctx),
        evidence=evidence,
        confidence=ctx.confidence.score if ctx.confidence else 0.0,
        retrieval_quality_warning=bool(ctx.confidence.retrieval_quality_warning) if ctx.confidence else False,
    )
