# Contextory AI 서버 연동 계약 (프론트엔드 · 백엔드)

이 문서는 AI 서버(`Contextory_AI`)가 제공하는 API와 응답 스키마의 **현재 구현 상태**를 정리한다.
코드가 곧 기준이므로, 값이 궁금하면 아래 파일을 보면 된다.

| 관심사 | 파일 |
| --- | --- |
| 요청·응답 DTO | `models/schemas.py` |
| LLM 출력 스키마(프롬프트와 1:1) | `services/prompt_builder.py` (`OUTPUT_SCHEMA_SECTION`, `RecordDraftOutput`) |
| 엔드포인트·인증 | `routers/*.py`, `core/security.py` |
| 임계값·모델·가중치 설정 | `core/config.py`, `.env.example` |

---

## 1. 엔드포인트 요약

| 메서드 | 경로 | 인증 | 용도 |
| --- | --- | --- | --- |
| GET | `/health` | ❌ | liveness (DB 보지 않음, 컨테이너 헬스체크용) |
| GET | `/api/v1/health` | ✅ | readiness (DB 연결 상태 포함) |
| POST | `/api/v1/analyze/pr` | ✅ | 동기 PR 분석 — **현재 백엔드 미사용**(10장 참고). 동작은 정상이며 존속 여부는 협의 대상 |
| POST | `/api/v1/repos/index` | ✅ | 레포 코드 인덱싱 (Upsert + 삭제 파일 정리) |
| POST | `/internal/v1/analyses` | ✅ | **비동기 PR 분석 요청 → 202 + jobId, 완료 시 콜백** |
| GET | `/internal/v1/analyses/{jobId}` | ✅ | 작업 상태 조회 (PostgreSQL 영속) |
| POST | `/internal/v1/analyses/maintenance` | ✅ | 좀비 작업 정리 / 보존기간 초과 삭제 |

### 인증

`/api/v1`, `/internal/v1` 하위 **모든** 엔드포인트는 헤더가 필요하다.

```
X-Internal-Api-Key: <INTERNAL_API_KEY>
```

- `hmac.compare_digest`로 비교하므로 값이 정확히 일치해야 한다 (`core/security.py`)
- 서버에 `INTERNAL_API_KEY`가 설정되지 않으면 **모든 요청을 401로 거부**한다 (빈 키 우회 방지)
- 실패 시 `401 {"detail": "내부 API 인증 실패"}`

---

## 2. 비동기 분석 흐름 (권장 경로)

```
Spring Boot                              AI 서버
    │  POST /internal/v1/analyses            │
    ├───────────────────────────────────────►│  diff 비어 있으면 400
    │◄──────── 202 {jobId, status} ──────────┤
    │                                        │  ① 임베딩 ② pgvector 검색 ③ Context Filter
    │                                        │  ④ Grounded Prompt ⑤ GPT 구조화 출력 ⑥ Confidence
    │◄──── POST {callbackUrl} (COMPLETED) ───┤  실패해도 FAILED로 반드시 콜백
    │                                        │
    │  GET /internal/v1/analyses/{jobId}     │  콜백 유실 시 상태 조회로 복구 가능
    └───────────────────────────────────────►│
```

- 콜백 전송은 **최대 3회, 지수 백오프(1s, 2s)** 재시도한다 (`services/callback_service.py`)
- 콜백 전송이 실패해도 `ai_analysis_jobs`에는 최종 상태가 기록되므로 `GET`으로 복구 가능하다
- 콜백 요청에도 `X-Internal-Api-Key` 헤더가 실린다

---

## 3. 콜백 payload (프론트가 최종적으로 받는 형태)

`POST {callbackUrl}` 바디는 `{jobId, status, modelName, result, errorMessage, completedAt}` 이고,
`result`가 아래 형태다. **camelCase**로 직렬화된다.

