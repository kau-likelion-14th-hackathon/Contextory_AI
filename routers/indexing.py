from fastapi import APIRouter, status, HTTPException
from models.schemas import RepoIndexingRequest, RepoIndexingResponse
from llamaindex.pipeline import index_repository_files

router = APIRouter(prefix="/api/v1/repos", tags=["Repository Indexing"])


@router.post("/index", response_model=RepoIndexingResponse, status_code=status.HTTP_200_OK)
async def index_repository(request: RepoIndexingRequest):
    """
    Spring Boot로부터 레포지토리의 전체 코드 파일들을 수신하여 pgvector에 임베딩 인덱싱을 수행합니다.
    """
    try:
        count = index_repository_files(request.repo_name, request.files)
        return RepoIndexingResponse(
            repo_name=request.repo_name,
            indexed_files_count=count,
            message=f"성공적으로 {count}개 파일의 pgvector 인덱싱이 완료되었습니다."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"인덱싱 처리 중 오류 발생: {str(e)}"
        )