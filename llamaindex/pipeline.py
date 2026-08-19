import psycopg2
from typing import List, Tuple

from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.embeddings.openai import OpenAIEmbedding

from core.config import settings
from llamaindex.vector_store import get_vector_store
from models.schemas import CodeFileChunk


def get_embed_model() -> OpenAIEmbedding:
    """
    인덱싱(pipeline)과 조회(services/retrieval)가 동일한 임베딩 모델을 쓰도록 하는 단일 지점.
    모델명은 하드코딩하지 않고 settings.EMBEDDING_MODEL을 따른다.
    """
    return OpenAIEmbedding(
        model=settings.EMBEDDING_MODEL,
        api_key=settings.OPENAI_API_KEY,
    )


def index_repository_files(
    repo_name: str,
    files: List[CodeFileChunk],
    deleted_files: List[str] = None
) -> Tuple[int, int]:
    """
    Spring Boot에서 넘어온 최신 파일들을 Chunking 및 Embedding 처리하여 repo_code_vectors(LlamaIndex 관리 테이블,
    실제 물리 테이블은 data_repo_code_vectors)에 저장(Upsert)하고,
    Rebase 및 삭제된 파일(deleted_files)은 같은 물리 테이블에서 제거합니다.
    """
    embed_model = get_embed_model()
    vector_store = get_vector_store(table_name=settings.REPO_CODE_TABLE_NAME)

    deleted_count = 0

    # 1. Rebase / 파일 삭제 대응 (psycopg2 Direct DB Connection)
    # LlamaIndex PGVectorStore가 관리하는 실제 물리 테이블(data_ 접두사)과 노드 스키마
    # (metadata_ JSON 컬럼)에 맞춰 삭제한다. 삽입 대상 테이블과 삭제 대상 테이블을 일치시킨다.
    if deleted_files:
        physical_table = f"data_{settings.REPO_CODE_TABLE_NAME}"
        # psycopg2는 SQLAlchemy 방언 접두사(postgresql+psycopg2://)가 붙은 DSN을 파싱하지 못하므로
        # 개별 접속 정보로 직접 연결한다.
        conn = psycopg2.connect(
            dbname=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM {physical_table}
                    WHERE metadata_->>'repo_name' = %s AND metadata_->>'file_path' = ANY(%s);
                    """,
                    (repo_name, deleted_files)
                )
                deleted_count = cur.rowcount
            conn.commit()
        finally:
            conn.close()

    # 2. 최신 파일 인덱싱 (Upsert)
    documents = []
    for f in files:
        doc = Document(
            text=f.content,
            metadata={"repo_name": repo_name, "file_path": f.file_path, "chunk_idx": f.chunk_idx},
        )
        documents.append(doc)

    if documents:
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        parser = SimpleNodeParser.from_defaults(chunk_size=512, chunk_overlap=50)
        nodes = parser.get_nodes_from_documents(documents)

        VectorStoreIndex(
            nodes=nodes,
            storage_context=storage_context,
            embed_model=embed_model,
            show_progress=True,
        )

    return len(files), deleted_count
