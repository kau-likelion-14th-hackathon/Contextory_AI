# PR 분석 평가 파이프라인 구축 보고서

한국어 PR 리뷰 데이터셋 구축부터 pgvector 인덱싱, LLM 예측 생성, 지표 산출까지의
평가 파이프라인을 구현하고 검증한 결과입니다.

| 항목 | 값 |
| --- | --- |
| 대상 브랜치 | `feat/#7-dataset-and-pr-analysis-api` |
| 기준 커밋 | `34db25b` |
| 평가 데이터셋 | `contextory-korean-pr-review` v0.4.0 (32건) |
| 벡터 저장소 | PostgreSQL + `pgvector` |
| 임베딩 / LLM | `text-embedding-3-small` / `gpt-4o` |
| 작성일 | 2026-08-03 |

> [!IMPORTANT]
> **파이프라인 구현·검증은 완료했으나, 실제 LLM·임베딩을 호출한 평가는 아직 실행하지
> 않았습니다.** 이 환경에 `OPENAI_API_KEY` 가 없어 dry-run 으로만 동작했고, 따라서
> [§6](#6-평가-데이터와-실측-지표) 의 모델 성능 수치는 전부 `null`(계산 불가) 입니다.
> 지표 계산 로직 자체는 합성 데이터로 별도 검증했으며 결과는 [§7](#7-지표-계산-로직-검증-합성-데이터) 에 있습니다.

---

## 목차

1. [브랜치 분석](#1-브랜치-분석)
2. [팀원 변경 파일과 통합 상태](#2-팀원-변경-파일과-통합-상태)
3. [충돌과 해결](#3-충돌과-해결)
4. [원본 데이터와 5GB 축소 전략](#4-원본-데이터와-5gb-축소-전략)
5. [인덱싱 결과](#5-인덱싱-결과-smoke-dry-run)
6. [평가 데이터와 실측 지표](#6-평가-데이터와-실측-지표)
7. [지표 계산 로직 검증](#7-지표-계산-로직-검증-합성-데이터)
8. [검증 결과 요약](#8-검증-결과-요약)
9. [전체 실행 방법과 예상 리소스](#9-전체-실행-방법과-예상-리소스)
10. [산출물](#10-산출물)
11. [남은 문제](#11-남은-문제)

---

## 1. 브랜치 분석

| 브랜치 | 최신 커밋 | 상태 |
| --- | --- | --- |
| `feat/#7-dataset-and-pr-analysis-api` (작업 중) | `34db25b` | 데이터셋 · PR 분석 API · 평가 파이프라인 |
| `origin/develop` | `8ed4e0c` | 팀원 RAG 파이프라인(PR #12) 머지됨 |
| `origin/feat/#11-rag-pipeline-and-api-integration` | `dfda0e4` | PR #12 의 원본 브랜치 |
| `origin/feat/#8-db-models-setup` | `2af456b` | develop 에 이미 반영 |
| `origin/main` | `a057ba3` | 초기 커밋 |

> [!WARNING]
> 현재 작업 브랜치는 `origin/develop` 을 조상으로 포함하지 않습니다.
> `origin/develop` 에만 있는 커밋이 4개 있습니다.

```
8ed4e0c  Merge pull request #12 from kau-likelion-14th-hackathon/feat/#11-rag-pipeline-and-api-integration
dfda0e4  docs: Dir 구조 가독성 개선 및 누락된 파일 추가
2dbc9e1  docs: 수정사항 README.md 반영
a25a404  feat: pgvector RAG 파이프라인 구축 및 OpenRouter GPT-4o PR 분석 연동
```

## 2. 팀원 변경 파일과 통합 상태

`origin/develop` 이 새로 추가한 5개 파일에 대한 현재 작업 트리 상태입니다.

| 팀원 파일 | 작업 트리 | 처리 |
| --- | :---: | --- |
| `llamaindex/vector_store.py` | 있음 | ✅ **통합 완료** — [§3](#3-충돌과-해결) |
| `models/schemas.py` | 있음 | ➖ 이 브랜치 자체 구현 존재 (별도 계보) |
| `routers/analysis.py` | 있음 | ➖ 이 브랜치 자체 구현 존재 (별도 계보) |
| `llamaindex/pipeline.py` | 없음 | ❌ **미통합** |
| `routers/indexing.py` | 없음 | ❌ **미통합** |

미통합 2개는 이 평가 파이프라인이 사용하지 않으므로 지금까지의 검증에는 영향이 없습니다.
다만 브랜치를 `develop` 에 올릴 때 반드시 함께 정리해야 합니다.

## 3. 충돌과 해결

**충돌 지점:** [`llamaindex/vector_store.py`](../llamaindex/vector_store.py) 가 양쪽에 존재했습니다.

| 항목 | 팀원 구현 | 이 브랜치 요구 |
| --- | --- | --- |
| 접속 정보 | `POSTGRES_*` 개별 변수 | `core/config.py` 기준 `DATABASE_URL` |
| 기본 테이블 | `code_embeddings` | `contextory_embeddings` |
| 임베딩 차원 변수 | `EMBED_DIM` | `EMBEDDING_DIM` |

### 해결 방식 — 시그니처 유지 + 해석 확장

- `get_vector_store(table_name=...)` **시그니처를 그대로 유지**했습니다.
  팀원의 `pipeline.py` 는 `get_vector_store(table_name="code_embeddings")` 처럼
  테이블명을 명시적으로 넘기므로, 통합 후에도 호출부가 깨지지 않습니다.
- 접속 정보는 `DATABASE_URL` 1순위 → `POSTGRES_*` 2순위로 **양쪽 모두 지원**합니다.
  어느 팀원의 `.env` 로도 동작합니다.
- 임베딩 차원도 `EMBEDDING_DIM` → `EMBED_DIM` 순으로 둘 다 읽습니다.
- 비밀번호와 접속 문자열은 로그에 남기지 않습니다. 표시가 필요한 곳은
  `describe_target()` 이 돌려주는 마스킹된 값(`postgresql://host:port/db`)만 사용합니다.

벡터 저장소는 pgvector 하나이며, 파일 기반 저장소(Chroma/FAISS)는 사용하지 않습니다.

## 4. 원본 데이터와 5GB 축소 전략

| 항목 | 값 |
| --- | --- |
| 원본 데이터셋 | [`ronantakizawa/github-codereview`](https://huggingface.co/datasets/ronantakizawa/github-codereview) |
| 원본 경로 | `data/hf_source/github-codereview` |
| 원본 용량 | 623 MB (652,892,020 bytes) |
| 축소 경로 | `data/subset_5gb` |
| 축소 용량 | 412 MB (431,506,900 bytes) — 원본 대비 **66.09%** |
| 5.00 GB 상한 | ✅ 충족 (원본이 애초에 상한 미만) |
| 추가 축소 라운드 | 0회 |

### 전략

원본이 653 MB 로 5 GB 상한을 크게 밑돌았습니다. 따라서 **샘플을 버리는 축소는 하지
않았습니다.** 레코드 237,877건을 100% 보존하고 parquet 재작성만으로 용량을 줄였습니다.
그 결과 split 비율과 라벨 분포가 원본과 **0.00pp 차이**로 완전히 동일합니다.

| split | 원본 | 축소 | 보존율 |
| --- | ---: | ---: | ---: |
| train | 219,394 | 219,394 | 100% |
| test | 11,013 | 11,013 | 100% |
| validation | 7,470 | 7,470 | 100% |
| **합계** | **237,877** | **237,877** | **100%** |

<details>
<summary>라벨 분포 (원본 = 축소, 차이 0.00pp)</summary>

| 라벨 | 건수 | 비율 |
| --- | ---: | ---: |
| suggestion | 111,737 | 46.97% |
| none | 52,536 | 22.09% |
| question | 32,503 | 13.66% |
| bug | 15,029 | 6.32% |
| refactor | 10,099 | 4.25% |
| performance | 5,207 | 2.19% |
| style | 3,658 | 1.54% |
| security | 3,644 | 1.53% |
| nitpick | 3,464 | 1.46% |

</details>

원본은 삭제하거나 덮어쓰지 않았습니다. 상세는 `reports/subset_5gb_report.md` 참고.

## 5. 인덱싱 결과 (smoke, dry-run)

### 설정

| 항목 | 값 |
| --- | --- |
| 벡터 저장소 | pgvector — `postgresql://localhost:5432/contextory_db` |
| 테이블 | `contextory_embeddings` |
| 임베딩 모델 | `text-embedding-3-small` (dim 1536) |
| chunk | size 1000 / overlap 150 |
| batch size | 16 |
| 최소 본문 길이 | 40자 |
| seed | 42 |

### smoke 결과 (문서 100건 제한)

| 항목 | 값 |
| --- | ---: |
| 문서 확인 / 인덱싱 | 100건 / 100건 |
| 생성된 chunk | 182개 |
| **저장된 chunk** | **0개** (dry-run — 임베딩 API 미호출) |
| 제외 (빈 문서 / 너무 짧음 / 체크포인트) | 0 / 0 / 0 |
| 실패 | 0건 |
| 소요 | 0.37초 (273.4 docs/sec) |

> [!NOTE]
> dry-run 이라 pgvector 에 접속하지 않았으므로 **①실제 벡터 적재, ②검색 동작,
> ③재실행 시 중복 없음(체크포인트 resume) 은 아직 확인되지 않았습니다.**
> 요약 JSON 의 `verification` 블록도 `vector_rows_in_table: null`,
> `matches: null` 로 계산 불가임을 명시합니다.

## 6. 평가 데이터와 실측 지표

### 평가 데이터셋 — `data/test_samples.json` (v0.4.0, 32건)

| 카테고리 | 건수 | | 카테고리 | 건수 |
| --- | ---: | --- | --- | ---: |
| refactoring | 7 | | functional_bug | 4 |
| performance | 5 | | api_contract | 3 |
| control (대조군) | 5 | | exception_handling | 3 |
| security | 4 | | n_plus_one | 1 |

정답 이슈 총 **27건**, 대조군(`has_actual_issue=false`) **5건**입니다.

### 실측 결과 (`results/metrics.dryrun.json`, 예측 20건)

| 지표 | 값 | 계산 불가 사유 |
| --- | :---: | --- |
| 이슈 탐지 Precision / Recall / F1 | `null` | 예측 이슈 0건 — 분모 0 |
| 심각도 일치도 | `null` | 매칭된 이슈 0건 |
| 근거 충실도 (evidence grounding) | `null` | 예측 이슈 0건 |
| 환각률 — control | `null` | 대조군 예측 0건 |
| 환각률 — ungrounded | `null` | 예측 이슈 0건 |
| 역할(affected_roles) P / R / F1 | `null` | 예측 역할 0건 |
| review_result 정확도 | `null` | 평가 가능 샘플 0건 |
| 검색 P@5 / R@5 / MRR | `null` | 정답 라벨(`relevant_record_ids`) 없음 |
| 성공 / 실패 / 빈답 | 0 / 20 / 0 | 전 건 `error: "dry_run"` |
| latency p50 / p95 | 0.0 / 0.0 ms | dry-run 이라 실제 지연 아님 |

> [!CAUTION]
> **20건 전부가 `dry_run` 오류로 집계에서 제외되었습니다.** 이는 모델 성능이 나쁜 것이
> 아니라 LLM 을 호출하지 않았기 때문입니다. 지표를 `0.0` 이 아니라 `null` +
> `unavailable_reason` 으로 표기해 **"계산된 0점"과 "계산 불가"를 구분**했습니다.

### 지표 정의

**이슈 매칭 규칙**

```
category 일치  AND  file_path 일치  AND  (양쪽에서 줄 번호를 읽을 수 있으면 ±5줄 이내)
```

이 데이터셋의 `line_reference` 는 줄 번호가 아니라 서술문이므로, 실제로는
`category` + `file_path` 규칙이 적용됩니다. 이 사실은 `metrics.json` 의
`metadata.matching_rule` 에도 기록됩니다.

**환각률 (LLM judge 미사용 — 정답과 diff 원문으로 직접 계산)**

```
control_hallucination_rate = (대조군에서 이슈를 1건 이상 만든 샘플 수) / (대조군 샘플 수)
ungrounded_issue_rate      = (evidence 를 diff 에서 찾지 못한 예측 이슈 수) / (전체 예측 이슈 수)
```

**근거 판정 규칙** — 예측 이슈의 `evidence` 중 공백 정규화 후 12자 이상인 줄이
diff 원문에 그대로 나타나면 근거 있음으로 봅니다.

## 7. 지표 계산 로직 검증 (합성 데이터)

실측이 전부 `null` 이므로, 계산식의 정확성은 **정답에서 합성한 예측 2종**으로 따로 확인했습니다.

### (A) perfect — 정답을 그대로 예측으로 사용 (32건)

| 지표 | 결과 | 판정 |
| --- | --- | --- |
| 이슈 탐지 | P=1.0 R=1.0 F1=1.0 (TP=27 FP=0 FN=0) | ✅ TP가 정답 총 27건과 일치 |
| 심각도 일치도 | 1.0 | ✅ |
| 근거 충실도 | 1.0 | ✅ **정답 evidence 27건이 모두 diff 에 실재함을 확인** |
| 대조군 환각률 | 0.0 (0/5) | ✅ |

### (B) mixed — 절반 이슈 누락 + 절반 오탐 추가 + 대조군 2건에 허위 이슈 (32건)

| 지표 | 결과 | 손계산 대조 |
| --- | --- | --- |
| 이슈 탐지 | TP=14 FP=16 FN=13 | ✅ TP+FN=27, TP+FP=30 |
| Precision | 0.4667 | ✅ 14/30 = 0.4667 |
| Recall | 0.5185 | ✅ 14/27 = 0.5185 |
| **F1 (조화평균)** | **0.4912** | ✅ 2·0.4667·0.5185 ÷ (0.4667+0.5185) = 0.4912 |
| 심각도 일치도 | 0.4286 | ✅ 매칭 14건 중 6건 |
| 근거 충실도 | 0.4667 | ✅ 14/30 (무근거율과 합 = 1.0) |
| 무근거 이슈률 | 0.5333 | ✅ 허위 이슈 16/30 |
| 대조군 환각률 | 0.4 (2/5) | ✅ 허위 이슈를 심은 대조군 2건 |

**전 지표가 손계산과 일치합니다.** 합성 예측 파일은 검증 전용이므로 산출물에 포함하지 않았습니다.

추가로 확인한 사항 — 모든 비율이 `0.0`–`1.0` 범위이고, 분모 0 은 예외 없이
`null` + `unavailable_reason` 으로 처리되며, 샘플별 상세(`evaluation_details.jsonl`)의
TP/FP/FN 합이 집계값과 일치합니다.

## 8. 검증 결과 요약

| 항목 | 결과 |
| --- | --- |
| `py_compile` — 4개 스크립트 | ✅ 통과 |
| `main.py` import | ✅ 통과 |
| `pytest -q` | ⚠️ 1 failed / 2 passed / 4 skipped |
| 축소 5GB 이하 · 재로딩 · 분포 | ✅ 통과 (원본 대비 0.00pp) |
| 인덱싱 smoke (100건, chunk 수, 실패 0) | ✅ 통과 (dry-run) |
| 인덱싱 검색 · 재실행 중복 없음 | ❌ 미검증 (DB 미접속) |
| 평가 smoke (20건, 예측 구조, 예외 처리) | ✅ 통과 (dry-run) |
| 지표 정확성 (F1 조화평균, 분모 0, 상세↔집계 일치) | ✅ 통과 ([§7](#7-지표-계산-로직-검증-합성-데이터)) |

**실패한 테스트** — `tests/integration/test_health.py::test_health` 가
`RuntimeError: DATABASE_URL 이 설정되어 있지 않습니다.` 로 실패합니다.
이 환경에 `.env` 가 없어 발생하는 환경 문제이며, 평가 파이프라인 변경과는 무관합니다.
`.env` 에 `DATABASE_URL` 을 설정하면 해소됩니다.

## 9. 전체 실행 방법과 예상 리소스

전체 인덱싱·평가는 유료 API 를 호출하므로 smoke 까지만 실행했습니다.

```bash
# 0) 사전 준비 — .env 에 OPENAI_API_KEY, DATABASE_URL 설정
#    PostgreSQL 에서: CREATE EXTENSION IF NOT EXISTS vector;

# 1) 5GB 축소 데이터 생성 (완료됨 — 재생성 시에만)
python scripts/create_5gb_subset.py

# 2) 전체 인덱싱 — 💰 비용 발생
python scripts/batch_index.py \
  --data-path ./data/subset_5gb \
  --table-name contextory_embeddings \
  --chunk-size 1000 --chunk-overlap 150 --batch-size 16

# 3) 예측 생성 — 💰 비용 발생
python scripts/run_evaluation.py \
  --eval-data ./data/test_samples.json \
  --prompt-file ./prompts/korean_code_review_v3_contextory.md \
  --output ./results/predictions.jsonl --top-k 5

# 4) 지표 계산 — 비용 없음
python scripts/evaluate_results.py \
  --predictions ./results/predictions.jsonl \
  --eval-data ./data/test_samples.json \
  --output ./results/metrics.json \
  --details-output ./results/evaluation_details.jsonl --top-k 5
```

### 예상 리소스 (추정치 — 실측 아님)

| 요인 | 근거 |
| --- | --- |
| 임베딩 대상 문서 | 237,877건 |
| 예상 chunk 수 | 약 43만개 — smoke 실측(문서 100건 → chunk 182개)에서 외삽 |
| 임베딩 API 호출 | 약 27,000 배치 (batch size 16) |
| 토큰 규모 | chunk 당 250–350 토큰 가정 시 1억 토큰대 |
| 예측 생성 | 32샘플 × `gpt-4o` 1회 — 소규모 |

비용이 부담되면 `batch_index.py --limit` 으로 문서 수를 제한해 부분 인덱싱한 뒤
평가할 수 있습니다. 인덱싱은 체크포인트 resume 을 지원하므로 나눠서 실행 가능합니다.

## 10. 산출물

### 생성 · 수정한 파일

| 경로 | 내용 |
| --- | --- |
| [`scripts/create_5gb_subset.py`](../scripts/create_5gb_subset.py) | 축소 데이터 생성 |
| [`scripts/batch_index.py`](../scripts/batch_index.py) | 배치 인덱싱 |
| [`scripts/run_evaluation.py`](../scripts/run_evaluation.py) | 예측 생성 |
| [`scripts/evaluate_results.py`](../scripts/evaluate_results.py) | 지표 계산 (예측 모드 + 프롬프트 변형 비교 모드) |
| [`llamaindex/vector_store.py`](../llamaindex/vector_store.py) | pgvector 접속 지점 (팀원 구현 통합) |
| `prompts/korean_code_review_v1_evidence_first.md` | 프롬프트 변형 1 |
| `prompts/korean_code_review_v2_risk_first.md` | 프롬프트 변형 2 |
| `prompts/korean_code_review_v3_contextory.md` | 프롬프트 변형 3 (기본값) |
| `results/indexing_summary.json` | 인덱싱 요약 (dry-run) |
| `results/predictions.dryrun.jsonl` (+ `.meta.json`) | 예측 (dry-run) |
| `results/metrics.dryrun.json` | 지표 (dry-run) |
| `results/evaluation_details.dryrun.jsonl` | 샘플별 상세 (dry-run) |
| `reports/subset_5gb_report.md` · `subset_5gb_manifest.json` | 축소 보고서 · 매니페스트 |
| `logs/batch_index.log` | 인덱싱 로그 |

> [!NOTE]
> `results/metrics.json` 과 `results/predictions.jsonl` 은 **아직 만들지 않았습니다.**
> dry-run 산출물을 실측인 것처럼 보이게 하지 않으려고 `.dryrun.` 접미사를 유지했습니다.
> [§9](#9-전체-실행-방법과-예상-리소스) 의 명령을 실행하면 접미사 없는 파일이 생성됩니다.

### 정리한 파일 — 삭제하지 않고 `retired/` 로 이동

| 이동 경로 | 사유 |
| --- | --- |
| `retired/scripts/fetch_hf_dataset.py` | 새 파이프라인이 대체 · 참조 0건 확인 |
| `retired/scripts/fetch_hf_demo.py` | 새 파이프라인이 대체 · 참조 0건 확인 |
| `retired/scripts/localize_reviews.py` | 임시 번역본 · 참조 0건 확인 |
| `retired/pycache/collect_hf_samples.*.pyc` | 캐시 |

이동 후 `py_compile` 4개 및 `main.py` import 재검증을 통과했습니다.

### 보존한 파일

`data/subset_5gb`, `data/test_samples.json`, `data/hf_source`(원본),
`evaluation/results/*`(프롬프트 변형 비교 모드가 읽음),
`scripts/build_test_samples.py` · `validate_test_samples.py`(데이터셋 재현 도구),
`scripts/run_prompt_experiment.py`(변형 실험 산출물 생성).
pgvector DB 는 손대지 않았습니다.

## 11. 남은 문제

| # | 문제 | 영향 |
| :---: | --- | --- |
| 1 | **`origin/develop` 미통합** — 팀원 RAG 파이프라인 커밋 4개 미포함, `llamaindex/pipeline.py`·`routers/indexing.py` 부재. 병합 방식은 판단이 필요해 `git merge` 미실행 | 🔴 병합 전 필수 |
| 2 | **실측 평가 미실행** — `OPENAI_API_KEY` 부재로 모델 성능 지표 전부 `null` | 🔴 성능 판단 불가 |
| 3 | **DB 연동 미검증** — 실제 벡터 적재 · 검색 · 재실행 중복 방지 미확인 | 🟡 |
| 4 | **`test_health` 실패** — `.env` 부재로 인한 환경 문제 | 🟢 설정으로 해소 |
| 5 | **검색 지표 산출 불가** — `test_samples.json` 에 `relevant_record_ids` 정답 라벨이 없어 P@K/R@K/MRR 은 구조적으로 계산 불가. 필요하면 라벨링이 선행되어야 함 | 🟡 |
| 6 | **스텁 스크립트 4개 판단 보류** — `ingest_documents.py`, `recreate_index.py`, `check_vectorstore.py`, `seed_sample_data.py` 는 `NotImplementedError` 상태의 14–19줄 자리표시자. 외부 참조는 없으나 팀원의 예정 작업일 수 있어 임의로 이동하지 않음 | 🟢 확인 필요 |

---

## 부록 — 커밋 전 확인 사항

- `results/` 와 `logs/` 는 `.gitignore` 에 포함되어 있어 산출물이 커밋되지 않습니다
  (`.gitignore:73-74`). 팀과 공유하려면 예외 규칙이 필요합니다.
- `reports/subset_5gb_manifest.json` 은 **97 MB** 입니다. GitHub 파일 크기 권고(50 MB)를
  넘으므로 `.gitignore` 에 추가하는 편이 안전합니다.
