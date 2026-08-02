# LlamaIndex ingest·query 테스트 결과 보고서

- 작성일: 2026-08-03
- 대상 저장소: `kau-likelion-14th-hackathon/Contextory_AI` (branch: `feat/#7huggingface데이터셋`)
- 작업 범위: 기준 데이터셋 저장 확인 → LlamaIndex ingest·query 테스트 → `.gitignore` 등록
- 관련 문서: `docs/dataset_build_report.md` (데이터셋 구축 보고서)
- 작성자: TODO
- 기여자: TODO

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 기준 데이터셋 | `data/test_samples.json` 32건, `candidate_gold` — 요건 충족 확인 |
| 문서 구성 (Document 변환) | **실행함** — 32건, 정답 누출 0건 |
| 인덱싱 | **실행함** — 32 문서 → 34 노드 `[fallback: in-memory]` |
| 검색 (Retrieval) | **실행함** — 질의 5건 전부 성공 |
| **pgvector 경로** | **미검증** — PostgreSQL·`DATABASE_URL` 없음 |
| **의미 기반 검색 품질** | **미검증** — `OPENAI_API_KEY` 없어 MockEmbedding 사용 |
| **LLM 분석 / 기준 대비 비교** | **미실행** — `OPENAI_API_KEY` 없음 |
| `.gitignore` 등록 | 완료 — 3개 항목, 추적 중인 파일 0건 |
| 기존 코드 변경 | **없음** (`.gitignore` 만 수정) |

**가장 중요한 발견**: 지시서가 전제한 기존 LlamaIndex/pgvector 파이프라인이 **저장소에 존재하지 않습니다.** 따라서 "재사용"이 불가능했고, 테스트 스크립트가 최소 인덱싱 경로를 직접 구성했습니다.

---

## 2. 저장소 조사 결과 — 재사용할 파이프라인이 없음

지시서는 `AI_service/llamaindex/` 의 VectorStore / Retriever / Ingestion 파이프라인을 재사용하라고 했지만, 실제로 조사한 결과는 다음과 같습니다.

| 조사 대상 | 실제 상태 |
| --- | --- |
| `llamaindex/` (VectorStore·Retriever·Ingestion) | **디렉터리 자체가 없음** |
| `core/config.py` (pgvector 연결 설정) | **0 bytes, 빈 파일** |
| `models/__init__.py` (Pydantic 스키마) | **0 bytes, 빈 파일** |
| `services/__init__.py` | **0 bytes, 빈 파일** |
| `dependencies.py` | **0 bytes, 빈 파일** |
| `requirements.txt` | **0 bytes, 빈 파일** |
| `.env.example` | **0 bytes, 빈 파일** |
| `Dockerfile` | **0 bytes, 빈 파일** |
| `tests/` | **없음** (pytest 설정도 없음) |
| pgvector / postgres 관련 코드 | `grep` 결과 **전무** |

저장소에 실제로 구현된 파일은 `main.py`(FastAPI 앱 + health 라우터 연결)와 `routers/health.py` 뿐입니다.

### 2.1 이에 대한 대응

지시서의 **"없는 함수/클래스/테이블/스키마를 존재하는 것처럼 가정하지 않는다"** 를 따라, 존재하지 않는 진입점을 임의로 만들어 부르지 않았습니다.

대신 테스트 스크립트가 최소한의 인덱싱·검색 경로를 직접 구성했고, 결과 JSON 에 이 사실을 기록했습니다.

```json
"reused_existing_pipeline": false,
"reuse_note": "저장소에 llamaindex/ 디렉터리와 DB 설정이 없어 재사용할 기존 파이프라인이 없습니다. 이 스크립트가 최소 인덱싱 경로를 직접 구성했습니다."
```

나중에 `llamaindex/` 가 구현되면 `make_documents()` 와 인덱싱 부분을 그 구현으로 교체하는 것을 전제로 작성했습니다.

### 2.2 기존 `.gitignore`

이미 다음이 등록되어 있었습니다: `.env`, `.env.*`(단 `.env.example` 제외), `__pycache__/`, `*.py[cod]`, `.venv/`, `.pytest_cache/`, `.coverage`, `.DS_Store`, `*.log`, `*.sqlite`, `*.db` 등.

---

## 3. 환경 실측

