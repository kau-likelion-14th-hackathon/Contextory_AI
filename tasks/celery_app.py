"""Celery 앱. 브로커 주소는 환경 변수에서만 읽습니다."""

from __future__ import annotations

from core.config import get_settings


def create_celery_app() -> object:
    """celery.Celery 인스턴스를 만들어 돌려줍니다."""
    settings = get_settings()
    if not settings.celery_broker_url:
        raise RuntimeError("CELERY_BROKER_URL 이 설정되어 있지 않습니다.")
    raise NotImplementedError
