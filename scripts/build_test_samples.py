
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATASET_NAME = "contextory-korean-pr-review"
DATASET_VERSION = "0.4.0"

# id 접두사. `<category>-NNN` 형식을 만든다.
CATEGORY_ORDER = [
    "security",
    "performance",
    "n_plus_one",
    "functional_bug",
    "api_contract",
    "exception_handling",
    "refactoring",
    "control",
]

# 주석 파일에서 쓰던 카테고리 이름을 데이터셋 허용값으로 맞춘다.
CATEGORY_ALIASES = {"error_handling": "exception_handling"}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_category(value: str) -> str:
    return CATEGORY_ALIASES.get(value, value)


def load_selection(path: Path) -> dict[str, str]:
    """`owner/repo#번호 <TAB> 카테고리` 형식의 선별 목록을 읽는다."""
    selection: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\t,]+", line, maxsplit=1)
        if len(parts) == 2:
            selection[parts[0].strip()] = normalize_category(parts[1].strip())
    return selection


def first_added_line(diff: str) -> str:
    """대조군처럼 issue 가 없는 샘플의 근거로 쓸 diff 라인을 고른다."""
    for line in diff.splitlines():
        if line.startswith("+") and len(line.strip()) > 4:
            return line.rstrip()
    return (diff.splitlines() or [""])[0].rstrip()