```json
{
  "summary": "로그인 API의 오류 응답 구조를 message 기반에서 errorCode 기반으로 변경했다.",
  "purpose": "오류 유형을 코드로 구분해 클라이언트가 오류별 처리를 할 수 있게 하기 위함",
  "changeReason": "확인 필요",
  "before": "로그인 실패 시 message 문자열로만 오류를 반환했다.",
  "after": "로그인 실패 시 errorCode 필드로 오류 유형을 반환한다.",
  "relatedFeatures": ["로그인", "오류 처리"],
  "affectedRoles": ["프론트엔드", "QA", "기획"],
  "roleImpacts": [
    {
      "role": "프론트엔드",
      "impact": "message 기반 오류 분기를 errorCode 기반으로 수정해야 한다.",
      "basis": "변경 기반 예상",
      "evidenceRefs": ["e1"]
    }
  ],
  "followUpTasks": ["오류 코드별 사용자 안내 문구 정의"],
  "needsConfirmation": ["변경 이유가 PR 본문에 없음 — PR 작성자에게 확인 필요"],
  "evidence": [
    {
      "id": "e1",
      "source": "pr_diff",
      "location": "src/main/java/auth/LoginResponse.java",
      "description": "errorCode 필드 추가",
      "chunkId": null,
      "similarityScore": null
    },
    {
      "id": "e2",
      "source": "context",
      "location": "cr-12",
      "description": "기존 오류 처리 정책(과거 기록)",
      "chunkId": "cr-12",
      "similarityScore": 0.83
    }
  ],
  "confidence": 0.6483,
  "retrievalQualityWarning": false,

  // 기존 계약 유지용 (새 스키마를 투영해 채운다)
  "changes": [{ "filePath": "src/main/java/auth/LoginResponse.java", "description": "errorCode 필드 추가" }],
  "impacts": ["프론트엔드: message 기반 오류 분기를 errorCode 기반으로 수정해야 한다."],
  "recommendations": ["오류 코드별 사용자 안내 문구 정의"],
  "risks": []
}
```

### 프론트 표시 항목 ↔ JSON 키

| 프론트 표시 항목 | JSON 키 | 타입 |
| --- | --- | --- |
| 작업 요약 | `summary` | string |
| 작업 목적 | `purpose` | string |
| 변경 이유 | `changeReason` | string |
| 변경 전 / 변경 후 | `before` / `after` | string |
| 관련 기능 | `relatedFeatures` | string[] |
| 영향받는 역할 | `affectedRoles` | string[] |
| 역할별 영향 | `roleImpacts` | `{role, impact, basis, evidenceRefs}[]` |
| 확인 필요 사항 | `needsConfirmation` | **string[]** |
| 분석 근거 | `evidence` | `{id, source, location, description}[]` |
| 후속 작업 | `followUpTasks` | `{role, task, evidenceRefs}[]` |

### 값 제약

- `affectedRoles`, `roleImpacts[].role` 허용 값 **7종만**:
  `프론트엔드`, `백엔드`, `AI`, `기획`, `디자인`, `QA`, `프로젝트 관리자`
  → 목록 밖 역할은 서버에서 제거한다 (`services/analysis_service._allowed_roles`)
- `roleImpacts[].basis`: `"확인된 사실"`(diff에서 직접 확인) 또는 `"변경 기반 예상"`(추론). 그 외 값은 `null`로 정리된다
- `roleImpacts[].evidenceRefs` → `evidence[].id` 참조 (프론트 "이 영향의 근거 보기")
- `evidence[].source`: `"pr_diff"`(현재 PR) 또는 `"context"`(검색된 기존 컨텍스트)
  - `pr_diff` → `location`은 파일 경로
  - `context` → `location`은 chunk id, `chunkId`·`similarityScore`가 채워진다
- `followUpTasks[].role`: 허용 역할 7종 중 하나. **담당을 특정할 수 없으면 `null`**(작업 자체는 유지)
- `followUpTasks[].evidenceRefs` → `evidence[].id` 참조 (역할별 영향과 동일한 근거 연결 방식)
- `confidence`: 0.0~1.0. **LLM 자기평가가 아니라 검색 신호(Top 유사도·근거 수·강한 근거 수·필터 잔존율)** 기반

### 프론트 표시 정책 (확정)

| 항목 | 정책 |
| --- | --- |
| `confidence` | **화면에 노출하지 않는다.** 내부 지표로만 사용 |
| `retrievalQualityWarning` | `true` 이면 **경고 UI를 노출**한다 (초안 품질을 신뢰하기 어려운 상태) |
| `affectedRoles` 에는 있으나 `roleImpacts` 에 상세가 없는 역할 | **역할 자체는 표시**하고, 상세 영향은 **"확인 필요" 상태**로 렌더한다 |
| 표시 항목의 JSON 키 | 백엔드 `analysisResult` 로 전달되는 키(**camelCase**) 기준으로 연동한다 |

---

## 4. 프론트가 반드시 처리해야 하는 특수 상태 3가지

