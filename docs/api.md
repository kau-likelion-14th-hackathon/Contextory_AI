# API

모든 엔드포인트는 프로젝트 소속 검증을 거칩니다. 다른 프로젝트의 자원에 접근하면 `403 PERMISSION_DENIED` 입니다.

> 확인 필요: 인증 방식(Spring Boot 가 검증 후 헤더 전달 / AI 서비스가 JWT 직접 검증).
> 현재는 `dependencies.get_current_user_id` 가 헤더에서 사용자 식별자만 읽습니다.

## Pull Request / 기록

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/projects/{project_id}/pull-requests/{pr_id}/analyze` | PR 분석 → draft 기록 생성 |
| GET | `/projects/{project_id}/pull-requests/{pr_id}` | PR 상세 |
| GET | `/projects/{project_id}/records` | 기록 목록 (`approval_status` 필터) |
| PATCH | `/projects/{project_id}/records/{record_id}/approval` | 승인/폐기 |
| PATCH | `/projects/{project_id}/follow-up-tasks/{task_id}` | 후속 작업 상태 변경 |

## 사용자

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/users/me` | 내 정보 |
| GET | `/projects/{project_id}/members` | 프로젝트 멤버 목록 |

## 문서 / 검색 / 채팅

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/projects/{project_id}/documents` | 문서 목록 |
| GET | `/projects/{project_id}/documents/{document_id}` | 문서 상세 |
| POST | `/projects/{project_id}/documents/ingest` | 인덱싱 |
| POST | `/search` | RAG 검색 |
| POST | `/chats` | 질의응답 |
| GET | `/chats/{chat_id}/messages` | 채팅 이력 |

## 오류 응답

```json
{ "code": "PERMISSION_DENIED", "message": "...", "detail": null }
```

| code | status |
| --- | --- |
| `NOT_FOUND` | 404 |
| `PERMISSION_DENIED` | 403 |
| `VALIDATION_ERROR` | 422 |
| `INVALID_STATE_TRANSITION` | 409 |
| `LLM_RESPONSE_ERROR` | 502 |
| `EXTERNAL_SERVICE_ERROR` | 502 |
