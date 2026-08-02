from __future__ import annotations

from core.constants import EXCLUDED_PATH_PATTERNS, MAX_DIFF_CHARS


def is_excluded_path(file_path: str) -> bool:
    """락 파일, 빌드 산출물 등 분석에서 제외할 경로인지 판정합니다."""
    raise NotImplementedError


def truncate_diff(diff: str, limit: int = MAX_DIFF_CHARS) -> tuple[str, bool]:
    """diff 를 길이 제한에 맞춰 자르고 (잘린 diff, 잘림 여부) 를 돌려줍니다."""
    raise NotImplementedError


def redact_secrets(text: str) -> str:
    """토큰, API 키, 이메일 등 민감 정보를 마스킹합니다."""
    raise NotImplementedError
