import json
from openai import OpenAI
from core.config import settings
from models.schemas import (
    PRAnalysisRequest, PRAnalysisResponse, Evidence, CodeReviewComment,
    AsyncAnalysisRequest, AnalysisResultPayload, AnalysisChangeItem, PullRequestFile,
)
from services.retrieval import retrieve_contexts, retrieve_repo_contexts
from services.context_filter import filter_contexts
from services.prompt_builder import build_grounded_prompt
from services.confidence import calculate_confidence
from services.token_utils import count_tokens, get_encoding
from services.translation_service import translate_pr_to_en_query  # ✅ 공통 모듈로 import

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# 내용 자체보다 존재 유무만 중요한 파일들 — 있어도 리뷰 신호가 거의 없는데 patch만 크게 잡아먹는
# 경우가 많아(자동 생성/vendored/lockfile) 프롬프트에서 통째로 제외한다.
_LOW_VALUE_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "Gemfile.lock", "composer.lock", "Cargo.lock", "go.sum",
}
_LOW_VALUE_SUFFIXES = (".min.js", ".min.css", ".map")
_LOW_VALUE_PATH_MARKERS = ("/vendor/", "/node_modules/", "/dist/", "/build/", "/generated/")


class PromptTooLargeError(Exception):
    """트리밍을 거쳐도 프롬프트가 LLM_MAX_PROMPT_TOKENS 예산을 넘는 경우.

    str(e)가 그대로 콜백 error_message(routers/internal_analysis.py의
    _run_analysis_job)로 노출되므로, 원인이 분명한 메시지를 유지한다.
    """

    def __init__(self, token_count: int, limit: int):
        self.token_count = token_count
        self.limit = limit
        super().__init__(f"PR diff too large to analyze ({token_count} tokens, limit {limit})")


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


def build_diff_content(files: list[PullRequestFile]) -> str:
    """PR 변경 파일 목록을 단일 Diff 문자열로 결합.

    LLM 요청이 OpenAI TPM 한도를 넘겨 매번 동일하게 실패하는 것(request-shape bug)을
    막기 위해, 개별 파일 단위에서부터 크기를 억제한다:
    - lockfile/생성/vendored/rename-only 파일은 리뷰 신호가 거의 없으므로 통째로 제외
    - 남은 각 파일의 patch는 MAX_PATCH_CHARS_PER_FILE로 상한을 둔다
      (prompt_builder.py가 context 스니펫을 300/500자로 자르는 것과 같은 취지)
    그래도 전체 프롬프트가 예산을 넘을 수 있으므로, 최종 안전판은
    _gather_grounded_context()의 토큰 예산 트리밍/실패 처리다.
    """
    blocks = []
    omitted_count = 0

    for f in files:
        if not f.patch or not f.patch.strip():
            continue
        if _is_low_value_file(f):
            omitted_count += 1
            continue

        patch = f.patch
        if len(patch) > settings.MAX_PATCH_CHARS_PER_FILE:
            patch = patch[: settings.MAX_PATCH_CHARS_PER_FILE] + "\n... (patch truncated)"

        blocks.append(f"### {f.file_path} ({f.change_type})\n{patch}")

    diff_content = "\n\n".join(blocks)
    if omitted_count:
        diff_content += (
            f"\n\n... (truncated, {omitted_count} files omitted: "
            "lockfile/generated/vendored/rename-only changes)"
        )
    return diff_content


def _fit_prompt_to_budget(
    title: str,
    diff_content: str,
    filtered_contexts: list,
    repo_contexts: list,
) -> str:
    """build_grounded_prompt()로 구성한 프롬프트가 LLM_MAX_PROMPT_TOKENS를 넘으면
    PR diff 부분만 토큰 단위로 잘라 다시 맞춘다. 그래도 안 맞으면 PromptTooLargeError.

    diff 이외 부분(제목/컨텍스트/고정 지시문)은 이미 build_diff_content()와
    context_filter.py의 유사도 필터로 통제되고 있어 diff가 압도적으로 큰 항목이므로,
    diff만 잘라도 대부분의 경우 예산 안에 들어온다.
    """
    budget = settings.LLM_MAX_PROMPT_TOKENS
    prompt = build_grounded_prompt(title, diff_content, filtered_contexts, repo_contexts)
    token_count = count_tokens(prompt, settings.LLM_MODEL)
    if token_count <= budget:
        return prompt

    truncation_marker = "\n\n... (diff truncated to fit token budget)"
    overhead_prompt = build_grounded_prompt(title, "", filtered_contexts, repo_contexts)
    overhead_tokens = count_tokens(overhead_prompt, settings.LLM_MODEL)
    marker_tokens = count_tokens(truncation_marker, settings.LLM_MODEL)
    diff_budget = budget - overhead_tokens - marker_tokens
    if diff_budget <= 0:
        raise PromptTooLargeError(token_count, budget)

    encoding = get_encoding(settings.LLM_MODEL)
    truncated_diff = encoding.decode(encoding.encode(diff_content)[:diff_budget])
    truncated_diff += truncation_marker

    prompt = build_grounded_prompt(title, truncated_diff, filtered_contexts, repo_contexts)
    token_count = count_tokens(prompt, settings.LLM_MODEL)
    if token_count > budget:
        raise PromptTooLargeError(token_count, budget)
    return prompt


