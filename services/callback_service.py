import time
from datetime import datetime, timezone

import httpx

from core.config import settings
from models.schemas import AnalysisCallbackPayload

CALLBACK_MAX_ATTEMPTS = 3
CALLBACK_TIMEOUT_SECONDS = 10.0


def now_iso() -> str:
    """ISO 8601 UTC 타임스탬프 (콜백 completedAt 필드용)"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def send_analysis_callback(callback_url: str, payload: AnalysisCallbackPayload) -> None:
    """
    분석 완료/실패 결과를 Spring Boot 콜백 URL로 전송한다.
    네트워크 순간 장애에 대비해 짧은 지수 백오프로 재시도하되, 모두 실패하면 예외를 그대로 전파해
    (BackgroundTasks 컨텍스트라 응답을 통해 알릴 수 없으므로) 서버 로그에 남긴다.
    """
    headers = {"X-Internal-Api-Key": settings.INTERNAL_API_KEY}
    body = payload.model_dump(by_alias=True)

    last_error: Exception = RuntimeError("callback not attempted")
    for attempt in range(1, CALLBACK_MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(callback_url, json=body, headers=headers, timeout=CALLBACK_TIMEOUT_SECONDS)
            response.raise_for_status()
            return
        except Exception as e:
            last_error = e
            if attempt < CALLBACK_MAX_ATTEMPTS:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s ...

    raise last_error
