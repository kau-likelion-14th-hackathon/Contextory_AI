# Dataset Card: contextory-korean-pr-review

| 항목 | 값 |
| --- | --- |
| 데이터셋 이름 | `contextory-korean-pr-review` |
| 버전 | 0.4.0 |
| 상태 | `candidate_gold` (사람 검토 전) |
| 언어 | ko-KR |
| 샘플 수 | 32 (대조군 5건 포함) |
| 원본 | Hugging Face `ronantakizawa/github-codereview` (train split) |
| 파일 | `data/test_samples.json` |
| 작성자 | TODO |
| 기여자 | TODO |

---

## 1. 목적

Contextory 의 RAG/LLM 코드 변경 분석 기능이 올바른 결과를 생성하는지 검증하기 위한 한국어 평가 데이터셋입니다.

1. 코드 diff 를 입력했을 때 LLM 이 **실제 사람 리뷰어가 지적한 문제**를 찾아내는지 확인합니다.
2. **문제가 없는 코드에 문제를 만들어 내지 않는지**(환각) 확인합니다. 이를 위해 대조군 5건을 포함합니다.
3. Contextory 가 화면에 노출하는 정보(변경 요약, 목적, 전후 비교, 역할별 영향, 후속 작업, 확인 필요 사항)의 품질을 비교합니다.
4. System Prompt 전략(v1/v2/v3)을 같은 조건에서 비교합니다.

이 데이터셋은 **모델 학습용이 아니라 평가용**입니다.

## 2. 데이터 출처

### 2.1 사용한 데이터셋

**Hugging Face `ronantakizawa/github-codereview` 의 `train` split** 에서 추출했습니다.
공개 데이터셋이므로 **토큰 없이 익명으로** 내려받았습니다.

데이터셋 카드가 밝히는 특성입니다.

- 167K+ positive triplet, 725개 상위 GitHub 저장소
- **51K+ negative example (~23%)** — 리뷰에서 지적을 받지 않은 코드, `"No issues found."` 로 라벨링
- 37개 프로그래밍 언어
- **사람 리뷰만 포함** (Copilot, 린터 봇 등 자동 리뷰어 제외)
- 리뷰 대상 코드 주변 약 50줄 청크 (파일 전체가 아님)
- 리뷰 이후 실제로 코드가 바뀐 경우만 포함

### 2.2 `microsoft/codereviewer` 를 쓰지 않은 이유

**`microsoft/codereviewer` 는 데이터셋이 아니라 모델입니다.**

```
$ curl -s -o /dev/null -w "%{http_code}" https://huggingface.co/api/models/microsoft/codereviewer
200
$ curl -s -o /dev/null -w "%{http_code}" https://huggingface.co/api/datasets/microsoft/codereviewer
401
```

데이터셋 경로로 조회하면 401 이 반환되는데, Hugging Face API 는 "권한 없음" 과 "존재하지 않음" 을 구분하지 않고 같은 401 을 돌려줍니다.
`microsoft` 조직의 공개 데이터셋 107개 중 이름에 `review` 가 포함된 것은 없습니다.
CodeReviewer 원본 데이터는 Hugging Face 가 아닌 별도 배포 경로를 따라야 합니다.

### 2.3 라이선스 — **확인 필요**

| 항목 | 값 |
| --- | --- |
| 데이터셋 카드 표기 | `other` |
| 이 데이터셋의 기록 | `license_status: "확인 필요"` |

데이터셋 카드 본문은 **"Permissive licenses only: all source repos use MIT, Apache-2.0, BSD, or similar licenses"** 라고 밝히고 있습니다.
다만 데이터셋 자체의 사용 조건이 `other` 로만 표기되어 있어, **사용 조건이 확인되기 전까지 이 데이터셋을 배포 가능(golden)으로 확정하지 않습니다.**

각 샘플에는 `source.repository` 가 기록되어 있어 원 저장소별 라이선스를 추적할 수 있습니다.
15개 저장소의 라이선스를 개별 확인하지는 않았습니다.

각 샘플의 `source.license` 에 `other (사용 조건 확인 필요)` 로 기록되어 있습니다.

### 2.4 출처 검증

