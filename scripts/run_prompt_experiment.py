"""한국어 코드 리뷰 System Prompt 3종을 동일 조건으로 비교 실행한다.

세 프롬프트(v1/v2/v3)에 같은 샘플, 같은 모델, 같은 파라미터, 같은 입력 순서를 적용하고
결과를 프롬프트별 JSONL 로 저장한다.

전제:
    - `OPENAI_API_KEY` 환경 변수가 설정되어 있어야 합니다. 키를 소스 코드에 적지 않습니다.
    - openai 파이썬 패키지가 필요합니다. 프로젝트 런타임 의존성이 아니므로
      requirements.txt 에는 추가하지 않았습니다. 실험을 돌릴 때만 설치합니다.

        pip install openai

실행:
    export OPENAI_API_KEY=...            # 값은 셸에서만 주입합니다.
    python scripts/run_prompt_experiment.py --dry-run          # 호출 없이 입력만 점검
    python scripts/run_prompt_experiment.py                     # 전체 실행
    python scripts/run_prompt_experiment.py --prompts v1_evidence_first --limit 3

출력:
    evaluation/results/{프롬프트}/results.jsonl
    evaluation/results/{프롬프트}/run_meta.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 고정 실험 조건. 프롬프트 간 비교가 성립하려면 이 값들이 모든 실행에서 같아야 한다.
FIXED_CONDITIONS = {
    "model": "gpt-4o",
    "temperature": 0.0,
    "top_p": 1.0,
    "max_output_tokens": 4096,
    "response_format": "json_object",
    "max_retries": 2,
    "retry_backoff_seconds": 5,
    "input_field_order": [
        "프로젝트 정보",
        "PR 제목",
        "PR 설명",
        "변경 파일 경로",
        "프로그래밍 언어",
        "코드 Diff",
        "관련 리뷰 댓글",
        "추가 문맥",
    ],
    # 리뷰 댓글은 기준 데이터셋의 정답 근거이므로 평가 입력에 넣지 않는다.
    "review_comment_in_input": False,
}

# 세 프롬프트에 동일하게 주입하는 프로젝트 정보. README 의 서비스 설명을 따른다.
PROJECT_INFO = {
    "이름": "Contextory",
    "한 줄 설명": "GitHub PR 변경 사항을 팀 전체가 이해할 수 있게 정리해 주는 서비스입니다.",
    "목적": "코드 변경의 목적과 영향을 역할별로 전달해, 코드를 직접 읽지 않는 팀원도 맥락을 파악하게 합니다.",
    "주요 기능": "PR 분석, 변경 요약과 전후 비교, 역할별 영향 정리, 후속 작업 제안, RAG 기반 관련 문맥 검색",
    "역할": "AI 분석 파이프라인 (FastAPI, LlamaIndex, PostgreSQL(pgvector))",
    "기본 언어": "한국어",
}

PROMPT_FILES = {
    "v1_evidence_first": "prompts/korean_code_review_v1_evidence_first.md",
    "v2_risk_first": "prompts/korean_code_review_v2_risk_first.md",
    "v3_contextory": "prompts/korean_code_review_v3_contextory.md",
}

SYSTEM_PROMPT_BLOCK_RE = re.compile(r"## System Prompt\s*```text\n(.*?)\n```", re.DOTALL)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_system_prompt(path: Path) -> str:
    """마크다운 문서에서 ```text 로 감싼 System Prompt 본문만 추출한다."""
    text = path.read_text(encoding="utf-8")
    match = SYSTEM_PROMPT_BLOCK_RE.search(text)
    if not match:
        raise SystemExit(f"{path} 에서 System Prompt 블록을 찾지 못했습니다.")
    return match.group(1).strip()


def build_user_message(sample: dict[str, Any]) -> str:
    """모든 프롬프트가 공유하는 입력 형식으로 사용자 메시지를 만든다.

    `관련 리뷰 댓글` 슬롯은 실제 서비스에서 같은 PR 의 다른 리뷰 댓글을 넣기 위한 자리다.
    평가 실험에서는 반드시 `(없음)` 으로 채운다. 기준 데이터셋의 `expected.issues` 가
    바로 그 리뷰 댓글에서 도출한 정답이므로, 넣으면 모델에게 답을 알려 주는 셈이 된다.
    """
    inp = sample["input"]
    language = sample["classification"]["programming_language"]

    project = "\n".join(f"- {k}: {v}" for k, v in PROJECT_INFO.items())
    review_comments = (
        (inp.get("review_comment_original") or "(없음)")
        if FIXED_CONDITIONS["review_comment_in_input"]
        else "(없음)"
    )

    return (
        f"## 프로젝트 정보\n{project}\n\n"
        f"## PR 제목\n{inp.get('pr_title_original') or '(없음)'}\n\n"
        f"## PR 설명\n{inp.get('pr_description_original') or '(없음)'}\n\n"
        f"## 변경 파일 경로\n{inp['file_path']}\n\n"
        f"## 프로그래밍 언어\n{language}\n\n"
        f"## 코드 Diff\n{inp['diff']}\n\n"
        f"## 관련 리뷰 댓글\n{review_comments}\n\n"
        f"## 추가 문맥\n{inp.get('additional_context') or '(없음)'}\n"
    )


def call_model(client: Any, system_prompt: str, user_message: str) -> tuple[str | None, str | None]:
    """모델을 호출하고 (응답 텍스트, 오류 메시지) 를 반환한다."""
    last_error: str | None = None
    for attempt in range(FIXED_CONDITIONS["max_retries"] + 1):
        try:
            response = client.chat.completions.create(
                model=FIXED_CONDITIONS["model"],
                temperature=FIXED_CONDITIONS["temperature"],
                top_p=FIXED_CONDITIONS["top_p"],
                max_tokens=FIXED_CONDITIONS["max_output_tokens"],
                response_format={"type": FIXED_CONDITIONS["response_format"]},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content, None
        except Exception as exc:  # SDK 예외 계층에 의존하지 않는다.
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < FIXED_CONDITIONS["max_retries"]:
                time.sleep(FIXED_CONDITIONS["retry_backoff_seconds"])
    return None, last_error


def run(args: argparse.Namespace) -> int:
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    samples = dataset["samples"]
    if args.limit:
        samples = samples[: args.limit]

    targets = args.prompts or list(PROMPT_FILES)
    unknown = [name for name in targets if name not in PROMPT_FILES]
    if unknown:
        raise SystemExit(f"알 수 없는 프롬프트: {', '.join(unknown)}")

    client = None
    if not args.dry_run:
        if not os.environ.get("OPENAI_API_KEY"):
            print(
                "OPENAI_API_KEY 가 설정되어 있지 않습니다. 키를 주입한 뒤 다시 실행하거나 "
                "--dry-run 으로 입력만 점검하세요.",
                file=sys.stderr,
            )
            return 1
        try:
            from openai import OpenAI
        except ImportError:
            print(
                "openai 패키지가 설치되어 있지 않습니다. `pip install openai` 후 다시 실행하세요.",
                file=sys.stderr,
            )
            return 1
        client = OpenAI()

    for name in targets:
        system_prompt = load_system_prompt(Path(PROMPT_FILES[name]))
        out_dir = Path(args.out_root) / name
        out_dir.mkdir(parents=True, exist_ok=True)
        results_path = out_dir / "results.jsonl"

        valid_json = 0
        failed = 0
        lines: list[str] = []

        for sample in samples:
            user_message = build_user_message(sample)
            record: dict[str, Any] = {
                "sample_id": sample["id"],
                "prompt_variant": name,
                "requested_at": now_utc(),
                "input_chars": len(user_message),
            }

            if args.dry_run:
                record["status"] = "dry_run"
                record["raw_output"] = None
                record["parsed"] = None
            else:
                raw, error = call_model(client, system_prompt, user_message)
                if raw is None:
                    record["status"] = "call_failed"
                    record["error"] = error
                    record["raw_output"] = None
                    record["parsed"] = None
                    failed += 1
                else:
                    record["status"] = "ok"
                    record["raw_output"] = raw
                    try:
                        record["parsed"] = json.loads(raw)
                        record["json_valid"] = True
                        valid_json += 1
                    except json.JSONDecodeError as exc:
                        record["parsed"] = None
                        record["json_valid"] = False
                        record["error"] = f"JSONDecodeError: {exc}"

            lines.append(json.dumps(record, ensure_ascii=False))
            print(f"[{name}] {sample['id']}: {record['status']}", file=sys.stderr)

        results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_dir / "run_meta.json").write_text(
            json.dumps(
                {
                    "prompt_variant": name,
                    "prompt_file": PROMPT_FILES[name],
                    "dataset": args.dataset,
                    "dataset_version": dataset["dataset_version"],
                    "sample_count": len(samples),
                    "fixed_conditions": FIXED_CONDITIONS,
                    "dry_run": args.dry_run,
                    "json_valid_count": valid_json,
                    "call_failed_count": failed,
                    "executed_at": now_utc(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[{name}] 결과 저장: {results_path}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="data/test_samples.json")
    parser.add_argument("--out-root", default="evaluation/results")
    parser.add_argument("--prompts", nargs="*", help=f"기본값: {', '.join(PROMPT_FILES)}")
    parser.add_argument("--limit", type=int, help="앞에서 N개 샘플만 실행")
    parser.add_argument("--dry-run", action="store_true", help="모델을 호출하지 않고 입력 구성만 확인")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
