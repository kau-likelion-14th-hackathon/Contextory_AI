import json
from openai import OpenAI
from core.config import settings
from models.schemas import PRAnalysisRequest, PRAnalysisResponse, Evidence, CodeReviewComment
from services.retrieval import retrieve_contexts
from services.context_filter import filter_contexts
from services.prompt_builder import build_grounded_prompt
from services.confidence import calculate_confidence
from services.translation_service import translate_pr_to_en_query  # ✅ 공통 모듈로 import

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def analyze_pr_pipeline(request: PRAnalysisRequest) -> PRAnalysisResponse:
    """
    전체 RAG Orchestration Pipeline (Query Translation 공통 모듈 적용)
    """
    # 1. Query Translation (한/영 PR Title & Description -> 영문 검색 쿼리 변환)
    description_text = getattr(request, "description", "")
    translated_query = translate_pr_to_en_query(request.title, description_text)

    # 1-1. PGVector Retrieval (영문 변환 쿼리 + request.diff_content 조합 적용)
    query_text = f"PR Title/Summary: {translated_query}\nPR Diff:\n{request.diff_content}"
    raw_contexts = retrieve_contexts(query_text=query_text, top_k=settings.RAG_TOP_K)
    
    # 2. Context Filter Agent (무관 Chunk 제거 & Top-1 보존)
    filtered_contexts, filter_ratio = filter_contexts(
        raw_contexts, 
        sim_threshold=settings.SIM_THRESHOLD
    )
    
    # 3. Grounded Prompt 생성
    prompt = build_grounded_prompt(request.title, request.diff_content, filtered_contexts)
    
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