from __future__ import annotations

from fastapi import APIRouter, Depends

from dependencies import get_current_user_id, get_search_service
from models.schemas import SearchRequest, SearchResponse
from services.search import SearchService

router = APIRouter(tags=["Search"])


@router.post("/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    user_id: int = Depends(get_current_user_id),
    svc: SearchService = Depends(get_search_service),
) -> SearchResponse:
    return svc.search(body, user_id)
