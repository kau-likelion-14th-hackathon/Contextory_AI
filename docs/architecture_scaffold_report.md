# AI 파트 레이어드 아키텍처 스캐폴딩 결과 보고서

- 작성일: 2026-08-03
- 대상 저장소: `kau-likelion-14th-hackathon/Contextory_AI` (branch: `feat/#7huggingface데이터셋`)
- 작업 범위: 레이어드 구조 스캐폴딩 → 도메인 모델·상태 전이 정의 → 앱 기동 및 API 문서 확인
- 관련 문서: `docs/architecture.md`, `docs/api.md`, `docs/rag-pipeline.md`
- 작성자: TODO
- 기여자: TODO

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 아키텍처 | 레이어드 (Router → Service → Repository → Store) 스캐폴딩 완료 |
| 신규 파일 | **68개** (계층 코드·설정·문서. 이 보고서 2개 제외) |
| 수정 파일 | **7개** (전부 빈 파일이거나 요청된 변경) |
| 모듈 import | **46/46 성공**, 순환 참조 없음 |
| API 경로 등록 | **15개** / 스키마 **27개** |
| 앱 기동 | **성공** — `/health` 200, Swagger UI 200 |
| 스텁 | **31개 파일 / 80곳** `NotImplementedError` |
| 기존 코드 변경 | `routers/health.py`, `README.md` **무수정** |

**가장 중요한 제약**: Contextory 의 Spring Boot 백엔드 저장소를 확인할 수 없어 **공유 테이블명·컬럼명을 검증하지 못했습니다.** ORM 은 기획서 기준 논리 모델로 작성했고 모든 `__tablename__` 에 `# 확인 필요` 를 달았습니다.

---

## 2. 사전 조사 결과

### 2.1 저장소 구조

| 조사 대상 | 실제 상태 |
| --- | --- |
| AI 파트 루트 | **저장소 루트 자체** (`AI_service/` 디렉터리 없음) |
| `core/`, `models/`, `routers/`, `services/` | 존재하나 **전부 빈 `__init__.py`** → 재사용 |
| `core/config.py`, `dependencies.py`, `requirements.txt`, `.env.example`, `Dockerfile` | **전부 0 bytes 빈 파일** |
| `routers/health.py`, `main.py` | 유일하게 구현된 파일 |
| `llamaindex/`, `repositories/`, `tasks/`, `utils/`, `tests/` | **없음** → 신규 생성 |
| 기존 예외 처리·응답 포맷·로깅 컨벤션 | **없음** (따를 기존 규약이 없어 새로 정의) |
| `docs/`, `scripts/`, `prompts/` | 이전 작업 산출물 존재 → **덮어쓰지 않고 추가만** |

### 2.2 공유 스키마 — **확인 불가**

조직(`kau-likelion-14th-hackathon`)의 공개 저장소는 3개이며, 그중 `OffCourse_BackEnd`(Java)는 **Contextory 와 무관한 다른 프로젝트**였습니다.

```
패키지 : com.kbj.OffCourse
도메인 : login(카카오 로그인), transit(서울 교통), user
엔티티 : BaseEntity, RefreshToken, User
```

Project·PullRequest·ProjectRecord 같은 Contextory 도메인이 전혀 없습니다.
Contextory 의 백엔드는 이 조직에 공개되어 있지 않거나 비공개입니다.

---

## 3. 전제와 실제가 다른 부분

| 전제 | 실제 | 대응 |
| --- | --- | --- |
| AI 파트 루트 `AI_service/` | 저장소 루트 자체 | 경로에서 `AI_service/` 접두사 제거 |
| 기존 LlamaIndex 파이프라인 재사용 | `llamaindex/` 디렉터리 없음 | 신규 생성. 재사용할 코드 없음 |
| Spring Boot 스키마에 정확히 매핑 | 백엔드 저장소 확인 불가 | 논리 모델로 작성 + `# 확인 필요` 표시 |
| 기존 예외/응답 포맷 컨벤션 준수 | 컨벤션 자체가 없음 | 새로 정의 (백엔드 규약 확인 시 교체) |

**보류한 것**: 공유 테이블명·컬럼명·enum 저장 형식·삭제 정책. 임의로 확정하지 않았습니다.
**진행한 것**: 그 외 전부. 스키마 소유권 원칙(매핑 전용, DDL 금지)은 그대로 지켰습니다.

---

