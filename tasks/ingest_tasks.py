"""인덱싱 비동기 태스크."""

from __future__ import annotations

from models.llamaindex_models import IngestRequest, IngestResult


def ingest_project_documents(request: IngestRequest) -> IngestResult:
    """Celery 태스크로 등록해 사용합니다. 오래 걸리는 인덱싱을 백그라운드로 넘깁니다."""
    raise NotImplementedError


def reindex_project(project_id: int) -> IngestResult:
    raise NotImplementedError
