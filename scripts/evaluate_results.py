"""프롬프트 실험 결과를 기준 데이터셋과 비교해 자동 지표를 계산한다.

계산하는 지표(자동):
    - JSON Valid Rate            : 유효한 JSON 을 반환한 비율
    - Schema Field Coverage      : 필수 필드를 모두 채운 비율
    - Precision / Recall / F1    : 문제 탐지 정확도 (카테고리 기준 매칭)
    - False Positive Rate        : 기준에 없는 문제를 만든 비율
    - Missed Issue Rate          : 기준에 있는 문제를 놓친 비율
    - Severity Accuracy          : 매칭된 문제의 심각도 일치율
    - Evidence Grounding Rate    : evidence 를 diff 에서 실제로 찾을 수 있는 비율
    - Control Hallucination Rate : 대조군에서 문제를 만들어 낸 비율
    - Review Result Accuracy     : review_result(approve/request_changes/needs_confirmation) 일치율
    - Avg Output Chars           : 평균 출력 길이 (간결성 비교용)
    - Needs-Confirmation Rate    : 확인 필요 항목을 분리해 낸 비율
    - 카테고리별(security/performance/n_plus_one) 탐지 정확도

사람 검토가 필요한 지표(자동 계산하지 않음):
    - 한국어 자연스러움 (1~5)
    - 후속 작업 실행 가능성 (1~5)
    - 변경 요약·목적·전후 설명 정확성
    - 역할별 영향 정확성
    이 항목들은 `pending_human_review` 로 표시되며, 채점 서식은
    `evaluation/human_review_sheet.md` 를 사용합니다.

사용 예:
    python scripts/evaluate_results.py
    python scripts/evaluate_results.py --results-root evaluation/results --out evaluation/metrics.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# 출력 스키마의 필수 필드. 기준 데이터셋의 expected 와 같은 이름을 쓰므로 매핑이 필요 없다.
REQUIRED_OUTPUT_FIELDS = [
    "change_summary_ko",
    "change_purpose_ko",
    "change_reason_ko",
    "before_ko",
    "after_ko",
    "review_result",
    "issues",
    "affected_roles",
    "role_impacts",
    "follow_up_tasks",
    "needs_confirmation",
]

TRACKED_CATEGORIES = ["security", "performance", "n_plus_one"]


def safe_div(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def normalize_evidence(text: str) -> str:
    """비교를 위해 diff 마커와 공백을 제거한다."""
    stripped = text.strip()
    if stripped[:1] in {"+", "-"}:
        stripped = stripped[1:]
    return " ".join(stripped.split())


def evidence_in_diff(evidence: str, diff: str) -> bool:
    """evidence 의 어느 한 줄이라도 diff 안에 실제로 존재하는지 확인한다."""
    normalized_diff = " ".join(diff.split())
    for line in evidence.splitlines():
        candidate = normalize_evidence(line)
        if len(candidate) >= 12 and candidate in normalized_diff:
            return True
    return False


def match_issues(
    predicted: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """예측 문제와 기준 문제를 카테고리로 짝짓는다.

    이 데이터셋은 샘플당 리뷰 지적이 1건이고 diff 도 코드 조각 하나이므로,
    파일 경로가 아니라 카테고리로 매칭한다. 하나의 기준 문제는 최대 한 번만 매칭된다.
    """
    unmatched_expected = list(expected)
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    false_positives: list[dict[str, Any]] = []

    for pred in predicted:
        pred_category = pred.get("category", "")
        hit = next((e for e in unmatched_expected if e.get("category") == pred_category), None)
        if hit is None and len(unmatched_expected) == 1 and not matched:
            # 카테고리가 어긋나도 기준 지적이 하나뿐이면 같은 지점을 가리킨 것으로 보고
            # 매칭하되, 카테고리 오분류는 카테고리별 지표에 반영된다.
            hit = unmatched_expected[0]
        if hit is None:
            false_positives.append(pred)
        else:
            unmatched_expected.remove(hit)
            matched.append((pred, hit))

    return matched, false_positives, unmatched_expected


def evaluate_variant(variant_dir: Path, samples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    results_path = variant_dir / "results.jsonl"
    if not results_path.exists():
        return {"variant": variant_dir.name, "status": "no_results"}

    records = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if all(r.get("status") == "dry_run" for r in records):
        return {
            "variant": variant_dir.name,
            "status": "dry_run_only",
            "note": "모델을 호출하지 않은 dry-run 결과이므로 지표를 계산하지 않습니다.",
            "sample_count": len(records),
        }

    total = len(records)
    json_valid = 0
    field_complete = 0
    tp = fp = fn = 0
    severity_match = 0
    severity_total = 0
    evidence_grounded = 0
    evidence_total = 0
    control_total = 0
    control_hallucinated = 0
    review_result_match = 0
    output_chars: list[int] = []
    needs_confirmation_present = 0
    needs_confirmation_expected = 0
    per_category: dict[str, dict[str, int]] = {c: {"tp": 0, "fp": 0, "fn": 0} for c in TRACKED_CATEGORIES}

    for record in records:
        sample = samples.get(record["sample_id"])
        if sample is None:
            continue
        parsed = record.get("parsed")
        if not isinstance(parsed, dict):
            continue

        json_valid += 1
        if all(field in parsed for field in REQUIRED_OUTPUT_FIELDS):
            field_complete += 1
        if parsed.get("review_result") == sample["expected"].get("review_result"):
            review_result_match += 1
        output_chars.append(len(record.get("raw_output") or ""))

        diff = sample["input"]["diff"]
        expected_issues = sample["expected"]["issues"]
        predicted_issues = parsed.get("issues") or []

        if sample["classification"]["category"] == "control":
            control_total += 1
            if predicted_issues:
                control_hallucinated += 1

        if sample["expected"].get("needs_confirmation"):
            needs_confirmation_expected += 1
            if parsed.get("needs_confirmation"):
                needs_confirmation_present += 1

        matched, false_positives, missed = match_issues(predicted_issues, expected_issues)
        tp += len(matched)
        fp += len(false_positives)
        fn += len(missed)

        for pred, exp in matched:
            severity_total += 1
            if pred.get("severity") == exp.get("severity"):
                severity_match += 1
            category = exp.get("category")
            if category in per_category:
                per_category[category]["tp"] += 1
        for pred in false_positives:
            category = pred.get("category")
            if category in per_category:
                per_category[category]["fp"] += 1
        for exp in missed:
            category = exp.get("category")
            if category in per_category:
                per_category[category]["fn"] += 1

        for pred in predicted_issues:
            evidence_total += 1
            if evidence_in_diff(str(pred.get("evidence", "")), diff):
                evidence_grounded += 1

    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall and (precision + recall) > 0
        else None
    )

    category_metrics = {}
    for category, counts in per_category.items():
        category_metrics[category] = {
            "precision": safe_div(counts["tp"], counts["tp"] + counts["fp"]),
            "recall": safe_div(counts["tp"], counts["tp"] + counts["fn"]),
            "tp": counts["tp"],
            "fp": counts["fp"],
            "fn": counts["fn"],
        }

    return {
        "variant": variant_dir.name,
        "status": "evaluated",
        "sample_count": total,
        "automatic_metrics": {
            "json_valid_rate": safe_div(json_valid, total),
            "schema_field_coverage": safe_div(field_complete, total),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_positive_rate": safe_div(fp, tp + fp),
            "missed_issue_rate": safe_div(fn, tp + fn),
            "severity_accuracy": safe_div(severity_match, severity_total),
            "evidence_grounding_rate": safe_div(evidence_grounded, evidence_total),
            "control_hallucination_rate": safe_div(control_hallucinated, control_total),
            "needs_confirmation_rate": safe_div(needs_confirmation_present, needs_confirmation_expected),
            "review_result_accuracy": safe_div(review_result_match, json_valid),
            "avg_output_chars": round(sum(output_chars) / len(output_chars)) if output_chars else None,
            "per_category": category_metrics,
        },
        "pending_human_review": [
            "korean_naturalness_1_to_5",
            "follow_up_task_actionability_1_to_5",
            "conciseness_clarity_1_to_5",
            "summary_purpose_before_after_accuracy",
            "role_impact_accuracy",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="data/test_samples.json")
    parser.add_argument("--results-root", default="evaluation/results")
    parser.add_argument("--out", default="evaluation/metrics.json")
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    samples = {s["id"]: s for s in dataset["samples"]}

    root = Path(args.results_root)
    variants = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not variants:
        print(f"{root} 아래에 결과 디렉터리가 없습니다.")
        return 1

    report = {
        "dataset_version": dataset["dataset_version"],
        "dataset_status": dataset["status"],
        "variants": [evaluate_variant(v, samples) for v in variants],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for variant in report["variants"]:
        if variant["status"] == "evaluated":
            metrics = variant["automatic_metrics"]
            print(
                f"{variant['variant']}: JSON={metrics['json_valid_rate']} "
                f"P={metrics['precision']} R={metrics['recall']} F1={metrics['f1']} "
                f"대조군환각={metrics['control_hallucination_rate']}"
            )
        else:
            print(f"{variant['variant']}: {variant['status']} - {variant.get('note', '')}")

    print(f"지표 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
