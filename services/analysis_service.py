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
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.config import settings
from models.schemas import (
    PRAnalysisRequest, PRAnalysisResponse, Evidence, CodeReviewComment, RoleImpact,
    AsyncAnalysisRequest, AnalysisResultPayload, AnalysisChangeItem, PullRequestFile,
    EvidenceRef, RoleImpactItem, FollowUpTask, FollowUpTaskItem,
)
from services.retrieval import (
    RetrievalOutcome, retrieve_with_signals, retrieve_contexts, retrieve_repo_contexts,
)
from services.context_filter import (
    FilterOutcome, filter_contexts, filter_contexts_with_llm,
)
from services.prompt_builder import (
    ALLOWED_BASIS, ALLOWED_ROLES, EVIDENCE_SOURCE_CONTEXT, EVIDENCE_SOURCE_DIFF, PRInput,
    ProjectInfo, RecordDraftOutput, build_system_prompt, build_user_prompt,
)
from services.role_normalizer import normalize_role, normalize_roles, partition_roles
from services.confidence import ConfidenceOutcome, calculate_confidence
from services.token_utils import count_tokens, get_encoding
from services.translation_service import translate_pr_to_en_query

# pyflakes Unused Import 경고 방어 및 테스트 monkeypatch 호환을 위한 명시적 export
__all__ = [
    "analyze_pr_pipeline",
    "analyze_pr_for_callback",
    "build_diff_content",
    "run_pipeline",
    "retrieve_contexts",
    "retrieve_repo_contexts",
    "translate_pr_to_en_query",
    "PipelineContext",
    "LLMResponseParseError",
    "PromptTooLargeError",
]

INSUFFICIENT_GROUNDING_SUMMARY = (
    "이번 PR을 설명할 만한 프로젝트 컨텍스트를 충분히 찾지 못했습니다. "
    "AI 초안 대신 사람이 직접 확인해 주세요."
)
EMPTY_DIFF_SUMMARY = "코드 diff가 비어 있어 변경 내용을 분석할 수 없습니다."


class LLMResponseParseError(RuntimeError):
    """GPT 응답이 약속한 JSON 구조가 아님. 상위에서 실패로 처리해야 하며 삼키지 않는다."""


class PromptTooLargeError(Exception):
    """트리밍을 거쳐도 프롬프트가 LLM_MAX_PROMPT_TOKENS 예산을 넘는 경우.

    str(e)가 그대로 콜백 error_message(routers/internal_analysis.py의
    _run_analysis_job)로 노출되므로, 원인이 분명한 메시지를 유지한다.
    """

    def __init__(self, token_count: int, limit: int):
        self.token_count = token_count
        self.limit = limit
        super().__init__(f"PR diff too large to analyze ({token_count} tokens, limit {limit})")


# 내용 자체보다 존재 유무만 중요한 파일들 — 있어도 리뷰 신호가 거의 없는데 patch만 크게 잡아먹는
# 경우가 많아(자동 생성/vendored/lockfile) 프롬프트에서 통째로 제외한다.
_LOW_VALUE_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "Gemfile.lock", "composer.lock", "Cargo.lock", "go.sum",
}
_LOW_VALUE_SUFFIXES = (".min.js", ".min.css", ".map")
_LOW_VALUE_PATH_MARKERS = ("/vendor/", "/node_modules/", "/dist/", "/build/", "/generated/")


def _is_low_value_file(f: PullRequestFile) -> bool:
    """lockfile/생성 파일/vendored 파일/rename-only 변경처럼 diff 신호가 거의 없는 파일."""
    name = f.file_path.rsplit("/", 1)[-1]
    path_lower = f"/{f.file_path.lower()}"

    if name in _LOW_VALUE_FILENAMES:
        return True
    if path_lower.endswith(_LOW_VALUE_SUFFIXES):
        return True
    if any(marker in path_lower for marker in _LOW_VALUE_PATH_MARKERS):
        return True
    if f.change_type == "RENAMED" and "@@" not in (f.patch or ""):
        return True
    return False