def _gather_grounded_context(title: str, description: str, diff_content: str, repo_name: str):
    """
    Query Translation + Multi-Source Retrieval(code_review_vectors + repo_code_vectors)을 수행하고
    Grounded Prompt를 구성한다. 동기(analyze_pr_pipeline)/비동기(analyze_pr_for_callback) 분석
    파이프라인이 공통으로 사용하는 조회 단계.

    프롬프트가 LLM_MAX_PROMPT_TOKENS를 넘으면 diff를 잘라 재시도하고, 그래도 넘으면
    PromptTooLargeError를 던져 OpenAI 호출 자체를 막는다(원 인시던트: 100K+ 토큰 요청이
    30K TPM 조직 한도에 걸려 매번 동일하게 429로 실패).
    """
    translated_query = translate_pr_to_en_query(title, description or "")
    query_text = f"PR Title/Summary: {translated_query}\nPR Diff:\n{diff_content}"

    raw_contexts = retrieve_contexts(query_text=query_text, top_k=settings.RAG_TOP_K)
    repo_contexts = retrieve_repo_contexts(
        query_text=query_text,
        repo_name=repo_name,
        top_k=settings.RAG_TOP_K,
    )

    filtered_contexts, filter_ratio = filter_contexts(
        raw_contexts,
        sim_threshold=settings.SIM_THRESHOLD
    )

    prompt = _fit_prompt_to_budget(title, diff_content, filtered_contexts, repo_contexts)
    return prompt, filtered_contexts, filter_ratio


def analyze_pr_pipeline(request: PRAnalysisRequest) -> PRAnalysisResponse:
    """
    전체 RAG Orchestration Pipeline (동기, POST /api/v1/analyze/pr 전용 — 기존 응답 계약 유지)
    """
    description_text = getattr(request, "description", "")
    prompt, filtered_contexts, filter_ratio = _gather_grounded_context(
        request.title, description_text, request.diff_content, request.repo_name
    )

    # 4. GPT-4o Structured Response 호출 (JSON Mode)
    system_instruction = """You are an expert Code Review AI.
Analyze the PR and output ONLY a valid JSON with the following structure:
{
  "summary": "Brief summary of the PR and code review",
  "risk_score": 0 to 100 integer,
  "reviews": [
    {
      "file_path": "path/to/file or null",
      "line_number": integer or null,
      "comment": "Specific review comment"
    }
  ]
}"""

    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.2
    )

    llm_output = json.loads(response.choices[0].message.content)

    # 5. Confidence Calculation
    confidence, needs_conf = calculate_confidence(filtered_contexts, filter_ratio)

    # 6. Evidences & Response DTO Mapping
    evidences = [
        Evidence(
            id=str(c["id"]),
            source_code=c.get("source_code"),
            pr_diff=c.get("pr_diff"),
            review_comment=c.get("review_comment"),
            similarity_score=c["similarity_score"]
        )
        for c in filtered_contexts
    ]

    reviews = [
        CodeReviewComment(
            file_path=r.get("file_path"),
            line_number=r.get("line_number"),
            comment=r.get("comment", "")
        )
        for r in llm_output.get("reviews", [])
    ]

    return PRAnalysisResponse(
        pr_id=request.pr_id,
        summary=llm_output.get("summary", "Analysis complete."),
        risk_score=llm_output.get("risk_score", 0),
        reviews=reviews,
        evidences=evidences,
        confidence=confidence,
        needs_confirmation=needs_conf,
        filter_ratio=filter_ratio
    )


def analyze_pr_for_callback(request: AsyncAnalysisRequest) -> AnalysisResultPayload:
    """
    비동기 내부 분석 파이프라인 (POST /internal/v1/analyses 전용).
    Backend 콜백 계약의 result 스키마(summary/changes/impacts/risks/recommendations)에 맞춰 응답한다.
    """
    diff_content = build_diff_content(request.pull_request.files)

    prompt, _filtered_contexts, _filter_ratio = _gather_grounded_context(
        request.pull_request.title,
        request.pull_request.body or "",
        diff_content,
        request.repository_full_name,
    )

    language_instruction = "Respond in Korean." if request.language != "en" else "Respond in English."
    system_instruction = f"""You are an expert Code Review AI.
Analyze the PR and output ONLY a valid JSON with the following structure:
{{
  "summary": "Brief summary of the PR's overall change and impact",
  "changes": [
    {{"filePath": "path/to/file", "description": "What changed in this file"}}
  ],
  "impacts": ["Notable ripple effects on the existing system"],
  "risks": ["Structural or security risks to double-check"],
  "recommendations": ["Concrete suggestions for the reviewer/author"]
}}
{language_instruction}"""

    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.2
    )

    llm_output = json.loads(response.choices[0].message.content)

    return AnalysisResultPayload(
        summary=llm_output.get("summary", ""),
        changes=[
            AnalysisChangeItem(
                file_path=c.get("filePath", "unknown"),
                description=c.get("description", "")
            )
            for c in llm_output.get("changes", [])
        ],
        impacts=llm_output.get("impacts", []),
        risks=llm_output.get("risks", []),
        recommendations=llm_output.get("recommendations", []),
    )
