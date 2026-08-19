from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from core.security import verify_internal_api_key
from models.schemas import (
    AsyncAnalysisRequest,
    AsyncAnalysisAcceptedResponse,
    AsyncAnalysisStatusResponse,
    JobMaintenanceResponse,
)
from core.config import settings
from services.analysis_service import build_diff_content
from services.callback_service import now_iso
from services.job_store import (
    create_job,
    get_job,
    purge_old_jobs,
    reap_stale_jobs,
)
from workers.tasks import run_analysis_job

router = APIRouter(
    prefix="/internal/v1", tags=["Internal Analysis"], dependencies=[Depends(verify_internal_api_key)]
)


@router.post(
    "/analyses",
    response_model=AsyncAnalysisAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_pr_analysis(request: AsyncAnalysisRequest):
    """
    Spring Boot가 PR 데이터와 분석 ID를 전달하면 즉시 202 + jobId를 반환하고,
    실제 분석은 Celery task(workers.tasks.run_analysis_job)로 비동기 실행한 뒤
    완료/실패 결과를 callbackUrl로 콜백한다.
    """
    diff_content = build_diff_content(request.pull_request.files)
    if not diff_content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 변경 파일(diff)이 비어 있습니다.")

    job_id = f"rag-job-{uuid4().hex[:12]}"
    await create_job(job_id, request.analysis_id, now_iso())
    run_analysis_job.delay(request.model_dump(), job_id)

    return AsyncAnalysisAcceptedResponse(job_id=job_id, status="PROCESSING")


@router.get(
    "/analyses/{job_id}",
    response_model=AsyncAnalysisStatusResponse,
)
async def get_analysis_job_status(job_id: str):
    """
    jobId로 비동기 분석 작업의 현재 상태를 조회한다.
    (PostgreSQL `ai_analysis_jobs` 영속 저장소 기반 — 재시작/멀티 워커 환경에서도 동일하게 조회된다)
    """
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="해당 jobId의 분석 작업을 찾을 수 없습니다.")

    return AsyncAnalysisStatusResponse(
        job_id=job_id,
        analysis_id=job["analysis_id"],
        status=job["status"],
        started_at=job["started_at"],
        completed_at=job["completed_at"],
    )


@router.post(
    "/analyses/maintenance",
    response_model=JobMaintenanceResponse,
)
async def run_job_maintenance():
    """
    좀비 작업 정리 및 보존 기간 초과 작업 삭제를 수행한다. Spring Boot 스케줄러가 주기적으로 호출한다.

    - 좀비 정리: JOB_TIMEOUT_MINUTES를 넘긴 PROCESSING 작업을 FAILED로 확정한다.
      (서버가 분석 도중 강제 종료되면 해당 행이 영원히 PROCESSING으로 남기 때문)
    - 보존 정리: JOB_RETENTION_DAYS가 설정된 경우에만 오래된 종료 작업 행을 삭제한다(기본 비활성).

    멱등하므로 여러 워커/스케줄러가 동시에 호출해도 안전하다.
    """
    reaped_count = await reap_stale_jobs()
    purged_count = await purge_old_jobs()

    return JobMaintenanceResponse(
        reaped_count=reaped_count,
        purged_count=purged_count,
        timeout_minutes=settings.JOB_TIMEOUT_MINUTES,
    )
