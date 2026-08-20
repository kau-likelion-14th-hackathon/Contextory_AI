# 스키마 점검 결과와 제안 (AI 연동 · 프로젝트 기록)

`Contextory_BackEnd` (`origin/develop` `dc76a95`)의 엔티티와 API를 확인하고,
AI 연동에 새로 필요한 스키마가 있는지 점검한 결과다.

**요약: AI 연동만 놓고 보면 새로 뚫을 스키마가 없다. 다만 제품 핵심인 "승인된 프로젝트 기록"을 담을 곳이 없다.**

---

## 1. AI 연동: 새 스키마 불필요 (확인 완료)

| 계층 | 현황 | 조치 |
| --- | --- | --- |
| AI 서버 DB | `code_review_vectors`, `data_repo_code_vectors`, `ai_analysis_jobs`(앱 기동 시 자동 생성), pgvector hnsw 인덱스 | 없음 |
| 백엔드 DB | `ai_analysis.result_json` 이 `json` 컬럼 → AI가 필드를 추가해도 **컬럼 추가 없이 저장**. `ddl-auto: update` 라 DDL도 불필요 | 없음 |
| 프론트 전달 | `AiAnalysisDetailResponse.analysisResult` 가 `JsonNode` → **통째로 전달** | 없음 |

근거가 되는 백엔드 구현:

```java
private Object result;                     // FastApiCallbackRequestDto — 필드 단위 매핑 없이 수신
@Column(columnDefinition = "json")         // AiAnalysis.resultJson — 통째로 저장
private JsonNode analysisResult;           // AiAnalysisDetailResponse — 통째로 전달
```

---

## 2. 프로젝트 메타: 백엔드가 이미 갖고 있다

AI 프롬프트의 `[프로젝트 정보]` 섹션은 `affectedRoles` / `roleImpacts` 판단의 근거다.
지금은 AI 서버가 `project.yml` 에서 읽고 있지만(임시), **백엔드 DB에 이미 대부분 존재한다.**

| AI 프롬프트 항목 | 백엔드 컬럼 | 상태 |
| --- | --- | --- |
| 프로젝트 이름 | `project.name` | 있음 |
| 한 줄 설명 | `project.summary` | 있음 |
| 프로젝트 목적 | `project.purpose` (TEXT) | 있음 |
| 기본 언어 | `project.default_language` | 있음 |
| 팀 역할 | `project_member.project_role` (varchar 50) | 있음 |
| 주요 기능 | — | **없음** (선택 항목) |

### 제안: 요청 DTO에 `project` 추가 (DB 변경 없음)

`FastApiAnalysisRequestDto` 에 아래를 추가하면 지금 있는 데이터로 채울 수 있다.
AI 서버는 요청에 `project` 가 오면 그것을 우선 쓰고, 없으면 `project.yml` 로 폴백한다(이미 구현됨).

```jsonc
"project": {
  "name": "Contextory",                    // project.name
  "description": "PR 기록 서비스",           // project.summary
  "purpose": "변경 이유를 나중에 검색해 이해하게 한다",  // project.purpose
  "features": [],                          // 없으면 생략 가능
  "roles": ["프론트엔드", "백엔드", "AI"],    // project_member.project_role 중 distinct
  "language": "ko"                         // project.default_language
}
```

### 확인 필요: `project_role` 값 정규화

`project_role` 은 자유 문자열이다(`normalizeProjectRole()` 이 `trim()` 만 수행, 50자 제한).
AI의 `affectedRoles` 는 **7종 고정**이라 값이 어긋나면 역할별 영향이 비게 된다.

```
허용: 프론트엔드 / 백엔드 / AI / 기획 / 디자인 / QA / 프로젝트 관리자
```

| 선택지 | 작업 위치 | 비고 |
| --- | --- | --- |
| (a) `project_role` 을 enum/셀렉트로 제한 | 백엔드 | 데이터 정합성이 근본적으로 개선됨 |
| (b) AI가 유사어를 정규화 (`"FE"`→`"프론트엔드"`) | AI | 백엔드 작업 0, 대신 매핑 테이블 유지 필요 |

---

## 3. 없는 스키마: 승인된 프로젝트 기록 (제품 핵심)