| 자원 | 상태 | 확인 방법 |
| --- | --- | --- |
| PostgreSQL | **없음** | `psql`/`pg_ctl`/`postgres` 명령 없음, `localhost:5432` 닫힘 |
| Docker | **없음** | `docker` 명령 없음 |
| `DATABASE_URL` | **없음** | 환경 변수 미설정 |
| `OPENAI_API_KEY` | **없음** | 환경 변수 미설정 |
| `llama-index` | 미설치 → `.venv` 에 설치 | — |

따라서 **pgvector 경로는 실행할 방법이 없었고**, 지시서가 허용한 in-memory 폴백으로 진행했습니다.

---

## 4. 기준 데이터셋 확인

`data/test_samples.json` 은 이미 존재해 그대로 사용했고, 지시서의 요건을 재확인했습니다.

| 요건 | 결과 |
| --- | --- |
| 최상위 필드 (`dataset_name`, `dataset_version`, `language`, `status`, `total_samples`, `created_at`, `updated_at`, `samples`) | 전부 존재 |
| `status` | `candidate_gold` |
| `total_samples` == 실제 개수 | 일치 (32) |
| 샘플 블록 (`id`, `classification`, `source`, `translation`, `input`, `expected`, `evaluation_metadata`, `human_review`) | 전부 존재 |
| `human_review.status` | 32건 전부 `pending` |

유형별: refactoring 7 / performance 5 / **control 5** / security 4 / functional_bug 4 / api_contract 3 / exception_handling 3 / n_plus_one 1

이 파일은 데이터 산출물이므로 **git 제외 대상이 아닙니다.**

---

## 5. 테스트 스크립트 설계

`eval_local/run_llamaindex_test.py` (415줄)

환경에 따라 실행 모드를 자동으로 정하고, **무엇이 실제로 검증되었는지를 결과에 기록**하도록 만들었습니다.

| 구분 | 모드 | 선택 조건 |
| --- | --- | --- |
| 벡터 스토어 | `pgvector` | `DATABASE_URL` 이 있고 접속 성공 |
| | `in-memory` | 그 외 — `[fallback: in-memory]` 로 표시하고 pgvector 는 미검증으로 남김 |
| 임베딩 | `openai` | `OPENAI_API_KEY` 존재 |
| | `mock` | 그 외 — `MockEmbedding` 으로 구조만 검증 |
| LLM 분석 | `openai` | `OPENAI_API_KEY` 존재 |
| | `skipped` | 그 외 |

### 5.1 문서 구성 규칙

- 코드와 식별자는 **원문 그대로** 유지
- 한국어 필드(`change_summary_ko`, `before_ko`, `after_ko`)는 번역된 값 사용
- **정답 근거인 `review_comment_original` 과 `expected.issues` 는 문서에 넣지 않음**
  검색 대상에 정답이 들어가면 검색 품질 확인이 무의미해지기 때문입니다.
- 메타데이터: `sample_id`, `category`, `programming_language`, `repository`, `pr_number`, `file_path`, `has_actual_issue`, `is_control`

### 5.2 민감 정보

API 키와 DB 접속 정보는 환경 변수로만 읽고 로그에 출력하지 않습니다. 코드에 직접 쓰지 않았습니다.

---

## 6. 실행 결과

```
[1/4] 문서 구성: 32건 (정답 누출 0건)
[2/4] 백엔드: 임베딩=mock, 벡터스토어=in-memory
[3/4] 인덱싱: 32건 완료 (in-memory)
[4/4] 검색: 질의 5건 중 5건 성공 (top_k=3)
       서로 다른 결과 집합 1개 / 의미 기반 순위 동작: False
[--] LLM 분석: 건너뜀 (OPENAI_API_KEY 없음)
```

### 6.1 단계별 상세

| 단계 | 상태 | 세부 |
| --- | --- | --- |
| 문서 구성 | **ok** | 32건, 평균 1,371자, **정답 누출 0건** |
| 인덱싱 | **ok** | 32 문서 → **34 노드**, `in-memory` |
| 검색 | **ok** | 질의 5건 전부 성공, `top_k=3` |
| LLM 분석 | skipped | `OPENAI_API_KEY` 없음 |
| 기준 대비 비교 | skipped | LLM 분석을 하지 않아 비교할 결과가 없음 |

### 6.2 의미 기반 검색은 검증되지 않았습니다

`MockEmbedding` 을 쓰면 모든 문서의 임베딩이 동일해, 질의가 달라도 같은 결과가 나옵니다.
이를 숨기지 않도록 판정 지표를 넣었습니다.