| 항목 | 결과 |
| --- | --- |
| PR URL (`repo_name` + `pr_number` 로 재구성) | 32 / 32 |
| 파일 경로 | 32 / 32 |
| PR 제목 | 32 / 32 |
| 서로 다른 저장소 | 15개 |

PR URL 은 `repo_name` 과 `pr_number` 가 모두 유효할 때만 만들었고, 둘 중 하나라도 없으면 해당 행을 버렸습니다.
**존재하지 않는 저장소·PR 번호·URL·커밋 SHA 를 지어내지 않았습니다.** `source.commit_sha` 는 원본 데이터에 없어 빈 문자열입니다.

### 2.5 정답 누출에 대한 주의 — **중요**

스키마상 리뷰 댓글은 `input` 블록 안에 있습니다.

```
input.diff                      = diff_context      리뷰어가 댓글을 단 코드 주변
input.review_comment_original   = reviewer_comment  실제 리뷰 지적
input.review_comment_ko                             그 한국어 번역
```

**이 데이터셋의 `expected.issues` 는 바로 그 리뷰 댓글에서 도출한 정답입니다.**
따라서 두 필드를 모델 프롬프트에 그대로 넣으면 Precision/Recall 이 무의미해집니다.

`input` 은 데이터 보관 구조이지 "모델에 주는 것 전부"가 아닙니다.
`scripts/run_prompt_experiment.py` 는 프롬프트를 만들 때 이 두 필드를 제외하며
(`FIXED_CONDITIONS.review_comment_in_input == False`), dry-run 에서 32건 전부 누출이 없음을 확인했습니다.

모델에 실제로 전달하는 것은 `pr_title_original`, `file_path`, `programming_language`, `diff` 뿐입니다.

## 3. 수집 방법

### 3.1 도구

- `scripts/fetch_hf_dataset.py` — HF streaming 수집
- `scripts/build_test_samples.py` — 원본 + 선별 목록 + 한국어 주석 병합
- `scripts/validate_test_samples.py` — 데이터셋 검증

### 3.2 실행

```bash
# 의존성 (프로젝트 런타임 의존성이 아니므로 requirements.txt 에는 넣지 않았습니다)
python -m venv .venv
.venv/bin/python -m pip install "datasets>=2.19" "huggingface_hub>=0.23" pandas pyarrow

# 1) HF 에서 수집 (공개 데이터셋이라 토큰 불필요)
.venv/bin/python scripts/fetch_hf_dataset.py --target 40 --negatives 5

# 2) 선별 목록 + 한국어 주석과 병합
python scripts/build_test_samples.py

# 3) 검증
python scripts/validate_test_samples.py
```

전체(356k)를 내려받지 않고 `streaming=True` 로 앞부분만 훑었습니다. **200,001행을 훑어 41건을 저장**했고, 그중 32건을 선별했습니다.

### 3.3 제외 규칙과 실제 제외 건수

`data/raw/hf_github_codereview_raw.meta.json` 에 기록되어 있습니다.

| 사유 | 건수 |
| --- | --- |
| 저장소 상한(한 저장소당 최대 3건) 초과 | 2,407 |
| diff 가 6,000자 초과 | 39 |
| diff 가 80자 미만 | 17 |
| 같은 PR 중복 | 17 |
| 리뷰 댓글 2,000자 초과 | 7 |

대조군은 댓글이 `"No issues found."`(16자) 한 줄이므로 댓글 길이 하한(25자)을 적용하지 않습니다.

### 3.4 샘플 선별 — 자동 라벨을 그대로 쓰지 않은 이유

데이터셋의 `comment_type` 은 참고용 1차 라벨입니다. 리뷰 댓글과 diff 를 직접 읽고 Contextory 평가 유형 체계에 맞게 카테고리를 확정했습니다.

- `DiceDB/dice#458` 은 `comment_type: security` 지만 실제 내용은 Redis 프로토콜 출력 호환성이라 `api_contract` 로 분류했습니다.
- `4ian/GDevelop#6970` 은 `comment_type: question` 이지만 불필요한 재계산 지적이라 성능 문제로 다뤘습니다.

확정 목록은 `data/hf_selection.tsv` 이며, 각 샘플의 `classification.category_source` 는 `human_selected`, 원래 라벨은 `classification.dataset_comment_type` 에 보존됩니다.

