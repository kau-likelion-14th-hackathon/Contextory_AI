from fastapi import APIRouter, HTTPException
from models.schemas import PRAnalysisRequest, PRAnalysisResponse
from services.analysis_service import analyze_pr_pipeline

router = APIRouter(prefix="/api/v1", tags=["Analysis"])

@router.post("/analyze/pr", response_model=PRAnalysisResponse)
async def analyze_pr(request: PRAnalysisRequest):
    try:
        response = analyze_pr_pipeline(request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG Pipeline Error: {str(e)}")