| 상태 | 판별 | 처리 |
| --- | --- | --- |
| 근거가 없어 판단 못한 필드 | 값이 리터럴 `"확인 필요"` | 값처럼 출력하지 말고 "확인 필요" 배지로 표시 (AI 판단으로 오해 방지) |
| 해당 없는 항목 | `[]` | 빈 상태 문구 또는 섹션 숨김 |
| 분석 자체가 불가 | `summary`가 안내 문구 + `needsConfirmation`에 사유 | "AI 초안 없음 — 사람이 작성" 상태로 렌더 |

세 번째가 발생하는 조건 (둘 다 **LLM을 호출하지 않는다**):

1. **diff가 비어 있음** → `"코드 diff가 비어 있어 변경 내용을 분석할 수 없습니다."`
2. **검색 근거 부족** (유사도 `SIM_THRESHOLD` 이상 chunk가 `MIN_GROUNDING_EVIDENCE_COUNT` 미만)
   → `"이번 PR을 설명할 만한 프로젝트 컨텍스트를 충분히 찾지 못했습니다. ..."`

2번은 인덱싱된 데이터가 적을 때 자주 발생한다. `retrievalQualityWarning: true`면 검색 결과의 대부분이
필터링된 상태(`FILTER_RATIO_WARN_THRESHOLD` 초과)라 초안 품질을 신뢰하기 어렵다는 신호다.

---

## 5. 실패 응답

### 동기 API (`POST /api/v1/analyze/pr`)

| 상태 | 원인 |
| --- | --- |
| 401 | 내부 API 키 불일치/누락 |
| 502 | `Vector DB 검색 실패: ...` (pgvector 오류) |
| 502 | `LLM 응답 파싱 실패: ...` (구조화 출력 위반·거부) |
| 500 | 그 외 |

> 근거 부족은 **실패가 아니다.** `200` + `grounding_sufficient: false` + `needs_confirmation: true`로 응답한다.

### 비동기 콜백 (`status: "FAILED"`)

`errorMessage`는 원인 분류 태그로 시작한다 (최대 300자, 전체 트레이스백은 서버 로그).

| 태그 | 의미 | 권장 처리 |
| --- | --- | --- |
| `[RETRIEVAL_FAILED]` | 벡터 DB 조회 실패 | 재시도 가치 있음 (인프라 일시 장애) |
| `[LLM_RESPONSE_INVALID]` | LLM 응답이 스키마 위반/거부 | 재시도 1회 후 사람 검토 |
| `[ANALYSIS_FAILED]` | 그 외 | 로그 확인 필요 |

---

## 6. 작업 상태 관리

```
GET  /internal/v1/analyses/{jobId}          → { jobId, analysisId, status, startedAt, completedAt }
POST /internal/v1/analyses/maintenance      → { reapedCount, purgedCount, timeoutMinutes }
```

- `status`: `PROCESSING` | `COMPLETED` | `FAILED`
- `ai_analysis_jobs` 테이블은 앱 기동 시 자동 생성된다 (`main.py` lifespan, `CREATE TABLE IF NOT EXISTS`)
- **`maintenance` 호출은 선택 사항이다.** 서버가 분석 도중 종료돼 행이 `PROCESSING`으로 남아도,
  `get_job()`이 조회 시점에 타임아웃을 판정해 `FAILED`로 확정한다 (`services/job_store.py`의 lazy 판정).
  → 백엔드 스케줄러 없이도 상태 조회는 정확하다. 주기 호출은 "조회되지 않는 행 청소 + 보존기간 삭제"
  목적이므로 운영 규모가 커질 때 붙이면 된다 (`JOB_TIMEOUT_MINUTES=30`, `JOB_RETENTION_DAYS=0`)

---

## 7. 동기 API 응답 (`POST /api/v1/analyze/pr`)

현재 백엔드는 이 API를 호출하지 않는다(10장 실측). 다만 기존 계약을 깨지 않도록
**필드의 이름·타입·의미를 그대로 유지**했고, 새 항목은 전부 추가만 했다.

