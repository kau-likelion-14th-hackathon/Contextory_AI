from typing import List
from openai import OpenAI
from core.config import settings
from db.connection import get_db_connection  # 프로젝트 내 DB 커넥션 유틸 함수
from models.schemas import RepoIndexingRequest, RepoIndexingResponse

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def generate_embedding(text: str) -> List[float]:
    """텍스트를 1536차원 벡터로 변환"""
    response = client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=text
    )
    return response.data[0].embedding


def index_repository_code(request: RepoIndexingRequest) -> RepoIndexingResponse:
    """
    메인 브랜치 소스코드 임베딩 적재(Upsert) 및 Rebase/삭제된 파일 정리(Delete)
    """
    indexed_files_count = 0
    deleted_files_count = 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # 1. Rebase/삭제 대응: deleted_files 목록 DB 제거
            if request.deleted_files:
                cur.execute(
                    """
                    DELETE FROM repo_code_vectors
                    WHERE repo_name = %s AND file_path = ANY(%s);
                    """,
                    (request.repo_name, request.deleted_files)
                )
                deleted_files_count = cur.rowcount

            # 2. 최신 소스코드 Upsert (ON CONFLICT DO UPDATE)
            upsert_query = """
                INSERT INTO repo_code_vectors (repo_name, file_path, chunk_idx, code_content, embedding, updated_at)
                VALUES (%s, %s, %s, %s, %s::vector, NOW())
                ON CONFLICT (repo_name, file_path, chunk_idx)
                DO UPDATE SET 
                    code_content = EXCLUDED.code_content,
                    embedding = EXCLUDED.embedding,
                    updated_at = NOW();
            """

            for file_chunk in request.files:
                vector = generate_embedding(file_chunk.content)
                cur.execute(
                    upsert_query,
                    (
                        request.repo_name,
                        file_chunk.file_path,
                        file_chunk.chunk_idx,
                        file_chunk.content,
                        vector
                    )
                )
                indexed_files_count += 1

        conn.commit()

    return RepoIndexingResponse(
        repo_name=request.repo_name,
        indexed_files_count=indexed_files_count,
        deleted_files_count=deleted_files_count,
        message="성공적으로 pgvector 인덱싱 및 정리가 완료되었습니다."
    )