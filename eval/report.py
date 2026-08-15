"""
report.py — 여러 PR에 대한 keyword_judge 결과를 모아서
사람이 읽을 리포트(Text Report)와 기계가 읽을 리포트(JSON Report)로 만든다.

judge.py의 keyword_judge()를 PR/keyword마다 돌린 결과를 아래 형태로 모아서 넘긴다:

pr_judge_results = [
    {"pr_id": 101, "keywords": [keyword_judge(...) 결과, keyword_judge(...) 결과, ...]},
    {"pr_id": 102, "keywords": [...]},
    ...
]
"""

import json
from collections import Counter
from typing import Any, Dict, List

# 여러 PR에 걸쳐 몇 번 이상 important로 판정돼야 "반복적으로 중요한 키워드"로 볼지 기준
RECURRING_THRESHOLD = 2


def build_report(pr_judge_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """pr_judge_results를 집계해서 리포트용 통계를 만든다."""
    if not pr_judge_results:
        return {
            "pr_count": 0,
            "total_keywords": 0,
            "error_count": 0,
            "important_rate": 0.0,
            "background_inference_rate": 0.0,
            "recurring_important_keywords": [],
            "per_pr": [],
        }

    important_counter: Counter[str] = Counter()
    total_keywords = 0
    important_count = 0
    background_count = 0
    error_count = 0

    for pr_result in pr_judge_results:
        for judged in pr_result.get("keywords", []):
            total_keywords += 1
            if judged.get("error"):
                # LLM 호출 자체가 실패한 경우 - "판단"이 아니므로 important_rate 등 비율 계산에서 제외한다.
                error_count += 1
                continue
            if judged.get("label") == "important":
                important_count += 1
                important_counter[judged["keyword"]] += 1
            if judged.get("inferred_from_background_knowledge"):
                background_count += 1

    judged_count = total_keywords - error_count

    recurring_important_keywords = [
        {"keyword": keyword, "count": count}
        for keyword, count in important_counter.most_common()
        if count >= RECURRING_THRESHOLD
    ]

    return {
        "pr_count": len(pr_judge_results),
        "total_keywords": total_keywords,
        "error_count": error_count,
        "important_rate": round(important_count / judged_count, 3) if judged_count else 0.0,
        "background_inference_rate": round(background_count / judged_count, 3) if judged_count else 0.0,
        "recurring_important_keywords": recurring_important_keywords,
        "per_pr": pr_judge_results,
    }


def to_json_report(report: Dict[str, Any]) -> str:
    """기계가 읽을 JSON Report."""
    return json.dumps(report, ensure_ascii=False, indent=2)


def to_text_report(report: Dict[str, Any]) -> str:
    """사람이 읽을 Text Report."""
    lines = [
        "=== Contextory Keyword Judge Report ===",
        f"평가한 PR 수: {report['pr_count']}",
        f"판단한 키워드 총 개수: {report['total_keywords']}",
        f"LLM 호출 실패로 판단 못한 키워드 수: {report.get('error_count', 0)}",
        f"important 비율(호출 실패 제외): {report['important_rate'] * 100:.1f}%",
        f"배경지식으로 추론한 비율(품질 감시용): {report['background_inference_rate'] * 100:.1f}%",
        "",
        "[여러 PR에서 반복적으로 important로 판정된 키워드]",
    ]

    if report["recurring_important_keywords"]:
        lines += [
            f"  - {item['keyword']} ({item['count']}회)"
            for item in report["recurring_important_keywords"]
        ]
    else:
        lines.append("  (없음)")

    return "\n".join(lines)
