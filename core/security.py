import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from core.config import settings

# fastapi.security.APIKeyHeader로 선언해야 OpenAPI에 securitySchemes로 등록되어
# Swagger UI(/docs) 상단에 "Authorize" 버튼이 뜬다. 단순 Header(...) 파라미터로는
# 요청은 검증되지만 스키마상 보안 요구사항으로 노출되지 않아 Authorize 버튼이 생기지 않는다.
# auto_error=False로 두고 아래에서 직접 401을 던져, 헤더 누락 시에도 우리가 원하는
# 메시지("내부 API 인증 실패")를 그대로 유지한다.
_api_key_header = APIKeyHeader(
    name="X-Internal-Api-Key",
    description="내부 통신용 Internal API Key (Backend <-> AI)",
    auto_error=False,
)


def verify_internal_api_key(x_internal_api_key: str = Security(_api_key_header)) -> None:
    """Backend(Spring Boot) <-> AI 내부 API 공통 인증.

    이 서버가 Cloudflare 등으로 공인 도메인에 노출되면 /api/v1, /internal/v1 하위
    엔드포인트는 인터넷 누구나 호출할 수 있는 상태가 된다(OpenAI 비용 발생, 벡터 DB
    오염 위험). Spring Boot의 FastApiClient가 모든 호출에 이 헤더를 실어 보내므로
    (Backend 측 CLAUDE.md: "every call sends X-Internal-Api-Key"), Backend와 통신하는
    모든 라우터에 이 의존성을 걸어야 한다.

    hmac.compare_digest로 비교해 타이밍 사이드채널을 통한 키 추측 가능성을 줄인다.
    """
    if (
        not settings.INTERNAL_API_KEY
        or not x_internal_api_key
        or not hmac.compare_digest(x_internal_api_key, settings.INTERNAL_API_KEY)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="내부 API 인증 실패")
