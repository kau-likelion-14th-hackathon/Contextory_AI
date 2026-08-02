"""도메인 enum.

저장 값(enum value)은 Spring Boot 백엔드가 DB 에 기록하는 값과 일치해야 합니다.
현재 백엔드 스키마를 확인하지 못해 아래 값은 기획서 기준이며, 실제 값이 다르면
백엔드 값을 우선해 교체해야 합니다(확인 필요).
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    AI = "ai"
    PLANNING = "planning"
    DESIGN = "design"
    QA = "qa"
    PROJECT_MANAGER = "project_manager"


class PrAnalysisStatus(str, Enum):
    """PR 분석 상태. 전이는 services 계층에서 검증합니다."""

    PENDING = "pending"           # 분석 전
    ANALYZING = "analyzing"       # 분석 중
    NEEDS_REVIEW = "needs_review"  # 검토 필요
    APPROVED = "approved"         # 승인 완료
    FAILED = "failed"             # 분석 실패


class RecordType(str, Enum):
    CHANGE = "change"                      # 변경
    IMPLEMENTATION = "implementation"      # 구현
    DECISION = "decision"                  # 결정
    PROBLEM_SOLUTION = "problem_solution"  # 문제 및 해결


class ApprovalStatus(str, Enum):
    DRAFT = "draft"          # 검토 필요
    APPROVED = "approved"    # 승인 완료
    DISCARDED = "discarded"  # 폐기


class FollowUpStatus(str, Enum):
    NEEDS_ACTION = "needs_action"  # 확인 필요
    DONE = "done"                  # 완료


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GithubConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


# --- 상태 전이 규칙 -------------------------------------------------------
# services 계층이 전이 검증에 사용합니다. 여기 없는 전이는 허용하지 않습니다.

PR_ANALYSIS_TRANSITIONS: dict[PrAnalysisStatus, frozenset[PrAnalysisStatus]] = {
    PrAnalysisStatus.PENDING: frozenset({PrAnalysisStatus.ANALYZING}),
    PrAnalysisStatus.ANALYZING: frozenset(
        {PrAnalysisStatus.NEEDS_REVIEW, PrAnalysisStatus.FAILED}
    ),
    PrAnalysisStatus.NEEDS_REVIEW: frozenset(
        {PrAnalysisStatus.APPROVED, PrAnalysisStatus.ANALYZING}
    ),
    PrAnalysisStatus.FAILED: frozenset({PrAnalysisStatus.ANALYZING}),
    PrAnalysisStatus.APPROVED: frozenset({PrAnalysisStatus.ANALYZING}),
}

APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.DRAFT: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.DISCARDED}),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.DISCARDED: frozenset(),
}

FOLLOW_UP_TRANSITIONS: dict[FollowUpStatus, frozenset[FollowUpStatus]] = {
    FollowUpStatus.NEEDS_ACTION: frozenset({FollowUpStatus.DONE}),
    FollowUpStatus.DONE: frozenset(),
}
