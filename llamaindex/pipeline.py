import os
from typing import List
from dotenv import load_dotenv

from llama_index.core import VectorStoreIndex, Document, StorageContext, PromptTemplate
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.core.vector_stores.types import VectorStoreQuery
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from llamaindex.vector_store import get_vector_store
from models.schemas import CodeFileChunk, PRAnalysisResponse, CodeReviewComment

load_dotenv()

# 1. OpenRouter 기반 LLM 및 Embedding 모델 설정
embed_model = OpenAIEmbedding(
    model="text-embedding-3-small",
    api_base="https://openrouter.ai/api/v1"
)

llm = OpenAI(
    model="gpt-4o",  # 'openai/gpt-4o' 대신 LlamaIndex 지원 이름인 'gpt-4o' 사용
    api_base="https://openrouter.ai/api/v1",
    temperature=0.2
)


def index_repository_files(repo_name: str, files: List[CodeFileChunk]) -> int:
    """
    Spring Boot에서 넘어온 전체 코드 파일들을 Chunking 및 Embedding 처리하여 pgvector에 저장합니다.
    """
    vector_store = get_vector_store(table_name="code_embeddings")
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    documents = []
    for f in files:
        # 파일 경로와 소스코드를 메타데이터와 함께 Document로 전환
        doc = Document(
            text=f.content,
            metadata={
                "repo_name": repo_name,
                "file_path": f.file_path
            }
        )
        documents.append(doc)
    
    # Node Parser를 이용한 코드 단락 Chunking
    parser = SimpleNodeParser.from_defaults(chunk_size=512, chunk_overlap=50)
    nodes = parser.get_nodes_from_documents(documents)
    
    # VectorStoreIndex 생성 및 pgvector 저장
    VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        embed_model=embed_model,
        show_progress=True
    )
    
    return len(files)


async def run_pr_rag_analysis(
    pr_id: int,
    repo_name: str,
    title: str,
    description: str,
    diff_content: str,
    author: str
) -> PRAnalysisResponse:
    """
    PR Diff 문맥을 기반으로 pgvector에서 연관 코드를 RAG로 검색해 오고, LLM으로 종합 분석 결과를 리턴합니다.
    """
    vector_store = get_vector_store(table_name="code_embeddings")
    
    # 1. Diff 관련 연관 코드 Retrieval (유사도 검색)
    diff_embedding = embed_model.get_text_embedding(diff_content[:1000]) # 상위 1000자 기준
    query_obj = VectorStoreQuery(
        query_embedding=diff_embedding,
        similarity_top_k=3
    )
    query_result = vector_store.query(query_obj)
    
    retrieved_contexts = []
    if query_result.nodes:
        for node in query_result.nodes:
            file_path = node.metadata.get("file_path", "unknown")
            retrieved_contexts.append(f"[연관 파일: {file_path}]\n{node.get_content()}")
            
    context_str = "\n\n".join(retrieved_contexts) if retrieved_contexts else "관련 연관 파일 없음 (독립적 변경)"

    # 2. GPT-4o 프롬프트 구성 및 PromptTemplate 변환
    prompt_str = f"""
너는 최고의 Senior Code Reviewer 및 Software Architect이다.
제공된 PR Diff와 pgvector에서 검색한 프로젝트 연관 코드 문맥(Context)을 종합하여 PR을 분석해라.

[PR 정보]
- PR ID: {pr_id}
- 저장소: {repo_name}
- 제목: {title}
- 작성자: {author}
- 설명: {description or '설명 없음'}

[Git Diff (변경점)]
{diff_content}

[RAG 검색된 프로젝트 연관 코드 문맥]
{context_str}

[요구사항]
1. PR의 전체 변경사항과 프로젝트에 미칠 파급 효과(영향도)를 한 줄로 요약해라.
2. 사이드 이펙트나 구조적 위험도를 고려하여 1~100점 사이의 위험도 점수(risk_score)를 산출해라. (숫자만)
3. 코드 내 예외 처리, N+1 쿼리, 보안, 컨벤션 등 피드백이 필요한 구체적인 리뷰 코멘트를 작성해라.

응답은 반드시 한국어로 작성해라.
"""
    prompt_template = PromptTemplate(prompt_str)
    
    # PromptTemplate 객체를 전달하여 구조화 응답 생성
    response = await llm.astructured_predict(
        PRAnalysisResponse,
        prompt_template
    )
    
    # 응답 객체의 pr_id 보장
    response.pr_id = pr_id
    return response