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
├── main.py                     # FastAPI 앱 실행 및 라우터 연결
├── dependencies.py             # FastAPI Depends 주입 모듈 (DB 세션 생명주기 관리 등)
├── .env.example                # 환경 변수 템플릿
├── requirements.txt            # 의존성 패키지 목록
│
├── core/                       # 환경설정, DB 커넥션, 공통 인프라 모듈
│   ├── config.py               # pydantic-settings 기반 환경변수/테이블명 상수 관리
│   └── db.py                   # SQLAlchemy Engine & SessionLocal 관리
│
├── routers/                    # API 엔드포인트 계층
│   ├── health.py               # 헬스체크 API
│   ├── indexing.py             # RAG 소스코드 인덱싱 API (POST /api/v1/repos/index)
│   ├── analysis.py             # RAG 기반 PR Diff 동기 분석 API (POST /api/v1/analyze/pr)
│   └── internal_analysis.py    # 비동기 PR 분석 내부 API (POST/GET /internal/v1/analyses[...])
│
├── services/                   # 비즈니스 로직 및 전처리 모듈
│   ├── analysis_service.py     # 동기/비동기 RAG 분석 오케스트레이션(공용 컨텍스트 조회 헬퍼 포함)
│   ├── retrieval.py            # code_review_vectors / repo_code_vectors 조회(raw SQL + LlamaIndex)
│   ├── context_filter.py       # 무관 컨텍스트 필터링
│   ├── prompt_builder.py       # Grounded Prompt 구성
│   ├── confidence.py           # 신뢰도 산출
│   ├── translation_service.py  # PR 제목/설명 영문 검색쿼리 번역
│   ├── callback_service.py     # 비동기 분석 완료/실패 Callback 전송
│   ├── job_store.py            # 비동기 작업 상태 In-Memory 저장소 (단일 프로세스 한정)
│   └── repo_index_service.py   # (미사용) 레포 코드 raw SQL 인덱싱 — 현재 어떤 라우터에도 연결되지 않음
│
├── models/                     # Pydantic 스키마
│   └── schemas.py              # Request/Response API DTO (동기/비동기, camelCase 내부 API 포함)
│
├── llamaindex/                 # LlamaIndex VectorStore / Ingestion 파이프라인
│   ├── pipeline.py             # repo_code_vectors(data_repo_code_vectors) Upsert/Delete 인덱싱 + 임베딩 모델 단일 지점
│   └── vector_store.py         # PostgreSQL pgvector PGVectorStore 연동 및 관리
│
├── eval/                       # 평가 시스템 (services/를 절대 import하지 않음 — callback 주입식)
│   ├── runner.py               # Filter OFF/ON 비교 실행, Ground Truth / Reference-Free 분기
│   ├── judge.py                # LLM Judge(주입식) + Offline Keyword Judge(LLM 없이 동작)
│   ├── report.py               # Text/JSON 리포트, Fake Confidence 판정
│   ├── error_analysis.py       # 실패 단계 분류(Retrieval→Ranking→Filter→Context→Interpretation→Generation→Hallucination)
│   ├── fakes.py                # LLM·DB 없이 돌리기 위한 가짜 callback 모음
│   ├── metrics/                # retrieval / filtering / generation / retrieval_signals (전부 순수 함수)
│   ├── datasets/               # ground_truth, loader(JSONL), codereview_adapters, silver_builder
│   └── data/                   # 최소 샘플 Ground Truth (sample_ground_truth.jsonl)
│
├── scripts/                    # 실행 스크립트 — services와 eval을 연결하는 유일한 조립 계층
│   ├── index_to_pg.py          # 임베딩·적재 로직 (노트북/CLI 공용 라이브러리)
│   ├── load_code_review_subset.py  # code_review_vectors 서브셋 적재 CLI (--dry-run 비용 추정 지원)
│   ├── create_vector_indexes.py    # pgvector hnsw ANN 인덱스 생성/점검 (기본 dry-run)
│   ├── eval_adapters.py        # services(필터·Confidence·검색·생성) → eval callback 어댑터
│   ├── run_eval.py             # Filter OFF/ON 비교 리포트 실행 (offline / live 모드)
│   └── build_eval_dataset.py   # 평가 데이터셋 생성 (codereview / fromdb / silver)
│
└── tests/                      # 런타임(services/routers) 테스트 — DB·LLM은 전부 주입/mock
```

### RAG Runtime Data Flow

```
routers/analysis.py (POST /api/v1/analyze/pr) / routers/internal_analysis.py (POST /internal/v1/analyses)
    ▼ services/analysis_service.py  (PipelineContext로 각 단계 산출물 추적)
    ① PR Diff Embedding          → llamaindex/pipeline.get_embed_model() 재사용
    ② PGVector Similarity Search → services/retrieval.py (근거 충분성 신호 포함)
    ③ Context Filter Agent       → services/context_filter.py (FILTER_MODE=on|off|llm, Top-1 무조건 보존, 제거분도 반환)
    ④ Grounded Prompt            → services/prompt_builder.py (필터 통과 Context만 주입)
    ⑤ GPT-4o Structured Output   → JSON 강제, 파싱 실패 시 LLMResponseParseError
    ⑥ Confidence                 → services/confidence.py (검색 신호 기반, LLM 자기평가 금지)
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

