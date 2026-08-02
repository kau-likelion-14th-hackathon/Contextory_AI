"""Hugging Face 의 공개 코드 리뷰 데이터셋에서 샘플을 내려받아 원본으로 저장한다.

대상 데이터셋
-------------
`ronantakizawa/github-codereview` (기본값)

    행 수  : 약 356k, split: train / validation / test
    컬럼   : before_code, after_code, diff_context, reviewer_comment, comment_type,
             quality_score, is_negative, file_path, comment_line, language,
             pr_title, pr_number, repo_name, repo_stars, repo_language,
             reviewer_username, author_username, comment_length,
             before_lines, after_lines

이 데이터셋은 `file_path` 와 `repo_name` + `pr_number` 를 모두 가지고 있어
PR URL 을 재구성할 수 있고, 문제 위치를 파일 경로로 지목할 수 있습니다.

참고: `microsoft/codereviewer` 는 **데이터셋이 아니라 모델**입니다.
      (`https://huggingface.co/api/models/microsoft/codereviewer` 는 200,
       `.../api/datasets/...` 는 401 을 반환합니다.)
      CodeReviewer 원본 데이터는 별도 배포 경로를 따라야 합니다.

토큰
----
공개 데이터셋이므로 토큰이 필요 없습니다. `HF_TOKEN` 이 설정되어 있으면 사용하고,
없으면 `None` 이 전달되어 익명으로 동작합니다. 토큰 값을 코드나 로그에 쓰지 않습니다.

사용 예
-------
    python scripts/fetch_hf_dataset.py --target 40
    python scripts/fetch_hf_dataset.py --dataset ronantakizawa/github-codereview \\
        --split train --target 40 --out data/raw/hf_github_codereview_raw.jsonl
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_DATASET = "ronantakizawa/github-codereview"
DEFAULT_SPLIT = "train"

# 리뷰 문맥으로 쓰기에 지나치게 큰 샘플은 제외한다.
MAX_DIFF_CHARS = 6000
MAX_COMMENT_CHARS = 2000
MIN_DIFF_CHARS = 80
MIN_COMMENT_CHARS = 25

# 유형별로 고르게 모으기 위한 목표 비중. 데이터셋의 comment_type 값을 그대로 쓴다.
TYPE_QUOTA = {
    "security": 6,
    "performance": 5,
    "bug": 6,
    "refactor": 4,
    "style": 2,
    "nitpick": 2,
    "question": 3,
    "suggestion": 8,
    "none": 4,
}

# 대조군(지적 없이 통과한 코드) 목표 개수. control 로 별도 관리한다.
NEGATIVE_QUOTA = 4

SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "<REDACTED_GITHUB_TOKEN>"),
    (r"sk-[A-Za-z0-9_\-]{20,}", "<REDACTED_API_KEY>"),
    (r"AKIA[0-9A-Z]{16}", "<REDACTED_AWS_KEY>"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "<REDACTED_PRIVATE_KEY>"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "<REDACTED_EMAIL>"),
]


def redact(text: Any) -> str:
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in SECRET_PATTERNS:
        out = re.sub(pattern, replacement, out)
    return out


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def pr_url(repo_name: str, pr_number: Any) -> str | None:
    """repo_name 과 pr_number 로 PR URL 을 재구성한다.

    두 값이 모두 있을 때만 만들고, 없으면 None 을 돌려준다. 임의로 지어내지 않는다.
    """
    if not repo_name or not str(pr_number).strip():
        return None
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", str(repo_name)):
        return None
    if not str(pr_number).isdigit():
        return None
    return f"https://github.com/{repo_name}/pull/{pr_number}"


# 데이터셋 카드가 밝힌 대조군 라벨. 리뷰에서 지적을 받지 않은 코드 조각입니다.
NEGATIVE_COMMENT = "No issues found."


def is_negative_example(row: dict[str, Any]) -> bool:
    """대조군(지적 없이 통과한 코드) 행인지 판정한다.

    데이터셋 카드에 따르면 전체의 약 23%가 "No issues found." 로 라벨링된
    negative example 이며, `is_negative` 필드로도 표시됩니다.
    """
    comment = (row.get("reviewer_comment") or "").strip()
    return comment == NEGATIVE_COMMENT or str(row.get("is_negative")).lower() == "true"


def is_usable(row: dict[str, Any], skipped: Counter, *, negative: bool = False) -> bool:
    """리뷰 문맥으로 쓸 수 있는 행인지 판정하고, 제외 사유를 집계한다."""
    diff = row.get("diff_context") or ""
    comment = row.get("reviewer_comment") or ""

    if not diff and not row.get("after_code"):
        skipped["diff_없음"] += 1
        return False
    if not comment.strip():
        skipped["리뷰댓글_없음"] += 1
        return False
    # 대조군은 댓글이 "No issues found." 한 줄이므로 길이 하한을 적용하지 않는다.
    if not negative and len(comment) < MIN_COMMENT_CHARS:
        skipped["리뷰댓글_너무_짧음"] += 1
        return False
    if len(comment) > MAX_COMMENT_CHARS:
        skipped["리뷰댓글_너무_김"] += 1
        return False
    if len(diff) < MIN_DIFF_CHARS:
        skipped["diff_너무_짧음"] += 1
        return False
    if len(diff) > MAX_DIFF_CHARS:
        skipped["diff_너무_김"] += 1
        return False
    if not pr_url(row.get("repo_name"), row.get("pr_number")):
        skipped["PR_URL_재구성_불가"] += 1
        return False
    return True


def fetch(args: argparse.Namespace) -> int:
    try:
        from datasets import load_dataset
    except ImportError:
        print(
            "datasets 패키지가 필요합니다.\n"
            '  python -m venv .venv && .venv/bin/python -m pip install "datasets>=2.19" '
            '"huggingface_hub>=0.23" pandas pyarrow',
            file=sys.stderr,
        )
        return 1

    token = os.environ.get("HF_TOKEN")  # 공개 데이터셋이면 None 이어도 동작한다.
    print(
        f"[info] {args.dataset} / {args.split} (streaming, "
        f"{'토큰 사용' if token else '익명'})",
        file=sys.stderr,
    )

    try:
        stream = load_dataset(args.dataset, split=args.split, streaming=True, token=token)
    except Exception as exc:  # gated / 네트워크 / 존재하지 않는 ID 등
        print(f"[fail] {args.dataset} 로드 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    quota = dict(TYPE_QUOTA)
    if args.target != sum(TYPE_QUOTA.values()):
        # 목표 개수를 바꾸면 비중을 유지한 채 정수로 배분한다.
        total = sum(TYPE_QUOTA.values())
        quota = {k: max(1, round(v * args.target / total)) for k, v in TYPE_QUOTA.items()}

    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in quota}
    buckets["control"] = []
    quota["control"] = args.negatives
    skipped: Counter = Counter()
    seen_pairs: set[tuple[str, str]] = set()
    seen_repos: Counter = Counter()
    scanned = 0

    for row in stream:
        scanned += 1
        if scanned > args.max_scan:
            break

        negative = is_negative_example(row)
        ctype = "control" if negative else ((row.get("comment_type") or "none").strip() or "none")
        if ctype not in buckets or len(buckets[ctype]) >= quota[ctype]:
            continue
        if not is_usable(row, skipped, negative=negative):
            continue

        repo = str(row.get("repo_name"))
        # 한 저장소가 표본을 독점하지 않도록 제한한다.
        if seen_repos[repo] >= args.max_per_repo:
            skipped["저장소_상한_초과"] += 1
            continue

        key = (repo, str(row.get("pr_number")))
        if key in seen_pairs:
            skipped["같은_PR_중복"] += 1
            continue

        seen_pairs.add(key)
        seen_repos[repo] += 1
        buckets[ctype].append(
            {
                "_source_dataset": args.dataset,
                "_source_split": args.split,
                "_source_row_index": scanned - 1,
                "_retrieved_at": now_utc(),
                "repo_name": repo,
                "repo_stars": row.get("repo_stars"),
                "repo_language": row.get("repo_language"),
                "pr_number": row.get("pr_number"),
                "pr_title": redact(row.get("pr_title")),
                "pr_url": pr_url(repo, row.get("pr_number")),
                "file_path": row.get("file_path"),
                "comment_line": row.get("comment_line"),
                "language": row.get("language"),
                "comment_type": ctype,
                "is_negative_example": negative,
                "quality_score": row.get("quality_score"),
                "is_negative": row.get("is_negative"),
                "comment_length": row.get("comment_length"),
                "diff_context": redact(row.get("diff_context")),
                "before_code": redact(row.get("before_code")),
                "after_code": redact(row.get("after_code")),
                "reviewer_comment": redact(row.get("reviewer_comment")),
            }
        )

        if all(len(v) >= quota[k] for k, v in buckets.items()):
            break

    rows = [item for bucket in buckets.values() for item in bucket]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "dataset": args.dataset,
                "split": args.split,
                "license": "other (데이터셋 카드 표기) — 사용 조건 확인 필요",
                "license_status": "확인 필요",
                "retrieved_at": now_utc(),
                "scanned_rows": scanned,
                "saved_rows": len(rows),
                "quota": quota,
                "saved_by_type": {k: len(v) for k, v in buckets.items()},
                "skipped_reasons": dict(skipped),
                "filters": {
                    "min_diff_chars": MIN_DIFF_CHARS,
                    "max_diff_chars": MAX_DIFF_CHARS,
                    "min_comment_chars": MIN_COMMENT_CHARS,
                    "max_comment_chars": MAX_COMMENT_CHARS,
                    "max_per_repo": args.max_per_repo,
                },
                "note": (
                    "reviewer_comment 는 평가 정답이므로 모델 입력에 넣지 않습니다. "
                    "민감정보(토큰/키/이메일)는 저장 전에 마스킹했습니다."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"saved {len(rows)} rows -> {out_path}")
    print(f"  훑은 행: {scanned}")
    print(f"  유형별: {dict(sorted((k, len(v)) for k, v in buckets.items()))}")
    print(f"  언어별: {dict(Counter(r['language'] for r in rows).most_common())}")
    print(f"  저장소: {len({r['repo_name'] for r in rows})}개")
    print(f"  제외 사유: {dict(skipped)}")
    print(f"  메타데이터 -> {meta_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--target", type=int, default=sum(TYPE_QUOTA.values()))
    parser.add_argument("--max-scan", type=int, default=200000, help="훑을 최대 행 수")
    parser.add_argument("--max-per-repo", type=int, default=3, help="한 저장소에서 최대 몇 건까지 받을지")
    parser.add_argument("--negatives", type=int, default=NEGATIVE_QUOTA,
                        help='대조군("No issues found.") 목표 개수')
    parser.add_argument("--out", default="data/raw/hf_github_codereview_raw.jsonl")
    return fetch(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
