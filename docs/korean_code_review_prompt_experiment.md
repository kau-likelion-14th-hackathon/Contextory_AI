# 한국어 코드 리뷰 System Prompt 비교 실험 계획서

- 문서 상태: 설계 완료, **실제 모델 호출 미실행** (7절 참고)
- 대상 데이터셋: `data/test_samples.json` (`contextory-korean-pr-review` v0.4.0, 32건, `status: candidate_gold`)
- 데이터 원본: Hugging Face `ronantakizawa/github-codereview` (train split)
- 대상 프롬프트: `prompts/korean_code_review_v1_evidence_first.md`, `korean_code_review_v2_risk_first.md`, `korean_code_review_v3_contextory.md`
- 공통 출력 스키마: `prompts/korean_code_review_output_schema.json`

---

## 1. 실험 목적

같은 코드 diff 를 입력했을 때, 세 가지 System Prompt 전략 중 어느 것이 **가장 깔끔하고 명확한 한국어 리뷰**를 만드는지 근거와 함께 정합니다.

이 데이터셋의 정답은 합성된 것이 아니라 **공개 PR 에 실제로 달린 사람 리뷰어의 지적**입니다.

## 2. 템플릿 3종의 핵심 차이

| | v1 Evidence First | v2 Risk First | v3 Contextory |
| --- | --- | --- | --- |
| 최적화 목표 | False Positive·환각 최소화 | 놓친 위험 최소화 | 역할별 전달력 |
| 핵심 장치 | 근거를 먼저 고르고 설명은 나중에 / 4단계 자기 점검 | 12단계 위험 체크리스트 순차 점검 / severity 정렬 | 9개 분석 질문 / 역할별 판단 기준 7종 / 분량 규칙 |
| 예상 강점 | 대조군 환각·FP 가 낮음 | 보안·데이터 무결성 Recall 이 높음 | 역할별 영향·후속 작업이 구체적 |
| 예상 약점 | 재현율 손실, 맥락 설명이 얕음 | 대조군 FP 증가 가능 | 출력이 길어 JSON 준수율·간결성 저하 가능 |
| System Prompt 길이 | 2,359자 | 2,767자 | 3,212자 |

세 템플릿은 **동일 입력 블록과 동일 출력 스키마**를 공유합니다. 바뀌는 것은 System Prompt 하나뿐입니다.

## 3. 고정 조건

실제 값은 `scripts/run_prompt_experiment.py` 의 `FIXED_CONDITIONS` 에 코드로 고정되어 있습니다.

| 항목 | 값 |
| --- | --- |
| 모델 | `gpt-4o` (README 의 AI 스택 기준) |
| temperature | `0.0` |
| top_p | `1.0` |
| max_tokens | `4096` |
| 응답 형식 | `json_object` |
| 재시도 횟수 | 2회 (백오프 5초) |
| 입력 샘플 | `data/test_samples.json` 32건 전체(대조군 5건 포함), 파일 순서 그대로 |
| 입력 필드 순서 | 프로젝트 정보 → PR 제목 → PR 설명 → 변경 파일 경로 → 프로그래밍 언어 → 코드 Diff → 관련 리뷰 댓글 → 추가 문맥 |
| 프로젝트 정보 | 세 템플릿에 동일하게 주입 (`PROJECT_INFO`) |
| 출력 스키마 | 세 프롬프트 공통 |

### 3.1 리뷰 댓글 슬롯 — 정답 누출 방지

입력 블록에 `관련 리뷰 댓글` 슬롯이 있습니다. 실제 서비스에서는 같은 PR 의 다른 리뷰 댓글을 문맥으로 넣기 위한 자리입니다.

**평가 실험에서는 이 슬롯을 항상 `(없음)` 으로 채웁니다.**
기준 데이터셋의 `expected.issues` 가 바로 그 리뷰 댓글에서 도출한 정답이므로, 입력에 넣으면 Precision/Recall 이 무의미해집니다.

`FIXED_CONDITIONS.review_comment_in_input == False` 로 강제하며, dry-run 에서 32건 전부 누출이 없음을 확인했습니다.

## 4. 실험 절차

```bash
# 0) 사전 점검: 모델 호출 없이 입력 구성만 확인
python scripts/run_prompt_experiment.py --dry-run

# 1) API 키 주입 (셸에서만. 코드나 파일에 적지 않습니다.)
export OPENAI_API_KEY=...

# 2) 실험 패키지 설치 (프로젝트 런타임 의존성이 아니므로 requirements.txt 에는 없습니다)
pip install openai

# 3) 소규모 시험 실행
python scripts/run_prompt_experiment.py --limit 3

# 4) 전체 실행
python scripts/run_prompt_experiment.py

# 5) 자동 지표 계산
python scripts/evaluate_results.py

# 6) 사람 검토 항목 채점 (evaluation/human_review_sheet.md 서식)
```

