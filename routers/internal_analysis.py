import traceback
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status

from core.config import settings
from models.schemas import (
    AsyncAnalysisRequest,
    AsyncAnalysisAcceptedResponse,
    AsyncAnalysisStatusResponse,
    AnalysisCallbackPayload,
)
from services.analysis_service import analyze_pr_for_callback, build_diff_content
from services.callback_service import send_analysis_callback, now_iso
from services.job_store import create_job, update_job_status, get_job

router = APIRouter(prefix="/internal/v1", tags=["Internal Analysis"])

# 콜백 errorMessage에 SQL/임베딩 벡터 등 내부 구현 세부사항이 그대로 노출되지 않도록 길이를 제한한다.
# 전체 트레이스백은 서버 로그에 남긴다.
MAX_CALLBACK_ERROR_LENGTH = 300


def _check_internal_api_key(x_internal_api_key: str) -> None:
    if not settings.INTERNAL_API_KEY or x_internal_api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="내부 API 인증 실패")


def _run_analysis_job(request: AsyncAnalysisRequest, job_id: str) -> None:
    """BackgroundTasks로 실행되는 실제 분석 작업. 완료/실패 여부와 무관하게 콜백을 전송한다."""
    try:
        result = analyze_pr_for_callback(request)
        payload = AnalysisCallbackPayload(
            job_id=job_id,
            status="COMPLETED",
            model_name=settings.LLM_MODEL,
            result=result,
            error_message=None,
            completed_at=now_iso(),
        )
    except Exception as e:
        print(f"[Analysis Job Failed] job_id={job_id} analysis_id={request.analysis_id}\n{traceback.format_exc()}")
        error_summary = str(e).splitlines()[0][:MAX_CALLBACK_ERROR_LENGTH]
        payload = AnalysisCallbackPayload(
            job_id=job_id,
            status="FAILED",
            model_name=settings.LLM_MODEL,
            result=None,
            error_message=error_summary,
            completed_at=now_iso(),
        )

    update_job_status(job_id, payload.status, payload.completed_at)
    send_analysis_callback(request.callback_url, payload)


@router.post(
    "/analyses",
    response_model=AsyncAnalysisAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_pr_analysis(
    request: AsyncAnalysisRequest,
    background_tasks: BackgroundTasks,
    x_internal_api_key: str = Header(None, alias="X-Internal-Api-Key"),
):
    """
    Spring Boot가 PR 데이터와 분석 ID를 전달하면 즉시 202 + jobId를 반환하고,
    실제 분석은 BackgroundTasks로 비동기 실행한 뒤 완료/실패 결과를 callbackUrl로 콜백한다.
    """
    _check_internal_api_key(x_internal_api_key)

    diff_content = build_diff_content(request.pull_request.files)
    if not diff_content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 변경 파일(diff)이 비어 있습니다.")

    job_id = f"rag-job-{uuid4().hex[:12]}"
    create_job(job_id, request.analysis_id, now_iso())
    background_tasks.add_task(_run_analysis_job, request, job_id)

    return AsyncAnalysisAcceptedResponse(job_id=job_id, status="PROCESSING")


@router.get(
    "/analyses/{job_id}",
    response_model=AsyncAnalysisStatusResponse,
)
async def get_analysis_job_status(
    job_id: str,
    x_internal_api_key: str = Header(None, alias="X-Internal-Api-Key"),
):
    """
    jobId로 비동기 분석 작업의 현재 상태를 조회한다.
    (단일 프로세스 In-Memory 저장소 기반 — 재시작/멀티 워커 환경에서는 유실될 수 있음)
    """
    _check_internal_api_key(x_internal_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="해당 jobId의 분석 작업을 찾을 수 없습니다.")

    return AsyncAnalysisStatusResponse(
        job_id=job_id,
        analysis_id=job["analysis_id"],
        status=job["status"],
        started_at=job["started_at"],
        completed_at=job["completed_at"],
    )
