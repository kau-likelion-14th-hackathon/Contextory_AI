"""Pydantic 요청/응답 DTO — API 계약.

ORM(orm_models.py)과 분리합니다. 응답 변환은 `from_attributes=True` 를 씁니다.
LLM 분석 결과 스키마는 `prompts/korean_code_review_output_schema.json` 과 필드가 대응합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Any

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


# ==========================================
# 0. 코드 인덱싱 DTO (POST /api/v1/repos/index)
# ==========================================


class CodeFileChunk(BaseModel):
    """레포지토리 인덱싱을 위한 단일 코드 파일/조각 DTO."""

    file_path: str = Field(
        ...,
        description="파일 경로",
        json_schema_extra={
            "example": "src/main/java/com/contextory/service/UserService.java"
        },
    )
    content: str = Field(
        ...,
        description="파일의 전체 소스코드 내용",
        json_schema_extra={
            "example": "package com.contextory.service;\n\npublic class UserService { ... }"
        },
    )


class RepoIndexingRequest(BaseModel):
    """Spring Boot -> FastAPI: 레포지토리 전체 코드 인덱싱(임베딩) 요청 DTO."""

    repo_name: str = Field(
        ...,
        description="리포지토리 이름",
        json_schema_extra={"example": "Contextory/Backend"},
    )
    branch: str = Field(
        default="main", description="대상 브랜치명", json_schema_extra={"example": "main"}
    )
    files: List[CodeFileChunk] = Field(..., description="인덱싱할 전체 코드 파일 목록")


class RepoIndexingResponse(BaseModel):
    """FastAPI -> Spring Boot: 레포지토리 인덱싱 결과 응답 DTO."""

    repo_name: str = Field(
        ...,
        description="인덱싱된 리포지토리 이름",
        json_schema_extra={"example": "Contextory/Backend"},
    )
    indexed_files_count: int = Field(
        ...,
        description="pgvector에 성공적으로 임베딩 처리된 파일 수",
        json_schema_extra={"example": 15},
    )
    message: str = Field(
        ...,
        description="처리 결과 메시지",
        json_schema_extra={"example": "성공적으로 pgvector 인덱싱이 완료되었습니다."},
    )


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
    """LLM 이 반환해야 하는 구조. 자유 텍스트가 아니라 이 스키마로 파싱합니다."""

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
    """분석 요청.

    백엔드/GitHub 연동 수집 diff 지원 및 호환 필드 제공.
    """

    project_id: Optional[int] = Field(default=1, description="프로젝트 ID")
    pr_number: Optional[int] = Field(default=1, description="PR 번호")
    pr_id: Optional[int] = Field(default=None, description="PR 식별자 ID 호환 필드")
    repo_name: Optional[str] = Field(default=None, description="리포지토리 이름")
    title: str = Field(..., description="PR 제목")
    body: str | None = Field(default=None, description="PR 본문/설명")
    description: Optional[str] = Field(default=None, description="PR 본문 설명 호환 필드")
    author: str | None = Field(default=None, description="PR 작성자")
    diff: str = Field(default="", description="통합 diff 원문.")
    diff_content: Optional[str] = Field(default=None, description="Git Diff 문맥 호환 필드")
    changed_files: list[str] = Field(default_factory=list)


class CodeReviewComment(BaseModel):
    """리뷰 코멘트 하나."""

    category: str = Field(
        default="general",
        description="security | performance | n_plus_one | functional_bug | api_contract | exception_handling | refactoring",
    )
    subtype: str | None = None
    severity: Severity = Field(default=Severity.MEDIUM)
    file_path: str = Field(..., description="리뷰 대상 파일 경로")
    line_reference: str | None = None
    line_number: Optional[int] = Field(default=None, description="코드 줄 번호 호환 필드")
    evidence: str = Field(default="", description="diff 에서 그대로 인용한 코드 라인.")
    comment: Optional[str] = Field(default=None, description="AI 코드 리뷰 피드백 내용")
    problem: str = Field(default="")
    impact: str = Field(default="")
    recommendation: str = Field(default="")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PRAnalysisResponse(BaseModel):
    """분석 결과."""

    project_id: Optional[int] = 1
    pr_number: Optional[int] = 1
    pr_id: Optional[int] = Field(default=None, description="요청받았던 PR ID")
    analysis_status: PrAnalysisStatus = PrAnalysisStatus.NEEDS_REVIEW
    review_result: Optional[ReviewResult] = ReviewResult.COMMENT
    risk_score: Optional[int] = Field(default=0, description="코드 변경 위험도 점수 (1~100)")

    summary: str = ""
    purpose: str = ""
    change_reason: str = ""
    before: str = ""
    after: str = ""

    comments: list[CodeReviewComment] = Field(default_factory=list)
    reviews: List[CodeReviewComment] = Field(
        default_factory=list, description="파일별 코드 리뷰 목록 호환 필드"
    )
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
    detail: Any | None = None