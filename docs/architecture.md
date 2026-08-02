# 아키텍처

레이어드 구조입니다. 의존 방향은 항상 위에서 아래이며 역참조하지 않습니다.

```
Router      표현: FastAPI APIRouter, 요청/응답 DTO 검증, 예외 -> HTTP 변환
 -> Service  비즈니스: 유스케이스, 트랜잭션 경계, LLM/RAG 오케스트레이션
   -> Repository  데이터 접근: SQLAlchemy 세션 CRUD/쿼리
     -> Store
        PostgreSQL(SQLAlchemy ORM)  관계형 핵심
        Firestore(firebase-admin)   비관계형(문서/채팅/검색로그)
        pgvector(LlamaIndex)        임베딩/RAG
```

## 스키마 소유권

**Spring Boot(JPA/Hibernate)가 스키마를 소유합니다.**
AI 파트의 SQLAlchemy 는 기존 테이블에 **매핑 전용**입니다.

- `Base.metadata.create_all()` 을 호출하지 않습니다.
- Alembic 등 마이그레이션 도구를 쓰지 않습니다(`migrations/` 미생성).
- 테이블명은 `__tablename__` 으로 실제 이름에 고정합니다.

> 확인 필요: 현재 `models/orm_models.py` 의 테이블명·컬럼명은 기획서 기준 논리 모델입니다.
> Contextory 의 Spring Boot 백엔드 저장소를 확인하지 못해 실제 스키마와 다를 수 있습니다.

## 저장소 경계

| 저장소 | 담당 |
| --- | --- |
| PostgreSQL | 프로젝트, 멤버, PR, 기록, 승인, 후속 작업 |
| Firestore | 문서 원문/청크, 채팅, 검색 로그 |
| pgvector | 임베딩 (LlamaIndex `PGVectorStore`) |

중복 저장하지 않습니다.

## 벡터 스토어 교체

`llamaindex/index_manager/vector_store.py` 의 `VectorStoreAdapter` 뒤로 감췄습니다.
Qdrant 등으로 바꿀 때 `get_vector_store_adapter()` 만 교체하면 됩니다.

## 트랜잭션 경계

Service 계층이 담당합니다. Repository 는 세션을 주입받아 데이터 접근만 합니다.

## 상태 전이

`models/enums.py` 에 규칙을 두고 Service 가 검증합니다.

- PR: `pending -> analyzing -> (needs_review | failed) -> approved`
- 기록: `draft -> approved | discarded`
- 후속 작업: `needs_action -> done`
