from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_CATEGORIES = {
    "security",
    "performance",
    "n_plus_one",
    "functional_bug",
    "api_contract",
    "exception_handling",
    "refactoring",
    "control",
}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}
ALLOWED_REVIEW_RESULTS = {"approve", "request_changes", "needs_confirmation"}
ALLOWED_HUMAN_REVIEW = {"pending", "reviewed", "approved"}
ALLOWED_TRANSLATION_METHODS = {
    "gpt-4o-assisted",
    "claude-assisted",
    "manually-translated",
    "original-korean",
    "pending-translation",
}
ALLOWED_ISSUE_LOCATIONS = {"pre_change", "post_change"}
ALLOWED_ROLES = {
    "frontend",
    "backend",
    "ai",
    "planning",
    "design",
    "qa",
    "project_manager",
}

REQUIRED_TOP_LEVEL = [
    "dataset_name",
    "dataset_version",
    "description",
    "language",
    "status",
    "total_samples",
    "created_at",
    "updated_at",
    "samples",
]

REQUIRED_EXPECTED_FIELDS = [
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

# 민감 정보로 의심되는 문자열. 발견되면 ERROR 로 처리한다.
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub 토큰 형태"),
    (r"sk-[A-Za-z0-9_\-]{20,}", "API 키 형태"),
    (r"AKIA[0-9A-Z]{16}", "AWS 액세스 키 형태"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "개인 키 블록"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "이메일 주소"),
]

