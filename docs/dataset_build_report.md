# Contextory AI 한국어 코드 리뷰 평가 데이터셋 구축 결과 보고서

- 작성일: 2026-08-03
- 대상 저장소: `kau-likelion-14th-hackathon/Contextory_AI` (branch: `feat/#7huggingface데이터셋`)
- 작업 범위: 코드 리뷰 데이터 수집 → 한국어화 → 평가 기준 데이터셋 구축 → System Prompt 3종 작성 → 비교 실험 설계
- 작성자: TODO
- 기여자: TODO

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 수집 원본 | 41건 (Hugging Face `ronantakizawa/github-codereview`) |
| 한국어화 | **41건 전부 완료** |
| 평가 기준 데이터셋 | **32건** (`candidate_gold`, 대조군 5건 포함) |
| System Prompt | **3종 작성 완료** (v1/v2/v3) |
| 검증 | **ERROR 0건 / WARN 0건** |
| 실제 모델 호출 실험 | **미실행** (API 키 없음) |

**핵심 성과**: 실제 사람 리뷰어가 남긴 지적을 정답으로 삼는 평가 데이터셋을 구축했고, 문제가 없는 코드에 대한 환각을 측정할 수 있는 대조군 5건을 확보했습니다.

**핵심 한계**: LLM API 키가 없어 프롬프트 비교 실험을 실행하지 못했습니다. 따라서 세 템플릿 중 무엇이 나은지는 **아직 결론이 없습니다.**

---

## 2. 저장소 조사 결과

작업 지시서의 기술과 실제 저장소 구조가 달라, 실제 코드를 우선했습니다.

| 항목 | 지시서 기재 | 실제 확인 |
| --- | --- | --- |
| AI 파트 루트 | `AI_service/` | **저장소 루트 자체** (`AI_service/` 디렉터리 없음) |
| 브랜치 | `develop` | `feat/#7huggingface데이터셋` |
| `models/` Pydantic 스키마 | 있을 수 있음 | **비어 있음** → 기획 기준 필드명 채택 |
| `llamaindex/`, `tests/` | 존재 | **없음** (README 트리는 계획안) |
| `data/`, `prompts/`, `scripts/`, `docs/`, `evaluation/` | 없을 것 | **없음 확인** → 신규 생성 |

기존 파일은 `main.py`, `routers/health.py` 뿐이며 **둘 다 수정하지 않았습니다.**

### 2.1 환경 실측

| 자원 | 상태 |
| --- | --- |
| 인터넷 | 가능 |
| Hugging Face | 가능 (공개 데이터셋은 토큰 불필요) |
| GitHub API | 가능 (비인증, 60req/hr) |
| LLM API 키 (`OPENAI_API_KEY`) | **없음** |
| `GITHUB_TOKEN` / `gh` CLI | 없음 |
| `datasets` 패키지 | 미설치 → `.venv` 생성 후 설치 |

---

## 3. 데이터 수집 결과

### 3.1 사용한 데이터셋

**Hugging Face `ronantakizawa/github-codereview` (train split)** — 공개 데이터셋이라 **토큰 없이 익명으로** 수집했습니다.

```
훑은 행: 200,001
저장:    41건 (대조군 5건 포함)
저장소:  15개
언어:    9종
```

### 3.2 `microsoft/codereviewer` 를 쓰지 않은 이유

**`microsoft/codereviewer` 는 데이터셋이 아니라 모델입니다.**

```
GET https://huggingface.co/api/models/microsoft/codereviewer    -> 200
GET https://huggingface.co/api/datasets/microsoft/codereviewer  -> 401
```

데이터셋 경로의 401 은 인증 문제로 보이지만, 존재하지 않는 이름에도 같은 401 이 반환됩니다.
`microsoft` 조직의 공개 데이터셋 107개 중 이름에 `review` 가 포함된 것은 없습니다.
**토큰을 발급받아도 이 경로는 사용할 수 없습니다.**

### 3.3 제외 규칙과 실제 제외 건수

| 사유 | 건수 |
| --- | --- |
| 저장소 상한(저장소당 최대 3건) 초과 | 2,407 |
| diff 6,000자 초과 | 39 |
| diff 80자 미만 | 17 |
| 같은 PR 중복 | 17 |
| 리뷰 댓글 2,000자 초과 | 7 |

