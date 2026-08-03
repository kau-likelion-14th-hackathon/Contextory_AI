from fastapi import APIRouter, status, HTTPException
from models.schemas import PRAnalysisRequest, PRAnalysisResponse
from llamaindex.pipeline import run_pr_rag_analysis

router = APIRouter(prefix="/api/v1/analyze", tags=["PR Analysis"])


@router.post("/pr", response_model=PRAnalysisResponse, status_code=status.HTTP_200_OK)
async def analyze_pr(request: PRAnalysisRequest):
    """
    Spring Boot로부터 PR 정보를 전달받아 LlamaIndex + pgvector RAG 파이프라인을 통해 AI 분석을 수행합니다.
    """
    try:
        response = await run_pr_rag_analysis(
            pr_id=request.pr_id,
            repo_name=request.repo_name,
            title=request.title,
            description=request.description,
            diff_content=request.diff_content,
            author=request.author
        )
        return response
    except Exception as e:
        # LLM 또는 DB 연동 에러 발생 시 처리
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PR 분석 처리 중 오류 발생: {str(e)}"
        )