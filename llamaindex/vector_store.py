from llama_index.vector_stores.postgres import PGVectorStore

from core.config import settings


def get_vector_store(table_name: str = "code_embeddings") -> PGVectorStore:
    conn_str = f"{settings.database_url}?client_encoding=utf8"

    vector_store = PGVectorStore.from_params(
        connection_string=conn_str,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        table_name=table_name,
        embed_dim=settings.EMBED_DIM,
        hybrid_search=False,
        perform_setup=True,
    )
    return vector_store