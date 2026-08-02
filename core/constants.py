"""도메인 상수."""

from __future__ import annotations

# LLM 분석 입력에 넣는 diff 최대 길이. 초과하면 잘라내고 diff_truncated 로 표시합니다.
MAX_DIFF_CHARS = 12000

# 분석 대상에서 제외할 경로 패턴(락 파일, 빌드 산출물 등).
EXCLUDED_PATH_PATTERNS = (
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
    "dist/",
    "build/",
)

# RAG 검색 기본값
DEFAULT_TOP_K = 5
DEFAULT_CHUNK_SIZE = 1024
DEFAULT_CHUNK_OVERLAP = 100

# Firestore 컬렉션 이름
FIRESTORE_DOCUMENTS = "documents"
FIRESTORE_CHATS = "chats"
FIRESTORE_MESSAGES = "messages"
FIRESTORE_SEARCH_LOGS = "search_logs"