**제외한 9건과 사유**도 `data/hf_selection.tsv` 주석에 남겼습니다(중복 diff, 동작 변화 없음, 오타 수정, 릴리스 노트 제안 등).

## 4. 한글화 방법

전체 32건 모두 `claude-assisted` 입니다. 이 세션에서 직접 번역·정리했습니다.

**GPT-4o 를 비롯한 외부 LLM API 는 사용하지 않았습니다.** 작업 환경에 `OPENAI_API_KEY` 가 설정되어 있지 않아 호출이 불가능했습니다.

| 번역 방법 | 건수 |
| --- | --- |
| `claude-assisted` | 32 |
| `gpt-4o-assisted` | 0 |

- 문체: 한국어 `~합니다` 설명체로 통일
- **번역한 것**: PR 제목, 리뷰 댓글, 변경 요약·목적·이유, 전후 설명, 문제점·영향·수정 권고, 역할별 영향, 후속 작업, 확인 필요 사항
- **원문 유지**: 코드, diff, 변수·함수·클래스·파일·디렉터리명, API 경로, 명령어, 로그, 오류 메시지
- **원문 보존**: `input` 의 `*_original` 과 `*_ko` 를 짝으로 보관합니다. diff 는 번역하지 않습니다.

원문에 없는 문제나 목적을 번역 과정에서 추가하지 않았습니다.

## 5. 민감 정보 제거

수집 단계(`redact()`)에서 GitHub 토큰, API 키, AWS 액세스 키, 개인 키 블록, 이메일 주소 패턴을 자동 치환합니다.
검증 단계가 같은 패턴을 다시 검사하며 하나라도 남아 있으면 ERROR 로 실패합니다. 현재 **검출 0건**입니다.

## 6. 구성

### 6.1 유형

| 카테고리 | 건수 | 대표 사례 |
| --- | --- | --- |
| `refactoring` | 7 | 책임 위치 오류, 리팩터와 로직 변경 혼재, 로케일 의존 동작, 불필요한 가변성 |
| `performance` | 5 | 디스크 중복 읽기, 슬라이스 사전 할당 누락, 클로저의 self 캡처 |
| `control` | 5 | **대조군** — 리뷰를 지적 없이 통과한 코드 |
| `security` | 4 | 검증 없는 원격 플러그인 실행(공급망 RCE), 호출자 지정 수수료, 인증서 처리 |
| `functional_bug` | 4 | 디버깅 코드 잔존, 애니메이션 대상 오류, 독스트링 들여쓰기, 불완전한 오류 메시지 |
| `api_contract` | 3 | Redis 프로토콜 불일치, 문서화되지 않은 파라미터 동작, 모호한 공개 타입명 |
| `exception_handling` | 3 | 옵셔널 처리, 인자 검증 취약, 무조건 재계산 |
| `n_plus_one` | 1 | 루프 내 반복 조회 |

원래 목표 분포(보안 5 / 성능 3 / N+1 3 …)와 다릅니다. **200,001행에서 조건을 만족하는 실제 사례가 그만큼만 나왔기 때문이며, 억지로 채우지 않았습니다.**
특히 `n_plus_one` 은 1건뿐입니다(9절 참고).

### 6.2 언어와 저장소

Swift 7 / Python 6 / TypeScript 5 / Go 4 / C# 3 / Shell 3 / Solidity 2 / JavaScript 1 / Kotlin 1 — **9개 언어, 15개 저장소**

한 저장소가 표본을 독점하지 않도록 저장소당 최대 3건으로 제한했습니다.

### 6.3 난이도와 심각도

- 난이도: easy 14 / medium 13 / hard 5
- 기대 문제 27건 — high 4 / medium 10 / low 13
- 대조군 5건은 `issues: []`

`low` 가 많은 것은 실제 리뷰 댓글에 `nit:` 수준의 지적이 흔하기 때문입니다.

### 6.4 대조군

`reviewer_comment` 가 `"No issues found."` 이고 `is_negative=True` 인 행 5건입니다.
같은 PR 에서 변경되었지만 리뷰 댓글을 받지 않은 코드 조각이며, 데이터셋 카드가 밝힌 negative example 입니다.

