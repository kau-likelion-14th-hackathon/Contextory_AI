# 로컬 DB 세팅 — 벡터 데이터 공유

> AI 서버는 각자 로컬에서 돌린다. 그런데 **벡터 데이터는 각 로컬 DB에 따로 있다.**
> 인덱싱을 한 사람의 DB에만 데이터가 있으면, 다른 사람이 돌린 분석은 근거 없이 나온다.

## 왜 필요한가 (실제로 겪은 문제)

프론트가 받은 분석 응답(analysisId 15)이 근거 없이 그럴듯한 내용만 담고 있었다.
확인해 보니 그 분석을 돌린 머신의 DB에 프로젝트 코드 벡터가 없었다.

```
검색 최고 유사도  0.3342   (임계값 0.5)
근거 충분         False
confidence        0.1871
```

`data_repo_code_vectors` 가 비어 있으면 검색이 무관한 코드리뷰 청크만 물어오고,
그 상태로 LLM이 diff만 보고 글을 쓴다. **인덱싱은 각자 DB에 되어 있어야 한다.**

## 방법 1 — 덤프 복원 (권장, 비용 0)

이미 인덱싱된 데이터를 그대로 옮긴다. 임베딩 API를 다시 부르지 않으므로 비용도 시간도 안 든다.

```bash
# 받는 쪽: pgvector 확장이 먼저 있어야 한다
psql -U postgres -d contextory_db -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 복원 (기존 테이블이 있으면 --clean 으로 갈아끼운다)
pg_restore -U postgres -d contextory_db --no-owner --no-privileges \
    --clean --if-exists data/contextory_vectors.dump

# 확인
psql -U postgres -d contextory_db -c \
  "SELECT metadata_->>'repo_name' AS repo, count(*) FROM data_repo_code_vectors GROUP BY 1;"
```

기대 결과:

| repo_name | chunks |
| --- | ---: |
| `kau-likelion-14th-hackathon/Contextory_AI` | 325 |
| `kau-likelion-14th-hackathon/OffCourse_FrontEnd` | 279 |
| `kau-likelion-14th-hackathon/Contextory_BackEnd` | 229 |

덤프 파일: `data/contextory_vectors.dump` (7.5MB, `data/` 는 git 추적 대상이 아니므로 직접 전달)
포함 테이블: `data_repo_code_vectors`, `code_review_vectors` (hnsw 인덱스 정의 포함)

## 방법 2 — 각자 인덱싱 (비용 ~$0.002, 5분)

저장소를 clone 한 뒤 직접 적재한다. 코드가 바뀌었을 때는 이 방법으로 갱신한다.

```bash
python -m scripts.index_repo_code --path . \
    --repo-name kau-likelion-14th-hackathon/Contextory_AI
python -m scripts.index_repo_code --path ../Contextory_BackEnd \
    --repo-name kau-likelion-14th-hackathon/Contextory_BackEnd

git clone --depth 1 -b develop \
    https://github.com/kau-likelion-14th-hackathon/OffCourse_FrontEnd.git /tmp/contextory_fe
python -m scripts.index_repo_code --path /tmp/contextory_fe \
    --repo-name kau-likelion-14th-hackathon/OffCourse_FrontEnd
```

`--dry-run` 을 붙이면 적재 없이 대상 파일과 비용 추정만 볼 수 있다.

## 세팅이 끝났는지 확인하는 법

```bash
# 1) 벡터가 들어갔는지
psql -U postgres -d contextory_db -c "SELECT count(*) FROM data_repo_code_vectors;"   # 833

# 2) hnsw 인덱스가 있는지 (없으면 데이터가 늘수록 검색이 느려진다)
psql -U postgres -d contextory_db -c \
  "SELECT indexname FROM pg_indexes WHERE indexdef LIKE '%hnsw%';"
# → idx_data_repo_code_vectors_embedding_hnsw, idx_code_review_vectors_embedding_hnsw
# 없으면: python -m scripts.create_vector_indexes --apply

# 3) 실제로 근거가 잡히는지 (임계값 0.5를 넘는지)
python -m scripts.measure_grounding --limit 5
```

3번에서 "근거 부족" 비율이 높게 나오면 인덱싱이 안 된 것이다.

## 주의

- **분석을 돌리는 머신의 DB에 데이터가 있어야 한다.** 프론트·백엔드가 보는 결과는
  AI 서버를 실행 중인 사람의 로컬 DB를 따른다.
- 인덱싱 대상이 아닌 저장소(예: 팀 외부 Android 프로젝트)의 PR은 근거를 못 찾아
  "근거 부족" 경로로 빠진다. 이건 정상 동작이다 — 분석하려면 그 저장소를 먼저 인덱싱해야 한다.
- 저장소 이름(`--repo-name`)은 분석 요청의 `repositoryFullName` 과 **정확히 같아야** 검색된다.
