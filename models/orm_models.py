from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base
from models.enums import (
    ApprovalStatus,
    FollowUpStatus,
    GithubConnectionStatus,
    PrAnalysisStatus,
    RecordType,
    Role,
    Severity,
)

# enum 컬럼은 DB 에 문자열로 저장되어 있다고 가정합니다.
# 백엔드가 native enum 타입이나 ordinal(int)을 쓴다면 여기를 맞춰야 합니다(확인 필요).
_ENUM_LEN = 32


class User(Base):
    __tablename__ = "users"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    memberships: Mapped[list[ProjectMember]] = relationship(back_populates="user")


class Project(Base):
    __tablename__ = "projects"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    one_line_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    members: Mapped[list[ProjectMember]] = relationship(back_populates="project")
    github_connection: Mapped[GithubConnection | None] = relationship(
        back_populates="project", uselist=False
    )
    pull_requests: Mapped[list[PullRequest]] = relationship(back_populates="project")
    records: Mapped[list[ProjectRecord]] = relationship(back_populates="project")


class ProjectMember(Base):
    """User 와 Project 의 브리지."""

    __tablename__ = "project_members"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")
    roles: Mapped[list[ProjectMemberRole]] = relationship(back_populates="member")


class ProjectMemberRole(Base):
    """팀원의 다중 역할 (1:N)."""

    __tablename__ = "project_member_roles"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_member_id: Mapped[int] = mapped_column(ForeignKey("project_members.id"))
    role: Mapped[Role] = mapped_column(String(_ENUM_LEN))

    member: Mapped[ProjectMember] = relationship(back_populates="roles")


class GithubConnection(Base):
    """프로젝트당 1개 (MVP)."""

    __tablename__ = "github_connections"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True)
    owner: Mapped[str] = mapped_column(String(255))
    repo: Mapped[str] = mapped_column(String(255))
    status: Mapped[GithubConnectionStatus] = mapped_column(String(_ENUM_LEN))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="github_connection")


class PullRequest(Base):
    __tablename__ = "pull_requests"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    pr_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    analysis_status: Mapped[PrAnalysisStatus] = mapped_column(String(_ENUM_LEN))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="pull_requests")
    files: Mapped[list[PullRequestFile]] = relationship(back_populates="pull_request")
    records: Mapped[list[ProjectRecord]] = relationship(back_populates="pull_request")


class PullRequestFile(Base):
    __tablename__ = "pull_request_files"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id"))
    file_path: Mapped[str] = mapped_column(Text)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    pull_request: Mapped[PullRequest] = relationship(back_populates="files")


class ProjectRecord(Base):
    """LLM 분석 결과를 담는 프로젝트 기록."""

    __tablename__ = "project_records"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    pull_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("pull_requests.id"), nullable=True
    )
    record_type: Mapped[RecordType] = mapped_column(String(_ENUM_LEN))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[str | None] = mapped_column(Text, nullable=True)
    after: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_confirmation: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[ApprovalStatus] = mapped_column(String(_ENUM_LEN))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="records")
    pull_request: Mapped[PullRequest | None] = relationship(back_populates="records")
    issues: Mapped[list[RecordIssue]] = relationship(back_populates="record")
    role_impacts: Mapped[list[RoleImpact]] = relationship(back_populates="record")
    follow_up_tasks: Mapped[list[FollowUpTask]] = relationship(back_populates="record")


class RecordIssue(Base):
    __tablename__ = "record_issues"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_record_id: Mapped[int] = mapped_column(ForeignKey("project_records.id"))
    category: Mapped[str] = mapped_column(String(64))
    subtype: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity: Mapped[Severity] = mapped_column(String(_ENUM_LEN))
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    record: Mapped[ProjectRecord] = relationship(back_populates="issues")


class RoleImpact(Base):
    __tablename__ = "role_impacts"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_record_id: Mapped[int] = mapped_column(ForeignKey("project_records.id"))
    role: Mapped[Role] = mapped_column(String(_ENUM_LEN))
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    record: Mapped[ProjectRecord] = relationship(back_populates="role_impacts")


class FollowUpTask(Base):
    __tablename__ = "follow_up_tasks"  # 확인 필요

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_record_id: Mapped[int] = mapped_column(ForeignKey("project_records.id"))
    role: Mapped[Role] = mapped_column(String(_ENUM_LEN))
    assignee_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_members.id"), nullable=True
    )
    task: Mapped[str] = mapped_column(Text)
    status: Mapped[FollowUpStatus] = mapped_column(String(_ENUM_LEN))

    record: Mapped[ProjectRecord] = relationship(back_populates="follow_up_tasks")