이 샘플들은 `has_actual_issue: false`, `review_result: "approve"`, `issues: []`, `is_control_sample: true` 이며 **환각 비율(Control Hallucination Rate) 측정에 사용합니다.**

## 7. 데이터 구조

```jsonc
{
  "id": "security-001",                        // <category>-NNN
  "classification": {
    "category": "security",                    // 사람이 확정
    "subtype": "supply_chain_unverified_execution",
    "difficulty": "medium",
    "programming_language": "Shell",
    "has_actual_issue": true,
    "category_source": "human_selected",
    "dataset_comment_type": "security"         // 데이터셋의 원래 라벨
  },
  "source": {
    "type": "huggingface_dataset",
    "dataset_name": "ronantakizawa/github-codereview",
    "dataset_split": "train",
    "dataset_row_id": "12345",
    "repository": "DrewThomasson/ebook2audiobook",
    "pr_number": 1289,
    "commit_sha": "",                          // 원본에 없어 비워 둡니다
    "source_url": "https://github.com/DrewThomasson/ebook2audiobook/pull/1289",
    "license": "other (사용 조건 확인 필요)",
    "retrieved_at": "2026-08-02T17:12:56+00:00"
  },
  "translation": { "method": "claude-assisted", "review_status": "pending", "notes": "" },
  "input": {
    "file_path": "ebook2audiobook.sh",
    "pr_title_original": "v25.12.13",
    "pr_title_ko": "v25.12.13",
    "pr_description_original": "",             // 원본 데이터셋에 PR 본문이 없습니다
    "pr_description_ko": "",
    "diff": "@@ -407,7 +407,23 @@ function install_programs {...",
    "diff_truncated": false,
    "review_comment_original": "The `install_programs` branch for `apk`...",  // 정답 근거 (2.5절)
    "review_comment_ko": "`apk` 용 `install_programs` 분기가...",
    "additional_context": "이 diff 는 파일 전체가 아니라 리뷰 대상 코드 주변 약 50줄입니다. ..."
  },
  "expected": {
    "change_summary_ko": "...", "change_purpose_ko": "...", "change_reason_ko": "...",
    "before_ko": "...", "after_ko": "...",
    "review_result": "request_changes",        // approve | request_changes | needs_confirmation
    "issues": [{
      "issue_id": "issue-001", "category": "security",
      "subtype": "supply_chain_unverified_execution",
      "severity": "high", "confidence": 0.9,
      "file_path": "ebook2audiobook.sh", "line_reference": "...",
      "evidence": "+\t\t\t\tcurl -L -o /tmp/un-get.plg https://...",
      "problem_ko": "...", "impact_ko": "...", "recommendation_ko": "..."
    }],
    "affected_roles": ["backend", "qa", "project_manager"],
    "role_impacts":    [{ "role": "backend", "impact_ko": "...", "evidence": "..." }],
    "follow_up_tasks": [{ "role": "backend", "task_ko": "...", "evidence": "..." }],
    "needs_confirmation": ["..."]
  },
  "evaluation_metadata": {
    "expected_issue_count": 1,
    "contains_security_issue": true,
    "contains_performance_issue": false,
    "contains_n_plus_one_issue": false,
    "contains_breaking_change": false,
    "is_control_sample": false
  },
  "human_review": { "status": "pending", "reviewer": null, "reviewed_at": null, "notes": "" }
}
```

`commit_sha` 는 원본 데이터셋에 없어 빈 문자열입니다. 지어내지 않았습니다.

### 7.1 출력 필드 이름 매핑

데이터셋의 `expected` 는 `_ko` 접미사가 붙은 snake_case 를, 프롬프트 출력 스키마(`prompts/korean_code_review_output_schema.json`)는 Contextory 서비스 필드명(camelCase)을 씁니다.

| 데이터셋 `expected` | 프롬프트 출력 |
| --- | --- |
| `summary_ko` / `purpose_ko` / `change_reason_ko` | `summary` / `purpose` / `changeReason` |
| `before_ko` / `after_ko` | `before` / `after` |
| `related_features` / `affected_roles` | `relatedFeatures` / `affectedRoles` |
| `role_impacts[].impact_ko` | `roleImpacts[].impact` |
| `follow_up_tasks` / `needs_confirmation` | `followUpTasks` / `needsConfirmation` |
| `issues[].problem_ko` / `line_reference` | `issues[].problem` / `lineReference` |

