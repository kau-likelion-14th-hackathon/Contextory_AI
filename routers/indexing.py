from fastapi import APIRouter, status, HTTPException
from models.schemas import RepoIndexingRequest, RepoIndexingResponse
from llamaindex.pipeline import index_repository_files

router = APIRouter(prefix="/api/v1/repos", tags=["Repository Indexing"])


@router.post("/index", response_model=RepoIndexingResponse, status_code=status.HTTP_200_OK)
async def index_repository(request: RepoIndexingRequest):
    """
    Spring Boot로부터 레포지토리 코드 파일들을 수신하여 pgvector 임베딩 인덱싱(Upsert) 및 삭제 파일 정리(Delete)를 수행합니다.
    """
    try:
        # 파이프라인에서 (indexed_count, deleted_count) 튜플을 반환받거나
        # deleted_files 인자를 함께 넘겨 처리하도록 연결
        indexed_count, deleted_count = index_repository_files(
            repo_name=request.repo_name,
            files=request.files,
            deleted_files=request.deleted_files
        )
        
        return RepoIndexingResponse(
            repo_name=request.repo_name,
            indexed_files_count=indexed_count,
            deleted_files_count=deleted_count,
            message=f"성공적으로 {indexed_count}개 파일 인덱싱 및 {deleted_count}개 파일 정리가 완료되었습니다."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"인덱싱 처리 중 오류 발생: {str(e)}"
        )