# 🚀 Contextory AI Service

Contextory 프로젝트의 AI 파트 서비스입니다.  
FastAPI 기반으로 구축되어 있으며, GitHub PR 분석 및 LlamaIndex / PostgreSQL(`pgvector`) 기반의 RAG(검색 증강 생성) 파이프라인을 전담합니다.

---

## 🛠 Tech Stack & Environment

- **Framework:** FastAPI
- **Language:** Python 3.11
- **Environment Management:** Conda
- **ASGI Server:** Uvicorn
- **Database / Vector Store:** PostgreSQL 18 + `pgvector`
- **ORM / DB Driver:** SQLAlchemy 2.0, `psycopg2-binary`
- **Settings Management:** `pydantic-settings`
- **AI / RAG Framework:** LlamaIndex, OpenRouter (GPT-4o), OpenAI (`text-embedding-3-small`)

## 📁 Directory Structure

```text
AI_service/
├── main.py                     # FastAPI 앱 실행 및 메인 라우터 연결
├── dependencies.py             # FastAPI Depends 주입 모듈 (DB 세션 생명주기 관리 등)
├── .env.example                # 환경 변수 템플릿
├── requirements.txt            # 의존성 패키지 목록
│
├── core/                       # 환경설정, DB 커넥션, 공통 인프라 모듈
│   ├── config.py               # pydantic-settings 기반 환경변수 관리
│   └── db.py                   # SQLAlchemy Engine & SessionLocal 관리
│
├── routers/                    # API 엔드포인트 계층
│   ├── health.py               # DB 연동 상태 검증 헬스체크 API
│   ├── repo.py                 # RAG 소스코드 인덱싱 API (/api/v1/repos/index)
│   └── analyze.py              # RAG 기반 PR Diff 분석 API (/api/v1/analyze/pr)
│
├── services/                   # 비즈니스 로직 및 전처리 모듈
│
├── models/                     # Pydantic 스키마 및 DB ORM 모델
│   └── schemas.py               # Request/Response API DTO 및 리뷰 Pydantic 스키마
│
├── llamaindex/                 # LlamaIndex VectorStore, Retriever, Ingestion 파이프라인
│   ├── pipeline.py             # RAG Ingestion & Retrieval 실행 파이프라인
│   └── vector_store.py         # PostgreSQL pgvector PGVectorStore 연동 및 관리
│
└── tests/                      # 테스트 코드
```

---

## 🚀 Getting Started

### 1. 가상환경 및 패키지 설치
```bash
# 패키지 일괄 설치
pip install -r requirements.txt
```

### 2. 환경 변수 설정 (.env)
.env.example 파일을 참고하여 직접 생성합니다.
```bash
# Server Config
APP_ENV=development
LOG_LEVEL=INFO

# PostgreSQL (pgvector) DB Config
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=contextory_db

# OpenAI Config
OPENAI_API_KEY=your_openai_api_key_here

# Google API Config
GOOGLE_API_KEY=your_google_api_key_here
GOOGLE_SEARCH_ENGINE_ID=your_google_search_engine_id_here
```

### 3. PostgreSQL 필수 설정 (`pgvector`)

`Contextory` 프로젝트는 임베딩 벡터 검색을 위해 PostgreSQL의 `pgvector` 확장을 사용합니다.

#### 1) pgvector 바이너리 설치
* **Docker 사용 시 (권장):** `pgvector/pgvector:pg16` 또는 `ankane/pgvector` 이미지 사용 시 별도 설치 없이 2번 단계를 진행합니다.
* **Windows 로컬 환경 사용 시:**
  1. [pgvector Releases](https://github.com/andreiramani/pgvector_pgsql_windows/releases)에서 본인의 PostgreSQL 버전에 맞는 실행 파일/Zip을 다운로드합니다.
  2. `vector.dll` ➔ `C:\Program Files\PostgreSQL\{버전}\lib\` 복사
  3. `vector.control` 및 `vector--*.sql` ➔ `C:\Program Files\PostgreSQL\{버전}\share\extension\` 복사
  4. **PostgreSQL 서비스 재시작** (PowerShell 관리자 권한: `net stop postgresql-x64-{버전}` ➔ `net start postgresql-x64-{버전}`)
  5. *참고:* `58P01` 에러 발생 시 [Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe) 설치가 필요할 수 있습니다.

#### 2) Database 확장 모듈 활성화
Database(`contextory_db`)에 접속 후 SQL 콘솔(DataGrip, psql 등)에서 확장 모듈을 활성화합니다.
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 4. 서버 실행 및 헬스 체크
```bash
# Uvicorn 서버 구동
uvicorn main:app --reload
```

---