def build_diff_content(files: List[PullRequestFile]) -> str:
    """PR 변경 파일 목록을 단일 Diff 문자열로 결합.

    LLM 요청이 OpenAI TPM 한도를 넘겨 매번 동일하게 실패하는 것(request-shape bug)을
    막기 위해, 개별 파일 단위에서부터 크기를 억제한다:
    - lockfile/생성/vendored/rename-only 파일은 리뷰 신호가 거의 없으므로 통째로 제외
    - 남은 각 파일의 patch는 MAX_PATCH_CHARS_PER_FILE로 상한을 둔다
    """
    blocks = []
    omitted_count = 0
    max_patch_chars = getattr(settings, "MAX_PATCH_CHARS_PER_FILE", 4000)

    for f in files:
        if not f.patch or not f.patch.strip():
            continue
        if _is_low_value_file(f):
            omitted_count += 1
            continue

        patch = f.patch
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars] + "\n... (patch truncated)"

        blocks.append(f"### {f.file_path} ({f.change_type})\n{patch}")

    diff_content = "\n\n".join(blocks)
    if omitted_count:
        diff_content += (
            f"\n\n... (truncated, {omitted_count} files omitted: "
            "lockfile/generated/vendored/rename-only changes)"
        )
    return diff_content


def _fit_pr_diff_to_budget(
    system_prompt: str,
    pr: PRInput,
    filtered_contexts: list,
    project: Optional[ProjectInfo] = None,
) -> str:
    """build_user_prompt()로 구성한 프롬프트가 (system_prompt와 합쳐) LLM_MAX_PROMPT_TOKENS를
    넘으면 PR diff 부분만 토큰 단위로 잘라 다시 맞춘다. 그래도 안 맞으면 PromptTooLargeError.

    2026-08-18 인시던트(REFACTOR_BRIEF.md): 대형 PR의 diff를 통째로 프롬프트에 넣어
    OpenAI TPM 한도(429)를 매번 동일하게 넘기던 request-shape 버그의 회귀 방지 지점.
    build_diff_content()의 파일별 patch 상한만으로는 컨텍스트가 많은 경우 전체 예산을
    보장하지 못하므로, 최종 프롬프트 조립 직후 여기서 한 번 더 예산에 맞춘다.
    """
    budget = getattr(settings, "LLM_MAX_PROMPT_TOKENS", 20000)
    model = settings.LLM_MODEL

    def _total_tokens(user_prompt: str) -> int:
        return count_tokens(system_prompt, model) + count_tokens(user_prompt, model)

    user_prompt = build_user_prompt(pr=pr, filtered_contexts=filtered_contexts, project=project)
    token_count = _total_tokens(user_prompt)
    if token_count <= budget:
        return user_prompt

    truncation_marker = "\n\n... (diff truncated to fit token budget)"
    overhead_prompt = build_user_prompt(pr=replace(pr, diff=""), filtered_contexts=filtered_contexts, project=project)
    overhead_tokens = _total_tokens(overhead_prompt)
    marker_tokens = count_tokens(truncation_marker, model)
    diff_budget = budget - overhead_tokens - marker_tokens
    if diff_budget <= 0:
        raise PromptTooLargeError(token_count, budget)

    encoding = get_encoding(model)
    truncated_diff = encoding.decode(encoding.encode(pr.diff or "")[:diff_budget])
    truncated_diff += truncation_marker

    user_prompt = build_user_prompt(
        pr=replace(pr, diff=truncated_diff), filtered_contexts=filtered_contexts, project=project
    )
    token_count = _total_tokens(user_prompt)
    if token_count > budget:
        raise PromptTooLargeError(token_count, budget)
    return user_prompt


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


def _default_translate(title: str, description: str) -> str:
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
    if getattr(settings, "FILTER_MODE", "similarity") == "llm":
        return lambda chunks: filter_contexts_with_llm(chunks, query_text=query_text)
    return lambda chunks: filter_contexts(chunks)


# ==========================================
# ⑤ GPT-4o Structured Response
# ==========================================

