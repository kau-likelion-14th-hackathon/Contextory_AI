import hmac

from fastapi import Header, HTTPException, status

from core.config import settings


def verify_internal_api_key(x_internal_api_key: str = Header(None, alias="X-Internal-Api-Key")) -> None:
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
