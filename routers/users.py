from __future__ import annotations

from fastapi import APIRouter, Depends

from dependencies import get_current_user_id, get_user_service
from models.schemas import ProjectMemberDTO, UserDTO
from services.user import UserService

router = APIRouter(tags=["Users"])


@router.get("/users/me", response_model=UserDTO)
def get_me(
    user_id: int = Depends(get_current_user_id),
    svc: UserService = Depends(get_user_service),
) -> UserDTO:
    return svc.get(user_id)


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberDTO])
def list_members(
    project_id: int,
    user_id: int = Depends(get_current_user_id),
    svc: UserService = Depends(get_user_service),
) -> list[ProjectMemberDTO]:
    return svc.list_project_members(project_id, user_id)
