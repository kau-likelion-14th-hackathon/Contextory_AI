import traceback
from uuid import uuid4

import anyio
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from core.config import settings
from core.security import verify_internal_api_key
from models.schemas import (
    AsyncAnalysisRequest,
    AsyncAnalysisAcceptedResponse,
    AsyncAnalysisStatusResponse,
    AnalysisCallbackPayload,
    JobMaintenanceResponse,
)
from services.analysis_service import (
    LLMResponseParseError,
    analyze_pr_for_callback,
    build_diff_content,
)
from services.callback_service import send_analysis_callback, now_iso
from services.job_store import (
    create_job,
    get_job,
    purge_old_jobs,
    reap_stale_jobs,
    update_job_status,
)
from services.retrieval import RetrievalError

router = APIRouter(
    prefix="/internal/v1", tags=["Internal Analysis"], dependencies=[Depends(verify_internal_api_key)]
)

# 콜백 errorMessage에 SQL/임베딩 벡터 등 내부 구현 세부사항이 그대로 노출되지 않도록 길이를 제한한다.
# 전체 트레이스백은 서버 로그에 남긴다.
MAX_CALLBACK_ERROR_LENGTH = 300

# 실패 원인을 Backend가 코드로 구분할 수 있도록 errorMessage 앞에 붙이는 분류 태그.
# (문자열 필드이므로 기존 계약을 깨지 않는 추가 정보다)
FAILURE_CODE_RETRIEVAL = "RETRIEVAL_FAILED"
FAILURE_CODE_LLM_RESPONSE = "LLM_RESPONSE_INVALID"
FAILURE_CODE_UNEXPECTED = "ANALYSIS_FAILED"


def _classify_failure(error: Exception) -> str:
    """외부 실패를 원인별로 분류한다 (검색/DB · LLM 응답 · 그 외)."""
    if isinstance(error, RetrievalError):
        return FAILURE_CODE_RETRIEVAL
    if isinstance(error, LLMResponseParseError):
        return FAILURE_CODE_LLM_RESPONSE
    return FAILURE_CODE_UNEXPECTED

# LLM 분석(수 초~수십 초)과 콜백 전송(최대 3회 재시도, 최악 수십 초)은 job_store의 짧은 DB
# 조회/갱신과 anyio 기본 스레드풀(전역 40 슬롯)을 공유하면 느린 작업이 슬롯을 오래 점유해
# GET /internal/v1/analyses/{jobId} 같은 짧은 요청까지 지연시킬 수 있다. 이 두 블로킹 호출만
# 별도 CapacityLimiter로 격리해, job_store CRUD(services/job_store.py의 run_in_threadpool)는
# 기본 풀을 그대로 쓰면서 서로 영향을 주지 않게 한다.
_ANALYSIS_THREAD_LIMITER = anyio.CapacityLimiter(10)


async def _run_analysis_job(request: AsyncAnalysisRequest, job_id: str) -> None:
    """BackgroundTasks로 실행되는 실제 분석 작업. 완료/실패 여부와 무관하게 콜백을 전송한다.

    analyze_pr_for_callback/send_analysis_callback은 동기(블로킹) 함수이므로,
    job_store가 async화된 이후에도 이벤트 루프를 막지 않도록 스레드풀에서 실행한다.

    job_store 갱신과 콜백 전송은 서로 독립적으로 시도한다 — 한쪽이 실패해도(DB 순단,
    Backend 콜백 URL 연결 거부 등) 다른 쪽까지 막히면 안 된다. 특히 콜백 전송 실패를
    여기서 잡지 않으면 BackgroundTasks 컨텍스트에서 예외가 그대로 전파되어 ASGI 서버
    로그에 "Exception in ASGI application"으로 찍히는데, 이미 job_store에는 정상적으로
    최종 상태가 기록된 뒤이므로 서버 다운이 아니라 콜백 전송 실패일 뿐이다.
    """
    try:
        result = await anyio.to_thread.run_sync(
            analyze_pr_for_callback, request, limiter=_ANALYSIS_THREAD_LIMITER
        )
        payload = AnalysisCallbackPayload(
            job_id=job_id,
            status="COMPLETED",
            model_name=settings.LLM_MODEL,
            result=result,
            error_message=None,
            completed_at=now_iso(),
        )
    except Exception as e:
        failure_code = _classify_failure(e)
        print(
            f"[Analysis Job Failed] job_id={job_id} analysis_id={request.analysis_id} "
            f"code={failure_code}\n{traceback.format_exc()}"
        )
        detail = (str(e).splitlines() or [""])[0]
        error_summary = f"[{failure_code}] {detail}"[:MAX_CALLBACK_ERROR_LENGTH]
        payload = AnalysisCallbackPayload(
            job_id=job_id,
            status="FAILED",
            model_name=settings.LLM_MODEL,
            result=None,
            error_message=error_summary,
            completed_at=now_iso(),
        )

    try:
        await update_job_status(job_id, payload.status, payload.completed_at, payload.error_message)
    except Exception:
        print(f"[Job Status Update Failed] job_id={job_id} analysis_id={request.analysis_id}\n{traceback.format_exc()}")

    try:
        await anyio.to_thread.run_sync(
            send_analysis_callback, request.callback_url, payload, limiter=_ANALYSIS_THREAD_LIMITER
        )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # 3회 재시도 후에도 연결 자체가 안 되는 경우. 이 FastAPI 프로세스가 서 있는 네트워크에서
        # callback_url로 나가는 경로가 없다는 뜻이므로(예: 이 서버만 별도 도메인/터널로 공개하고
        # Backend는 localhost/사설 IP에만 떠 있는 경우) DB/코드 문제가 아니라 배포 구성 문제일
        # 가능성이 높다. job_store에는 이미 최종 상태가 기록되어 있어 GET으로 조회는 가능하다.
        print(
            f"[Analysis Callback Unreachable] job_id={job_id} analysis_id={request.analysis_id} "
            f"callback_url={request.callback_url}\n"
            f"콜백 URL에 연결할 수 없습니다 — 이 FastAPI 서버 프로세스 기준으로 {request.callback_url} "
            f"로 나가는 네트워크 경로가 열려 있는지 확인하세요 (Backend가 이 FastAPI와 다른 도달 범위에 "
            f"있다면 Backend도 공인 도메인/터널이 필요할 수 있습니다). "
            f"작업 상태 자체는 GET /internal/v1/analyses/{job_id} 로 조회 가능합니다.\n{traceback.format_exc()}"
        )
    except Exception:
        print(
            f"[Analysis Callback Failed] job_id={job_id} analysis_id={request.analysis_id} "
            f"callback_url={request.callback_url}\n{traceback.format_exc()}"
        )


@router.post(
    "/analyses",
    response_model=AsyncAnalysisAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_pr_analysis(
    request: AsyncAnalysisRequest,
    background_tasks: BackgroundTasks,
):
    """
    Spring Boot가 PR 데이터와 분석 ID를 전달하면 즉시 202 + jobId를 반환하고,
    실제 분석은 BackgroundTasks로 비동기 실행한 뒤 완료/실패 결과를 callbackUrl로 콜백한다.
    """
    diff_content = build_diff_content(request.pull_request.files)
    if not diff_content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 변경 파일(diff)이 비어 있습니다.")

    job_id = f"rag-job-{uuid4().hex[:12]}"
    await create_job(job_id, request.analysis_id, now_iso())
    background_tasks.add_task(_run_analysis_job, request, job_id)

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
