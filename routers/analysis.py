"""PR 분석 API.

Spring Boot 또는 클라이언트로부터 PR 정보를 수신받아
LlamaIndex + pgvector RAG 파이프라인을 통해 AI 분석을 수행합니다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from llamaindex.pipeline import run_pr_rag_analysis
from models.schemas import PRAnalysisRequest, PRAnalysisResponse

router = APIRouter(prefix="/api/v1/analyze", tags=["PR Analysis"])


@router.post("/pr", response_model=PRAnalysisResponse, status_code=status.HTTP_200_OK)
async def analyze_pr(
    request: PRAnalysisRequest,
) -> PRAnalysisResponse:
    """PR diff 및 연관 소스코드를 LlamaIndex + pgvector RAG 파이프라인으로 전달하여

    변경 이유, 역할별 영향도, 코드 리뷰 피드백을 분석합니다.
    """
    try:
        # 필드 호환성 처리 (pr_id/pr_number, diff/diff_content, description/body)
        pr_id = request.pr_id if request.pr_id is not None else request.pr_number
        diff_content = request.diff_content or request.diff
        description = request.description or request.body
        repo_name = request.repo_name or f"project_{request.project_id}"

        response = await run_pr_rag_analysis(
            pr_id=pr_id,
            repo_name=repo_name,
            title=request.title,
            description=description,
            diff_content=diff_content,
            author=request.author or "unknown",
        )
        return response
    except Exception as e:
        # LLM 또는 DB 연동 에러 발생 시 500 에러 반환
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PR 분석 처리 중 오류가 발생했습니다: {str(e)}",
        )