## 4. 생성·수정한 파일

### 4.1 수정 (7개)

| 파일 | 이유 |
| --- | --- |
| `main.py` | 라우터 5개 include + 예외 핸들러 등록 (기존 health·read_root 보존) |
| `dependencies.py` | 빈 파일 → 세션 주입·서비스 조립 |
| `core/config.py` | 빈 파일 → 환경 변수 설정 |
| `requirements.txt` | 빈 파일 → 의존성 명시 |
| `.env.example` | 빈 파일 → 환경 변수 템플릿 |
| `Dockerfile` | 빈 파일 → 컨테이너 정의 |
| `.gitignore` | 이전 작업의 로컬 테스트 제외 (이번 작업 무관) |

### 4.2 신규 (계층별)

| 계층 | 파일 수 | 내용 |
| --- | --- | --- |
| `core/` | 5 | config, logging, exceptions, constants, database |
| `models/` | 5 | enums, orm_models, schemas, firestore_models, llamaindex_models |
| `repositories/` | 8 | base + user, project, project_member, github_connection, pull_request, project_record, follow_up_task |
| `services/` | 8 | llm_client, firebase, pr_analysis, project_record, user, document, search, chat |
| `routers/` | 6 | health(기존) + pr, users, documents, search, chat |
| `llamaindex/` | 8 | ingestion(3), index_manager(3), retriever(1), prompts(1) |
| `tasks/` | 2 | celery_app, ingest_tasks |
| `utils/` | 2 | helpers, validators |
| `tests/` | 3 | unit(2), integration(1) |
| 루트 | 4 | firebase_admin.py, pyproject.toml, docker-compose.yml + `__init__.py` 다수 |
| `docs/` | 3 | architecture.md, api.md, rag-pipeline.md |
| `scripts/` | 4 | ingest_documents, recreate_index, check_vectorstore, seed_sample_data |

**`migrations/` 는 생성하지 않았습니다.** 스키마 소유권이 백엔드에 있기 때문입니다.

---

## 5. 계층 매핑

```
Router      FastAPI APIRouter, DTO 검증, 예외 → HTTP 변환
 → Service  유스케이스, 트랜잭션 경계, 상태 전이 검증, LLM/RAG 오케스트레이션
   → Repository  SQLAlchemy 세션 CRUD/쿼리
     → Store   PostgreSQL(ORM) / Firestore / pgvector(LlamaIndex)
```

| 도메인 | Router | Service | Repository | ORM |
| --- | --- | --- | --- | --- |
| PR 분석 | `pr.py` | `pr_analysis.py` | `pull_request.py`, `project_record.py` | `PullRequest`, `PullRequestFile` |
| 기록/승인 | `pr.py` | `project_record.py` | `project_record.py`, `follow_up_task.py` | `ProjectRecord`, `RecordIssue`, `RoleImpact`, `FollowUpTask` |
| 사용자 | `users.py` | `user.py` | `user.py`, `project_member.py` | `User`, `ProjectMember`, `ProjectMemberRole` |
| 문서 | `documents.py` | `document.py` | — (Firestore) | `FirestoreDocument`, `FirestoreChunk` |
| 검색 | `search.py` | `search.py` | — (pgvector) | `RetrievalRequest/Result` |
| 채팅 | `chat.py` | `chat.py` | — (Firestore) | `FirestoreChat`, `FirestoreMessage` |

**의존 방향 검사 결과 위반 0건**입니다. `core`/`models` 가 상위 계층을 참조하지 않고, `repositories` 가 `services`/`routers` 를 참조하지 않습니다.

### 5.1 벡터 스토어 추상화

`llamaindex/index_manager/vector_store.py` 에 `VectorStoreAdapter` 프로토콜을 두고 `PgVectorAdapter` 를 그 뒤로 감췄습니다. Qdrant 등으로 교체할 때 `get_vector_store_adapter()` 만 바꾸면 됩니다.

---

## 6. Enum 과 상태 전이

7개 enum 을 정의했습니다: `Role`, `PrAnalysisStatus`, `RecordType`, `ApprovalStatus`, `FollowUpStatus`, `Severity`, `GithubConnectionStatus`.

**전이 규칙을 자료구조로 명시**해 Service 가 검증하도록 했습니다.