전체(356k)를 내려받지 않고 `streaming=True` 로 앞부분만 훑었습니다.

### 3.4 라이선스 — **확인 필요**

| 항목 | 값 |
| --- | --- |
| 데이터셋 카드 표기 | `other` |
| 기록 | `license_status: "확인 필요"` |

카드 본문은 *"Permissive licenses only: all source repos use MIT, Apache-2.0, BSD, or similar"* 라고 밝히지만,
데이터셋 자체 조건이 `other` 이므로 **확인 전까지 `gold` 로 확정하지 않습니다.**
15개 저장소의 라이선스를 개별 확인하지는 않았습니다.

---

## 4. 한국어화 결과

수집한 **41건 전부**를 한국어로 정리했습니다.

| 산출물 | 크기 | 내용 |
| --- | --- | --- |
| `data/localized/reviews_ko.json` | 136K | 원본 무손실 (diff 포함) |
| `data/localized/reviews_ko.csv` | 44K | 요약 열람용 (diff 제외, UTF-8 BOM 없음) |

| 한국어 필드 | 채움 |
| --- | --- |
| `pr_title_ko` / `review_comment_ko` / `change_summary_ko` | 41/41 |
| `change_purpose_ko` | 38/41 |
| `problem_ko` / `impact_ko` / `recommendation_ko` | 30/41 |
| `pr_description_ko` | **0/41** |

### 4.1 비워 둔 필드와 사유

- **`pr_description_ko` 41건 전부**: 원본 데이터셋에 PR 본문 자체가 없습니다.
- **`change_purpose_ko` 3건**: `ebook2audiobook#1289/1290/1291` 의 PR 제목이 `v25.12.13`(버전 표기)뿐이라 목적 근거가 없습니다.
- **`problem_ko` 등 11건**: 리뷰가 문제를 지적하지 않은 경우입니다.
  - 대조군 5건 (`"No issues found."`)
  - 질문 1건, 작성자 설명 1건, 스타일·의견 4건

각 건마다 사유를 `needs_confirmation` 에 기록했습니다. **억지로 채우지 않았습니다.**

### 4.2 번역 방식

- 전체 `claude-assisted` (이 세션에서 직접 번역)
- **GPT-4o 등 외부 LLM API 는 호출하지 않았습니다.** API 키가 없습니다.
- 문체: 한국어 `~합니다` 설명체 통일
- 코드·식별자·경로·명령어·로그는 원문 유지
- 원문(`*_original`)과 번역(`*_ko`)을 짝으로 보존

---

## 5. 평가 기준 데이터셋

`data/test_samples.json` — v0.4.0, **32건**, `status: candidate_gold`

### 5.1 유형별 구성

| 카테고리 | 건수 | 목표(24 기준) | 대표 사례 |
| --- | --- | --- | --- |
| `refactoring` | 7 | 2 | 책임 위치 오류, 리팩터·로직 변경 혼재 |
| `performance` | 5 | 3 | 디스크 중복 읽기, 슬라이스 사전 할당 누락 |
| `control` | 5 | 2 | **대조군** — 리뷰를 지적 없이 통과한 코드 |
| `security` | 4 | 5 | 검증 없는 원격 플러그인 실행(공급망 RCE) |
| `functional_bug` | 4 | 4 | 디버깅 코드 잔존, 애니메이션 대상 오류 |
| `api_contract` | 3 | 3 | Redis 프로토콜 불일치 |
| `exception_handling` | 3 | 2 | 옵셔널 처리, 인자 검증 취약 |
| `n_plus_one` | **1** | 3 | 루프 내 반복 조회 |

목표 분포와 다른 이유: **200,001행에서 조건을 만족하는 실제 사례가 그만큼만 나왔습니다. 억지로 채우지 않았습니다.**
필수 6개 유형(보안/성능/N+1/기능오류/리팩터링/대조군)은 모두 1건 이상 포함됩니다.

### 5.2 그 밖의 분포