def _default_generate(system_prompt: str, user_prompt: str) -> str:
    """
    OpenAI Structured Output 호출.

    RecordDraftOutput(Pydantic) 으로 JSON 스키마를 강제하고 그대로 파싱하므로
    자유 텍스트 후처리 파싱이 필요 없다.
    """
    from openai import OpenAI  # 지연 import

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

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
    """
    ctx = PipelineContext()

    # ⓪ diff가 비어 있으면 분석할 대상이 없다
    if not pr.has_diff:
        ctx.grounding_sufficient = False
        ctx.notes.append("코드 diff가 비어 있어 분석을 진행하지 않았습니다.")
        ctx.llm_output = {
            "summary": EMPTY_DIFF_SUMMARY,
            "needsConfirmation": ["코드 diff가 비어 있음 — PR에 실제 변경이 있는지 확인 필요"],
        }
        ctx.confidence = calculate_confidence([], 0.0)
        return ctx

    # ① 쿼리 구성 (+ 임베딩 8192 토큰 제한 방어를 위한 diff 길이 제어)
    translate = translate_fn or _default_translate
    translated = translate(pr.title, pr.body)
    query_diff = pr.diff[:4000] if pr.diff else ""
    ctx.query_text = f"PR Title/Summary: {translated}\nPR Diff:\n{query_diff}"

    # ② 검색 — DB 오류는 RetrievalError로 그대로 올라간다
    retrieve = retrieve_fn or retrieve_with_signals
    ctx.retrieval = retrieve(
        query_text=ctx.query_text,
        repo_name=repo_name,
        top_k=top_k if top_k is not None else settings.RAG_TOP_K,
    )

    # ③ 필터 (제거분도 보존)
    do_filter = filter_fn or _make_default_filter(ctx.query_text)
    ctx.filtering = do_filter(ctx.retrieval.chunks)
    if ctx.filtering.notes:
        ctx.notes.extend(ctx.filtering.notes)

    # ⑥' 근거 충분성 판단
    ctx.grounding_sufficient = bool(ctx.retrieval.grounding_sufficient)
    ctx.confidence = calculate_confidence(
        ctx.kept,
        ctx.filter_ratio,
        information_loss_ratio=ctx.filtering.information_loss_ratio if ctx.filtering else None,
    )

    if not ctx.grounding_sufficient:
        ctx.notes.append(f"근거 부족: {ctx.retrieval.reason}")
        ctx.llm_output = {
            "summary": INSUFFICIENT_GROUNDING_SUMMARY,
            "needsConfirmation": [ctx.retrieval.reason or "검색된 근거가 부족합니다."],
        }
        ctx.confidence.needs_confirmation = True
        return ctx

    # ④ Grounded Prompt (필터 통과 Context만) — 조립 직후 토큰 예산에 맞춘다
    ctx.system_prompt = build_system_prompt(project)
    ctx.prompt = _fit_pr_diff_to_budget(ctx.system_prompt, pr, ctx.kept, project)

    # ⑤ 구조화 출력 생성 + 파싱
    generate = generate_fn or _default_generate
    ctx.llm_raw = generate(ctx.system_prompt, ctx.prompt)
    ctx.llm_output = parse_llm_json(ctx.llm_raw)

    return ctx


# ==========================================
# 응답 매핑
# ==========================================

def _confirmation_items(ctx: PipelineContext) -> List[str]:
    raw = ctx.llm_output.get("needsConfirmation")
    if isinstance(raw, bool):
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
    signal_flag = bool(ctx.confidence.needs_confirmation) if ctx.confidence else True
    return bool(_confirmation_items(ctx)) or signal_flag or not ctx.grounding_sufficient


def _build_evidences(ctx: PipelineContext) -> List[Evidence]:
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


_NULLISH_STRINGS = {"null", "none", "nil", "n/a", "na", "-", ""}


def _nullable_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULLISH_STRINGS else text


def _nullable_int(value: Any) -> Optional[int]:
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
    parsed = _nullable_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _risk_score(output: Dict[str, Any]) -> int:
    raw = output.get("riskScore", output.get("risk_score"))
    parsed = _nullable_int(raw)
    if parsed is None:
        return 0
    return max(0, min(parsed, 100))


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _team_roles(project: Optional[ProjectInfo]) -> Sequence[str]:
    """
    응답에 허용할 역할 목록.

    프로젝트에 팀 역할이 등록돼 있으면 그 목록으로 제한한다. 프롬프트에 팀 역할을
    넣어도 LLM은 팀에 없는 역할(예: QA)을 만들어낼 수 있고, 전역 허용 7종으로만
    거르면 그대로 통과해 없는 담당자에게 작업이 배정된다(실측으로 확인된 문제).
    등록된 역할이 없으면 전역 허용 목록으로 폴백한다.
    """
    roles = getattr(project, "roles", None) or []
    return roles or ALLOWED_ROLES


def _allowed_roles(value: Any, allowed: Sequence[str] = ALLOWED_ROLES) -> List[str]:
    """
    affectedRoles를 허용 역할 목록으로 제한한다.
    프롬프트 규칙 7("근거 없는 역할을 만들지 않는다")을 코드에서도 강제해,
    LLM이 만들어낸 임의 역할(예: "개발자")이 응답에 새는 것을 막는다.
    표기 흔들림("Frontend", "프론트")은 정규화해서 받아들인다 — 같은 역할을
    다르게 적었다는 이유로 영향 항목이 사라지면 안 된다.
    """
    return [role for role in normalize_roles(_str_list(value)) if role in allowed]


def _follow_up_tasks(value: Any, allowed: Sequence[str] = ALLOWED_ROLES) -> List[Dict[str, Any]]:
    """
    followUpTasks 정규화 — `{role, task, evidenceRefs}` 형태.

    - role은 허용 역할만 인정하고, 그 외/빈 값은 None(담당 미정)으로 둔다.
      작업 자체는 버리지 않는다 — 담당을 특정 못 했다고 해야 할 일이 사라지는 건 아니다.
    - 구버전 출력(string[])도 받아들인다.
    """
    if not isinstance(value, list):
        return []

    tasks: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                tasks.append({"role": None, "task": text, "evidence_refs": []})
            continue
        if not isinstance(item, dict):
            continue

        text = str(item.get("task", "")).strip()
        if not text:
            continue
        role = normalize_role(item.get("role"))
        tasks.append(
            {
                "role": role if role in allowed else None,
                "task": text,
                "evidence_refs": _str_list(
                    item.get("evidenceRefs") or item.get("evidence_refs")
                ),
            }
        )
    return tasks


def _role_impacts(value: Any, allowed: Sequence[str] = ALLOWED_ROLES) -> List[Dict[str, Any]]:
    """roleImpacts 정규화 — 허용 역할만, basis는 허용 값만 남긴다."""
    if not isinstance(value, list):
        return []

    parsed = []
    for item in value:
        if not isinstance(item, dict) or not item.get("role"):
            continue
        role = normalize_role(item.get("role"))
        if role is None or role not in allowed:
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
    team_roles = _team_roles(project)

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
        affected_roles=_allowed_roles(out.get("affectedRoles"), team_roles),
        role_impacts=[RoleImpact(**ri) for ri in _role_impacts(out.get("roleImpacts"), team_roles)],
        follow_up_tasks=[
            FollowUpTask(**task) for task in _follow_up_tasks(out.get("followUpTasks"), team_roles)
        ],
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

    project = project or _lookup_project(request.repository_full_name, language=request.language)
    if project is None:
        project = ProjectInfo(name=request.repository_full_name, language=request.language)

    member_roles, unresolved = partition_roles(request.project_roles)
    if member_roles:
        project.roles = member_roles
    if unresolved:
        print(
            f"[Role] 해석하지 못한 project_role 값 무시 analysisId={request.analysis_id}: {unresolved}"
        )

    ctx = run_pipeline(pr=pr, repo_name=request.repository_full_name, project=project, **injected)
    out = ctx.llm_output

    team_roles = _team_roles(project)
    role_impacts = _role_impacts(out.get("roleImpacts"), team_roles)
    evidence = _build_evidence_refs(ctx)
    follow_up_tasks = _follow_up_tasks(out.get("followUpTasks"), team_roles)

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
        affected_roles=_allowed_roles(out.get("affectedRoles"), team_roles),
        role_impacts=[RoleImpactItem(**ri) for ri in role_impacts],
        follow_up_tasks=[FollowUpTaskItem(**task) for task in follow_up_tasks],
        needs_confirmation=_confirmation_items(ctx),
        evidence=evidence,
        confidence=ctx.confidence.score if ctx.confidence else 0.0,
        retrieval_quality_warning=bool(ctx.confidence.retrieval_quality_warning) if ctx.confidence else False,
    )