결과 저장 경로:

```text
evaluation/results/v1_evidence_first/results.jsonl
evaluation/results/v2_risk_first/results.jsonl
evaluation/results/v3_contextory/results.jsonl
evaluation/results/{변형}/run_meta.json
evaluation/metrics.json
```

`results.jsonl` 각 줄의 필드: `sample_id`, `prompt_variant`, `requested_at`, `input_chars`, `status`, `raw_output`, `parsed`, `json_valid`.

## 5. 선정 기준

### 5.1 자동 계산 (`scripts/evaluate_results.py`)

| # | 지표 | 정의 | 방향 |
| --- | --- | --- | --- |
| 1 | JSON Valid Rate | 유효 JSON 을 반환한 비율 | 높을수록 |
| 1 | Schema Field Coverage | 필수 출력 필드를 모두 채운 비율 | 높을수록 |
| 2 | Evidence Grounding Rate | evidence 를 diff 에서 실제로 찾을 수 있는 비율 | 높을수록 |
| 3 | Control Hallucination Rate | 대조군 5건에서 문제를 만들어 낸 비율 | **낮을수록** |
| 4 | Precision / Recall / F1 | 기준 정답 대비 문제 탐지 정확도 | 높을수록 |
| 4 | False Positive Rate / Missed Issue Rate | 오탐 비율 / 놓친 비율 | 낮을수록 |
| 5 | Severity Accuracy | 매칭된 문제의 심각도 일치율 | 높을수록 |
| 5 | Review Result Accuracy | `approve`/`request_changes`/`needs_confirmation` 일치율 | 높을수록 |
| 8 | Avg Output Chars | 평균 출력 길이 (간결성 비교용) | 참고값 |
| — | Needs-Confirmation Rate | 확인 필요 항목을 분리해 낸 비율 | 높을수록 |
| — | 카테고리별 정확도 | security / performance / n_plus_one 각각의 Precision·Recall | 높을수록 |

**문제 매칭 규칙**: 샘플당 기준 문제가 1건이고 diff 도 코드 조각 하나이므로 `category` 기준으로 짝짓습니다. 카테고리가 어긋나도 기준 지적이 하나뿐이면 같은 지점을 가리킨 것으로 보고 매칭하되, 오분류는 카테고리별 지표에 반영됩니다.

**출력 필드명이 기준 데이터셋의 `expected` 와 동일**하므로 별도 매핑 없이 바로 비교합니다.

### 5.2 사람 검토 (`evaluation/human_review_sheet.md`)

| # | 지표 | 척도 |
| --- | --- | --- |
| 6 | 한국어 자연스러움 | 1~5 (5 = 실제 한국 개발자 리뷰처럼 자연스럽고 명확) |
| 7 | 후속 작업 실행 가능성 | 1~5 (5 = 담당 역할·대상 파일·수정 방향이 명확) |
| 8 | 간결성/명확성 | 1~5 (5 = 장황함 없이 핵심이 드러남) |
| — | 변경 요약·목적·전후 정확성 | 정확 / 부분 정확 / 부정확 |
| — | 역할별 영향 정확성 | 정확 / 과잉 / 누락 |

`Avg Output Chars` 는 간결성의 보조 지표일 뿐이며, 짧다고 좋은 것은 아닙니다. 8번은 사람이 최종 판정합니다.

## 6. 최종 선정 규칙

위에서부터 순서대로 적용합니다.

1. **대조군 환각 비율(3번)이 0이 아닌 템플릿은 기본값에서 제외합니다.** 문제가 없는 코드에 문제를 만들어 내면 서비스 신뢰도가 직접 훼손됩니다.
2. **JSON Valid Rate(1번)가 100% 미만이면 감점합니다.** 파이프라인이 파싱에 실패하면 나머지 품질은 의미가 없습니다.
3. **Evidence Grounding Rate(2번)가 가장 낮은 템플릿을 제외합니다.** diff 에 없는 근거를 인용하면 사용자가 결과를 신뢰할 수 없습니다.
4. 남은 템플릿 중 **F1(4번)이 가장 높은 것**을 고릅니다.
5. F1 차이가 0.05 이내면 **한국어 자연스러움 + 후속 작업 실행 가능성 + 간결성(6·7·8번) 합계**가 높은 쪽을 고릅니다.

