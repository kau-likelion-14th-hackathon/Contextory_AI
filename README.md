# 🚀 Contextory AI Service

Contextory 프로젝트의 AI 파트 서비스입니다.  
FastAPI 기반으로 구축되어 있으며, GitHub PR 분석 및 LlamaIndex / PostgreSQL(`pgvector`) 기반의 RAG(검색 증강 생성) 파이프라인을 전담합니다.

---

## 🛠 Tech Stack & Environment

- **Framework:** FastAPI
- **Language:** Python 3.11
- **Container:** Docker / Docker Compose (권장 실행 방식)
- **ASGI Server:** Uvicorn
- **Database / Vector Store:** PostgreSQL 18 + `pgvector`
- **ORM / DB Driver:** SQLAlchemy 2.0, `psycopg2-binary`
- **Async Task Queue:** Celery + Redis (비동기 PR 분석 작업 처리)
- **Settings Management:** `pydantic-settings`
- **AI / RAG Framework:** LlamaIndex, OpenRouter (GPT-4o), OpenAI (`text-embedding-3-small`)

## 📁 Directory Structure

```text
AI_service/
├── main.py                     # FastAPI 앱 실행 및 라우터 연결
├── dependencies.py             # FastAPI Depends 주입 모듈 (DB 세션 생명주기 관리 등)
├── .env.example                # 환경 변수 템플릿
├── requirements.txt            # 의존성 패키지 목록
├── Dockerfile                  # app / celery_worker 공용 이미지 정의
├── docker-compose.yml          # postgres + redis + app + celery_worker 오케스트레이션
├── docker/
│   └── postgres/init.sql       # 컨테이너 최초 기동 시 pgvector 확장 자동 활성화
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
│   ├── job_store.py            # 비동기 작업 상태 저장소 (PostgreSQL ai_analysis_jobs 영속화 + 좀비 작업 정리)
│   └── repo_index_service.py   # (미사용) 레포 코드 raw SQL 인덱싱 — 현재 어떤 라우터에도 연결되지 않음
│
├── workers/                    # Celery 비동기 작업 워커
│   ├── celery_app.py           # Celery 앱 인스턴스 (Redis broker/backend)
│   └── tasks.py                # run_analysis_job task — PR 분석 실행 + job_store 갱신 + 콜백 전송
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
│   ├── datasets/                # ground_truth, loader(JSONL), codereview_adapters, silver_builder
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
    ⑤ GPT-4o Structured Output   → RecordDraftOutput(Pydantic)으로 JSON 스키마 강제,
                                   파싱 실패/거부 시 LLMResponseParseError
    ⑥ Confidence                 → services/confidence.py (검색 신호 기반, LLM 자기평가 금지)
```

`POST /internal/v1/analyses`(비동기 API)는 위 파이프라인을 **Celery task**(`workers/tasks.py:run_analysis_job`)로 실행한다. 요청을 받으면 즉시 `202 Accepted` + `jobId`를 반환하고, 실제 분석은 `celery_worker` 컨테이너가 Redis 큐에서 task를 꺼내 처리한 뒤 결과를 Backend `callbackUrl`로 콜백한다.

---

## 🚀 Getting Started (Docker — 권장)

이 프로젝트는 **PostgreSQL(pgvector) + Redis + FastAPI(app) + Celery(celery_worker)** 4개 컨테이너로 구성되며, Docker Compose로 한 번에 띄우는 것을 기본 실행 방식으로 한다. 로컬에 Postgres/Redis를 직접 설치할 필요가 없다.

### 1. Docker 설치

