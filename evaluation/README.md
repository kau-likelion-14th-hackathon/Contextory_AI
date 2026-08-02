# evaluation

프롬프트 비교 실험의 결과와 평가 지표를 두는 디렉터리입니다.

```text
evaluation/
├── README.md                 이 문서
├── human_review_sheet.md     사람 검토 채점 서식
├── metrics.json              자동 지표 계산 결과 (생성물)
└── results/
    ├── v1_evidence_first/
    │   ├── results.jsonl     샘플별 모델 응답
    │   └── run_meta.json     실행 조건과 요약
    ├── v2_risk_first/
    └── v3_contextory/
```

## 현재 상태: dry-run 결과만 들어 있습니다

`results/*/results.jsonl` 의 모든 레코드는 `"status": "dry_run"` 이며 **모델 응답이 아닙니다.**
작업 환경에 `OPENAI_API_KEY` 가 설정되어 있지 않아 실제 호출을 하지 않았습니다.

dry-run 은 다음을 확인한 것입니다.

- 세 프롬프트 문서에서 System Prompt 블록이 정상적으로 추출되는지
- 정답인 리뷰 댓글이 모델 입력에 섞이지 않는지
- 32건 전체에 대해 공통 입력 형식이 구성되는지
- 결과 저장 경로와 파일 형식이 의도대로 만들어지는지

각 `run_meta.json` 의 `"dry_run": true` 로도 확인할 수 있습니다.

## 실제 실행 방법

```bash
export OPENAI_API_KEY=...     # 값은 셸에서만 주입합니다
pip install openai            # 프로젝트 런타임 의존성이 아니므로 requirements.txt 에는 없습니다

python scripts/run_prompt_experiment.py          # 결과가 덮어써집니다
python scripts/evaluate_results.py               # metrics.json 생성
```

실행 조건은 `scripts/run_prompt_experiment.py` 의 `FIXED_CONDITIONS` 에 고정되어 있습니다.
프롬프트 간 비교가 성립하려면 이 값을 바꾸지 않아야 합니다. 자세한 설계는 `docs/korean_code_review_prompt_experiment.md` 를 참고하세요.

## 지표 해석

`scripts/evaluate_results.py` 는 dry-run 결과를 감지하면 지표를 계산하지 않고 `dry_run_only` 로 보고합니다.
자동 지표만으로는 품질을 판정할 수 없으며, `human_review_sheet.md` 의 사람 검토 항목을 함께 채점해야 합니다.

판정 기준은 `docs/korean_code_review_prompt_experiment.md` 6절에 있습니다.
