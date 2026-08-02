"""공통 Depends. 세션 주입과 서비스 조립을 담당합니다."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from core.database import get_db
from core.exceptions import PermissionDeniedError
from repositories.follow_up_task import FollowUpTaskRepository
from repositories.github_connection import GithubConnectionRepository
from repositories.project import ProjectRepository
from repositories.project_member import ProjectMemberRepository
from repositories.project_record import ProjectRecordRepository
from repositories.pull_request import PullRequestRepository
from repositories.user import UserRepository
from services.document import DocumentService
from services.firebase import FirebaseService
from services.llm_client import LlmClient
from services.pr_analysis import PrAnalysisService
from services.project_record import ProjectRecordService
from services.search import SearchService
from services.user import UserService


def get_current_user_id(x_user_id: str | None = Header(default=None)) -> int:
    """인증 컨텍스트.

    확인 필요: 실제 인증은 Spring Boot 백엔드가 담당하는지, AI 서비스가 JWT 를
    직접 검증하는지 정해야 합니다. 지금은 헤더에서 사용자 식별자만 읽습니다.
    """
    if not x_user_id:
        raise PermissionDeniedError("사용자 식별 정보가 없습니다.")
    return int(x_user_id)


# --- Repository ----------------------------------------------------------


def get_user_repository(db: Session = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


def get_project_repository(db: Session = Depends(get_db)) -> ProjectRepository:
    return ProjectRepository(db)


def get_project_member_repository(db: Session = Depends(get_db)) -> ProjectMemberRepository:
    return ProjectMemberRepository(db)


def get_github_connection_repository(
    db: Session = Depends(get_db),
) -> GithubConnectionRepository:
    return GithubConnectionRepository(db)


def get_pull_request_repository(db: Session = Depends(get_db)) -> PullRequestRepository:
    return PullRequestRepository(db)


def get_project_record_repository(db: Session = Depends(get_db)) -> ProjectRecordRepository:
    return ProjectRecordRepository(db)


def get_follow_up_task_repository(db: Session = Depends(get_db)) -> FollowUpTaskRepository:
    return FollowUpTaskRepository(db)


# --- 외부 클라이언트 ------------------------------------------------------


def get_llm_client() -> LlmClient:
    return LlmClient()


def get_firebase_service() -> FirebaseService:
    return FirebaseService()


# --- Service -------------------------------------------------------------


def get_pr_analysis_service(
    prs: PullRequestRepository = Depends(get_pull_request_repository),
    records: ProjectRecordRepository = Depends(get_project_record_repository),
    members: ProjectMemberRepository = Depends(get_project_member_repository),
    llm: LlmClient = Depends(get_llm_client),
) -> PrAnalysisService:
    return PrAnalysisService(prs, records, members, llm)


def get_project_record_service(
    records: ProjectRecordRepository = Depends(get_project_record_repository),
    tasks: FollowUpTaskRepository = Depends(get_follow_up_task_repository),
    members: ProjectMemberRepository = Depends(get_project_member_repository),
) -> ProjectRecordService:
    return ProjectRecordService(records, tasks, members)


def get_user_service(
    users: UserRepository = Depends(get_user_repository),
    members: ProjectMemberRepository = Depends(get_project_member_repository),
) -> UserService:
    return UserService(users, members)


def get_document_service(
    firebase: FirebaseService = Depends(get_firebase_service),
) -> DocumentService:
    return DocumentService(firebase)


def get_search_service(
    firebase: FirebaseService = Depends(get_firebase_service),
) -> SearchService:
    # retriever 는 llamaindex/retriever 구현을 주입합니다.
    return SearchService(firebase, retriever=None)