```python
PR_ANALYSIS_TRANSITIONS = {
    PENDING:      {ANALYZING},
    ANALYZING:    {NEEDS_REVIEW, FAILED},
    NEEDS_REVIEW: {APPROVED, ANALYZING},   # 재분석 허용
    FAILED:       {ANALYZING},
    APPROVED:     {ANALYZING},             # 재분석 시 승인 기록 보존
}
APPROVAL_TRANSITIONS  = { DRAFT: {APPROVED, DISCARDED}, APPROVED: {}, DISCARDED: {} }
FOLLOW_UP_TRANSITIONS = { NEEDS_ACTION: {DONE}, DONE: {} }
```

`assert_pr_transition()`, `assert_approval_transition()`, `assert_follow_up_transition()` 이 위반 시 `InvalidStateTransitionError`(HTTP 409)를 던집니다.

### 6.1 평가 데이터셋과의 정합성

이전 작업의 기준 데이터셋(`data/test_samples.json`)과 교차 검증했습니다.

```
Severity  데이터셋 {high, low, medium}  ⊆  enum {critical, high, low, medium}   ✓
Role      데이터셋 {backend, design, frontend, planning, project_manager, qa}  ⊆  enum  ✓
```

---

## 7. 실행 검증

### 7.1 정적 검증

| 항목 | 결과 |
| --- | --- |
| 모듈 import | **46/46 성공** |
| 순환 import | **없음** |
| 계층 의존 방향 | **위반 0건** |
| `migrations/` 미생성 | 확인 |
| `create_all()` 호출 | **없음** (금지 안내 주석만 존재) |
| 시크릿 하드코딩 | **없음** |
| `.env` 제외 / `.env.example` 추적 | 정상 |

### 7.2 앱 기동

```
$ DATABASE_URL="sqlite:///:memory:" uvicorn main:app --host 127.0.0.1 --port 8000

INFO:     Started server process [85441]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     127.0.0.1:58499 - "GET /health HTTP/1.1" 200 OK
```

`DATABASE_URL` 은 화면 확인용 임시값입니다. 실제 운영은 PostgreSQL 이며 DB 연결은 lazy 라 문서 확인에는 영향이 없습니다.

### 7.3 API 문서

| 화면 | 응답 |
| --- | --- |
| Swagger UI `/docs` | **HTTP 200** (1,020 bytes) |
| ReDoc `/redoc` | **HTTP 200** (902 bytes) |
| OpenAPI `/openapi.json` | 경로 **15개**, 스키마 **27개** |

등록된 경로:

```
▸ Health          GET  /health
▸ Users           GET  /users/me
                  GET  /projects/{project_id}/members
▸ Pull Request    POST /projects/{project_id}/pull-requests/{pr_id}/analyze
                  GET  /projects/{project_id}/pull-requests/{pr_id}
                  GET  /projects/{project_id}/records
                  PATCH /projects/{project_id}/records/{record_id}/approval
                  PATCH /projects/{project_id}/follow-up-tasks/{task_id}
▸ Documents       GET  /projects/{project_id}/documents
                  GET  /projects/{project_id}/documents/{document_id}
                  POST /projects/{project_id}/documents/ingest
▸ Search          POST /search
▸ Chat            POST /chats
                  GET  /chats/{chat_id}/messages
```

### 7.4 엔드포인트 실제 호출

```
GET  /health          → 200  {"status":"ok","message":"Contextory AI Service is running"}
GET  /                → 200  {"message":"Welcome to Contextory AI API"}
GET  /users/me        → 403  {"code":"PERMISSION_DENIED","message":"사용자 식별 정보가 없습니다."}
POST /search          → 422  {"detail":[{"type":"missing","loc":["body","project_id"],...}]}
POST /.../analyze     → 500  (스텁 NotImplementedError)
```

세 가지가 실제로 동작함을 확인했습니다.

- **403** — 인증 헤더 없이 호출하니 `PermissionDeniedError` 가 예외 핸들러를 통해 규정된 포맷으로 변환되었습니다.
- **422** — Pydantic DTO 검증이 `project_id` 누락을 잡았습니다.
- **500** — 라우팅과 의존성 주입을 통과해 Service 스텁까지 도달했다는 뜻입니다(예상된 결과).

> 브라우저 스크린샷 도구(`chromium-cli`, `playwright`)가 이 환경에 없어 이미지 대신 OpenAPI 스펙을 렌더링해 확인했습니다.

---

## 8. 의존성

`requirements.txt` 에 명시한 항목입니다.

