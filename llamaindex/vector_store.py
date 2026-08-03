import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from llama_index.vector_stores.postgres import PGVectorStore

load_dotenv()

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_NAME = os.getenv("POSTGRES_DB", "contextory_db")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
EMBED_DIM = int(os.getenv("EMBED_DIM", 1536))


def get_vector_store(table_name: str = "code_embeddings") -> PGVectorStore:
    safe_password = quote_plus(DB_PASSWORD)

    connection_string = (
        f"postgresql+psycopg2://{DB_USER}:{safe_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?client_encoding=utf8"
    )

    vector_store = PGVectorStore.from_params(
        connection_string=connection_string,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        table_name=table_name,
        embed_dim=EMBED_DIM,
        hybrid_search=False,
        perform_setup=True  # <-- 테이블 자동 생성(CREATE TABLE IF NOT EXISTS) 명시
    )
    return vector_store