이 매핑은 `scripts/evaluate_results.py` 가 처리합니다.

## 8. 사람이 검토해야 하는 항목

**현재 32건 전부 `human_review.status: "pending"` 입니다. 상태는 `candidate_gold` 이며 확정된 정답이 아닙니다.**

절차와 항목은 `evaluation/human_review_sheet.md` 에 있습니다. 요약하면:

1. `source.source_url` 이 실제로 열리고, 그 PR 에 해당 리뷰 댓글이 존재하는지
2. `input.diff` 가 리뷰 댓글의 지적 대상과 일치하는지
3. 각 `evidence` 를 `input.diff` 에서 찾을 수 있는지
4. `expected` 서술이 diff·PR 제목·리뷰 댓글 범위를 넘지 않는지
5. 확정 카테고리가 리뷰 댓글의 실제 내용과 맞는지
6. 대조군 5건이 정말 지적할 것이 없는 코드인지

전부 승인되면 `human_review.status` 를 `approved` 로 바꾸고 데이터셋 `status` 를 `gold` 로 올립니다.
**단, 2.3절의 라이선스 조건이 확인되기 전에는 `gold` 로 올리지 않습니다.**
검증 스크립트는 미승인 샘플이 있는데 `gold` 로 표시되면 ERROR 로 실패합니다.

## 9. 알려진 한계

1. **표본이 작습니다.** 32건으로는 통계적 유의성을 주장할 수 없습니다. 프롬프트 비교 결과는 경향으로만 해석해야 합니다.
2. **`n_plus_one` 이 1건뿐입니다.** 200,001행을 훑었지만 반복 조회를 명시적으로 지적한 리뷰가 드물었습니다. 이 유형의 지표는 신뢰하기 어렵습니다.
3. **라이선스 미확인** (2.3절).
4. **저장소별 라이선스 미확인.** 15개 저장소를 개별 확인하지 않았습니다.
5. **입력이 코드 청크 단위입니다.** 파일 전체나 PR 전체가 아니라 리뷰 대상 주변 약 50줄이므로, PR 전체를 다루는 실제 서비스와 입력 형태가 다릅니다.
6. **PR 설명(body)이 없습니다.** 원본 데이터셋에 PR 제목만 있고 본문이 없어, `purpose`·`changeReason` 을 판단할 근거가 제한적입니다. 다수 샘플의 `change_reason_ko` 가 "확인할 수 없습니다" 로 되어 있는 이유입니다.
7. **샘플당 기준 문제가 1건입니다.** 실제 리뷰어가 한 지점만 지적했기 때문이며, 모델이 다른 타당한 문제를 찾아도 False Positive 로 집계됩니다. 사람 재확인이 필요합니다.
8. **`low` 심각도가 13건으로 많습니다.** 실제 리뷰에 `nit:` 수준 지적이 흔하기 때문입니다.
9. **일부 샘플은 리뷰어 본인도 확신하지 않았습니다.** 예: `Chia-Network/chia-blockchain#19630`("다시 확인해 봐야겠지만"), `CryptoBlades/cryptoblades#1349`("지금 당장 바꿀 필요는 없고"). 해당 샘플의 `confidence` 를 0.6~0.75 로 낮췄습니다.
10. **diff 뒷부분이 잘린 샘플이 있습니다.** 리뷰어가 지적한 지점이 제공된 청크 밖에 있는 경우, `needs_confirmation` 에 그 사실을 적었습니다.
11. **번역 교차 검토 부재.**

## 10. 평가 시 주의사항

1. **리뷰 댓글을 입력에 넣지 마세요.** 정답 누출입니다. 검증 스크립트가 검사합니다.
2. **대조군 5건을 반드시 포함해 평가하세요.** 탐지율만 보면 "모든 것을 문제로 지적하는" 프롬프트가 유리해집니다. `control_hallucination_rate` 를 함께 봐야 합니다.
3. **`expected` 를 유일한 정답으로 취급하지 마세요** (9절 7번).
4. **`needs_confirmation` 을 문제로 채점하지 마세요.** 확인이 필요한 항목을 단정하지 않는 것이 올바른 동작입니다.
5. **문제 매칭은 카테고리 기준입니다.** 샘플당 기준 문제가 1건이고 diff 도 조각 하나이므로 파일 경로로 매칭하지 않습니다.
6. **입력은 원문을 사용하세요.** 번역본(`translation.*.korean`)이 아니라 `input.pr_title` / `input.file_path` / `input.diff` 를 씁니다.

