"""도메인 예외와 HTTP 변환.

백엔드(Spring Boot)의 응답 포맷을 따라야 하면 to_response() 를 그에 맞게 고칩니다(확인 필요).
"""

from __future__ import annotations

from typing import Any


class ContextoryError(Exception):
    """모든 도메인 예외의 최상위."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_response(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class NotFoundError(ContextoryError):
    status_code = 404
    code = "NOT_FOUND"


class PermissionDeniedError(ContextoryError):
    """다른 프로젝트/PR/기록 접근 차단, 관리자 전용 기능 접근 차단에 사용합니다."""

    status_code = 403
    code = "PERMISSION_DENIED"


class ValidationError(ContextoryError):
    status_code = 422
    code = "VALIDATION_ERROR"


class InvalidStateTransitionError(ContextoryError):
    """허용되지 않은 상태 전이. models/enums.py 의 전이 규칙을 위반한 경우."""

    status_code = 409
    code = "INVALID_STATE_TRANSITION"


class LlmResponseError(ContextoryError):
    """LLM 응답을 출력 스키마로 파싱하지 못한 경우."""

    status_code = 502
    code = "LLM_RESPONSE_ERROR"


class ExternalServiceError(ContextoryError):
    """GitHub, Firebase 등 외부 서비스 호출 실패."""

    status_code = 502
    code = "EXTERNAL_SERVICE_ERROR"