Contextory의 정의는 **"AI가 초안 → 사람이 검토·승인 → 프로젝트 메모리에 축적"** 이다.
현재 스키마에는 초안까지만 있고 그 뒤가 없다.

```
AiAnalysis.result_json   ← AI 초안 (원본, 수정 이력 없음)
        ↓
      (없음)              ← 사람이 수정·승인한 최종 기록
```

엔티티 전수 확인 결과 `record` / `memory` / `history` / `draft` / `approval` 관련 엔티티가 **0건**이고,
AI 분석 API도 생성·목록·상세·재시도·취소뿐이라 **승인/수정 엔드포인트가 없다.**

```
POST   /api/projects/{projectId}/analyses              생성
GET    /api/projects/{projectId}/analyses              목록
GET    /api/projects/{projectId}/analyses/{analysisId} 상세
POST   .../{analysisId}/retry, .../{analysisId}/cancel
```

즉 지금 구조로는 AI 초안을 **보여줄 수는 있어도 승인해서 남길 수 없다.**

### 제안 스키마 (최소 형태)

```sql
CREATE TABLE project_record (
    record_id     BIGSERIAL PRIMARY KEY,
    project_id    BIGINT      NOT NULL,
    analysis_id   BIGINT,                 -- 어느 AI 초안에서 왔는지 (NULL이면 수기 작성)
    pr_number     INT,
    content_json  JSON        NOT NULL,   -- 승인된 최종 기록 (초안과 동일 구조)
    status        VARCHAR(30) NOT NULL,   -- DRAFT / APPROVED / REJECTED
    edited_by     BIGINT,
    approved_by   BIGINT,
    approved_at   TIMESTAMP,
    created_at    TIMESTAMP   NOT NULL,
    updated_at    TIMESTAMP   NOT NULL
);
```

**`content_json` 을 AI 초안과 같은 구조로 두는 것이 핵심이다.**

- 프론트가 초안·승인본을 **같은 컴포넌트로 렌더**할 수 있다
- 사람이 고친 결과를 그대로 담을 수 있다 (필드가 늘어도 컬럼 변경 없음)
- 승인본을 다시 임베딩해 **검색 대상에 넣을 수 있다** → 아래 4번

필요한 API:

```
POST   /api/projects/{projectId}/records                    초안 → 기록 생성(승인)
PATCH  /api/projects/{projectId}/records/{recordId}         수정
GET    /api/projects/{projectId}/records                    목록 (프로젝트 메모리)
GET    /api/projects/{projectId}/records/{recordId}         상세
```

---

## 4. 이어지는 AI 작업: 승인 기록 재임베딩

현재 검색 대상은 **코드와 외부 리뷰 데이터뿐**이다. 승인된 기록이 쌓이면 그것을 임베딩해
검색 대상에 추가하는 순간, "과거에 왜 이렇게 결정했는지"가 다음 PR 분석의 근거가 된다.

```
지금:  PR diff → [코드 청크, 외부 리뷰 KB] → 초안
이후:  PR diff → [코드 청크, 외부 리뷰 KB, 승인된 프로젝트 기록] → 초안
```

3번이 생기면 AI 쪽에서 준비할 것은 두 가지뿐이다.

- `project_record_vectors` 테이블 + 인덱싱 경로 (`scripts/index_repo_code.py` 와 같은 구조)
- `retrieve_with_signals()` 에 소스 하나 추가 (`source_type="project_record"`)

---

## 5. 정리

| # | 항목 | 새 스키마 | 담당 | 우선순위 |
| --- | --- | --- | --- | --- |
| 1 | AI 분석 연동 | 불필요 | — | 완료 |
| 2 | 프로젝트 메타 전달 | 불필요 (DTO 필드만) | 백엔드 | 낮음 (AI가 `project.yml` 로 대체 중) |
| 3 | `project_role` 값 정규화 | 선택 | 백엔드 or AI | 중간 |
| 4 | **승인 기록(`project_record`)** | **필요** | 백엔드 | **높음 — 제품 핵심** |
| 5 | 승인 기록 재임베딩 | 4번 이후 | AI | 4번 완료 후 |

4번이 없으면 데모에서 "AI가 초안을 만든다"까지만 보여줄 수 있고, "기록이 축적된다"는 보여줄 수 없다.
