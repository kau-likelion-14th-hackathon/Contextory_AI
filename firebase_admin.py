"""firebase-admin 초기화.

자격 증명은 환경 변수(FIREBASE_CREDENTIALS, FIREBASE_PROJECT_ID)로만 읽습니다.
값을 코드나 로그에 쓰지 않습니다.
"""

from __future__ import annotations

from typing import Any

from core.config import get_settings

_app: Any = None
_firestore_client: Any = None


def init_firebase() -> Any:
    """앱 기동 시 한 번 호출합니다. 자격 증명이 없으면 초기화를 건너뜁니다."""
    global _app
    if _app is not None:
        return _app
    settings = get_settings()
    if not settings.firebase_credentials:
        return None
    raise NotImplementedError


def get_firestore_client() -> Any:
    raise NotImplementedError