- **Windows / Mac:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) 설치 후 실행 (WSL2 백엔드 권장, Windows 기준)
- **Linux:** [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose plugin](https://docs.docker.com/compose/install/linux/) 설치
- 설치 확인:
  ```bash
  docker --version
  docker compose version
  ```

### 2. 환경 변수 설정 (.env)

`.env.example`을 복사해 `.env`를 만든다.

```bash
# Server Config
APP_ENV=development
LOG_LEVEL=INFO

# PostgreSQL (pgvector) DB Config
# docker-compose로 실행할 때는 POSTGRES_HOST를 컨테이너 서비스명(postgres)으로 둔다.
# (로컬에 uvicorn을 직접 띄워 Docker 없이 실행하려는 경우에만 localhost로 바꾼다)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=contextory_db

# Redis / Celery Config
# docker-compose 네트워크 안에서는 서비스명(redis)으로 접속한다.
REDIS_URL=redis://redis:6379/0

# OpenAI Config
OPENAI_API_KEY=your_openai_api_key_here

# Internal API (Backend <-> AI) Config
INTERNAL_API_KEY=your_internal_api_key_here

# 비동기 분석 작업(ai_analysis_jobs) 관리 정책 (선택, 미설정 시 기본값 사용)
# JOB_TIMEOUT_MINUTES: PROCESSING이 이 시간을 넘기면 좀비로 보고 FAILED 처리 (기본 30)
# JOB_RETENTION_DAYS: 종료된 작업 행 보존 기간. 0이면 자동 삭제 안 함 (기본 0)
JOB_TIMEOUT_MINUTES=30
JOB_RETENTION_DAYS=0

# Google API Config
GOOGLE_API_KEY=your_google_api_key_here
GOOGLE_SEARCH_ENGINE_ID=your_google_search_engine_id_here
```

> `.env`는 `.dockerignore`에 의해 이미지에 절대 포함되지 않는다 — `docker-compose.yml`이 `env_file: .env`로 컨테이너 실행 시점에 환경변수로만 주입한다.

### 3. 전체 스택 기동

저장소 루트(`docker-compose.yml`이 있는 위치)에서:

```bash
docker compose up -d --build
```

최초 실행 시 자동으로 일어나는 일:

1. `pgvector/pgvector:pg18` 이미지로 `postgres` 컨테이너가 뜨고, `docker/postgres/init.sql`이 실행되어 `CREATE EXTENSION IF NOT EXISTS vector;`가 자동 적용된다 (README 예전 버전에 있던 "pgvector 수동 설치/확장 활성화" 단계가 더 이상 필요 없다).
2. `redis` 컨테이너가 뜬다 (Celery broker/backend).
3. `postgres`/`redis`가 healthy 상태가 되면(healthcheck 통과) `app`(FastAPI/uvicorn, 8000 포트) 컨테이너가 뜬다. 기동 시 `main.py`의 `lifespan`이 `ai_analysis_jobs` 테이블을 자동 생성한다.
4. 같은 이미지로 `celery_worker` 컨테이너가 뜨고, `run_analysis_job` task를 Redis 큐에서 대기한다.

기동 확인:

```bash
docker compose ps
# 4개 서비스(postgres, redis, app, celery_worker) 모두 STATUS가 healthy/Up 인지 확인

curl http://localhost:8000/health
# {"status":"ok","service":"Contextory AI Engine"}
```

`docker-compose.yml`의 4개 서비스 모두 `restart: unless-stopped`로 설정되어 있어, 컨테이너가 죽거나 PC/Docker Desktop이 재시작되어도 자동으로 다시 뜬다.

### 4. 컨테이너 관리 명령어

```bash
# 전체 기동
docker compose up -d

# 코드 수정 후 반영 (이미지 재빌드 + 재기동)
docker compose up -d --build

# 상태 확인
docker compose ps

# 실시간 로그 (PowerShell로 uvicorn 직접 실행할 때 보이던 콘솔 로그와 동일)
docker compose logs -f app              # API 요청 로그
docker compose logs -f celery_worker    # PR 분석 처리 로그 (성공/실패, 콜백 결과)
docker compose logs -f                  # 전체 서비스 로그

# 최근 N줄만
docker compose logs --tail 50 app

# 리소스 사용량 확인 (컨테이너별 CPU/메모리 상한은 별도 설정하지 않음 — Docker Desktop VM 전체 자원을 공유)
docker stats

# 특정 서비스만 재시작
docker compose restart app
docker compose restart celery_worker

# 잠깐 멈추기 (DB 데이터는 volume에 남아있으므로 유지됨)
docker compose down
```

> ⚠️ **`docker compose down -v`는 절대 습관적으로 쓰지 않는다.** `-v`는 `pgdata`(Postgres 데이터 볼륨)까지 삭제해 DB의 모든 데이터가 사라진다. 정말 초기화가 필요할 때만 신중하게 사용한다.

### 5. DB 직접 조회 (DataGrip 등 GUI 클라이언트)

`postgres` 컨테이너는 호스트 포트로 publish되어 있어 DataGrip/psql 등으로 직접 접속할 수 있다 (컨테이너 안에 들어가서 확인하는 게 정석은 아니다).

| 항목 | 값 |
|---|---|
| Host | `localhost` |
| Port | `docker-compose.yml`의 `postgres.ports`에 매핑된 값 (기본 `5432`; 로컬에 별도 Postgres가 이미 5432를 쓰고 있다면 충돌을 피해 다른 포트로 바꿔서 매핑) |
| Database | `.env`의 `POSTGRES_DB` (기본 `contextory_db`) — **반드시 정확히 이 값으로 지정**해야 한다. DataGrip이 자동으로 만들어주는 데이터소스 이름(`{database}@{host}`)에 이끌려 Database 필드에 임의의 라벨을 입력하면 존재하지 않는 DB를 가리켜 스키마가 비어 보인다 |
| User / Password | `.env`의 `POSTGRES_USER` / `POSTGRES_PASSWORD` |

연결 후 `public` 스키마가 비어 보이면: 데이터소스 우클릭 → `Refresh`(F5) 또는 `Schemas...`에서 `public` 체크 여부 확인.

### 6. (참고) Docker 없이 로컬에서 직접 실행하는 경우

Docker를 쓰지 않고 예전처럼 `uvicorn`을 직접 띄우는 것도 가능하지만, Redis + Celery 도입 이후로는 아래 셋을 **각각 별도 프로세스로** 띄워야 `POST /internal/v1/analyses`(비동기 분석 API)가 정상 동작한다. Redis/Celery worker 없이 uvicorn만 띄우면 요청은 `202`로 접수되지만 분석이 영원히 처리되지 않다가 타임아웃으로 실패 처리된다(`GET /api/v1/analyze/pr` 같은 동기 API나 GET/maintenance 엔드포인트는 영향 없음).

```bash
# 1) PostgreSQL(pgvector 확장 활성화된 상태)이 로컬에 떠 있어야 함
#    .env의 POSTGRES_HOST=localhost로 변경

# 2) Redis 로컬 설치/실행 (예: choco install redis-64, 또는 WSL/별도 컨테이너)
#    .env의 REDIS_URL=redis://localhost:6379/0 로 변경

# 3) 패키지 설치
pip install -r requirements.txt

# 4) Celery worker 실행 (별도 터미널)
celery -A workers.celery_app.celery_app worker --loglevel=info

# 5) FastAPI 서버 실행 (또 다른 터미널)
uvicorn main:app --reload
```

---

## 🧪 테스트 & 평가 (Evaluation-Driven Development)

### 테스트
```bash
# 런타임 + 평가 테스트 (OpenAI/DB/Redis 호출 없음 — 전부 주입/mock)
pytest tests/ eval/tests/ -q
```

`tests/test_worker_tasks.py`는 Celery task(`workers.tasks.run_analysis_job`)를 `.delay()` 없이 직접 동기 호출해 검증하므로, Redis/Docker가 없어도 그대로 통과한다.

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

### 프로젝트 메타 · 레포 코드 인덱싱 (백엔드 작업 없이 운영)

```bash
# 1) 프로젝트 정보: project.yml 에 저장소별 이름/목적/주요 기능/팀 역할을 적는다
#    (요청에 프로젝트 정보가 없어도 AI가 여기서 찾아 프롬프트에 채운다)

# 2) 프로젝트 코드 인덱싱 — 검색 근거가 되는 컨텍스트를 채운다
python -m scripts.index_repo_code --path . \
    --repo-name kau-likelion-14th-hackathon/Contextory_AI --dry-run   # 대상·비용 추정
python -m scripts.index_repo_code --path . \
    --repo-name kau-likelion-14th-hackathon/Contextory_AI             # 실제 적재
```

> `--repo-name` 은 검색 격리 키다. 분석 요청의 `repo_name` / `repositoryFullName` 과 정확히 같아야 검색된다.

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

### 분석 결과 출력 스키마 (프론트 표시 항목 ↔ JSON 키)

`services/prompt_builder.py` 의 `RecordDraftOutput` 이 프롬프트의 출력 스키마와 1:1로 대응하며,
GPT 호출 시 이 모델로 JSON 스키마를 강제해 그대로 파싱한다(자유 텍스트 후처리 없음).

| 프론트 표시 항목 | JSON 키 | 타입 |
| --- | --- | --- |
| 작업 요약 | `summary` | string |
| 작업 목적 | `purpose` | string (근거 없으면 `"확인 필요"`) |
| 변경 이유 | `changeReason` | string (근거 없으면 `"확인 필요"`) |
| 변경 전 / 변경 후 | `before` / `after` | string |
| 관련 기능 | `relatedFeatures` | string[] |
| 영향받는 역할 | `affectedRoles` | string[] (아래 7개 값만) |
| 역할별 영향 | `roleImpacts` | `{role, impact, basis, evidenceRefs}[]` |
| 확인 필요 사항 | `needsConfirmation` | string[] |
| 분석 근거 | `evidence` | `{id, source, location, description}[]` |

- `affectedRoles` 허용 값: 프론트엔드, 백엔드, AI, 기획, 디자인, QA, 프로젝트 관리자
  (허용 목록 밖 역할은 `services/analysis_service._allowed_roles` 에서 걸러낸다)
- `roleImpacts[].basis`: `"확인된 사실"`(diff에서 직접 확인) 또는 `"변경 기반 예상"`(추론)
- `roleImpacts[].evidenceRefs` → `evidence[].id` 참조 (프론트 "이 영향의 근거 보기")
- `evidence[].source`: `pr_diff`(현재 PR) 또는 `context`(검색된 기존 컨텍스트)
- diff가 비어 있거나 검색 근거가 부족하면 **LLM을 호출하지 않고** `needsConfirmation` 경로로 응답한다

### Fake Confidence 판정 규칙 (`eval/report.py`)

| 조건 | 판정 |
| --- | --- |
| Confidence↑ AND Precision↑ AND Faithfulness↑ AND recall_delta ≥ 0 AND false_deletion = 0 | 정상 개선 |
| Confidence↑ BUT (recall_delta < 0 OR false_deletion > 0) | ⚠ Fake Confidence — 필터 threshold 하향 권고 |
| filter_ratio > `FILTER_RATIO_WARN_THRESHOLD` | Confidence와 무관하게 "⚠ 검색 품질 확인 필요" |

---
