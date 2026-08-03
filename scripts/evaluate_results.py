#!/usr/bin/env python3
"""예측 결과를 기준 데이터셋과 대조해 PR-분석 지표를 계산한다.

두 가지 입력 형태를 지원한다.

    1) 예측 모드 (--predictions)
       `scripts/run_evaluation.py` 가 만든 JSONL 을 읽어 `results/metrics.json`
       과 샘플별 상세 JSONL 을 만든다.

    2) 프롬프트 변형 비교 모드 (--results-root)
       `evaluation/results/<variant>/results.jsonl` 을 읽어 프롬프트별로 비교한다.

이슈 매칭 규칙 (TP/FP/FN 의 정의)
    예측 이슈와 기준 이슈는 다음 순서로 짝짓는다. 기준 이슈 하나는 최대 한 번만
    매칭되고, 남은 예측은 FP, 남은 기준은 FN 이 된다.

      (a) category 가 같고 file_path 가 같고, 양쪽 line_reference 에서 줄 번호를
          읽을 수 있으면 그 차이가 --line-tolerance 이내일 때 매칭한다.
      (b) 줄 번호를 읽을 수 없으면 category + file_path 로만 매칭한다.

    주의: 이 데이터셋의 `line_reference` 는 줄 번호가 아니라 한국어 서술이다.
    (예: "`install_programs` 의 `apk` 분기 ...") 따라서 실제로는 거의 항상
    (b) 규칙이 적용된다. 이 사실은 metrics.json 의 matching_rule 에도 남긴다.

검색 지표
    검색 정답 라벨(relevant_record_ids)이 기준 데이터에 있을 때만 계산한다.
    없으면 전부 null 로 두고 사유를 함께 적는다. 계산하지 않은 값을 계산한 것처럼
    표시하지 않는다.

사용 예
    python scripts/evaluate_results.py --predictions ./results/predictions.jsonl \\
        --output ./results/metrics.json \\
        --details-output ./results/evaluation_details.jsonl --top-k 5
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# 출력 스키마의 필수 필드. 기준 데이터셋의 expected 와 같은 이름을 쓰므로 매핑이 필요 없다.
REQUIRED_OUTPUT_FIELDS: list[str] = [
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

TRACKED_CATEGORIES: list[str] = ["security", "performance", "n_plus_one"]

RETRIEVAL_UNAVAILABLE_REASON = "PR-분석 평가에 검색 정답 라벨(relevant_record_ids)이 없음"

# 기준 데이터에서 검색 정답 라벨을 찾을 키 이름.
RELEVANT_ID_KEYS: tuple[str, ...] = (
    "relevant_record_ids",
    "relevant_document_ids",
    "relevant_ids",
)

LINE_NUMBER_RE = re.compile(r"\d+")
# 줄 번호로 볼 수 있는 형태만 통과시킨다. 한국어 서술은 걸러진다.
LINE_REFERENCE_SHAPE_RE = re.compile(r"^[\sLl#:~\-–,\d]+$")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 정규화 / 비율 계산
# ---------------------------------------------------------------------------


def normalize_text(text: Any) -> str:
    """한국어·영어 문자열을 비교 가능한 형태로 맞춘다.

    전각/반각(NFKC)을 통일하고 대소문자와 연속 공백 차이를 없앤다.
    카테고리·severity·역할·review_result 같은 라벨 비교에만 쓰고,
    evidence 원문 대조에는 쓰지 않는다(원문이 바뀌면 근거 확인이 무의미해진다).
    """
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return " ".join(normalized.strip().lower().split())


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    """비율을 0.0~1.0 으로 돌려준다. 분모가 0 이면 계산 불가이므로 None."""
    if not denominator:
        return None
    value = numerator / denominator
    return round(min(max(value, 0.0), 1.0), 4)


def harmonic_f1(precision: float | None, recall: float | None) -> float | None:
    """F1 = 2PR/(P+R). 어느 한쪽이 계산 불가면 F1 도 계산하지 않는다."""
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    """TP/FP/FN 에서 precision/recall/f1 을 만든다. 계산 불가는 사유를 남긴다."""
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    out: dict[str, Any] = {
        "precision": precision,
        "recall": recall,
        "f1": harmonic_f1(precision, recall),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }
    reasons: list[str] = []
    if precision is None:
        reasons.append("precision: 예측 이슈가 0건이라 분모(TP+FP)가 0")
    if recall is None:
        reasons.append("recall: 기준 이슈가 0건이라 분모(TP+FN)가 0")
    if reasons:
        out["unavailable_reason"] = " / ".join(reasons)
    return out


def percentile(values: Sequence[float], pct: float) -> float:
    """가장 가까운 순위(nearest-rank) 백분위수. 표본이 적어도 정의된다."""
    if not values:
        raise ValueError("빈 목록의 백분위수는 계산할 수 없습니다.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(-(-pct / 100.0 * len(ordered) // 1))))
    return ordered[rank - 1]


# ---------------------------------------------------------------------------
# 근거 대조
# ---------------------------------------------------------------------------


def normalize_evidence(text: str) -> str:
    """비교를 위해 diff 마커와 공백만 정리한다. 내용은 바꾸지 않는다."""
    stripped = text.strip()
    if stripped[:1] in {"+", "-"}:
        stripped = stripped[1:]
    return " ".join(stripped.split())


def evidence_in_diff(evidence: str, diff: str, min_chars: int = 12) -> bool:
    """evidence 의 어느 한 줄이라도 diff 안에 실제로 존재하는지 확인한다."""
    if not evidence or not diff:
        return False
    normalized_diff = " ".join(diff.split())
    for line in evidence.splitlines():
        candidate = normalize_evidence(line)
        if len(candidate) >= min_chars and candidate in normalized_diff:
            return True
    return False


# ---------------------------------------------------------------------------
# 이슈 매칭
# ---------------------------------------------------------------------------


def extract_line_number(value: Any) -> int | None:
    """line_reference 에서 줄 번호를 읽는다. 서술문이면 None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or not LINE_REFERENCE_SHAPE_RE.match(text):
        return None
    match = LINE_NUMBER_RE.search(text)
    return int(match.group()) if match else None


