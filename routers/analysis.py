"""PR 분석 API.

현재는 Mock 응답을 반환합니다. 실제 분석(RAG 검색 + LLM 호출 + 스키마 파싱)은
services/pr_analysis.py 로 옮기고, 여기서는 Depends 로 주입만 받도록 바꿉니다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from core.constants import MAX_DIFF_CHARS
from dependencies import get_current_user_id
from models.enums import FollowUpStatus, PrAnalysisStatus, ReviewResult, Role, Severity
from models.schemas import (
    CodeReviewComment,
    FollowUpTaskDTO,
    PRAnalysisRequest,
    PRAnalysisResponse,
    RoleImpactDTO,
)

router = APIRouter(prefix="/api/v1/analyze", tags=["Analysis"])


@router.post("/pr", response_model=PRAnalysisResponse)
def analyze_pr(
    body: PRAnalysisRequest,
    user_id: int = Depends(get_current_user_id),
) -> PRAnalysisResponse:
    """PR diff 를 분석해 요약과 리뷰 코멘트를 돌려줍니다.

    TODO: Mock 응답입니다. PrAnalysisService 연결 시 이 본문을 교체하고,
    user_id 로 프로젝트 소속(ProjectMemberRepository)을 검증합니다.
    """
    target_file = body.changed_files[0] if body.changed_files else "unknown"

    return PRAnalysisResponse(
        project_id=body.project_id,
        pr_number=body.pr_number,
        analysis_status=PrAnalysisStatus.NEEDS_REVIEW,
        review_result=ReviewResult.REQUEST_CHANGES,
        summary=f"[Mock] '{body.title}' 변경을 분석했습니다.",
        purpose="[Mock] 변경 목적입니다.",
        change_reason="[Mock] 변경이 필요했던 이유입니다.",
        before="[Mock] 변경 전 동작입니다.",
        after="[Mock] 변경 후 동작입니다.",
        comments=[
            CodeReviewComment(
                category="exception_handling",
                subtype="missing-null-check",
                severity=Severity.MEDIUM,
                file_path=target_file,
                line_reference="analyze_pr",
                evidence="+ result = repo.find(pr_id)",
                problem="[Mock] 조회 결과가 None 일 때를 처리하지 않습니다.",
                impact="[Mock] 존재하지 않는 PR 요청 시 500 이 발생합니다.",
                recommendation="[Mock] None 검사 후 404 를 반환하세요.",
                confidence=0.6,
            )
        ],
        affected_roles=[Role.BACKEND],
        role_impacts=[
            RoleImpactDTO(
                role=Role.BACKEND,
                impact="[Mock] 예외 처리 경로를 확인해야 합니다.",
                evidence="+ result = repo.find(pr_id)",
            )
        ],
        follow_up_tasks=[
            FollowUpTaskDTO(
                role=Role.BACKEND,
                task="[Mock] None 처리 분기와 테스트를 추가합니다.",
                evidence="+ result = repo.find(pr_id)",
                status=FollowUpStatus.NEEDS_ACTION,
            )
        ],
        needs_confirmation=["[Mock] PR 설명이 없어 변경 목적을 확정할 수 없습니다."],
        diff_truncated=len(body.diff) > MAX_DIFF_CHARS,
        analyzed_at=datetime.now(),
    )