## 11. 데이터 추가·수정 방법

`data/test_samples.json` 은 **생성물**입니다. 손으로 편집하지 않습니다.

```text
data/raw/hf_github_codereview_raw.jsonl   (수집 원본: 편집하지 않음)
data/hf_selection.tsv                     (선별 목록: 샘플 추가/제거 시 편집)
        +
data/annotations.json                     (한국어 주석: 기대값 수정 시 편집)
        ↓ scripts/build_test_samples.py
data/test_samples.json                    (생성물)
```

### 샘플 추가

```bash
# 1) 더 많은 후보 수집 (--target 을 늘리거나 --max-per-repo 를 조정)
.venv/bin/python scripts/fetch_hf_dataset.py --target 60 --negatives 8

# 2) data/hf_selection.tsv 에 "owner/repo#번호<TAB>카테고리" 한 줄 추가
# 3) data/annotations.json 에 항목 추가 (ref, id, classification, translation, expected)
# 4) 재생성 및 검증
python scripts/build_test_samples.py && python scripts/validate_test_samples.py
```

빌드 스크립트는 선별 목록과 주석이 서로 어긋나면(주석만 있거나 선별만 있으면) 오류로 중단합니다.

### 검증 규칙

- JSON 파싱, 최상위 필수 필드, `total_samples` 와 실제 개수 일치
- `id` 중복, PR 중복, 동일 diff 중복
- 카테고리·난이도·심각도·리뷰 결과·번역 방법·검토 상태의 허용값
- **`id` 가 `<category>-NNN` 형식인지**
- **`review_result` 가 approve/request_changes/needs_confirmation 중 하나인지**
- **대조군은 `is_control_sample`, `review_result == approve`, `issues == []` 인지**
- **`contains_*_issue` 플래그가 category 와 일치하는지**
- **`role_impacts` 의 역할이 `affected_roles` 안에 있는지, evidence 가 있는지**
- **`follow_up_tasks` 에 role 과 evidence 가 있는지**
- **`*_ko` 만 있고 `*_original` 이 없는 번역이 없는지**
- `source.type` 이 `huggingface_dataset` 인지, 필수 출처 필드(`source_url`, `dataset_row_id` 포함)가 채워졌는지
- `commit_sha` 가 비어 있지 않다면 40자리 SHA 형식인지
- PR URL 형식
- `confidence` 가 0.0~1.0 범위인지
- `has_actual_issue` 와 `issues` 개수의 정합성, 대조군에 `issues` 가 없는지
- 각 문제에 `issue_id`, `evidence`, `file_path`, `line_reference` 가 있는지, `evidence` 첫 줄을 diff 에서 찾을 수 있는지
- 민감 정보 의심 문자열
- 대조군 존재 여부, 라이선스 확인 상태
- 미승인 샘플이 있는데 데이터셋이 `gold` 로 표시되어 있는지

## 12. 현재 검증 결과

```text
$ python scripts/validate_test_samples.py
검증 대상: data/test_samples.json (샘플 32건)
  ERROR 0건 / WARN 0건
  문제 없음
```

`consistency_checks` 항목을 별도로 재확인한 결과도 전부 통과입니다.

- `has_actual_issue == false` → `issues == []` 이고 `expected_issue_count == 0`
- `expected_issue_count == len(issues)`
- `is_control_sample == true` → `category == control`, `issues` 비어 있음, `review_result == approve`
- `security` / `performance` / `n_plus_one` → 대응 `contains_*_issue` 플래그 참
- 모든 issue 에 `evidence` 존재
- 모든 샘플 `human_review.status == "pending"`, 데이터셋 `status == "candidate_gold"`
- 모든 `role_impacts` / `follow_up_tasks` 항목에 `role` 과 `evidence` 존재