```
fastapi                              sqlalchemy>=2
uvicorn[standard]                    psycopg[binary]
pydantic>=2                          llama-index-core
llama-index-vector-stores-postgres   firebase-admin
celery[redis]                        datasets / huggingface_hub
```

검증 목적으로 `.venv` 에 `fastapi`, `sqlalchemy`, `uvicorn[standard]`, `httpx` 를 설치했습니다.

---

## 9. 미구현(스텁) 목록

**31개 파일 / 80곳**이 `NotImplementedError` 로 표시되어 있습니다.

| 영역 | 상태 |
| --- | --- |
| Repository 7개 전체 | 시그니처만 |
| Service 8개 전체 | 시그니처 + 절차 주석 |
| LlamaIndex 8개 | 시그니처만 |
| tasks 2개, utils 2개 | 시그니처만 |
| scripts 4개 | argparse 골격만 |
| tests | 전이 규칙 2건은 실제 검증, 나머지는 `@pytest.mark.skip` |

`PrAnalysisService.analyze()` 등 핵심 유스케이스는 구현 절차를 주석으로 남겨 두었습니다.

---

## 10. 확인 필요 사항

우선순위 순입니다.

1. **공유 테이블명·컬럼명** — 가장 중요합니다. 백엔드 스키마 확인 후 `orm_models.py` 의 `__tablename__` 을 실제 값으로 교체해야 합니다. 그전까지 DB 연동 코드를 작성해도 동작하지 않습니다.
2. **enum 저장 형식** — 문자열로 가정했습니다. 백엔드가 native enum 이나 ordinal(int)을 쓰면 컬럼 타입을 맞춰야 합니다.
3. **삭제 정책(cascade)** — 임의 확정하지 않고 FK 만 걸었습니다.
4. **인증 방식** — Spring Boot 가 검증 후 헤더로 전달하는지, AI 서비스가 JWT 를 직접 검증하는지. 현재는 `X-User-Id` 헤더만 읽습니다.
5. **응답 포맷** — 백엔드의 `ApiResponse` 규약을 따라야 하면 `core/exceptions.py` 의 `to_response()` 를 맞춰야 합니다.
6. **SSE 발신 주체** — 백엔드일 가능성이 높아 인터페이스도 만들지 않았습니다.

---

## 11. 준수 사항 확인

| 항목 | 결과 |
| --- | --- |
| 기존 코드 임의 변경 | **없음** (`routers/health.py`, `README.md` 무수정) |
| `main.py` 변경 | 지시 범위(라우터 include·예외 핸들러)만, 기존 코드 보존 |
| 없는 함수·클래스·테이블 가정 | **없음** (확인 불가 항목은 `# 확인 필요` 로 표시) |
| DDL / 마이그레이션 생성 | **없음** (`migrations/` 미생성, `create_all()` 미호출) |
| API Key / DB 접속 정보 하드코딩 | **없음** (환경 변수로만 읽음) |
| 실행하지 않은 것을 실행했다고 기재 | **없음** |
| Git 커밋 / 푸시 / 태그 / 머지 | **미수행** |
| 산출물 내 작성자·AI 표기 | **검출 0건** |

---

## 12. 다음 단계 제안

1. **백엔드 스키마 확보** → `orm_models.py` 의 테이블명·컬럼명 교체 (10절 1~3번)
2. **Repository 구현** → 스키마 확정 후 CRUD 채우기
3. **`PrAnalysisService.analyze()` 구현** → 이미 만들어 둔 프롬프트 3종과 평가 데이터셋 32건으로 검증 가능
4. **인증 방식 합의** → `dependencies.get_current_user_id` 교체

3번은 이전 작업의 산출물(`prompts/`, `data/test_samples.json`, `scripts/evaluate_results.py`)을 그대로 쓸 수 있어 백엔드 의존 없이 먼저 진행할 수 있습니다.

---

## 13. 추천 커밋 메시지

```
feat(ai): add layered architecture skeleton for PR analysis service

- Add core layer: config, logging, exceptions, constants, database session
- Add SQLAlchemy ORM models mapped to backend-owned tables (no DDL)
- Add Pydantic DTOs and domain enums with state transition rules
- Add repository, service, and router layers with dependency injection
- Add LlamaIndex ingestion/index/retriever with vector store abstraction
- Add Celery task stubs, utils, tests, and architecture docs

Table and column names follow the planning document and must be verified
against the backend schema before use.
```