```json
"distinct_result_sets": 1,
"semantic_ranking_works": false,
"semantic_ranking_note": "질의가 서로 다른데 검색 결과가 모두 동일합니다. 임베딩이 mock 이라 예상된 결과이며, 의미 기반 순위는 검증되지 않았습니다."
```

실제 검색 결과 — 5개 질의 모두 동일한 3건을 `score=1.0` 으로 반환했습니다.

```
Q: 이 PR 의 보안 위험은 무엇입니까?
    -> exception_handling-003  score=1.0
    -> refactoring-006         score=1.0
    -> n_plus_one-001          score=1.0

Q: 반복문 안에서 쿼리를 실행하는 N+1 문제가 있습니까?
    -> (위와 동일)
```

**따라서 이 실행으로 확인된 것은 "인덱싱·검색 경로가 동작한다"는 사실뿐이며, 검색 품질은 확인되지 않았습니다.**

### 6.3 실행 명령

```bash
# 현재 실행한 것
.venv/bin/python eval_local/run_llamaindex_test.py

# pgvector 를 검증하려면
pip install llama-index-vector-stores-postgres
DATABASE_URL=postgresql://user:pass@localhost:5432/contextory \
  .venv/bin/python eval_local/run_llamaindex_test.py

# 의미 기반 검색과 LLM 분석까지 하려면
export OPENAI_API_KEY=...
pip install llama-index-embeddings-openai
.venv/bin/python eval_local/run_llamaindex_test.py
```

---

## 7. 실행 중 막혔던 문제와 해결

### 7.1 nltk 의 import 훅이 `regex` 로드를 차단

인덱싱 단계에서 다음 오류로 실패했습니다.

```
ImportError: Blocked import of regex from current working directory for security reasons.
Use '-P' or set PYTHONSAFEPATH to prevent Python from searching the current working directory.
```

**호출 경로**: LlamaIndex 기본 노드 파서(`SentenceSplitter`) → `punkt_tokenizer` → `nltk.data` → `nltk/text.py` → `import regex` → `nltk/inisec.py` 의 `find_spec` 에서 차단.

`regex` 는 `.venv/lib/python3.14/site-packages/regex/` 에 정상 설치되어 있었고 단독 `import regex` 도 성공했지만, nltk 자체 보안 훅이 차단했습니다.
`-P` 플래그와 `PYTHONSAFEPATH=1` 을 모두 시도했으나 **우회되지 않았습니다.**

문서가 짧으면(2건 테스트) 문장 분할 경로를 타지 않아 성공하고, 32건 전체에서는 실패하는 형태였습니다.

### 7.2 해결 — `TokenTextSplitter` 로 교체

코드 diff 는 문장 경계가 의미를 갖지 않으므로, nltk 를 쓰지 않는 `TokenTextSplitter` 로 명시했습니다.

```python
Settings.node_parser = TokenTextSplitter(chunk_size=1024, chunk_overlap=100)
```

이 선택과 사유를 결과 JSON 에 기록했습니다.

```json
"node_parser": "TokenTextSplitter(chunk_size=1024, chunk_overlap=100)",
"node_parser_note": "기본 SentenceSplitter 는 nltk punkt 를 불러오는데 이 환경에서 nltk 의 import 훅이 regex 로드를 차단해 사용할 수 없었습니다."
```

---

## 8. `.gitignore` 등록과 추적 확인

### 8.1 추가한 항목

```gitignore
# ==========================================
# Local evaluation / test-run code & outputs (커밋 대상 아님)
# ==========================================
# 이 저장소는 루트 자체가 AI 파트이므로 AI_service/ 접두사 없이 등록합니다.
eval_local/
evaluation/results/
*.result.jsonl
```

지시서는 `AI_service/eval_local/` 로 지정했으나, 이 저장소는 **루트 자체가 AI 파트**라 `AI_service/` 접두사를 빼야 실제로 매칭됩니다.

### 8.2 적용 확인 (`git check-ignore`)

| 경로 | 결과 |
| --- | --- |
| `eval_local/run_llamaindex_test.py` | **무시됨(제외)** |
| `eval_local/results/llamaindex_test.result.jsonl` | **무시됨(제외)** |
| `evaluation/results/v1_evidence_first/results.jsonl` | **무시됨(제외)** |
| `data/test_samples.json` | 추적 대상 (의도대로 유지) |
| `evaluation/metrics.json` | 추적 대상 (8.4절 참고) |