def build(raw_path: Path, sel_path: Path, ann_path: Path, out_path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = {f"{r['repo_name']}#{r['pr_number']}": r for r in rows}

    selection = load_selection(sel_path)
    ann = json.loads(ann_path.read_text(encoding="utf-8"))

    meta_path = raw_path.with_suffix(".meta.json")
    raw_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    dataset_license = raw_meta.get("license", "other") + " (사용 조건 확인 필요)"

    missing = [i["ref"] for i in ann["annotations"] if i["ref"] not in records]
    if missing:
        raise SystemExit(f"수집 원본에 없는 주석이 있습니다: {', '.join(missing)}")
    unselected = [i["ref"] for i in ann["annotations"] if i["ref"] not in selection]
    if unselected:
        raise SystemExit(f"선별 목록에 없는 주석이 있습니다: {', '.join(unselected)}")
    annotated = {i["ref"] for i in ann["annotations"]}
    not_annotated = [r for r in selection if r not in annotated]
    if not_annotated:
        raise SystemExit(f"선별했지만 주석이 없는 항목이 있습니다: {', '.join(not_annotated)}")

    # 카테고리별로 모아 `<category>-NNN` 순번을 매긴다.
    items = sorted(
        ann["annotations"],
        key=lambda i: (
            CATEGORY_ORDER.index(selection[i["ref"]]) if selection[i["ref"]] in CATEGORY_ORDER else 99,
            i["id"],
        ),
    )
    counters: Counter = Counter()

    samples: list[dict[str, Any]] = []
    for item in items:
        ref = item["ref"]
        record = records[ref]
        category = selection[ref]
        counters[category] += 1
        sample_id = f"{category}-{counters[category]:03d}"

        expected = item["expected"]
        raw_issues = expected.get("issues") or []
        is_control = category == "control"
        has_issue = bool(raw_issues)

        # issue 가 없는 샘플의 근거로 쓸 diff 라인
        fallback_evidence = first_added_line(record["diff_context"])
        primary_evidence = raw_issues[0]["evidence"] if raw_issues else fallback_evidence

        issues = []
        for index, issue in enumerate(raw_issues, start=1):
            issues.append(
                {
                    "issue_id": f"issue-{index:03d}",
                    "category": normalize_category(issue["category"]),
                    "subtype": item["classification"].get("subtype", ""),
                    "severity": issue["severity"],
                    "file_path": record["file_path"],
                    "line_reference": issue["line_reference"],
                    "evidence": issue["evidence"],
                    "problem_ko": issue["problem_ko"],
                    "impact_ko": issue["impact_ko"],
                    "recommendation_ko": issue["recommendation_ko"],
                    "confidence": issue["confidence"],
                }
            )

        if is_control:
            review_result = "approve"
        elif has_issue:
            review_result = "request_changes"
        else:
            review_result = "needs_confirmation"

        samples.append(
            {
                "id": sample_id,
                "classification": {
                    "category": category,
                    "subtype": item["classification"].get("subtype", ""),
                    "difficulty": item["classification"]["difficulty"],
                    "programming_language": record["language"],
                    "has_actual_issue": has_issue,
                    "category_source": "human_selected",
                    "dataset_comment_type": record["comment_type"],
                },
                "source": {
                    "type": "huggingface_dataset",
                    "dataset_name": record["_source_dataset"],
                    "dataset_split": record["_source_split"],
                    "dataset_row_id": str(record["_source_row_index"]),
                    "repository": record["repo_name"],
                    "pr_number": int(record["pr_number"]),
                    "commit_sha": "",
                    "source_url": record["pr_url"],
                    "license": dataset_license,
                    "retrieved_at": record["_retrieved_at"],
                },
                "translation": {
                    "method": item["translation"].get("method", ann["translation_method_default"]),
                    "review_status": "pending",
                    "notes": "",
                },
                "input": {
                    "file_path": record["file_path"],
                    "pr_title_original": record.get("pr_title", ""),
                    "pr_title_ko": item["translation"]["title_ko"],
                    "pr_description_original": "",
                    "pr_description_ko": "",
                    "diff": record["diff_context"],
                    "diff_truncated": False,
                    "review_comment_original": record["reviewer_comment"],
                    "review_comment_ko": item["translation"]["review_comment_ko"],
                    "additional_context": (
                        "이 diff 는 파일 전체가 아니라 리뷰 대상 코드 주변 약 50줄입니다. "
                        "review_comment_* 는 정답의 근거이므로 모델 프롬프트에 넣지 않습니다."
                    ),
                },
                "expected": {
                    "change_summary_ko": expected.get("summary_ko", ""),
                    "change_purpose_ko": expected.get("purpose_ko", ""),
                    "change_reason_ko": expected.get("change_reason_ko", ""),
                    "before_ko": expected.get("before_ko", ""),
                    "after_ko": expected.get("after_ko", ""),
                    "review_result": review_result,
                    "issues": issues,
                    "affected_roles": expected.get("affected_roles", []),
                    "role_impacts": [
                        {
                            "role": impact["role"],
                            "impact_ko": impact["impact_ko"],
                            "evidence": primary_evidence,
                        }
                        for impact in expected.get("role_impacts", [])
                    ],
                    "follow_up_tasks": [
                        {
                            "role": task["role"],
                            "task_ko": task["task_ko"],
                            "evidence": primary_evidence,
                        }
                        for task in expected.get("follow_up_tasks", [])
                    ],
                    "needs_confirmation": list(expected.get("needs_confirmation") or []),
                },
                "evaluation_metadata": {
                    "expected_issue_count": len(issues),
                    "contains_security_issue": category == "security",
                    "contains_performance_issue": category == "performance",
                    "contains_n_plus_one_issue": category == "n_plus_one",
                    "contains_breaking_change": category == "api_contract",
                    "is_control_sample": is_control,
                },
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at": None,
                    "notes": "",
                },
            }
        )

    dataset = {
        "dataset_name": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "description": "Contextory RAG/LLM 코드 변경 분석 평가용 기준 데이터셋",
        "language": "ko-KR",
        "status": "candidate_gold",
        "total_samples": len(samples),
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "style_guide": ann["style_guide"],
        "dataset_design": ann["dataset_design"],
        "license_note": (
            f"원본 데이터셋 {raw_meta.get('dataset', '')} 의 라이선스는 "
            f"'{raw_meta.get('license', 'other')}' 이며 사용 조건 확인이 필요합니다. "
            "데이터셋 카드는 수집 대상 저장소가 모두 MIT/Apache-2.0/BSD 등 허용적 라이선스라고 "
            "밝히고 있으나, 저장소별로 개별 확인하지는 않았습니다."
        ),
        "samples": samples,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default="data/raw/hf_github_codereview_raw.jsonl")
    parser.add_argument("--selection", default="data/hf_selection.tsv")
    parser.add_argument("--annotations", default="data/annotations.json")
    parser.add_argument("--out", default="data/test_samples.json")
    args = parser.parse_args()

    dataset = build(Path(args.raw), Path(args.selection), Path(args.annotations), Path(args.out))
    samples = dataset["samples"]

    by_category = Counter(s["classification"]["category"] for s in samples)
    by_language = Counter(s["classification"]["programming_language"] for s in samples)
    with_issue = sum(1 for s in samples if s["classification"]["has_actual_issue"])
    controls = sum(1 for s in samples if s["evaluation_metadata"]["is_control_sample"])

    print(f"{args.out} 생성 완료: 총 {dataset['total_samples']}건")
    print("  유형별:", ", ".join(f"{k}={v}" for k, v in sorted(by_category.items())))
    print("  언어별:", ", ".join(f"{k}={v}" for k, v in sorted(by_language.items())))
    print(f"  문제 있는 샘플: {with_issue}건 / 대조군: {controls}건")
    print(f"  id 예시: {samples[0]['id']}, {samples[-1]['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
