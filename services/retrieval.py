import os
from typing import List, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from openai import OpenAI
from core.config import settings
from core.db import engine  # DB Engine import

client = OpenAI(api_key=settings.OPENAI_API_KEY)

def embed_query(text_input: str) -> List[float]:
    """입력받은 PR Diff / 쿼리 텍스트를 1536차원 임베딩 벡터로 변환"""
    response = client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=[text_input]
    )
    return response.data[0].embedding

def retrieve_contexts(
    query_text: str, 
    top_k: int = None, 
    sim_threshold: float = None
) -> List[Dict[str, Any]]:
    """
    PR Diff 기반 pgvector 코사인 유사도 Top-K 검색
    """
    if top_k is None:
        top_k = settings.RAG_TOP_K
    if sim_threshold is None:
        sim_threshold = settings.SIM_THRESHOLD

    # 1. 쿼리 임베딩
    query_vector = embed_query(query_text)
    
    # 2. pgvector Cosine Distance (<=>) 연산 쿼리
    # Cosine Similarity = 1 - Cosine Distance
    sql = text("""
        SELECT 
            id,
            orig_idx,
            dataset_source,
            source_code,
            pr_diff,
            review_comment,
            has_issue,
            1 - (embedding <=> :query_vector) AS similarity_score
        FROM code_review_vectors
        ORDER BY embedding <=> :query_vector ASC
        LIMIT :top_k;
    """)

    # pgvector 파라미터는 [0.1, 0.2, ...] 형태의 문자열 포맷 필요
    vector_str = f"[{','.join(map(str, query_vector))}]"

    results = []
    with engine.connect() as conn:
        rows = conn.execute(
            sql, 
            {"query_vector": vector_str, "top_k": top_k}
        ).mappings().all()

        for row in rows:
            sim_score = float(row["similarity_score"])
            results.append({
                "id": str(row["id"]),
                "orig_idx": row["orig_idx"],
                "dataset_source": row["dataset_source"],
                "source_code": row["source_code"],
                "pr_diff": row["pr_diff"],
                "review_comment": row["review_comment"],
                "has_issue": row["has_issue"],
                "similarity_score": round(sim_score, 4)
            })

    return results