| 필드 | 타입 | 상태 |
| --- | --- | --- |
| `pr_id`, `summary`, `confidence`, `filter_ratio` | int/str/float | 기존 그대로 |
| `risk_score` | int (0~100) | 기존 그대로 — 출력 스키마에 유지 |
| `reviews` | `{file_path, line_number, comment}[]` | 기존 그대로 — 출력 스키마에 유지 |
| `evidences` | `{id, similarity_score, ...}[]` | 기존 + `chunk_id`/`source_type`/`file_path` 추가 |
| `needs_confirmation` | **bool (유지)** | 확인 항목 목록은 새 필드 `confirmation_items`(string[])로 제공 |
| `purpose`, `change_reason`, `before`, `after`, `related_features`, `affected_roles`, `role_impacts`, `follow_up_tasks`, `retrieval_quality_warning`, `grounding_sufficient` | — | **신규 추가** (프론트가 사용, 백엔드는 통과만 시키면 됨) |

> `risk_score`·`reviews`는 프론트 기록 초안 스키마에는 없지만, 백엔드가 이미 소비하고 있어
> LLM 출력 스키마(`RecordDraftOutput`)에 유지한다. 이 두 필드가 스키마에서 빠지면
> 응답이 **조용히 `0` / `[]` 로 비므로**, 회귀 테스트(`tests/test_llm_output_normalization.py`)로 고정해 두었다.

---

## 8. 아직 합의되지 않은 항목

| # | 항목 | 현재 상태 | 필요한 결정 |
| --- | --- | --- | --- |
| 1 | ~~프로젝트 메타 전달~~ | **해결됨** — AI 서버가 `project.yml`에서 저장소 이름으로 찾아 채운다 (백엔드 작업 0). 나중에 요청에 `project` 객체를 담게 되면 그쪽이 우선한다 | 없음 (요청 스키마 확장은 선택) |
| 2 | ~~레포 코드 인덱싱 트리거~~ | **해결됨** — `python -m scripts.index_repo_code` 로 AI 파트가 운영 (백엔드 작업 0). `POST /api/v1/repos/index` 는 그대로 남아 있어 나중에 백엔드가 붙여도 된다 | 없음 (자동화 시점만 추후 결정) |
| 3 | `maintenance` 스케줄러 | 호출 주체 없음 | 호출하지 않아도 `get_job()`의 lazy 타임아웃으로 정확성은 유지됨 → **MVP에서는 생략 가능** |
| 4 | `followUpTasks` 형태 | `string[]` | `{role, task, evidenceRefs}[]`로 확장할지 |

### 프로젝트 메타 (`project.yml`)

```yaml
default:
  language: ko
  roles: [프론트엔드, 백엔드, AI, 기획, 디자인, QA, 프로젝트 관리자]

projects:
  "kau-likelion-14th-hackathon/Contextory_AI":
    name: Contextory
    description: PR을 프로젝트 기록으로 축적하는 서비스
    purpose: 팀원이 변경 이유와 영향을 나중에 검색해 이해할 수 있게 한다
    features: [PR 분석 및 기록 초안 생성, 기록 승인, 컨텍스트 검색(RAG)]
    roles: [프론트엔드, 백엔드, AI, 기획]
```

- 키는 `owner/repo` 와 `repo` 양쪽으로 매칭된다 (대소문자 무시)
- `roles` 는 허용 7종만 유효하며 목록 밖 값은 로드 시 제거된다
- 파일이 없거나 항목이 없으면 프롬프트에 `(정보 없음)`으로 표기되고 역할 판단을 하지 않는다
- 경로는 `PROJECT_REGISTRY_PATH` 로 바꿀 수 있다

### 레포 코드 인덱싱 (AI 파트 운영)

```bash
python -m scripts.index_repo_code --path . \
    --repo-name kau-likelion-14th-hackathon/Contextory_AI --dry-run   # 대상·비용 추정
python -m scripts.index_repo_code --path . \
    --repo-name kau-likelion-14th-hackathon/Contextory_AI             # 실제 적재
```

- git 저장소면 `git ls-files` 를 쓰므로 `.gitignore` 가 자동 반영된다
- `--repo-name` 은 검색 격리 키다. 분석 요청의 `repo_name` / `repositoryFullName` 과 **정확히 같아야** 검색된다

### 1번 제안 스키마

```jsonc
// POST /internal/v1/analyses 에 추가
"project": {
  "name": "Contextory",
  "description": "PR을 프로젝트 기록으로 축적하는 서비스",
  "purpose": "팀원이 변경 이유와 영향을 나중에 검색해 이해할 수 있게 한다",
  "features": ["PR 분석", "기록 승인"],
  "roles": ["프론트엔드", "백엔드", "AI", "기획"]   // 이 프로젝트에 실제 존재하는 역할만
}
```

