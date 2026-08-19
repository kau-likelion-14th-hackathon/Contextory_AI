"""
Celery 워커에서 실행되는 비동기 PR 분석 task.

기존에는 FastAPI BackgroundTasks(routers/internal_analysis.py의 _run_analysis_job)로
같은 프로세스 안에서 실행됐으나, Celery worker는 이미 별도 프로세스라 anyio 스레드풀
격리(CapacityLimiter)가 필요 없다 — 그건 FastAPI 이벤트 루프를 막지 않기 위한 장치였다.
"""

import traceback

import httpx

from core.config import settings
from models.schemas import AnalysisCallbackPayload, AsyncAnalysisRequest
from services.analysis_service import LLMResponseParseError, analyze_pr_for_callback
from services.callback_service import now_iso, send_analysis_callback
from services.job_store import update_job_status_sync
from services.retrieval import RetrievalError
from workers.celery_app import celery_app

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


@celery_app.task(name="run_analysis_job")
def run_analysis_job(request_data: dict, job_id: str) -> None:
    """실제 분석 작업. 완료/실패 여부와 무관하게 콜백을 전송한다.

    job_store 갱신과 콜백 전송은 서로 독립적으로 시도한다 — 한쪽이 실패해도(DB 순단,
    Backend 콜백 URL 연결 거부 등) 다른 쪽까지 막히면 안 된다.
    """
    request = AsyncAnalysisRequest.model_validate(request_data)

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
        update_job_status_sync(job_id, payload.status, payload.completed_at, payload.error_message)
    except Exception:
        print(f"[Job Status Update Failed] job_id={job_id} analysis_id={request.analysis_id}\n{traceback.format_exc()}")

    try:
        send_analysis_callback(request.callback_url, payload)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # 3회 재시도 후에도 연결 자체가 안 되는 경우. 이 celery_worker 프로세스가 서 있는
        # 네트워크에서 callback_url로 나가는 경로가 없다는 뜻이므로(예: 이 서버만 별도
        # 도메인/터널로 공개하고 Backend는 localhost/사설 IP에만 떠 있는 경우) DB/코드
        # 문제가 아니라 배포 구성 문제일 가능성이 높다. job_store에는 이미 최종 상태가
        # 기록되어 있어 GET으로 조회는 가능하다.
        print(
            f"[Analysis Callback Unreachable] job_id={job_id} analysis_id={request.analysis_id} "
            f"callback_url={request.callback_url}\n"
            f"콜백 URL에 연결할 수 없습니다 — 이 celery_worker 프로세스 기준으로 {request.callback_url} "
            f"로 나가는 네트워크 경로가 열려 있는지 확인하세요 (Backend가 이 서버와 다른 도달 범위에 "
            f"있다면 Backend도 공인 도메인/터널이 필요할 수 있습니다). "
            f"작업 상태 자체는 GET /internal/v1/analyses/{job_id} 로 조회 가능합니다.\n{traceback.format_exc()}"
        )
    except Exception:
        print(
            f"[Analysis Callback Failed] job_id={job_id} analysis_id={request.analysis_id} "
            f"callback_url={request.callback_url}\n{traceback.format_exc()}"
        )
