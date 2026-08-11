import psycopg2
from typing import List, Tuple

from llama_index.core import Document, PromptTemplate, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.core.vector_stores.types import VectorStoreQuery
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from core.config import settings
from llamaindex.vector_store import get_vector_store
from models.schemas import CodeFileChunk, PRAnalysisResponse, Evidence
from services.translation_service import translate_pr_to_en_query  # 1단계 번역 유틸


def get_embed_model() -> OpenAIEmbedding:
    return OpenAIEmbedding(
        model="text-embedding-3-small",
        api_key=settings.OPENAI_API_KEY,
    )


def get_llm() -> OpenAI:
    return OpenAI(
        model="gpt-4o",
        api_key=settings.OPENAI_API_KEY,
        temperature=0.2,
    )


def index_repository_files(
    repo_name: str, 
    files: List[CodeFileChunk], 
    deleted_files: List[str] = None
) -> Tuple[int, int]:
    """
    Spring Boot에서 넘어온 최신 파일들을 Chunking 및 Embedding 처리하여 repo_code_vectors에 저장(Upsert)하고,
    Rebase 및 삭제된 파일(deleted_files)은 DB에서 제거합니다.
    """
    embed_model = get_embed_model()
    # 2단계: 신규 테이블인 repo_code_vectors에 연결
    vector_store = get_vector_store(table_name="repo_code_vectors")
    
    deleted_count = 0

    # 1. Rebase / 파일 삭제 대응 (psycopg2 Direct DB Connection)
    if deleted_files:
        try:
            conn = psycopg2.connect(settings.DATABASE_URL)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM repo_code_vectors
                    WHERE repo_name = %s AND file_path = ANY(%s);
                    """,
                    (repo_name, deleted_files)
                )
                deleted_count = cur.rowcount
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Warning] Failed to delete files from DB: {e}")

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


async def run_pr_rag_analysis(
    pr_id: int,
    repo_name: str,
    title: str,
    description: str,
    diff_content: str,
    author: str,
) -> PRAnalysisResponse:
    """
    Query Translation + Multi-Source Retrieval(code_review_vectors + repo_code_vectors)을 통해
    프로젝트 소스코드 맥락과 오픈소스 리뷰 모범사례를 모두 참고하여 PR 분석을 수행합니다.
    """
    embed_model = get_embed_model()
    llm = get_llm()

    # 1. Query Translation (한글 PR Title/Description -> 영문 RAG Query 변환)
    translated_query = await translate_pr_to_en_query(title, description)
    search_text = f"PR Title/Summary: {translated_query}\nPR Diff:\n{diff_content[:1000]}"
    query_embedding = embed_model.get_text_embedding(search_text)

    # 2-A. Multi-Source Retrieval 1: repo_code_vectors (우리 프로젝트 소스코드 맥락)
    repo_vector_store = get_vector_store(table_name="repo_code_vectors")
    repo_query_obj = VectorStoreQuery(query_embedding=query_embedding, similarity_top_k=3)
    repo_query_result = repo_vector_store.query(repo_query_obj)

    repo_contexts = []
    if repo_query_result.nodes:
        for node in repo_query_result.nodes:
            fpath = node.metadata.get("file_path", "unknown")
            repo_contexts.append(f"[우리 프로젝트 연관 파일: {fpath}]\n{node.get_content()}")

    repo_context_str = "\n\n".join(repo_contexts) if repo_contexts else "우리 레포지토리 연관 파일 없음"

    # 2-B. Multi-Source Retrieval 2: code_review_vectors (오픈소스 범용 리뷰 모범 사례)
    review_vector_store = get_vector_store(table_name="code_review_vectors")
    review_query_obj = VectorStoreQuery(query_embedding=query_embedding, similarity_top_k=3)
    review_query_result = review_vector_store.query(review_query_obj)

    review_contexts = []
    evidences = []

    if review_query_result.nodes:
        for idx, node in enumerate(review_query_result.nodes):
            review_comment = node.metadata.get("review_comment", "")
            source_code = node.get_content()
            score = review_query_result.similarities[idx] if review_query_result.similarities else 0.0

            review_contexts.append(f"[과거 리뷰 모범사례 {idx+1}]\n코드: {source_code[:300]}\n피드백: {review_comment}")

            # Evidence DTO 매핑
            evidences.append(
                Evidence(
                    id=str(node.node_id),
                    source_code=source_code,
                    pr_diff=node.metadata.get("pr_diff"),
                    review_comment=review_comment,
                    similarity_score=round(score, 4)
                )
            )

    review_context_str = "\n\n".join(review_contexts) if review_contexts else "유사 과거 리뷰 모범사례 없음"

    # 3. Multi-Source Context가 결합된 Grounded Prompt 구성
    prompt_str = f"""
너는 최고의 Senior Code Reviewer 및 Software Architect이다.
제공된 PR 정보, 우리 프로젝트 소스코드 맥락, 그리고 유사 과거 코드 리뷰 모범사례를 종합적으로 분석하여 코드 리뷰를 수행하라.

[PR 정보]
- PR ID: {pr_id}
- 저장소: {repo_name}
- 제목: {title}
- 영문 요약 (검색용): {translated_query}
- 작성자: {author}
- 설명: {description or '설명 없음'}

[Git Diff (현재 PR 변경점)]
{diff_content}

[1. 우리 프로젝트 소스코드 맥락 (Domain Context)]
{repo_context_str}

[2. 검증된 과거 코드 리뷰 모범사례 (Review Knowledge)]
{review_context_str}

[요구사항]
1. PR의 전체 변경사항과 프로젝트 기존 코드에 미칠 파급 효과를 한 줄로 요약해라.
2. 사이드 이펙트나 구조적 위험도를 고려하여 1~100점 사이의 위험도 점수(risk_score)를 산출해라.
3. 우리 프로젝트의 컨벤션/구조와 과거 리뷰 모범사례를 참고하여 예외 처리, N+1, 보안, 로깅 등 구체적인 리뷰 코멘트를 작성해라.

응답은 반드시 한국어로 작성해라.
"""
    prompt_template = PromptTemplate(prompt_str)

    # 4. GPT-4o 추론 및 응답 매핑
    response = await llm.astructured_predict(
        PRAnalysisResponse, prompt_template
    )

    response.pr_id = pr_id
    response.evidences = evidences
    
    # RAG 신뢰도 계산 (Evidences 상위 유사도 기준)
    if evidences:
        top_score = max(e.similarity_score for e in evidences)
        response.confidence = round(top_score, 2)
        response.needs_confirmation = top_score < settings.SIM_THRESHOLD
    else:
        response.confidence = 0.5
        response.needs_confirmation = True

    return response