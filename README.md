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
│   ├── pipeline.py             # repo_code_vectors(data_repo_code_vectors) Upsert/Delete 인덱싱
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

AI 서버는 **루프백(`127.0.0.1`)에만 바인딩**한다. 인터넷에 직접 노출하지 않으며,
백엔드(AWS)는 SSH 터널을 통해서만 접근한다.

```bash
# 로컬 개발 (코드 변경 시 자동 리로드)
uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# 서버 구동 (운영)
uvicorn main:app --host 127.0.0.1 --port 8000
```

> ⚠️ `--host 0.0.0.0` 으로 실행하지 않는다. 모든 네트워크 인터페이스에 바인딩되어
> 인터넷 전체에 노출되고, 외부 스캐닝 봇이 `/mcp`, `/sse` 등 존재하지 않는 경로를
> 두드리며 404 로그가 계속 쌓인다. (404 응답 자체는 정상 동작이며, 라우트를 추가해
> 없앨 문제가 아니라 노출을 막아야 하는 문제다.)
>
> 이 서버에는 `/internal/v1/...` 내부 API가 포함되어 있다. `X-Internal-Api-Key`로
> 보호되지만, 애초에 외부에서 도달할 수 없게 두는 것이 우선이다.

#### 헬스 체크 (서버 호스트에서)
```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","database":"connected"}
```

#### 백엔드에서의 접근 (SSH 터널)
백엔드 서버에서 아래로 터널을 연 뒤, `http://localhost:8000` 으로 호출한다.

```bash
# 백엔드 서버에서 실행 (AI 서버의 8000 포트를 로컬 8000으로 포워딩)
ssh -N -L 8000:localhost:8000 <user>@<ai-server-host>

# 터널이 열린 상태에서 (백엔드 서버의 다른 셸)
curl http://localhost:8000/health
```

#### 외부 노출 차단 확인
```bash
# 서버의 공인 IP로는 접속되지 않아야 한다 (timeout 또는 connection refused가 정상)
curl --max-time 5 http://<서버-공인-IP>:8000/health

# 8000 포트가 127.0.0.1 에만 열려 있는지 확인 (0.0.0.0:8000 이 보이면 안 된다)
ss -tlnp | grep 8000     # 또는: lsof -nP -iTCP:8000 -sTCP:LISTEN
```

> Docker로 구동하는 경우: 컨테이너 내부에서는 `--host 0.0.0.0` 이 필요하지만,
> 호스트에 publish할 때 `-p 127.0.0.1:8000:8000` 처럼 루프백에만 매핑한다.
> `-p 8000:8000` 은 모든 인터페이스에 노출되므로 사용하지 않는다.

#### 공개 도메인(`https://ai.contextory.org`) 유지

루프백 바인딩과 공개 도메인은 **양립한다.** 앞단(Cloudflare → 리버스 프록시 또는
Cloudflare Tunnel)이 **같은 호스트의 루프백으로** 앱에 접속하면 되기 때문이다.

```
인터넷 → Cloudflare(443) → [같은 호스트의 nginx 또는 cloudflared] → 127.0.0.1:8000 (uvicorn)
                                                                     ▲ 외부에서 직접 접근 불가
```

Cloudflare 프록시는 오리진에 **80/443 포트로** 접속하므로, 도메인이 동작해 왔다면
오리진 호스트에는 이미 리버스 프록시나 터널이 떠 있다. 따라서 앱을 `127.0.0.1`로
내려도 도메인은 그대로 동작한다. 단, 앞단이 앱을 가리키는 주소가 **루프백**이어야 한다.

**nginx를 쓰는 경우** — `proxy_pass` 대상이 공인 IP가 아닌 루프백인지 확인한다.
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;   # ✅ (http://<공인IP>:8000 이면 끊긴다)
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

**Cloudflare Tunnel(`cloudflared`)을 쓰는 경우** — `service`가 루프백인지 확인한다.
```yaml
ingress:
  - hostname: ai.contextory.org
    service: http://127.0.0.1:8000   # ✅
  - service: http_status:404
```

**적용 전 현재 방식 확인** (AI 서버 호스트에서)
```bash
systemctl status cloudflared 2>/dev/null || ps aux | grep -i "[c]loudflared"
sudo ss -tlnp | grep -E ':(80|443)\b'      # nginx/caddy/cloudflared 중 무엇이 잡고 있는지
grep -rn "proxy_pass" /etc/nginx/ 2>/dev/null
```

80/443을 잡은 프로세스가 없고 Cloudflare가 8000 포트로 직접 붙는 구성이라면,
**루프백 바인딩 전에** 위 둘 중 하나를 먼저 세워야 도메인이 끊기지 않는다.

---