### 8.3 이미 추적 중인 파일 — **없음**

```
$ git ls-files eval_local/ evaluation/
(출력 없음)
```

제외 대상 파일이 **전부 신규(untracked)** 이므로 `.gitignore` 만으로 정상 제외됩니다.
**`git rm --cached` 가 필요한 파일은 없습니다.**

### 8.4 판단이 필요한 항목

`evaluation/metrics.json` 은 `evaluation/results/` 하위가 아니어서 제외 대상에서 빠졌고, 현재 커밋 대상입니다.
지시서가 명시한 3개 항목만 추가했고 임의로 늘리지 않았습니다.
이 파일도 실행 산출물로 보고 제외하려면 `.gitignore` 에 한 줄 추가하면 됩니다.

---

## 9. 새 의존성

**`requirements.txt` 는 수정하지 않았습니다.** 아래는 `.venv` 에만 설치했습니다.

| 패키지 | 버전 | 사유 |
| --- | --- | --- |
| `llama-index-core` | 0.14.23 | 테스트 대상 |
| `llama-index-instrumentation` | 0.5.0 | core 의존성 |
| `llama-index-workflows` | 2.22.2 | core 의존성 |
| `nltk` | 3.10.1 | core 전이 의존성 |
| `regex` | 2026.7.19 | core 전이 의존성 |
| `tiktoken` | 0.13.0 | core 전이 의존성 |

pgvector 를 검증하려면 `llama-index-vector-stores-postgres` 가, 실제 임베딩을 쓰려면 `llama-index-embeddings-openai` 가 추가로 필요합니다.

---

## 10. 생성·수정한 파일

| 파일 | 상태 | git |
| --- | --- | --- |
| `eval_local/run_llamaindex_test.py` (20K, 415줄) | 신규 | **제외** |
| `eval_local/results/llamaindex_test.result.jsonl` (4K) | 신규 | **제외** |
| `.gitignore` | **수정** (3개 항목 추가) | 추적 |
| `docs/llamaindex_test_report.md` / `.txt` | 신규 (이 보고서) | 추적 |

`data/test_samples.json` 은 이미 존재해 **수정하지 않았습니다.**

---

## 11. 준수 사항 확인

| 항목 | 결과 |
| --- | --- |
| 기존 FastAPI/LlamaIndex/pgvector 코드 변경 | **없음** (`main.py`, `routers/`, `core/`, `models/`, `services/` 전부 무수정) |
| 존재하지 않는 함수·클래스·테이블 가정 | **없음** (파이프라인 부재를 사실대로 기록) |
| 실행하지 않은 테스트를 실행했다고 기재 | **없음** (pgvector·LLM 은 미실행으로 명시) |
| API Key / DB 접속 정보 코드에 직접 기재 | **없음** (환경 변수로만 읽음) |
| 민감정보 스캔 | **검출 0건** |
| Git 커밋 / 푸시 / 태그 / 머지 / 리베이스 | **미수행** |
| 산출물 내 작성자·AI 표기 | **검출 0건** |

---

## 12. 미완료·확인 필요 사항

| 항목 | 사유 | 조치 방법 |
| --- | --- | --- |
| **pgvector 경로** | PostgreSQL·Docker 없음 | `DATABASE_URL` 주입 + `llama-index-vector-stores-postgres` 설치 후 재실행 |
| **의미 기반 검색 품질** | `OPENAI_API_KEY` 없어 mock 임베딩 | 키 주입 + `llama-index-embeddings-openai` 설치 |
| **LLM 분석 / 기준 대비 지표** | `OPENAI_API_KEY` 없음 | 위와 동일 |
| **기존 파이프라인 재사용** | 대상 코드 자체가 없음 | `llamaindex/` 구현 후 `make_documents()` 와 인덱싱부를 교체 |
| `evaluation/metrics.json` 제외 여부 | 지시서에 미명시 | 사용자 판단 필요 (8.4절) |

---

## 13. 추천 커밋 메시지

```
chore(ai): add PR review evaluation dataset; ignore local test runners

- Add ground-truth dataset of 32 samples with 5 control samples
- Add prompt templates, build/validation scripts, and dataset card
- Ignore local evaluation runners and their outputs
```