| 항목 | 값 |
| --- | --- |
| 언어 (9종) | Swift 7 / Python 6 / TypeScript 5 / Go 4 / C# 3 / Shell 3 / Solidity 2 / JavaScript 1 / Kotlin 1 |
| 저장소 | 15개 |
| 난이도 | easy 14 / medium 13 / hard 5 |
| 문제 있는 샘플 | 27건 |
| 기대 문제(issues) | 27건 — high 4 / medium 10 / low 13 |
| `review_result` | `request_changes` 27 / `approve` 5 |
| `role_impacts` / `follow_up_tasks` | 70건 / 32건 (전부 `evidence` 보유) |
| `needs_confirmation` 보유 샘플 | 24건 |
| diff 평균 길이 | 974자 |

### 5.3 정답의 성격

이 데이터셋의 정답은 **합성된 것이 아니라 공개 PR 에 실제로 달린 사람 리뷰어의 지적**입니다.

```
input.diff                    = 리뷰어가 댓글을 단 코드 주변 약 50줄
input.review_comment_original = 실제 리뷰 지적  ← expected.issues 의 출처
expected.issues               = 그 지적을 한국어로 정리한 정답
```

### 5.4 정답 누출 방지 — 중요

`input.review_comment_original` 을 모델 프롬프트에 그대로 넣으면 **Precision/Recall 이 무의미해집니다.**

- `run_prompt_experiment.py` 가 이 필드를 프롬프트에서 제외합니다 (`review_comment_in_input == False`).
- dry-run 으로 **32건 전부 누출 없음**을 확인했습니다.
- 모델에 실제 전달되는 것: 프로젝트 정보, PR 제목, PR 설명, 파일 경로, 언어, diff, 추가 문맥.

### 5.5 대조군 확보 경위

데이터셋 카드에서 *"51K+ negative examples (~23%) labeled 'No issues found.'"* 를 발견해 **대조군 5건**을 확보했습니다.
`is_negative=True` 이고 같은 PR 에서 변경되었지만 리뷰 댓글을 받지 않은 코드 조각입니다.

이 5건으로 **환각 비율(Control Hallucination Rate)** 을 측정합니다.

### 5.6 자동 라벨을 그대로 쓰지 않은 이유

데이터셋의 `comment_type` 은 참고용 1차 라벨입니다. 리뷰 댓글과 diff 를 직접 읽고 카테고리를 확정했습니다.

- `DiceDB/dice#458` 은 `comment_type: security` 지만 실제로는 Redis 프로토콜 호환성 → `api_contract`
- `4ian/GDevelop#6970` 은 `comment_type: question` 이지만 불필요한 재계산 지적 → 성능 문제

확정 목록은 `data/hf_selection.tsv`, 원래 라벨은 `classification.dataset_comment_type` 에 보존됩니다.
**제외한 9건과 사유**도 같은 파일 주석에 기록했습니다.

### 5.7 검증 결과

```
$ python scripts/validate_test_samples.py
검증 대상: data/test_samples.json (샘플 32건)
  ERROR 0건 / WARN 0건
  문제 없음
```

`consistency_checks` 를 독립 스크립트로 재확인한 결과도 **전부 통과**입니다.

- `has_actual_issue == false` → `issues == []` 이고 `expected_issue_count == 0`
- `expected_issue_count == len(issues)`
- `is_control_sample == true` → `category == control`, `issues` 비어 있음, `review_result == approve`
- `security`/`performance`/`n_plus_one` → 대응 `contains_*_issue` 플래그 참
- 모든 issue 에 `evidence` 존재
- 모든 샘플 `human_review.status == "pending"`, 데이터셋 `status == "candidate_gold"`

---

## 6. System Prompt 3종

| | v1 Evidence First | v2 Risk First | v3 Contextory |
| --- | --- | --- | --- |
| 최적화 목표 | FP·환각 최소화 | 놓친 위험 최소화 | 역할별 전달력 |
| 핵심 장치 | 근거를 먼저 고르고 설명은 나중에 / 4단계 자기 점검 | 12단계 위험 체크리스트 / severity 정렬 | 9개 분석 질문 / 역할별 판단 기준 7종 / 분량 규칙 |
| System Prompt 길이 | 2,359자 | 2,767자 | 3,212자 |

**공정성 확보**: 세 템플릿은 동일 입력 블록과 동일 출력 스키마를 공유합니다. 바뀌는 것은 System Prompt 하나뿐입니다.

출력 스키마의 필드명을 기준 데이터셋의 `expected` 와 **일치**시켜, 별도 매핑 없이 바로 비교할 수 있게 했습니다.

---