def issues_match(
    predicted: dict[str, Any], expected: dict[str, Any], line_tolerance: int
) -> tuple[bool, str]:
    """두 이슈가 같은 지적인지 판정하고, 어떤 규칙으로 판정했는지 함께 돌려준다."""
    if normalize_text(predicted.get("category")) != normalize_text(expected.get("category")):
        return False, "category_mismatch"
    if normalize_text(predicted.get("file_path")) != normalize_text(expected.get("file_path")):
        return False, "file_mismatch"

    pred_line = extract_line_number(predicted.get("line_reference"))
    exp_line = extract_line_number(expected.get("line_reference"))
    if pred_line is not None and exp_line is not None:
        if abs(pred_line - exp_line) <= line_tolerance:
            return True, "category+file+line"
        return False, "line_out_of_tolerance"
    return True, "category+file"


def match_issues(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    line_tolerance: int,
) -> tuple[
    list[tuple[dict[str, Any], dict[str, Any], str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """(매칭, 오탐, 미탐) 을 돌려준다. 기준 이슈 하나는 최대 한 번만 매칭된다."""
    remaining = list(expected)
    matched: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    false_positives: list[dict[str, Any]] = []

    for pred in predicted:
        hit: dict[str, Any] | None = None
        rule = ""
        for candidate in remaining:
            ok, why = issues_match(pred, candidate, line_tolerance)
            if ok:
                hit, rule = candidate, why
                break
        if hit is None:
            false_positives.append(pred)
        else:
            remaining.remove(hit)
            matched.append((pred, hit, rule))

    return matched, false_positives, remaining


def set_counts(predicted: Iterable[Any], expected: Iterable[Any]) -> tuple[int, int, int]:
    """집합 비교의 TP/FP/FN 을 센다. 역할 지표에 쓴다."""
    pred_set = {normalize_text(x) for x in predicted if normalize_text(x)}
    exp_set = {normalize_text(x) for x in expected if normalize_text(x)}
    tp = len(pred_set & exp_set)
    return tp, len(pred_set) - tp, len(exp_set) - tp


# ---------------------------------------------------------------------------
# 검색 지표
# ---------------------------------------------------------------------------


def find_relevant_ids(sample: dict[str, Any]) -> list[str] | None:
    """기준 데이터에서 검색 정답 라벨을 찾는다. 없으면 None."""
    containers = (sample, sample.get("evaluation_metadata"), sample.get("expected"))
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in RELEVANT_ID_KEYS:
            value = container.get(key)
            if isinstance(value, list) and value:
                return [str(v) for v in value]
    return None


def retrieval_metrics(
    records: Sequence[dict[str, Any]], samples: dict[str, dict[str, Any]], top_k: int
) -> dict[str, Any]:
    """검색 정답 라벨이 있을 때만 Precision@K / Recall@K / MRR 을 계산한다."""
    labelled: list[tuple[dict[str, Any], list[str]]] = []
    for record in records:
        sample = samples.get(record.get("id"))
        if sample is None:
            continue
        relevant = find_relevant_ids(sample)
        if relevant:
            labelled.append((record, relevant))

    if not labelled:
        return {
            "precision_at_k": None,
            "recall_at_k": None,
            "mrr": None,
            "k": top_k,
            "labelled_samples": 0,
            "unavailable_reason": RETRIEVAL_UNAVAILABLE_REASON,
        }

    precisions: list[float] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []

    for record, relevant in labelled:
        retrieved = [
            str(ctx.get("record_id"))
            for ctx in (record.get("retrieved_contexts") or [])[:top_k]
        ]
        relevant_set = set(relevant)
        hits = [r for r in retrieved if r in relevant_set]
        precisions.append(len(hits) / len(retrieved) if retrieved else 0.0)
        recalls.append(len(set(hits)) / len(relevant_set) if relevant_set else 0.0)
        rank = next((i for i, r in enumerate(retrieved, start=1) if r in relevant_set), 0)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)

    return {
        "precision_at_k": round(statistics.fmean(precisions), 4),
        "recall_at_k": round(statistics.fmean(recalls), 4),
        "mrr": round(statistics.fmean(reciprocal_ranks), 4),
        "k": top_k,
        "labelled_samples": len(labelled),
        "unavailable_reason": None,
    }


# ---------------------------------------------------------------------------
# 예측 읽기
# ---------------------------------------------------------------------------


def load_predictions(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """JSONL 또는 JSON 배열을 읽는다. 잘못된 행은 건너뛰고 사유를 남긴다."""
    if not path.exists():
        raise SystemExit(f"예측 파일을 찾을 수 없습니다: {path}")

    raw = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    candidates: list[tuple[int, Any]] = []

    if raw.lstrip().startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"JSON 배열을 해석하지 못했습니다: {exc}")
        candidates = list(enumerate(data, start=1))
    else:
        for line_no, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                candidates.append((line_no, json.loads(line)))
            except json.JSONDecodeError as exc:
                skipped.append({"line": line_no, "reason": f"JSON 파싱 실패: {exc}"})

    for line_no, obj in candidates:
        if not isinstance(obj, dict):
            skipped.append({"line": line_no, "reason": "객체가 아닌 행"})
            continue
        if "id" not in obj:
            skipped.append({"line": line_no, "reason": "필수 필드 'id' 가 없음"})
            continue
        if not isinstance(obj.get("predicted"), dict):
            skipped.append(
                {"line": line_no, "reason": f"필수 필드 'predicted' 가 없거나 객체가 아님 (id={obj['id']})"}
            )
            continue
        records.append(obj)

    return records, skipped


# ---------------------------------------------------------------------------
# 예측 모드: 샘플별 평가
# ---------------------------------------------------------------------------


def evaluate_sample(
    record: dict[str, Any], sample: dict[str, Any], line_tolerance: int
) -> dict[str, Any]:
    """샘플 1건의 상세 결과를 만든다. 집계는 이 결과들만으로 재현된다."""
    predicted = record.get("predicted") or {}
    expected = sample.get("expected") or {}
    diff = (sample.get("input") or {}).get("diff", "")
    classification = sample.get("classification") or {}
    evaluation_meta = sample.get("evaluation_metadata") or {}

    predicted_issues = [i for i in (predicted.get("issues") or []) if isinstance(i, dict)]
    expected_issues = [i for i in (expected.get("issues") or []) if isinstance(i, dict)]

    matched, false_positives, missed = match_issues(
        predicted_issues, expected_issues, line_tolerance
    )

    severity_correct = sum(
        1
        for pred, exp, _ in matched
        if normalize_text(pred.get("severity")) == normalize_text(exp.get("severity"))
    )

    grounded_flags = [
        evidence_in_diff(str(issue.get("evidence", "")), diff) for issue in predicted_issues
    ]

    role_tp, role_fp, role_fn = set_counts(
        predicted.get("affected_roles") or [], expected.get("affected_roles") or []
    )

    is_control = bool(evaluation_meta.get("is_control_sample")) or (
        classification.get("has_actual_issue") is False
    )

    review_predicted = predicted.get("review_result")
    has_content = bool(
        predicted_issues
        or normalize_text(predicted.get("change_summary_ko"))
        or normalize_text(review_predicted)
    )

    return {
        "id": record.get("id"),
        "category": classification.get("category"),
        "is_control": is_control,
        "error": record.get("error"),
        "latency_ms": record.get("latency_ms"),
        "schema_fields_present": sum(1 for f in REQUIRED_OUTPUT_FIELDS if f in predicted),
        "schema_fields_required": len(REQUIRED_OUTPUT_FIELDS),
        "empty_answer": not has_content,
        "issue_counts": {
            "predicted": len(predicted_issues),
            "expected": len(expected_issues),
            "tp": len(matched),
            "fp": len(false_positives),
            "fn": len(missed),
        },
        "match_rules": [rule for _, _, rule in matched],
        "severity": {"matched": len(matched), "correct": severity_correct},
        "evidence": {
            "predicted_issues": len(predicted_issues),
            "grounded": sum(grounded_flags),
            "ungrounded": len(grounded_flags) - sum(grounded_flags),
        },
        "roles": {
            "predicted": predicted.get("affected_roles") or [],
            "expected": expected.get("affected_roles") or [],
            "tp": role_tp,
            "fp": role_fp,
            "fn": role_fn,
        },
        "review_result": {
            "predicted": review_predicted,
            "expected": expected.get("review_result"),
            "correct": normalize_text(review_predicted)
            == normalize_text(expected.get("review_result")),
        },
        "control_hallucinated": bool(is_control and predicted_issues),
        "retrieved_context_count": len(record.get("retrieved_contexts") or []),
        "false_positive_categories": [i.get("category") for i in false_positives],
        "missed_categories": [i.get("category") for i in missed],
    }


def load_run_meta(predictions_path: Path) -> dict[str, Any]:
    """run_evaluation.py 가 남긴 실행 정보를 읽는다. 없으면 빈 dict."""
    meta_path = predictions_path.with_suffix(predictions_path.suffix + ".meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def build_category_metrics(evaluated: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """기준 샘플의 카테고리별 TP/FP/FN 과 지표를 만든다."""
    buckets: dict[str, dict[str, int]] = {}
    for detail in evaluated:
        name = detail["category"] or "unknown"
        bucket = buckets.setdefault(name, {"tp": 0, "fp": 0, "fn": 0, "samples": 0})
        bucket["samples"] += 1
        bucket["tp"] += detail["issue_counts"]["tp"]
        bucket["fp"] += detail["issue_counts"]["fp"]
        bucket["fn"] += detail["issue_counts"]["fn"]
    for name in TRACKED_CATEGORIES:
        buckets.setdefault(name, {"tp": 0, "fp": 0, "fn": 0, "samples": 0})

    return {
        name: {**prf(b["tp"], b["fp"], b["fn"]), "samples": b["samples"]}
        for name, b in sorted(buckets.items())
    }


def build_macro(category_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """샘플이 있는 카테고리만 대상으로 macro 평균을 낸다."""
    usable = [m for m in category_metrics.values() if m["samples"] > 0]
    precisions = [m["precision"] for m in usable if m["precision"] is not None]
    recalls = [m["recall"] for m in usable if m["recall"] is not None]
    f1s = [m["f1"] for m in usable if m["f1"] is not None]

    macro: dict[str, Any] = {
        "precision": round(statistics.fmean(precisions), 4) if precisions else None,
        "recall": round(statistics.fmean(recalls), 4) if recalls else None,
        "f1": round(statistics.fmean(f1s), 4) if f1s else None,
        "categories_included": len(usable),
    }
    if not f1s:
        macro["unavailable_reason"] = "F1 을 계산할 수 있는 카테고리가 없습니다."
    return macro


def aggregate(
    details: Sequence[dict[str, Any]],
    records: Sequence[dict[str, Any]],
    samples: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    skipped: Sequence[dict[str, Any]],
    dataset: dict[str, Any],
) -> dict[str, Any]:
    """샘플별 상세를 집계해 metrics.json 구조를 만든다."""
    evaluated = [d for d in details if not d["error"]]
    failed = [d for d in details if d["error"]]

    tp = sum(d["issue_counts"]["tp"] for d in evaluated)
    fp = sum(d["issue_counts"]["fp"] for d in evaluated)
    fn = sum(d["issue_counts"]["fn"] for d in evaluated)

    severity_total = sum(d["severity"]["matched"] for d in evaluated)
    severity_correct = sum(d["severity"]["correct"] for d in evaluated)

    predicted_issue_total = sum(d["evidence"]["predicted_issues"] for d in evaluated)
    grounded_total = sum(d["evidence"]["grounded"] for d in evaluated)
    ungrounded_total = sum(d["evidence"]["ungrounded"] for d in evaluated)

    control_samples = [d for d in evaluated if d["is_control"]]
    control_hallucinated = sum(1 for d in control_samples if d["control_hallucinated"])

    role_tp = sum(d["roles"]["tp"] for d in evaluated)
    role_fp = sum(d["roles"]["fp"] for d in evaluated)
    role_fn = sum(d["roles"]["fn"] for d in evaluated)

    review_correct = sum(1 for d in evaluated if d["review_result"]["correct"])
    empty_answers = sum(1 for d in evaluated if d["empty_answer"])

    category_metrics = build_category_metrics(evaluated)
    run_meta = load_run_meta(Path(args.predictions))

    latencies = [
        float(d["latency_ms"]) for d in details if isinstance(d.get("latency_ms"), (int, float))
    ]

    errors: list[dict[str, Any]] = [{"type": "skipped_prediction_line", **s} for s in skipped]
    errors.extend({"type": "prediction_error", "id": d["id"], "reason": d["error"]} for d in failed)
    errors.extend(
        {"type": "unknown_sample_id", "id": r.get("id")}
        for r in records
        if r.get("id") not in samples
    )

    severity_accuracy = ratio(severity_correct, severity_total)
    evidence_rate = ratio(grounded_total, predicted_issue_total)
    control_rate = ratio(control_hallucinated, len(control_samples))
    ungrounded_rate = ratio(ungrounded_total, predicted_issue_total)
    review_accuracy = ratio(review_correct, len(evaluated))

    return {
        "metadata": {
            "generated_at": now_utc(),
            "dataset_version": dataset.get("dataset_version"),
            "evaluation_dataset": str(args.eval_data),
            "predictions_file": str(args.predictions),
            "evaluation_samples": len(details),
            "evaluated_samples": len(evaluated),
            "random_seed": args.seed,
            "embedding_model": run_meta.get("embedding_model"),
            "llm_model": run_meta.get("llm_model"),
            "prompt_version": run_meta.get("prompt_version"),
            "vector_store": "pgvector",
            "retrieval_enabled": run_meta.get("retrieval_enabled"),
            "top_k": args.top_k,
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.chunk_overlap,
            "matching_rule": (
                "category 일치 AND file_path 일치 AND (양쪽에서 줄 번호를 읽을 수 있으면 "
                f"±{args.line_tolerance}줄 이내). 줄 번호를 읽을 수 없으면 category+file_path 로만 "
                "매칭한다. 이 데이터셋의 line_reference 는 서술문이라 실제로는 "
                "category+file_path 규칙이 적용된다."
            ),
            "evidence_rule": (
                "예측 이슈의 evidence 중 공백 정규화 후 12자 이상인 줄이 diff 원문에 "
                "그대로 나타나면 근거 있음으로 본다."
            ),
        },
        "issue_detection_metrics": prf(tp, fp, fn),
        "issue_detection_metrics_macro": build_macro(category_metrics),
        "severity_accuracy": severity_accuracy,
        "severity_accuracy_detail": {
            "matched_issues": severity_total,
            "severity_correct": severity_correct,
            "unavailable_reason": None if severity_accuracy is not None else "매칭된 이슈가 0건",
        },
        "evidence_grounding_rate": evidence_rate,
        "evidence_grounding_detail": {
            "predicted_issues": predicted_issue_total,
            "grounded_issues": grounded_total,
            "unavailable_reason": None if evidence_rate is not None else "예측 이슈가 0건",
        },
        "hallucination_metrics": {
            "control_hallucination_rate": control_rate,
            "ungrounded_issue_rate": ungrounded_rate,
            "control_sample_count": len(control_samples),
            "control_hallucinated_count": control_hallucinated,
            "total_predicted_issues": predicted_issue_total,
            "ungrounded_issue_count": ungrounded_total,
            "evaluation_method": "ground_truth",
            "definition": (
                "control_hallucination_rate = (대조군에서 이슈를 1건 이상 만든 샘플 수) / "
                "(대조군 샘플 수). ungrounded_issue_rate = (evidence 를 diff 에서 찾지 못한 "
                "예측 이슈 수) / (전체 예측 이슈 수). LLM judge 없이 정답과 diff 원문만으로 "
                "계산한다."
            ),
            "unavailable_reason": None if control_rate is not None else "대조군 샘플이 0건",
        },
        "role_metrics": {
            **prf(role_tp, role_fp, role_fn),
            "note": "affected_roles 집합 비교를 샘플 전체에 대해 micro 로 합산했다.",
        },
        "review_result_accuracy": review_accuracy,
        "review_result_detail": {
            "correct": review_correct,
            "evaluated": len(evaluated),
            "unavailable_reason": None if review_accuracy is not None else "평가 가능한 샘플이 0건",
        },
        "retrieval_metrics": retrieval_metrics(records, samples, args.top_k),
        "runtime_metrics": {
            "successful_samples": len(evaluated),
            "failed_samples": len(failed),
            "empty_answers": empty_answers,
            "average_latency_ms": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else None,
            "p95_latency_ms": round(percentile(latencies, 95), 2) if latencies else None,
            "latency_samples": len(latencies),
            "unavailable_reason": None if latencies else "latency_ms 가 기록된 샘플이 없음",
        },
        "category_metrics": category_metrics,
        "errors": errors,
    }


def run_prediction_mode(args: argparse.Namespace) -> int:
    """예측 JSONL 을 읽어 metrics.json 과 상세 JSONL 을 만든다."""
    eval_path = Path(args.eval_data)
    if not eval_path.exists():
        print(f"기준 데이터셋을 찾을 수 없습니다: {eval_path}", file=sys.stderr)
        return 1

    dataset = json.loads(eval_path.read_text(encoding="utf-8"))
    samples = {s["id"]: s for s in dataset.get("samples", [])}
    if not samples:
        print(f"기준 데이터셋에 샘플이 없습니다: {eval_path}", file=sys.stderr)
        return 1

    records, skipped = load_predictions(Path(args.predictions))
    if not records:
        print("평가할 예측 레코드가 없습니다. 필수 필드(id, predicted)를 확인하세요.", file=sys.stderr)
        return 1

    details = [
        evaluate_sample(record, samples[record["id"]], args.line_tolerance)
        for record in records
        if record["id"] in samples
    ]
    if not details:
        print("예측의 id 가 기준 데이터셋의 어떤 샘플과도 맞지 않습니다.", file=sys.stderr)
        return 1

    metrics = aggregate(details, records, samples, args, skipped, dataset)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    details_path = Path(args.details_output)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8") as handle:
        for detail in details:
            handle.write(json.dumps(detail, ensure_ascii=False) + "\n")

    issue = metrics["issue_detection_metrics"]
    hallucination = metrics["hallucination_metrics"]
    runtime = metrics["runtime_metrics"]
    print("=" * 70)
    print(
        f"샘플 {metrics['metadata']['evaluation_samples']}건 "
        f"(평가 가능 {metrics['metadata']['evaluated_samples']}건 / "
        f"실패 {runtime['failed_samples']}건)"
    )
    print(
        f"이슈 탐지     : P={issue['precision']} R={issue['recall']} F1={issue['f1']} "
        f"(TP={issue['tp']} FP={issue['fp']} FN={issue['fn']})"
    )
    print(f"심각도 일치   : {metrics['severity_accuracy']}")
    print(f"근거 충실도   : {metrics['evidence_grounding_rate']}")
    print(
        f"대조군 환각률 : {hallucination['control_hallucination_rate']} "
        f"({hallucination['control_hallucinated_count']}/{hallucination['control_sample_count']})"
    )
    print(
        f"무근거 이슈률 : {hallucination['ungrounded_issue_rate']} "
        f"({hallucination['ungrounded_issue_count']}/{hallucination['total_predicted_issues']})"
    )
    print(f"역할 P/R/F1   : {metrics['role_metrics']['precision']}/"
          f"{metrics['role_metrics']['recall']}/{metrics['role_metrics']['f1']}")
    print(f"review_result : {metrics['review_result_accuracy']}")
    print(f"검색 지표     : {metrics['retrieval_metrics']['unavailable_reason'] or '계산됨'}")
    print(f"지연 p50/p95  : {runtime['p50_latency_ms']} / {runtime['p95_latency_ms']} ms")
    print("=" * 70)
    print(f"지표 저장 : {out_path}")
    print(f"상세 저장 : {details_path}")
    return 0


# ---------------------------------------------------------------------------
# 프롬프트 변형 비교 모드
# ---------------------------------------------------------------------------


def match_issues_by_category(
    predicted: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """변형 비교 모드의 매칭 규칙.

    샘플당 리뷰 지적이 1건이고 diff 도 코드 조각 하나여서 파일 경로가 아니라
    카테고리로 매칭한다. 예측 모드의 규칙(category+file_path)과 다르다.
    """
    unmatched = list(expected)
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    false_positives: list[dict[str, Any]] = []

    for pred in predicted:
        category = pred.get("category", "")
        hit = next((e for e in unmatched if e.get("category") == category), None)
        if hit is None and len(unmatched) == 1 and not matched:
            # 카테고리가 어긋나도 기준 지적이 하나뿐이면 같은 지점을 가리킨 것으로 본다.
            hit = unmatched[0]
        if hit is None:
            false_positives.append(pred)
        else:
            unmatched.remove(hit)
            matched.append((pred, hit))

    return matched, false_positives, unmatched


def evaluate_variant(variant_dir: Path, samples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """프롬프트 변형 1개의 결과를 평가한다."""
    results_path = variant_dir / "results.jsonl"
    if not results_path.exists():
        return {"variant": variant_dir.name, "status": "no_results"}

    records = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if records and all(r.get("status") == "dry_run" for r in records):
        return {
            "variant": variant_dir.name,
            "status": "dry_run_only",
            "note": "모델을 호출하지 않은 dry-run 결과이므로 지표를 계산하지 않습니다.",
            "sample_count": len(records),
        }

    total = len(records)
    json_valid = field_complete = 0
    tp = fp = fn = 0
    severity_match = severity_total = 0
    evidence_grounded = evidence_total = 0
    control_total = control_hallucinated = 0
    review_result_match = 0
    needs_confirmation_present = needs_confirmation_expected = 0
    output_chars: list[int] = []
    per_category = {c: {"tp": 0, "fp": 0, "fn": 0} for c in TRACKED_CATEGORIES}

    for record in records:
        sample = samples.get(record.get("sample_id"))
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

        matched, false_positives, missed = match_issues_by_category(
            predicted_issues, expected_issues
        )
        tp += len(matched)
        fp += len(false_positives)
        fn += len(missed)

        for pred, exp in matched:
            severity_total += 1
            if pred.get("severity") == exp.get("severity"):
                severity_match += 1
            if exp.get("category") in per_category:
                per_category[exp["category"]]["tp"] += 1
        for pred in false_positives:
            if pred.get("category") in per_category:
                per_category[pred["category"]]["fp"] += 1
        for exp in missed:
            if exp.get("category") in per_category:
                per_category[exp["category"]]["fn"] += 1

        for pred in predicted_issues:
            evidence_total += 1
            if evidence_in_diff(str(pred.get("evidence", "")), diff):
                evidence_grounded += 1

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)

    return {
        "variant": variant_dir.name,
        "status": "evaluated",
        "sample_count": total,
        "automatic_metrics": {
            "json_valid_rate": ratio(json_valid, total),
            "schema_field_coverage": ratio(field_complete, total),
            "precision": precision,
            "recall": recall,
            "f1": harmonic_f1(precision, recall),
            "false_positive_rate": ratio(fp, tp + fp),
            "missed_issue_rate": ratio(fn, tp + fn),
            "severity_accuracy": ratio(severity_match, severity_total),
            "evidence_grounding_rate": ratio(evidence_grounded, evidence_total),
            "control_hallucination_rate": ratio(control_hallucinated, control_total),
            "needs_confirmation_rate": ratio(
                needs_confirmation_present, needs_confirmation_expected
            ),
            "review_result_accuracy": ratio(review_result_match, json_valid),
            "avg_output_chars": round(sum(output_chars) / len(output_chars))
            if output_chars
            else None,
            "per_category": {
                name: {
                    "precision": ratio(c["tp"], c["tp"] + c["fp"]),
                    "recall": ratio(c["tp"], c["tp"] + c["fn"]),
                    **c,
                }
                for name, c in per_category.items()
            },
        },
        "pending_human_review": [
            "korean_naturalness_1_to_5",
            "follow_up_task_actionability_1_to_5",
            "conciseness_clarity_1_to_5",
            "summary_purpose_before_after_accuracy",
            "role_impact_accuracy",
        ],
    }


def run_variant_mode(args: argparse.Namespace) -> int:
    """프롬프트 변형별 결과를 비교한다."""
    eval_path = Path(args.eval_data)
    if not eval_path.exists():
        print(f"기준 데이터셋을 찾을 수 없습니다: {eval_path}", file=sys.stderr)
        return 1

    dataset = json.loads(eval_path.read_text(encoding="utf-8"))
    samples = {s["id"]: s for s in dataset["samples"]}

    root = Path(args.results_root)
    variants = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not variants:
        print(f"{root} 아래에 결과 디렉터리가 없습니다.", file=sys.stderr)
        return 1

    report = {
        "generated_at": now_utc(),
        "dataset_version": dataset.get("dataset_version"),
        "dataset_status": dataset.get("status"),
        "variants": [evaluate_variant(v, samples) for v in variants],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for variant in report["variants"]:
        if variant["status"] == "evaluated":
            m = variant["automatic_metrics"]
            print(
                f"{variant['variant']}: JSON={m['json_valid_rate']} P={m['precision']} "
                f"R={m['recall']} F1={m['f1']} 대조군환각={m['control_hallucination_rate']}"
            )
        else:
            print(f"{variant['variant']}: {variant['status']} - {variant.get('note', '')}")

    print(f"지표 저장: {out_path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="예측 결과를 기준 데이터셋과 대조해 PR-분석 지표를 계산한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--predictions",
        default=None,
        help="run_evaluation.py 가 만든 예측 JSONL/JSON. 지정하면 예측 모드로 동작한다.",
    )
    parser.add_argument(
        "--eval-data",
        "--dataset",
        dest="eval_data",
        default="./data/test_samples.json",
        help="기준 정답 데이터셋",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="지표 저장 경로 (기본: 예측 모드 ./results/metrics.json, 변형 모드 evaluation/metrics.json)",
    )
    parser.add_argument(
        "--details-output",
        default="./results/evaluation_details.jsonl",
        help="샘플별 상세 저장 경로 (예측 모드)",
    )
    parser.add_argument("--top-k", type=int, default=5, help="검색 지표의 K")
    parser.add_argument(
        "--line-tolerance", type=int, default=5, help="이슈 매칭 시 허용할 줄 번호 차이(±N)"
    )
    parser.add_argument("--seed", type=int, default=42, help="재현용 시드 (기록 목적)")
    parser.add_argument("--chunk-size", type=int, default=1000, help="인덱싱 chunk 크기 (기록 목적)")
    parser.add_argument(
        "--chunk-overlap", type=int, default=150, help="인덱싱 chunk 겹침 (기록 목적)"
    )
    parser.add_argument(
        "--results-root",
        default="evaluation/results",
        help="프롬프트 변형 비교 모드에서 읽을 디렉터리",
    )
    args = parser.parse_args(argv)

    if args.output is None:
        args.output = "./results/metrics.json" if args.predictions else "evaluation/metrics.json"
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.predictions:
        return run_prediction_mode(args)
    return run_variant_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
