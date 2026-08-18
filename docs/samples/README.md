# 연동 샘플 페이로드

프론트엔드·백엔드가 **AI 서버를 띄우지 않고도** 개발·테스트할 수 있도록, 실제 코드(`services/analysis_service.py`)를
실행해 뽑은 페이로드다. 손으로 쓴 예시가 아니므로 필드명·타입이 실제 응답과 정확히 일치한다.

> 재생성이 필요하면 이 디렉터리의 파일을 만든 스크립트 로직을 참고하되,
> **직접 손으로 고치지 말고** 코드에서 다시 뽑는 것을 권장한다 (스키마가 바뀌면 샘플도 같이 틀어지기 때문).

| 파일 | 내용 | 주 사용자 |
| --- | --- | --- |
| `01_internal_analyses.request.json` | 백엔드 → AI 비동기 분석 요청 (`POST /internal/v1/analyses`) | 백엔드 |
| `02_callback.completed.json` | AI → 백엔드 성공 콜백 전문 (`result` 포함) | 백엔드·프론트 |
| `03_callback.failed.json` | AI → 백엔드 실패 콜백 (`errorMessage` 분류 태그 포함) | 백엔드 |
| `04_analyze_pr.request.json` | 동기 분석 요청 (`POST /api/v1/analyze/pr`) | 백엔드 |
| `05_analyze_pr.response.json` | 동기 분석 응답 (정상) | 백엔드·프론트 |
| `06_analyze_pr.response.insufficient_grounding.json` | **검색 근거 부족** — LLM 미호출 경로 | 프론트 |
| `07_analyze_pr.response.empty_diff.json` | **빈 diff** — LLM 미호출 경로 | 프론트 |

## 프론트엔드가 꼭 확인할 것

`05`(정상) 하나만 보고 UI를 만들면 실제 운영에서 화면이 비어 보인다. **`06`, `07`을 반드시 함께 렌더링해 보라.**

| 확인 항목 | 어디서 보이나 |
| --- | --- |
| `"확인 필요"` 리터럴 처리 | `05`의 `changeReason` — 값처럼 출력하면 AI 판단으로 오해된다 |
| 분석 불가 상태 | `06`, `07`의 `summary` + `grounding_sufficient: false` + `confirmation_items` |
| `basis` 구분 표시 | `05`의 `role_impacts[].basis` = `"변경 기반 예상"` |
| 근거 링크 | `05`의 `role_impacts[].evidence_refs` → `02`의 `result.evidence[].id` |
| 근거 종류별 표시 | `evidence[].source` = `pr_diff`(파일 경로) / `context`(chunk + `similarityScore`) |
| 빈 배열 처리 | `06`의 `related_features`, `affected_roles`, `role_impacts` 등 |

## 백엔드가 꼭 확인할 것

| 확인 항목 | 어디서 보이나 |
| --- | --- |
| 기존 5개 필드가 그대로 채워지는지 | `02`의 `result.summary` / `changes` / `impacts` / `risks` / `recommendations` |
| 동기 응답 기존 필드 유지 | `05`의 `risk_score`(45), `reviews`(2건), `evidences`, `confidence`, `needs_confirmation` |
| 프론트로 전달할 신규 필드 | `05`·`02`의 `purpose`/`change_reason`/`before`/`after`/`related_features`/`affected_roles`/`role_impacts`/`follow_up_tasks`/`evidence` |
| 실패 분류 처리 | `03`의 `errorMessage` — `[RETRIEVAL_FAILED]` / `[LLM_RESPONSE_INVALID]` / `[ANALYSIS_FAILED]` |
| 요청 형식 | `01`(camelCase, 내부 API) vs `04`(snake_case, 동기 API) — **두 API의 표기법이 다르다** |

## 로컬에서 실제 호출해 보기

```bash
# AI 서버 기동
uvicorn main:app --reload

# 동기 분석 (요청/응답 샘플과 비교)
curl -X POST http://127.0.0.1:8000/api/v1/analyze/pr \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -d @docs/samples/04_analyze_pr.request.json | jq

# 비동기 분석 (202 + jobId → callbackUrl로 콜백 전송)
curl -X POST http://127.0.0.1:8000/internal/v1/analyses \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  -d @docs/samples/01_internal_analyses.request.json | jq
```

> `01`의 `projectRoles`는 **선택 필드**다. 빼고 보내도 동작하며(그때는 `project.yml` 폴백),
> 보낼 때는 `project_role` 원본 값을 그대로 담으면 된다 — `"BE"` → `"백엔드"` 정규화는 AI 서버가 한다.
> `01`의 `callbackUrl`은 예시 주소다. 로컬 테스트 시 본인 백엔드 주소로 바꿔야 콜백을 받을 수 있다.
> 응답의 실제 내용(요약·역할 등)은 LLM 호출 결과라 매번 달라진다. **필드 구조만 샘플과 일치**하면 된다.
