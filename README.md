# 🚀 Contextory AI Service

Contextory 프로젝트의 AI 파트 서비스입니다.  
FastAPI 기반으로 구축되어 있으며, GitHub PR 분석 및 LlamaIndex / PostgreSQL(`pgvector`) 기반의 RAG(검색 증강 생성) 파이프라인을 전담합니다.

---

## 🛠 Tech Stack

- **Framework:** FastAPI
- **Language:** Python 3.11
- **Environment Management:** Conda
- **ASGI Server:** Uvicorn
- **AI / RAG Framework:** LlamaIndex, OpenAI GPT-4o
- **Database:** PostgreSQL (`pgvector`)

---

## 📁 Directory Structure

```text
AI_service/
├── main.py                     # FastAPI 앱 실행 및 메인 라우터 연결
├── dependencies.py             # FastAPI Depends 주입 모듈
├── .env.example                # 환경 변수 템플릿
├── requirements.txt            # 의존성 패키지 목록
│
├── routers/                    # API 엔드포인트 계층 (Health, PR 분석, RAG 검색 등)
├── core/                       # 환경설정, DB 커넥션, 공통 모듈
├── services/                   # 비즈니스 로직 및 전처리 모듈
├── models/                     # Pydantic 스키마 및 DB 모델
├── llamaindex/                 # LlamaIndex VectorStore, Retriever, Ingestion 파이프라인
└── tests/                      # 테스트 코드