"""영문 코드 리뷰 원본을 한국어로 정리한 산출물(JSON + CSV)을 생성한다.

    data/raw/hf_github_codereview_raw.jsonl  (수집 원본 41건)
  + data/annotations.json                    (한국어 번역)
  = data/localized/reviews_ko.json           (원본 무손실)
    data/localized/reviews_ko.csv            (요약 열람용)

한국어 번역은 `data/annotations.json` 에 들어 있습니다.

    annotations        평가 데이터셋에 선별된 레코드 (expected 블록 보유)
    extra_annotations  선별되지 않았지만 한글화 산출물에는 포함하는 레코드

두 곳 어디에도 없는 레코드는 골격만 만들고 `translation_method` 를
`pending-translation` 으로 표시합니다.

필드 규칙
--------
- 원문에 값이 없으면 대응하는 한국어 필드도 비워 둡니다.
- `problem_ko` / `impact_ko` / `recommendation_ko` 는 리뷰가 실제로 문제를
  지적한 경우에만 채웁니다. 단순 제안·질문·스타일 코멘트면 비웁니다.
- `change_purpose_ko` 는 PR 제목이나 리뷰에 근거가 있을 때만 채웁니다.
  근거가 없으면 비우고 그 사실을 `needs_confirmation` 에 적습니다.

CSV 규칙
-------
- 모든 값을 큰따옴표로 감싸고 내부 `"` 는 `""` 로 이스케이프합니다(csv 모듈이 처리).
- `diff` / `before_code` / `after_code` 는 CSV 에 넣지 않습니다. `id` 로 JSON 을 참조합니다.
- 기본 인코딩은 UTF-8(BOM 없음). `--excel` 을 주면 UTF-8-SIG 로 저장합니다.

사용 예:
    python scripts/localize_reviews.py
    python scripts/localize_reviews.py --excel
    python scripts/localize_reviews.py --format json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

DATASET_NAME = "contextory-korean-pr-review"
TONE = "설명체"

# 민감 정보로 의심되는 문자열. 저장 전에 치환하고 그 사실을 needs_confirmation 에 남긴다.
SECRET_PATTERNS: list[tuple[str, str, str]] = [
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "<REDACTED_GITHUB_TOKEN>", "GitHub 토큰 형태"),
    (r"sk-[A-Za-z0-9_\-]{20,}", "<REDACTED_API_KEY>", "API 키 형태"),
    (r"AKIA[0-9A-Z]{16}", "<REDACTED_AWS_KEY>", "AWS 액세스 키 형태"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "<REDACTED_PRIVATE_KEY>", "개인 키 블록"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "<REDACTED_EMAIL>", "이메일 주소"),
]

# PR 제목이 목적을 설명하지 않는 레코드. 제목이 버전 표기뿐이라
# change_purpose_ko 를 비우고 그 사실을 needs_confirmation 에 적는다.
NO_PURPOSE_EVIDENCE = {
    "DrewThomasson/ebook2audiobook#1289",
    "DrewThomasson/ebook2audiobook#1290",
    "DrewThomasson/ebook2audiobook#1291",
}

CSV_COLUMNS = [
    "id",
    "repository",
    "pr_number",
    "file_path",
    "language",
    "comment_type",
    "pr_title_ko",
    "review_comment_ko",
    "change_summary_ko",
    "problem_ko",
    "impact_ko",
    "recommendation_ko",
    "needs_confirmation",
    "review_status",
]


def redact(text: Any) -> tuple[str, list[str]]:
    """민감 정보를 치환하고 어떤 종류가 있었는지 돌려준다."""
    if not text:
        return "", []
    out = str(text)
    found: list[str] = []
    for pattern, replacement, label in SECRET_PATTERNS:
        if re.search(pattern, out):
            found.append(label)
            out = re.sub(pattern, replacement, out)
    return out, found


def load_korean(ann_path: Path) -> dict[str, dict[str, Any]]:
    """주석 파일에서 레코드별 한국어 필드를 모은다."""
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    korean: dict[str, dict[str, Any]] = {}

    # 평가 데이터셋에 선별된 레코드: expected 블록에서 필드를 옮겨 온다.
    for item in ann.get("annotations", []):
        expected = item["expected"]
        issues = expected.get("issues") or []
        first = issues[0] if issues else {}
        korean[item["ref"]] = {
            "pr_title_ko": item["translation"].get("title_ko", ""),
            "pr_description_ko": "",
            "review_comment_ko": item["translation"].get("review_comment_ko", ""),
            "change_summary_ko": expected.get("summary_ko", ""),
            "change_purpose_ko": expected.get("purpose_ko", ""),
            "problem_ko": first.get("problem_ko", ""),
            "impact_ko": first.get("impact_ko", ""),
            "recommendation_ko": first.get("recommendation_ko", ""),
            "needs_confirmation": list(expected.get("needs_confirmation") or []),
        }

    # 선별되지 않은 레코드: 필드를 직접 적어 둔 형태 그대로 쓴다.
    for item in ann.get("extra_annotations", []):
        korean[item["ref"]] = {
            "pr_title_ko": item.get("pr_title_ko", ""),
            "pr_description_ko": item.get("pr_description_ko", ""),
            "review_comment_ko": item.get("review_comment_ko", ""),
            "change_summary_ko": item.get("change_summary_ko", ""),
            "change_purpose_ko": item.get("change_purpose_ko", ""),
            "problem_ko": item.get("problem_ko", ""),
            "impact_ko": item.get("impact_ko", ""),
            "recommendation_ko": item.get("recommendation_ko", ""),
            "needs_confirmation": list(item.get("needs_confirmation") or []),
        }

    return korean


def empty_korean() -> dict[str, Any]:
    return {
        "pr_title_ko": "",
        "pr_description_ko": "",
        "review_comment_ko": "",
        "change_summary_ko": "",
        "change_purpose_ko": "",
        "problem_ko": "",
        "impact_ko": "",
        "recommendation_ko": "",
        "needs_confirmation": [],
    }


def build(raw_path: Path, ann_path: Path, out_json: Path, out_csv: Path, args: argparse.Namespace) -> dict[str, Any]:
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    korean_by_ref = load_korean(ann_path)

    records: list[dict[str, Any]] = []
    pending = 0

    for index, row in enumerate(rows, start=1):
        ref = f"{row['repo_name']}#{row['pr_number']}"
        korean = dict(korean_by_ref.get(ref) or empty_korean())
        translated = ref in korean_by_ref

        pr_title, s1 = redact(row.get("pr_title"))
        # 이 데이터셋에는 PR 본문이 없다. 원문이 없으므로 한국어도 비워 둔다.
        pr_description, s2 = redact(row.get("pr_description") or row.get("pr_body") or "")
        review_comment, s3 = redact(row.get("reviewer_comment"))
        diff, s4 = redact(row.get("diff_context") or row.get("diff") or "")

        secrets = sorted(set(s1 + s2 + s3 + s4))
        notes = list(korean["needs_confirmation"])
        for label in secrets:
            note = f"원문에서 민감 정보로 의심되는 문자열({label})을 마스킹했습니다."
            if note not in notes:
                notes.append(note)
        if not pr_description:
            note = "원본 데이터셋에 PR 설명(본문)이 없어 pr_description_ko 를 비워 두었습니다."
            if note not in notes:
                notes.append(note)
        if ref in NO_PURPOSE_EVIDENCE:
            korean["change_purpose_ko"] = ""
            note = f"PR 제목이 버전 표기({pr_title!r})뿐이라 이 변경의 목적을 확인할 수 없습니다."
            if note not in notes:
                notes.append(note)
        korean["needs_confirmation"] = notes

        if not translated:
            pending += 1

        records.append(
            {
                "id": f"cr-{index:04d}",
                "source": {
                    "dataset": row.get("_source_dataset", ""),
                    "split": row.get("_source_split", ""),
                    "repository": row.get("repo_name", ""),
                    "pr_number": int(row["pr_number"]) if str(row.get("pr_number", "")).isdigit() else None,
                    "pr_url": row.get("pr_url", ""),
                    "file_path": row.get("file_path", ""),
                    "language": row.get("language", ""),
                    "retrieved_at": row.get("_retrieved_at", ""),
                },
                "original": {
                    "pr_title": pr_title,
                    "pr_description": pr_description,
                    "review_comment": review_comment,
                    "comment_type": row.get("comment_type", ""),
                    "diff": diff,
                },
                "korean": {k: v for k, v in korean.items() if k != "needs_confirmation"},
                "needs_confirmation": korean["needs_confirmation"],
                "translation_method": "claude-assisted" if translated else "pending-translation",
                "review_status": "pending",
            }
        )

    payload = {
        "dataset_name": DATASET_NAME,
        "language": "ko-KR",
        "tone": TONE,
        "translation_method": "claude-assisted",
        "total_records": len(records),
        "records": records,
    }

    if args.format in {"json", "both"}:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.format in {"csv", "both"}:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        encoding = "utf-8-sig" if args.excel else "utf-8"
        with out_csv.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        "id": record["id"],
                        "repository": record["source"]["repository"],
                        "pr_number": record["source"]["pr_number"],
                        "file_path": record["source"]["file_path"],
                        "language": record["source"]["language"],
                        "comment_type": record["original"]["comment_type"],
                        "pr_title_ko": record["korean"]["pr_title_ko"],
                        "review_comment_ko": record["korean"]["review_comment_ko"],
                        "change_summary_ko": record["korean"]["change_summary_ko"],
                        "problem_ko": record["korean"]["problem_ko"],
                        "impact_ko": record["korean"]["impact_ko"],
                        "recommendation_ko": record["korean"]["recommendation_ko"],
                        "needs_confirmation": " | ".join(record["needs_confirmation"]),
                        "review_status": record["review_status"],
                    }
                )

    payload["_pending"] = pending
    payload["_csv_encoding"] = "utf-8-sig" if args.excel else "utf-8"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default="data/raw/hf_github_codereview_raw.jsonl")
    parser.add_argument("--annotations", default="data/annotations.json")
    parser.add_argument("--out-json", default="data/localized/reviews_ko.json")
    parser.add_argument("--out-csv", default="data/localized/reviews_ko.csv")
    parser.add_argument("--format", choices=["json", "csv", "both"], default="both")
    parser.add_argument("--excel", action="store_true", help="CSV 를 UTF-8-SIG 로 저장(Excel 호환)")
    args = parser.parse_args()

    payload = build(Path(args.raw), Path(args.annotations), Path(args.out_json), Path(args.out_csv), args)

    total = payload["total_records"]
    pending = payload.pop("_pending")
    encoding = payload.pop("_csv_encoding")

    filled = sum(1 for r in payload["records"] if r["korean"]["problem_ko"])
    print(f"총 {total}건 처리")
    print(f"  번역 완료: {total - pending}건 / 미번역(pending-translation): {pending}건")
    print(f"  문제 지적이 있는 레코드: {filled}건 (나머지는 problem/impact/recommendation 을 비웠습니다)")
    if args.format in {"json", "both"}:
        print(f"  JSON -> {args.out_json}")
    if args.format in {"csv", "both"}:
        print(f"  CSV  -> {args.out_csv} (인코딩: {encoding})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