# Internal API (Backend <-> AI) Config
INTERNAL_API_KEY=your_internal_api_key_here

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

## 🧪 테스트 & 평가 (Evaluation-Driven Development)

### 테스트
```bash
# 런타임 + 평가 테스트 (OpenAI/DB 호출 없음 — 전부 주입/mock)
pytest tests/ eval/tests/ -q
```

### Filter OFF vs ON 비교 리포트
`eval/` 은 `services/` 를 import하지 않는다. 실제 필터·신뢰도 로직을 평가에 연결하는 조립은
`scripts/eval_adapters.py` + `scripts/run_eval.py` 가 담당하며, `offline` 모드는 LLM·DB 없이 끝까지 실행된다.

```bash
# offline(기본): fixture 검색기 + 템플릿 생성기 + Offline Keyword Judge
#                필터·Confidence는 실제 services 로직을 주입한다
python -m scripts.run_eval

# 임계값을 높인 실행을 함께 비교 (필터가 gold를 지우는 상황 확인)
python -m scripts.run_eval --strict-threshold 0.9

# Confidence를 '평균 유사도' 취약 공식으로 바꿔 Fake Confidence 판정이 잡히는지 확인
python -m scripts.run_eval --strict-threshold 0.9 --vulnerable-confidence

# live 검색만: 실제 pgvector 검색·필터·Confidence 검증 (임베딩 비용만, GPT 미호출)
python -m scripts.run_eval --live --no-generation \
    --dataset eval/data/live_selfretrieval_cases.jsonl --strict-threshold 0.9

# live 전체: 실제 검색 + GPT-4o 생성 + LLM Judge (호출 비용 발생)
python -m scripts.run_eval --live --dataset eval/data/live_selfretrieval_cases.jsonl \
    --criteria faithfulness,completeness,answer_relevance

# JSON 리포트
python -m scripts.run_eval --json
```

### 평가 데이터 준비 (라이브 평가용 셋업 순서)

```bash
# 1) 벡터 데이터 서브셋 적재 (먼저 --dry-run 으로 건수·비용 추정)
python -m scripts.load_code_review_subset --limit 300 --dry-run
python -m scripts.load_code_review_subset --limit 300

# 2) ANN 인덱스 생성 (코사인 hnsw). 대량 적재는 "적재 후 인덱스 생성"이 빠르다
python -m scripts.create_vector_indexes --apply

# 3) 적재된 실제 id로 self-retrieval 평가 케이스 생성 (DB 조회만, LLM 미호출)
python -m scripts.build_eval_dataset fromdb --limit 10

# (선택) 외부 코드리뷰 JSONL → 평가 포맷. gold id가 cr-{index}라 live 평가엔 부적합
python -m scripts.build_eval_dataset codereview --limit 20

# (선택) 실제 검색 결과로 silver 라벨 생성 (회귀 비교 전용)
python -m scripts.build_eval_dataset silver --input eval/data/codereview_cases.jsonl
```

> gold chunk id는 `services.retrieval.make_review_chunk_id()` 한 곳에서만 정의한다.
> `fromdb` 데이터셋은 이 함수로 gold를 만들기 때문에 런타임 검색 결과와 그대로 맞물린다.
> 반면 `codereview` 데이터셋의 `cr-{index}`는 DB id와 무관하므로 `--live` 평가에 쓰면 지표가 0으로 나온다.

### 측정 지표

| 구분 | 지표 | 위치 |
| --- | --- | --- |
| 검색 | Precision@K, Recall@K, MRR, Hit Rate, Retrieval Failure Rate | `eval/metrics/retrieval.py` |
| 필터 | filter_ratio, gold_retained, false_deletion, recall_delta@K | `eval/metrics/filtering.py` |
| 생성 | Groundedness/Faithfulness, Completeness, Answer/Context Relevance, Hallucination, EM, F1 | `eval/metrics/generation.py` |
| 신뢰 신호 | Top Score, Evidence 수, Strong Evidence 수, Filter Ratio | `eval/metrics/retrieval_signals.py` |

### Fake Confidence 판정 규칙 (`eval/report.py`)

| 조건 | 판정 |
| --- | --- |
| Confidence↑ AND Precision↑ AND Faithfulness↑ AND recall_delta ≥ 0 AND false_deletion = 0 | 정상 개선 |
| Confidence↑ BUT (recall_delta < 0 OR false_deletion > 0) | ⚠ Fake Confidence — 필터 threshold 하향 권고 |
| filter_ratio > `FILTER_RATIO_WARN_THRESHOLD` | Confidence와 무관하게 "⚠ 검색 품질 확인 필요" |

---