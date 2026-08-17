from fastapi import APIRouter, Depends, HTTPException, status

from core.security import verify_internal_api_key
from models.schemas import PRAnalysisRequest, PRAnalysisResponse
from services.analysis_service import LLMResponseParseError, analyze_pr_pipeline
from services.retrieval import RetrievalError

router = APIRouter(prefix="/api/v1", tags=["Analysis"], dependencies=[Depends(verify_internal_api_key)])


@router.post("/analyze/pr", response_model=PRAnalysisResponse)
async def analyze_pr(request: PRAnalysisRequest):
    """
    PR Diff를 RAG 파이프라인(검색 → 필터 → Grounded Prompt → 구조화 생성 → Confidence)으로 분석한다.

    외부 실패는 원인별로 구분해 응답한다. (근거 부족은 실패가 아니라
    grounding_sufficient=false / needs_confirmation=true 응답으로 정상 반환된다)
    """
    try:
        return analyze_pr_pipeline(request)
    except RetrievalError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vector DB 검색 실패: {e}",
        )
    except LLMResponseParseError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 응답 파싱 실패: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG Pipeline Error: {e}",
        )
