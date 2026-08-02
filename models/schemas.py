"""Pydantic 요청/응답 DTO — API 계약.

ORM(orm_models.py)과 분리합니다. 응답 변환은 `from_attributes=True` 를 씁니다.
LLM 분석 결과 스키마는 `prompts/korean_code_review_output_schema.json` 과 필드가 대응합니다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.enums import (
    ApprovalStatus,
    FollowUpStatus,
    GithubConnectionStatus,
    PrAnalysisStatus,
    RecordType,
    ReviewResult,
    Role,
    Severity,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- 사용자 / 프로젝트 ----------------------------------------------------


class UserDTO(ORMModel):
    id: int
    github_id: str
    username: str
    email: str | None = None
    created_at: datetime | None = None


class ProjectDTO(ORMModel):
    id: int
    name: str
    one_line_description: str | None = None
    purpose: str | None = None
    main_features: str | None = None
    default_language: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectMemberDTO(ORMModel):
    id: int
    project_id: int
    user_id: int
    is_admin: bool
    roles: list[Role] = Field(default_factory=list)


class GithubConnectionDTO(ORMModel):
    id: int
    project_id: int
    owner: str
    repo: str
    status: GithubConnectionStatus
    created_at: datetime | None = None


# --- Pull Request --------------------------------------------------------


class PullRequestFileDTO(ORMModel):
    id: int
    file_path: str
    diff: str | None = None
    diff_truncated: bool = False


class PullRequestDTO(ORMModel):
    id: int
    project_id: int
    pr_number: int
    title: str
    body: str | None = None
    author: str | None = None
    github_state: str | None = None
    analysis_status: PrAnalysisStatus
    synced_at: datetime | None = None


class PullRequestDetailDTO(PullRequestDTO):
    files: list[PullRequestFileDTO] = Field(default_factory=list)


# --- LLM 분석 결과 --------------------------------------------------------


class IssueDTO(ORMModel):
    """분석이 찾은 문제. 모든 항목에 evidence 가 있어야 합니다."""

    category: str
    subtype: str | None = None
    severity: Severity
    file_path: str | None = None
    line_reference: str | None = None
    evidence: str
    problem: str
    impact: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)


class RoleImpactDTO(ORMModel):
    role: Role
    impact: str
    evidence: str


class FollowUpTaskDTO(ORMModel):
    id: int | None = None
    role: Role
    assignee_member_id: int | None = None
    task: str
    evidence: str | None = None
    status: FollowUpStatus = FollowUpStatus.NEEDS_ACTION


class AnalysisResultDTO(BaseModel):
    """LLM 이 반환해야 하는 구조. 자유 텍스트가 아니라 이 스키마로 파싱합니다.

    파싱에 실패하면 PR 의 analysis_status 를 failed 로 기록합니다.
    """

    summary: str
    purpose: str = ""
    change_reason: str = ""
    before: str = ""
    after: str = ""
    related_features: list[str] = Field(default_factory=list)
    affected_roles: list[Role] = Field(default_factory=list)
    role_impacts: list[RoleImpactDTO] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTaskDTO] = Field(default_factory=list)
    needs_confirmation: list[str] = Field(default_factory=list)
    issues: list[IssueDTO] = Field(default_factory=list)


# --- PR 분석 API (POST /api/v1/analyze/pr) --------------------------------


class PRAnalysisRequest(BaseModel):
    """분석 요청. diff 는 호출자(백엔드/GitHub 연동)가 수집해 전달합니다.

    LLM 은 여기 담긴 내용만 근거로 삼습니다. diff 밖의 정보는 추측하지 않습니다.
    """

    project_id: int
    pr_number: int
    title: str
    body: str | None = None
    author: str | None = None
    diff: str = Field(description="통합 diff 원문. MAX_DIFF_CHARS 초과분은 잘립니다.")
    changed_files: list[str] = Field(default_factory=list)


class CodeReviewComment(BaseModel):
    """리뷰 코멘트 하나. evidence 없는 코멘트는 만들지 않습니다."""

    category: str  # security | performance | n_plus_one | functional_bug | api_contract | exception_handling | refactoring
    subtype: str | None = None
    severity: Severity
    file_path: str
    line_reference: str | None = None
    evidence: str = Field(description="diff 에서 그대로 인용한 코드 라인.")
    problem: str
    impact: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)


class PRAnalysisResponse(BaseModel):
    """분석 결과. 필드는 korean_code_review_output_schema.json 과 대응합니다."""

    project_id: int
    pr_number: int
    analysis_status: PrAnalysisStatus = PrAnalysisStatus.NEEDS_REVIEW
    review_result: ReviewResult

    summary: str
    purpose: str = ""
    change_reason: str = ""
    before: str = ""
    after: str = ""

    comments: list[CodeReviewComment] = Field(default_factory=list)
    affected_roles: list[Role] = Field(default_factory=list)
    role_impacts: list[RoleImpactDTO] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTaskDTO] = Field(default_factory=list)
    needs_confirmation: list[str] = Field(default_factory=list)

    diff_truncated: bool = False
    analyzed_at: datetime | None = None


# --- 프로젝트 기록 --------------------------------------------------------


class ProjectRecordDTO(ORMModel):
    id: int
    project_id: int
    pull_request_id: int | None = None
    record_type: RecordType
    title: str
    summary: str | None = None
    purpose: str | None = None
    change_reason: str | None = None
    before: str | None = None
    after: str | None = None
    related_features: str | None = None
    needs_confirmation: str | None = None
    approval_status: ApprovalStatus
    approver_id: int | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectRecordDetailDTO(ProjectRecordDTO):
    issues: list[IssueDTO] = Field(default_factory=list)
    role_impacts: list[RoleImpactDTO] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTaskDTO] = Field(default_factory=list)


class RecordApprovalRequest(BaseModel):
    approval_status: ApprovalStatus
    approver_id: int


class FollowUpStatusUpdateRequest(BaseModel):
    status: FollowUpStatus


# --- 검색 / 채팅 (Firestore + RAG) ----------------------------------------


class SearchRequest(BaseModel):
    project_id: int
    query: str
    top_k: int = 5


class SearchHitDTO(BaseModel):
    document_id: str
    score: float | None = None
    snippet: str
    metadata: dict = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitDTO] = Field(default_factory=list)


class DocumentDTO(BaseModel):
    document_id: str
    project_id: int
    title: str
    content: str | None = None
    metadata: dict = Field(default_factory=dict)


# --- 공통 오류 응답 -------------------------------------------------------


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: object | None = None
