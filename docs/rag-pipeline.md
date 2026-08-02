# RAG 파이프라인

## 인덱싱

```
DocumentLoader (Firestore/Storage 원문)
  -> TextSplitter (토큰 기준 분할)
    -> IndexBuilder (임베딩 -> pgvector 적재)
```

오래 걸리는 인덱싱은 Celery(`tasks/ingest_tasks.py`)로 넘깁니다.

## 검색

```
ProjectRetriever (project_id 필터 필수)
  -> 상위 K 노드
    -> LlmClient.answer_with_context
```

프로젝트 범위를 벗어난 문서가 섞이지 않도록 `project_id` 필터를 반드시 적용합니다.

## PR 분석

LLM 출력은 자유 텍스트가 아니라 `models/schemas.py` 의 `AnalysisResultDTO` 로 파싱합니다.
파싱에 실패하면 PR `analysis_status` 를 `failed` 로 기록합니다.

System Prompt 는 `prompts/korean_code_review_v1_evidence_first.md` 등 3종이 있고,
공통 출력 스키마는 `prompts/korean_code_review_output_schema.json` 입니다.

### 환각 방지

- diff 에 없는 변경을 만들지 않습니다.
- PR 본문에 없는 목적을 확정하지 않습니다.
- 근거가 약하면 `needs_confirmation` 으로 분리합니다.
- 모든 issue 에 `evidence` 가 있어야 하며, `utils/validators.validate_evidence_grounded` 로 확인합니다.

### 평가

`data/test_samples.json` 이 기준 데이터셋입니다(대조군 포함).
`scripts/run_prompt_experiment.py` 와 `scripts/evaluate_results.py` 로 프롬프트를 비교합니다.
