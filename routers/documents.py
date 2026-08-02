from __future__ import annotations

from fastapi import APIRouter, Depends

from dependencies import get_current_user_id, get_document_service
from models.llamaindex_models import IngestRequest, IngestResult
from models.schemas import DocumentDTO
from services.document import DocumentService

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])


@router.get("", response_model=list[DocumentDTO])
def list_documents(
    project_id: int,
    user_id: int = Depends(get_current_user_id),
    svc: DocumentService = Depends(get_document_service),
) -> list[DocumentDTO]:
    return svc.list_by_project(project_id, user_id)


@router.get("/{document_id}", response_model=DocumentDTO)
def get_document(
    project_id: int,
    document_id: str,
    user_id: int = Depends(get_current_user_id),
    svc: DocumentService = Depends(get_document_service),
) -> DocumentDTO:
    return svc.get(project_id, document_id, user_id)


@router.post("/ingest", response_model=IngestResult)
def ingest_documents(
    project_id: int,
    body: IngestRequest,
    user_id: int = Depends(get_current_user_id),
    svc: DocumentService = Depends(get_document_service),
) -> IngestResult:
    raise NotImplementedError