`roles`가 비면 프롬프트 규칙에 따라 AI는 역할을 **만들어내지 않고** 확인 필요로 남긴다.

---

## 9. 운영 참고

- 신규 환경변수는 `.env.example`에 기본값·설명과 함께 정리되어 있다
  (`FILTER_MODE`, `SIM_THRESHOLD`, `RAG_TOP_K`, Confidence 가중치 4종, 보조 모델 2종 등)
- pgvector ANN 인덱스: `python -m scripts.create_vector_indexes --apply` (hnsw, 코사인)
- 컨테이너: `Dockerfile` (헬스체크는 인증 없는 `/health` 사용)
- CI: `.github/workflows/ci.yml` — pgvector 컨테이너로 DB 테스트까지 실행, `OPENAI_API_KEY=""`로 두어
  테스트가 유료 호출을 하지 않음을 강제한다

---

## 10. 백엔드 연동 실측 검증

`Contextory_BackEnd` (origin/develop, `dc76a95`)의 실제 구현을 기준으로 계약 호환성을 확인했다.

### 백엔드 구현 현황

| 확인 지점 | 파일 | 결과 |
| --- | --- | --- |
| 호출하는 AI 엔드포인트 | `global/client/FastApiClient.java` | `POST /internal/v1/analyses`, `GET /internal/v1/analyses/{jobId}` **비동기 API만** 사용. `/api/v1/analyze/pr` 호출부 없음 |
| 요청 직렬화 | `dto/request/FastApiAnalysisRequestDto.java` | `@JsonProperty("analysis_id")` 등 **snake_case** (단 `callbackUrl` 은 camelCase) |
| 콜백 수신 | `dto/request/FastApiCallbackRequestDto.java` | `private Object result` — **필드 단위 매핑 없이 통째로 수신** |
| 저장 | `entity/AiAnalysis.java` | `@Column(columnDefinition = "json") private String resultJson` |
| 프론트 전달 | `dto/response/AiAnalysisDetailResponse.java` | `private JsonNode analysisResult` — **통째로 전달** |

> 결론: AI가 `result`에 필드를 추가해도 백엔드는 **코드 수정 없이 저장·중계**한다.
> 프론트가 받는 `analysisResult` 가 곧 이 문서 3장의 `result` 객체다.

### 요청 표기법 (snake_case) 호환성

AI의 `AsyncAnalysisRequest`는 `CamelModel`(alias=camelCase) 이지만 `populate_by_name=True` 이므로
**필드명(snake_case)과 alias(camelCase) 양쪽 모두 허용**한다. 백엔드가 보내는 형태 그대로 파싱된다.

### 실측 결과

백엔드 DTO가 직렬화하는 형태 그대로 AI 서버에 요청하고, 로컬 콜백 수신 서버로 전 구간을 확인했다.

| 단계 | 결과 |
| --- | --- |
| `POST /internal/v1/analyses` (snake_case 요청) | `202` + `{"jobId": "...", "status": "PROCESSING"}` → `FastApiAnalysisResponseDto` 호환 |
| `GET /internal/v1/analyses/{jobId}` | `jobId` / `analysisId` / `status` / `startedAt` / `completedAt` 전부 존재 → `FastApiJobStatusResponseDto` 호환 |
| 콜백 수신 | 경로·`X-Internal-Api-Key` 일치. `jobId` / `status` / `modelName` / `result` / `errorMessage` 전부 존재 → `FastApiCallbackRequestDto` 호환 |
| `result` 내용 | 17개 키 (기존 5개 + 기록 초안 12개) |

### 백엔드에서 확인이 필요한 것 (1건)

`FastApiClient` 는 `RestClient.builder()` 를 직접 생성한다(Spring Boot가 구성한 `RestClient.Builder` 를 주입받지 않음).
이 경우 Boot가 설정한 Jackson `ObjectMapper`(JavaTimeModule 포함)가 적용되지 않을 수 있는데,
`FastApiJobStatusResponseDto` 는 `OffsetDateTime startedAt / completedAt` 을 사용한다.

- AI가 보내는 값은 `"2026-08-18T08:49:17Z"` (ISO-8601 UTC)로 정상이다
- `getJobStatus()` 를 실제로 한 번 호출해 역직렬화가 되는지만 확인하면 된다
- 실패한다면 `RestClient.Builder` 를 주입받도록 바꾸거나 `JavaTimeModule` 을 등록한다