### 6.1 트레이드오프에 대한 사전 정리

실험 결과가 나오기 전이라도 설계상 예상되는 트레이드오프는 다음과 같습니다. **아래는 가설이며 측정값이 아닙니다.**

- v1 은 근거 없는 지적을 차단하는 장치가 가장 강하므로 3번·2번에서 유리하지만, 맥락 설명이 얕아 6·7번에서 불리할 수 있습니다.
- v2 는 체크리스트를 순차로 훑으므로 4번의 Recall 에서 유리하지만, 항목을 채우려는 경향 때문에 3번에서 불리할 수 있습니다.
- v3 는 역할별 영향과 후속 작업을 강제하므로 7번에서 유리하지만, 출력이 길어 1번·8번에서 불리할 수 있습니다.

Contextory 는 "코드를 읽지 않는 팀원에게 변경 맥락을 전달하는" 제품이므로, 3번(환각)이 동률이라면 6·7번 비중을 높여 판단합니다.

## 7. 실험 실행 현황

| 항목 | 상태 |
| --- | --- |
| 템플릿 3종 작성 | 완료 |
| 공통 출력 스키마 작성 | 완료 |
| 실행 스크립트 작성 | 완료 (`scripts/run_prompt_experiment.py`) |
| 지표 계산 스크립트 작성 | 완료 (`scripts/evaluate_results.py`) |
| dry-run (모델 호출 없음) | **실행함** — 32건 × 3변형 |
| **실제 모델 호출** | **미실행** |

**미실행 사유**: 작업 환경에 `OPENAI_API_KEY` 가 설정되어 있지 않아 실제 모델을 호출할 수 없었습니다.
따라서 **5절의 어떤 지표도 아직 측정값이 없으며, 6절의 최종 선정도 내리지 않았습니다.**

dry-run 으로 확인한 것:

- 세 프롬프트 문서에서 System Prompt 블록이 정상 추출됨 (2,359 / 2,767 / 3,212자)
- 32건 전부에 대해 동일한 입력 블록이 구성됨
- **리뷰 댓글이 입력에 섞이지 않음** (32건 전부 확인)
- 결과 저장 경로와 JSONL 형식이 의도대로 생성됨

`evaluation/results/*/results.jsonl` 의 현재 레코드는 `status: "dry_run"` 이며 모델 응답이 아닙니다.
`scripts/evaluate_results.py` 는 이를 감지해 지표 계산을 거부하고 `dry_run_only` 로 보고합니다.

## 8. 예상 출력 형태

실제 실행 시 `results.jsonl` 의 각 줄은 아래 형태가 됩니다.

```json
{
  "sample_id": "security-001",
  "prompt_variant": "v1_evidence_first",
  "requested_at": "2026-08-03T00:00:00Z",
  "input_chars": 1820,
  "status": "ok",
  "raw_output": "{ ... 모델이 반환한 JSON 문자열 ... }",
  "parsed": {
    "change_summary_ko": "...",
    "review_result": "request_changes",
    "issues": [{ "category": "security", "severity": "high", "evidence": "+...", "confidence": 0.9 }]
  },
  "json_valid": true
}
```

## 9. 알려진 제약

- 데이터셋 32건은 통계적 유의성을 주장하기에 작습니다. 템플릿 간 차이는 경향으로만 해석합니다.
- **대조군이 5건뿐이라 환각 비율의 해상도가 20% 단위입니다.** 1건만 틀려도 20%가 됩니다.
- **`n_plus_one` 이 1건뿐**이라 해당 카테고리 지표는 신뢰하기 어렵습니다.
- 기준 데이터셋이 `candidate_gold` 이며 사람 검토 전입니다. 원본 데이터셋 라이선스도 확인 전입니다.
- **샘플당 기준 문제가 1건입니다.** 모델이 다른 타당한 문제를 찾아도 False Positive 로 집계되므로 사람 재확인이 필요합니다.
- **입력이 코드 청크 단위입니다.** 리뷰 대상 주변 약 50줄이므로 PR 전체를 다루는 실제 서비스와 입력 형태가 다릅니다.
- **PR 설명이 없습니다.** 원본에 PR 제목만 있어 `change_purpose_ko` 생성 품질이 낮게 나올 수 있고, 세 템플릿 모두 이를 `needs_confirmation` 으로 보낼 가능성이 큽니다.
- `temperature=0.0` 이어도 출력이 완전히 결정적이지는 않습니다. 결론이 갈리면 동일 조건에서 반복 실행이 필요합니다.
