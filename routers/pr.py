from __future__ import annotations

from fastapi import APIRouter, Depends

from dependencies import (
    get_current_user_id,
    get_pr_analysis_service,
    get_project_record_service,
)
from models.enums import ApprovalStatus
from models.schemas import (
    FollowUpStatusUpdateRequest,
    ProjectRecordDetailDTO,
    PullRequestDetailDTO,
    RecordApprovalRequest,
)
from services.pr_analysis import PrAnalysisService
from services.project_record import ProjectRecordService

router = APIRouter(prefix="/projects/{project_id}", tags=["Pull Request"])


@router.post("/pull-requests/{pr_id}/analyze", response_model=ProjectRecordDetailDTO)
def analyze_pr(
    project_id: int,
    pr_id: int,
    user_id: int = Depends(get_current_user_id),
    svc: PrAnalysisService = Depends(get_pr_analysis_service),
) -> ProjectRecordDetailDTO:
    return svc.analyze(project_id, pr_id, user_id)


@router.get("/pull-requests/{pr_id}", response_model=PullRequestDetailDTO)
def get_pull_request(
    project_id: int,
    pr_id: int,
    user_id: int = Depends(get_current_user_id),
    svc: PrAnalysisService = Depends(get_pr_analysis_service),
) -> PullRequestDetailDTO:
    raise NotImplementedError


@router.get("/records", response_model=list[ProjectRecordDetailDTO])
def list_records(
    project_id: int,
    approval_status: ApprovalStatus | None = None,
    user_id: int = Depends(get_current_user_id),
    svc: ProjectRecordService = Depends(get_project_record_service),
) -> list[ProjectRecordDetailDTO]:
    return svc.list_by_project(project_id, user_id, approval_status)


@router.patch("/records/{record_id}/approval", response_model=ProjectRecordDetailDTO)
def update_record_approval(
    project_id: int,
    record_id: int,
    body: RecordApprovalRequest,
    user_id: int = Depends(get_current_user_id),
    svc: ProjectRecordService = Depends(get_project_record_service),
) -> ProjectRecordDetailDTO:
    raise NotImplementedError


@router.patch("/follow-up-tasks/{task_id}", status_code=204)
def update_follow_up_task(
    project_id: int,
    task_id: int,
    body: FollowUpStatusUpdateRequest,
    user_id: int = Depends(get_current_user_id),
    svc: ProjectRecordService = Depends(get_project_record_service),
) -> None:
    raise NotImplementedError
