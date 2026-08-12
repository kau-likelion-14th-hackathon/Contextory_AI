from typing import List
from urllib.parse import urlparse, unquote
import psycopg2
from openai import OpenAI

from core.config import settings
from models.schemas import RepoIndexingRequest, RepoIndexingResponse

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def get_raw_psycopg2_connection():
    """settings.database_url에서 정보를 추출하여 psycopg2 커넥션 생성"""
    parsed = urlparse(settings.database_url)
    return psycopg2.connect(
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=unquote(parsed.password) if parsed.password else "",
        host=parsed.hostname,
        port=parsed.port or 5432
    )


def generate_embedding(text: str) -> List[float]:
    """텍스트를 벡터로 변환 (settings에 지정된 EMBEDDING_MODEL 사용)"""
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

    # settings에서 생성한 파싱 정보로 psycopg2 직접 커넥션 오픈
    with get_raw_psycopg2_connection() as conn:
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