## 7. 비교 실험 — **미실행**

### 7.1 실행 여부

| 항목 | 상태 |
| --- | --- |
| 템플릿 3종 작성 | 완료 |
| 실행 스크립트 작성 | 완료 |
| 지표 계산 스크립트 작성 | 완료 |
| dry-run (모델 호출 없음) | **실행함** — 32건 × 3변형 |
| **실제 모델 호출** | **미실행** |

**미실행 사유**: 작업 환경에 `OPENAI_API_KEY` 가 설정되어 있지 않습니다.

### 7.2 결과 표 — 비어 있음

| 지표 | v1 | v2 | v3 |
| --- | --- | --- | --- |
| JSON Valid Rate | 미측정 | 미측정 | 미측정 |
| Evidence Grounding Rate | 미측정 | 미측정 | 미측정 |
| Control Hallucination Rate | 미측정 | 미측정 | 미측정 |
| Precision / Recall / F1 | 미측정 | 미측정 | 미측정 |
| Severity / Review Result 일치도 | 미측정 | 미측정 | 미측정 |
| 한국어 자연스러움·후속작업·간결성 | 미채점 | 미채점 | 미채점 |

`evaluate_results.py` 가 dry-run 을 감지해 지표 계산을 거부하고 `dry_run_only` 로 보고합니다.

### 7.3 최종 추천 — 아직 없음

측정값 없이 추천하면 근거 없는 결론이 됩니다. 대신 **판정 규칙을 확정**해 두었습니다.

1. 대조군 환각 ≠ 0 → 제외
2. JSON Valid Rate < 100% → 감점
3. Evidence Grounding Rate 최하위 → 제외
4. 남은 것 중 F1 최고
5. F1 차이 0.05 이내면 → 자연스러움 + 후속작업 실행가능성 + 간결성 합계

**설계상 예상되는 트레이드오프** (가설이며 측정값 아님):

- **v1**: 환각·근거정확성 유리 / 맥락 설명이 얕아 자연스러움·후속작업 불리
- **v2**: Recall 유리 / 체크리스트를 채우려는 경향으로 대조군 FP 증가 가능
- **v3**: 역할별 영향·후속작업 구체적 / 출력이 길어 JSON 준수율·간결성 불리 가능

Contextory 는 "코드를 읽지 않는 팀원에게 맥락을 전달하는" 제품이므로, 환각이 동률이면 자연스러움·후속작업 비중을 높여 판단하도록 규칙에 명시했습니다.

### 7.4 실험 실행 방법

```bash
export OPENAI_API_KEY=...        # 셸에서만 주입
pip install openai               # 실험 전용. requirements.txt 에 넣지 않았습니다

python scripts/run_prompt_experiment.py --limit 3   # 소규모 시험
python scripts/run_prompt_experiment.py             # 전체 32건 × 3변형
python scripts/evaluate_results.py                  # 자동 지표
# evaluation/human_review_sheet.md 로 사람 검토 채점
```

---

## 8. 산출물 목록

### 8.1 데이터

| 파일 | 크기 | 내용 |
| --- | --- | --- |
| `data/test_samples.json` | 208K | **평가 기준 데이터셋 32건** |
| `data/raw/hf_github_codereview_raw.jsonl` | 356K | 수집 원본 41건 |
| `data/raw/hf_github_codereview_raw.meta.json` | — | 수집 메타(제외 사유 포함) |
| `data/annotations.json` | 116K | 한국어 주석 (선별 32 + 미선별 9) |
| `data/hf_selection.tsv` | — | 선별 목록과 제외 사유 |
| `data/localized/reviews_ko.json` | 136K | 한글화 41건 (원본 무손실) |
| `data/localized/reviews_ko.csv` | 44K | 한글화 요약 (열람용) |
| `data/DATASET_CARD.md` | — | 데이터셋 문서 |

### 8.2 프롬프트

- `prompts/korean_code_review_v1_evidence_first.md`
- `prompts/korean_code_review_v2_risk_first.md`
- `prompts/korean_code_review_v3_contextory.md`
- `prompts/korean_code_review_output_schema.json`

### 8.3 스크립트

