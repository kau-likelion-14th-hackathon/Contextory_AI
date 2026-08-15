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
from services.translation_service import translate_pr_to_en_query  # ✅ 공통 모듈로 import

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def build_diff_content(files: list[PullRequestFile]) -> str:
    """PR 변경 파일 목록을 단일 Diff 문자열로 결합"""
    blocks = [
        f"### {f.file_path} ({f.change_type})\n{f.patch}"
        for f in files if f.patch
    ]
    return "\n\n".join(blocks)


def _gather_grounded_context(title: str, description: str, diff_content: str, repo_name: str):
    """
    Query Translation + Multi-Source Retrieval(code_review_vectors + repo_code_vectors)을 수행하고
    Grounded Prompt를 구성한다. 동기(analyze_pr_pipeline)/비동기(analyze_pr_for_callback) 분석
    파이프라인이 공통으로 사용하는 조회 단계.
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

    prompt = build_grounded_prompt(title, diff_content, filtered_contexts, repo_contexts)
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
