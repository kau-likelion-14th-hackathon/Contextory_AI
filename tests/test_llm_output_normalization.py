"""
LLM 출력 정규화 회귀 테스트

라이브 호출에서 실제로 발견된 결함들을 고정한다.
  - GPT가 JSON null 대신 문자열 "null"을 내보내 chunk_id="null"이 응답에 실렸다
  - riskScore가 문자열/범위 밖 값으로 와도 0~100 정수로 응답해야 한다
  - reviews / changes 가 프롬프트 스키마에 없어 항상 빈 배열이었다(하위 호환 회귀)
"""

import json

import pytest

from models.schemas import PRAnalysisRequest
from services.analysis_service import (
    _nullable_int, _nullable_str, _risk_score, analyze_pr_pipeline,
)
from services.context_filter import filter_contexts
from services.prompt_builder import _OUTPUT_SCHEMA_SECTION
from services.retrieval import build_outcome

CHUNKS = [
    {"chunk_id": "cr-1", "id": "1", "source_type": "code_review", "similarity_score": 0.88,
     "text": "JWT 필터", "review_comment": "JWT 필터"},
]

LLM_OUTPUT = {
    "summary": "요약",
    "riskScore": "45",
    "reviews": [
        {"file_path": "AuthService.java", "line_number": "21", "comment": "필터 순서 확인"},
        {"file_path": "null", "line_number": "null", "comment": "전체 구조 검토"},
        {"file_path": "X.java", "line_number": None, "comment": "   "},          # 빈 코멘트 → 제외
    ],
    "changes": [{"filePath": "null", "description": "설명"}],
    "evidence": [
        {"chunkId": "null", "filePath": "None", "diffLocation": "@@ -1 +1 @@", "description": "판단 근거"},
        {"chunkId": "cr-1", "filePath": None, "diffLocation": None, "description": "직접 근거"},
    ],
}


def _run(output):
    request = PRAnalysisRequest(
        pr_id=1, repo_name="org/contextory", title="t", description="d",
        diff_content="@@ -1 +1 @@", author="dev",
    )
    return analyze_pr_pipeline(
        request,
        retrieve_fn=lambda **kwargs: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=lambda system, prompt: json.dumps(output, ensure_ascii=False),
        translate_fn=lambda title, body: "q",
    )


# ==========================================
# 정규화 헬퍼
# ==========================================

@pytest.mark.parametrize("raw", ["null", "None", "n/a", "-", "  ", None, "NIL"])
def test_nullish_strings_become_none(raw):
    assert _nullable_str(raw) is None


def test_real_strings_survive_normalization():
    assert _nullable_str("  AuthService.java ") == "AuthService.java"


@pytest.mark.parametrize("raw, expected", [("21", 21), (21, 21), (21.7, 21), ("null", None), ("미정", None), (None, None), (True, None)])
def test_nullable_int(raw, expected):
    assert _nullable_int(raw) == expected


@pytest.mark.parametrize("raw, expected", [("45", 45), (45, 45), (150, 100), (-5, 0), ("null", 0), (None, 0)])
def test_risk_score_is_clamped_int(raw, expected):
    assert _risk_score({"riskScore": raw}) == expected


def test_risk_score_accepts_snake_case_key():
    assert _risk_score({"risk_score": 30}) == 30


# ==========================================
# 응답 매핑
# ==========================================

def test_string_null_does_not_leak_into_evidence():
    response = _run(LLM_OUTPUT)

    # 동기 응답의 evidences는 실제 검색 chunk 기반이라 "null"이 있을 수 없다
    assert [e.chunk_id for e in response.evidences] == ["cr-1"]


def test_reviews_are_populated_and_cleaned():
    response = _run(LLM_OUTPUT)

    assert len(response.reviews) == 2                       # 빈 코멘트 1건 제외
    assert response.reviews[0].file_path == "AuthService.java"
    assert response.reviews[0].line_number == 21
    assert response.reviews[1].file_path is None            # "null" → None
    assert response.reviews[1].line_number is None


def test_risk_score_is_parsed_from_string():
    assert _run(LLM_OUTPUT).risk_score == 45


def test_prompt_schema_requests_backward_compatible_fields():
    """프롬프트가 reviews/changes/riskScore를 요구해야 기존 응답 필드가 채워진다."""
    for field in ('"reviews"', '"changes"', '"riskScore"'):
        assert field in _OUTPUT_SCHEMA_SECTION
    assert "0~100" in _OUTPUT_SCHEMA_SECTION


def test_prompt_forbids_inventing_roles():
    from services.prompt_builder import PRInput, ProjectInfo, build_grounded_prompt

    prompt = build_grounded_prompt(pr=PRInput(title="t", diff="d"), filtered_contexts=[], project=ProjectInfo())

    assert "팀 역할: (정보 없음)" in prompt
    assert "빈 배열로 두고" in prompt