PR_URL_RE = re.compile(r"^https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def check_secrets(report: Report, where: str, text: str) -> None:
    for pattern, label in SECRET_PATTERNS:
        match = re.search(pattern, text)
        if match:
            report.error(f"{where}: 민감 정보 의심 문자열({label})이 남아 있습니다: {match.group()[:12]}...")
            return


def validate_sample(report: Report, index: int, sample: dict[str, Any]) -> None:
    sid = sample.get("id") or f"index-{index}"

    for key in ("id", "classification", "source", "translation", "input", "expected", "evaluation_metadata", "human_review"):
        if key not in sample:
            report.error(f"[{sid}] 필수 필드 누락: {key}")
            return

    cls = sample["classification"]
    category = cls.get("category")
    if category not in ALLOWED_CATEGORIES:
        report.error(f"[{sid}] 허용되지 않은 category: {category!r}")
    if cls.get("difficulty") not in ALLOWED_DIFFICULTIES:
        report.error(f"[{sid}] 허용되지 않은 difficulty: {cls.get('difficulty')!r}")
    if not str(cls.get("programming_language", "")).strip():
        report.error(f"[{sid}] programming_language 가 비어 있습니다.")
    if not isinstance(cls.get("has_actual_issue"), bool):
        report.error(f"[{sid}] has_actual_issue 가 불리언이 아닙니다.")

    src = sample["source"]
    url = src.get("source_url", "")
    if not PR_URL_RE.match(url or ""):
        report.error(f"[{sid}] PR URL 형식이 올바르지 않습니다: {url!r}")
    if src.get("type") != "huggingface_dataset":
        report.error(f"[{sid}] source.type 이 huggingface_dataset 이 아닙니다: {src.get('type')!r}")
    for field in ("dataset_name", "dataset_split", "dataset_row_id", "repository", "pr_number", "license", "retrieved_at"):
        if not str(src.get(field, "")).strip():
            report.error(f"[{sid}] source.{field} 가 비어 있습니다.")
    # 출처 확인은 별도 플래그가 아니라 실제 값의 존재로 판정한다.
    if not (src.get("source_url") and src.get("dataset_row_id")):
        report.error(f"[{sid}] 출처를 확인할 수 없습니다(source_url / dataset_row_id 필요).")
    sha = src.get("commit_sha", "")
    if sha and not SHA_RE.match(str(sha)):
        report.error(f"[{sid}] commit_sha 가 40자리 SHA 형식이 아닙니다: {sha!r}")

    method = sample["translation"].get("method")
    if method not in ALLOWED_TRANSLATION_METHODS:
        report.error(f"[{sid}] 허용되지 않은 translation.method: {method!r}")
    if sample["translation"].get("review_status") not in ALLOWED_HUMAN_REVIEW:
        report.error(f"[{sid}] 허용되지 않은 translation.review_status: {sample['translation'].get('review_status')!r}")

    # 원문과 한국어가 짝을 이루는지 확인한다.
    for orig_key, ko_key in (
        ("pr_title_original", "pr_title_ko"),
        ("pr_description_original", "pr_description_ko"),
        ("review_comment_original", "review_comment_ko"),
    ):
        original = str(sample["input"].get(orig_key, "")).strip()
        korean = str(sample["input"].get(ko_key, "")).strip()
        if original and not korean:
            report.warn(f"[{sid}] {orig_key} 은 있는데 {ko_key} 가 비어 있습니다.")
        if korean and not original:
            report.error(f"[{sid}] {ko_key} 만 있고 {orig_key} 이 비어 있습니다. 원문 없는 번역입니다.")

    inp = sample["input"]
    diff = inp.get("diff", "")
    if not diff.strip():
        report.error(f"[{sid}] input.diff 가 비어 있습니다.")
    if not str(inp.get("file_path", "")).strip():
        report.error(f"[{sid}] input.file_path 가 비어 있습니다.")
    if not isinstance(inp.get("diff_truncated"), bool):
        report.error(f"[{sid}] diff_truncated 가 불리언이 아닙니다.")
    if not str(inp.get("review_comment_original", "")).strip():
        report.error(f"[{sid}] input.review_comment_original 이 비어 있습니다.")

    check_secrets(report, f"[{sid}] input.diff", diff)
    check_secrets(report, f"[{sid}] input.review_comment_original", str(inp.get("review_comment_original", "")))

    expected = sample["expected"]
    for field in REQUIRED_EXPECTED_FIELDS:
        if field not in expected:
            report.error(f"[{sid}] expected.{field} 가 없습니다.")

    if expected.get("review_result") not in ALLOWED_REVIEW_RESULTS:
        report.error(f"[{sid}] 허용되지 않은 review_result: {expected.get('review_result')!r}")

    roles = set(expected.get("affected_roles", []))
    for role in roles:
        if role not in ALLOWED_ROLES:
            report.error(f"[{sid}] 허용되지 않은 affected_roles 값: {role!r}")

    for impact in expected.get("role_impacts", []):
        if impact.get("role") not in ALLOWED_ROLES:
            report.error(f"[{sid}] 허용되지 않은 역할: {impact.get('role')!r}")
        if impact.get("role") not in roles:
            report.error(f"[{sid}] role_impacts 의 {impact.get('role')!r} 가 affected_roles 에 없습니다.")
        for field in ("impact_ko", "evidence"):
            if not str(impact.get(field, "")).strip():
                report.error(f"[{sid}] role_impacts 항목에 {field} 가 비어 있습니다.")

    for task in expected.get("follow_up_tasks", []):
        if task.get("role") not in ALLOWED_ROLES:
            report.error(f"[{sid}] 허용되지 않은 follow_up_tasks 역할: {task.get('role')!r}")
        for field in ("task_ko", "evidence"):
            if not str(task.get(field, "")).strip():
                report.error(f"[{sid}] follow_up_tasks 항목에 {field} 가 비어 있습니다.")

    issues = expected.get("issues", [])
    if not isinstance(issues, list):
        report.error(f"[{sid}] expected.issues 가 리스트가 아닙니다.")
        return

    if cls.get("has_actual_issue") is False and issues:
        report.error(f"[{sid}] has_actual_issue=false 인데 issues 가 비어 있지 않습니다.")
    if cls.get("has_actual_issue") is True and not issues:
        report.error(f"[{sid}] has_actual_issue=true 인데 issues 가 비어 있습니다.")
    meta = sample["evaluation_metadata"]
    if meta.get("expected_issue_count") != len(issues):
        report.error(
            f"[{sid}] expected_issue_count({meta.get('expected_issue_count')}) 와 실제 issues 개수({len(issues)}) 가 다릅니다."
        )
    if category == "control":
        if issues:
            report.error(f"[{sid}] 대조군 샘플에 issues 가 들어 있습니다.")
        if not meta.get("is_control_sample"):
            report.error(f"[{sid}] category 가 control 인데 is_control_sample 이 참이 아닙니다.")
        if expected.get("review_result") != "approve":
            report.error(f"[{sid}] 대조군의 review_result 는 approve 여야 합니다.")
    if meta.get("is_control_sample") and category != "control":
        report.error(f"[{sid}] is_control_sample 이 참인데 category 가 control 이 아닙니다.")

    flag_map = {
        "security": "contains_security_issue",
        "performance": "contains_performance_issue",
        "n_plus_one": "contains_n_plus_one_issue",
    }
    if category in flag_map and not meta.get(flag_map[category]):
        report.error(f"[{sid}] category 가 {category} 인데 {flag_map[category]} 가 참이 아닙니다.")

    if issues and expected.get("review_result") == "approve" and category != "control":
        report.warn(f"[{sid}] issues 가 있는데 review_result 가 approve 입니다.")

    for issue in issues:
        iid = issue.get("issue_id", "(id 없음)")
        for field in ("issue_id", "category", "severity", "confidence", "file_path", "line_reference", "evidence", "problem_ko", "impact_ko", "recommendation_ko"):
            if field not in issue:
                report.error(f"[{sid}/{iid}] issue 필수 필드 누락: {field}")
        if issue.get("severity") not in ALLOWED_SEVERITIES:
            report.error(f"[{sid}/{iid}] 허용되지 않은 severity: {issue.get('severity')!r}")
        if issue.get("category") not in ALLOWED_CATEGORIES:
            report.error(f"[{sid}/{iid}] 허용되지 않은 issue category: {issue.get('category')!r}")
        confidence = issue.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            report.error(f"[{sid}/{iid}] confidence 가 0.0~1.0 범위의 수가 아닙니다: {confidence!r}")

        for field in ("evidence", "problem_ko", "impact_ko", "recommendation_ko", "line_reference"):
            if not str(issue.get(field, "")).strip():
                report.error(f"[{sid}/{iid}] {field} 가 비어 있습니다.")

        evidence = str(issue.get("evidence", ""))
        # 근거는 diff 에서 인용해야 한다. 첫 줄이 diff 안에 있는지 확인한다.
        first_line = evidence.splitlines()[0].strip() if evidence.splitlines() else ""
        if first_line and first_line not in diff:
            report.warn(
                f"[{sid}/{iid}] evidence 첫 줄을 diff 에서 그대로 찾지 못했습니다. 인용 정확성을 확인하세요."
            )

    hr = sample["human_review"]
    if hr.get("status") not in ALLOWED_HUMAN_REVIEW:
        report.error(f"[{sid}] 허용되지 않은 human_review.status: {hr.get('status')!r}")


def validate(path: Path, strict: bool) -> int:
    report = Report()

    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"JSON 파싱 실패: {exc}", file=sys.stderr)
        return 1

    for key in REQUIRED_TOP_LEVEL:
        if key not in dataset:
            report.error(f"최상위 필수 필드 누락: {key}")

    samples = dataset.get("samples", [])
    if dataset.get("total_samples") != len(samples):
        report.error(
            f"total_samples({dataset.get('total_samples')}) 와 실제 샘플 수({len(samples)}) 가 다릅니다."
        )

    ids = [s.get("id") for s in samples]
    duplicated_ids = [i for i, count in Counter(ids).items() if count > 1]
    if duplicated_ids:
        report.error(f"중복된 샘플 id: {', '.join(map(str, duplicated_ids))}")

    ID_RE = re.compile(r"^[a-z_]+-\d{3}$")
    for s in samples:
        if not ID_RE.match(s.get("id", "")):
            report.error(f"id 형식이 <category>-NNN 이 아닙니다: {s.get('id')!r}")

    pr_refs = [f"{s.get('source', {}).get('repository')}#{s.get('source', {}).get('pr_number')}" for s in samples]
    duplicated_prs = [r for r, count in Counter(pr_refs).items() if count > 1]
    if duplicated_prs:
        report.error(f"중복된 PR: {', '.join(duplicated_prs)}")

    diff_hashes = Counter(hash(s.get("input", {}).get("diff", "")) for s in samples)
    if any(count > 1 for count in diff_hashes.values()):
        duplicated = [
            s.get("id") for s in samples if diff_hashes[hash(s.get("input", {}).get("diff", ""))] > 1
        ]
        report.error(f"동일한 diff 를 가진 샘플이 있습니다: {', '.join(map(str, duplicated))}")

    statuses = {s.get("source", {}).get("license_status", "") for s in samples}
    if any(st and st != "확인함" for st in statuses):
        report.warn(
            "원본 데이터셋의 라이선스 사용 조건이 확인되지 않았습니다(license_status='확인 필요'). "
            "배포 전에 데이터셋 카드와 저장소별 라이선스를 확인해야 합니다."
        )

    controls = [s for s in samples if s.get("classification", {}).get("category") == "control"]
    if not controls:
        report.warn("대조군 샘플이 없습니다. 환각 비율을 측정할 수 없습니다.")

    pending = [s for s in samples if s.get("human_review", {}).get("status") == "pending"]
    if dataset.get("status") in {"gold", "golden"} and pending:
        report.error(
            f"사람 검토가 끝나지 않은 샘플이 {len(pending)}건 있는데 데이터셋 status 가 "
            f"{dataset.get('status')!r} 로 표시되어 있습니다."
        )

    for index, sample in enumerate(samples):
        validate_sample(report, index, sample)

    print(f"검증 대상: {path} (샘플 {len(samples)}건)")
    print(f"  ERROR {len(report.errors)}건 / WARN {len(report.warnings)}건")
    for message in report.errors:
        print(f"  [ERROR] {message}")
    for message in report.warnings:
        print(f"  [WARN ] {message}")

    if not report.errors and not report.warnings:
        print("  문제 없음")

    if report.errors:
        return 1
    if strict and report.warnings:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default="data/test_samples.json")
    parser.add_argument("--strict", action="store_true", help="WARN 도 실패로 처리")
    args = parser.parse_args()
    return validate(Path(args.file), args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
