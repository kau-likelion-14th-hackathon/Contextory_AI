"""
keyword_report.py — 여러 PR에 대한 keyword_judge 결과를 모아서
사람이 읽을 리포트(Text Report)와 기계가 읽을 리포트(JSON Report)로 만든다.

[경로 안내] 원래 eval/report.py 였으나, RAG 평가 실행 리포트(Filter OFF/ON 비교 + Fake Confidence
판정)가 같은 파일명·같은 함수명(build_report/to_text_report/to_json_report)을 쓰게 되어
키워드 판정 리포트를 이 모듈로 분리했다. 함수 이름과 동작은 그대로이므로 import 경로만 바꾸면 된다.
    eval/report.py         → RAG 평가 실행 리포트 (scripts/run_eval.py 가 사용)
    eval/keyword_report.py → 이 파일: 키워드 중요도 판정 리포트

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


def to_frontend_draft_fragment(keyword_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    한 PR의 keyword_judge 결과 리스트를, 프론트 PullRequestReviewScreen의 draft 타입 중
    featureTags/impacts/followUps/checks 4개 필드로 변환한다.

    draft의 나머지 필드(recordType/summary/purpose/before/after/evidence/version)는
    keyword 하나하나의 중요도 판단이 아니라 PR 전체를 보는 다른 분석 단계에서 채워야 해서
    여기서는 다루지 않는다.

    variant는 아직 팀에서 심각도 판단 기준이 정해지지 않아서 전부 "info"로 고정해뒀다.
    기준이 정해지면 이 부분만 바꾸면 된다.
    """
    feature_tags: List[str] = []
    impacts: List[Dict[str, Any]] = []
    follow_ups: List[Dict[str, Any]] = []
    checks: List[str] = []

    for idx, judged in enumerate(keyword_results):
        if judged.get("label") != "important":
            continue

        keyword = judged.get("keyword", "")
        matched = set(judged.get("matched_criteria", []))
        reason = judged.get("reason", "")

        # 문장이 아니라 짧은 단어/구인 것만 태그 후보로 본다 (공백 3개 이하 = 대략 4단어 이하).
        if keyword and keyword.count(" ") <= 3:
            feature_tags.append(keyword)

        for role in judged.get("relevant_roles", []):
            impacts.append({
                "role": role,
                "description": reason,
                "variant": "info",  # TODO: 실제 심각도 기준 정해지면 교체
            })

        if "B" in matched:
            follow_ups.append({
                "id": f"kw-{idx}",
                "label": keyword,
                "completed": False,
            })

        if judged.get("inferred_from_background_knowledge"):
            checks.append(reason)

    return {
        "featureTags": feature_tags,
        "impacts": impacts,
        "followUps": follow_ups,
        "checks": checks,
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