| 파일 | 역할 |
| --- | --- |
| `scripts/fetch_hf_dataset.py` | HF streaming 수집 |
| `scripts/localize_reviews.py` | 한글화 JSON/CSV 생성 |
| `scripts/build_test_samples.py` | 원본 + 선별 + 주석 병합 |
| `scripts/validate_test_samples.py` | 데이터셋 검증 |
| `scripts/run_prompt_experiment.py` | 프롬프트 3종 비교 실행 |
| `scripts/evaluate_results.py` | 자동 지표 계산 |

### 8.4 문서·평가

- `docs/korean_code_review_prompt_experiment.md` — 실험 계획서
- `docs/dataset_build_report.md` / `.txt` — 이 보고서
- `evaluation/README.md`, `evaluation/human_review_sheet.md`
- `evaluation/results/{v1,v2,v3}/results.jsonl` — **dry-run 결과** (모델 응답 아님)
- `evaluation/metrics.json`

---

## 9. 사람이 검토해야 하는 항목

**32건 전부 `human_review.status: "pending"` 입니다.**

1. `source.source_url` 이 실제로 열리고, 그 PR 에 해당 리뷰 댓글이 존재하는지
2. `input.diff` 가 리뷰 댓글의 지적 대상과 일치하는지
3. 각 `evidence` 를 `input.diff` 에서 찾을 수 있는지
4. `expected` 서술이 diff·PR 제목·리뷰 댓글 범위를 넘지 않는지
5. 확정 카테고리가 리뷰 댓글의 실제 내용과 맞는지
6. 대조군 5건이 정말 지적할 것이 없는 코드인지

전부 승인되고 **라이선스 조건이 확인되면** `status` 를 `gold` 로 올립니다.

---

## 10. 미완료 및 확인 필요 사항

| 항목 | 상태 | 조치 방법 |
| --- | --- | --- |
| **실제 모델 호출 실험** | 미실행 | `OPENAI_API_KEY` 주입 후 7.4절 실행 |
| **사람 검토 채점** | 미실행 | 실험 결과가 나와야 채점 가능 |
| **데이터셋 라이선스** | 확인 필요 | 카드는 `other`, 본문은 permissive 라고만 표기 |
| **저장소별 라이선스** | 미확인 | 15개 저장소 개별 확인 필요 |
| `n_plus_one` 1건 | 표본 부족 | 해당 카테고리 지표 신뢰 불가 |
| 대조군 5건 | 해상도 한계 | 환각 비율이 20% 단위 (1건 = 20%) |
| PR 설명 부재 | 원본 제약 | `change_purpose_ko` 품질 저하 가능 |
| 번역 교차 검토 | 미실시 | 단일 출처 번역 |

---

## 11. 준수 사항 확인

| 항목 | 결과 |
| --- | --- |
| 존재하지 않는 저장소·PR·URL·커밋 SHA 생성 | **없음** (`commit_sha` 는 원본에 없어 빈 문자열) |
| diff 에 없는 문제를 정답으로 작성 | **없음** (모든 issue 에 diff 인용 evidence) |
| 대조군에 억지 문제 | **없음** (`issues: []`) |
| 사람 미검토 데이터를 gold 로 표시 | **없음** (`candidate_gold` 유지) |
| 민감정보 저장 | **검출 0건** (토큰/키/이메일 마스킹 적용) |
| 실행하지 않은 실험을 실행했다고 기재 | **없음** |
| 기존 FastAPI 코드 변경 | **없음** |
| `requirements.txt` 변경 | **없음** (실험 의존성은 `.venv` 에만 설치) |
| API 키 하드코딩 | **없음** (환경 변수로만 읽음) |
| Git 커밋·푸시 | **미수행** (워킹 트리에 그대로 둠) |
| 산출물 내 작성자·AI 표기 | **검출 0건** |

---

## 12. 추천 커밋 메시지

```
feat(ai): add Korean code review evaluation dataset and prompt templates

- Collect 41 code review samples from Hugging Face (streaming, 200k rows scanned)
- Localize all 41 samples into Korean with original text preserved
- Build ground-truth dataset of 32 samples across 8 categories with 5 controls
- Add three System Prompt variants sharing one input block and output schema
- Add fetch, localize, build, validation, experiment, and evaluation scripts
- Add dataset card, experiment plan, and human review sheet

Dataset status is candidate_gold; dataset license condition is unverified.
Prompt comparison experiment is not executed yet (no LLM API key available).
```
