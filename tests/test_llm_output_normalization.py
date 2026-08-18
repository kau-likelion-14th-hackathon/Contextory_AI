"""
LLM 출력 정규화 / 응답 매핑 회귀 테스트

라이브 호출과 명세 변경 과정에서 실제로 문제가 됐던 지점들을 고정한다.
  - GPT가 JSON null 대신 문자열 "null"을 내보내 응답에 그대로 실렸다
  - 허용되지 않은 역할("개발자" 등)을 만들어내면 응답에서 걸러야 한다
  - needsConfirmation은 string[] 이고, 검색 신호로 판단한 항목도 합쳐야 한다
  - roleImpacts[].basis / evidence[].id·source·location 매핑
  - diff가 비어 있으면 LLM을 호출하지 않고 확인 필요 경로로 나간다
"""

import json

import pytest

from models.schemas import PRAnalysisRequest, PullRequestFile, PullRequestInfo, AsyncAnalysisRequest
from services.analysis_service import (
    EMPTY_DIFF_SUMMARY, _allowed_roles, _nullable_int, _nullable_str, _risk_score,
    analyze_pr_for_callback, analyze_pr_pipeline,
)
from services.context_filter import filter_contexts
from services.prompt_builder import ALLOWED_ROLES, BASIS_EXPECTED, OUTPUT_SCHEMA_SECTION
from services.retrieval import build_outcome

CHUNKS = [
    {"chunk_id": "cr-1", "id": "1", "source_type": "code_review", "similarity_score": 0.88,
     "text": "JWT 필터", "review_comment": "JWT 필터", "file_path": None},
]

LLM_OUTPUT = {
    "summary": "요약",
    "purpose": "목적",
    "changeReason": "확인 필요",
    "before": "before",
    "after": "after",
    "relatedFeatures": ["로그인"],
    "affectedRoles": ["프론트엔드", "개발자", "QA"],          # "개발자"는 허용 목록 밖 → 제거되어야 함
    "roleImpacts": [
        {"role": "프론트엔드", "impact": "오류 분기 수정", "basis": BASIS_EXPECTED, "evidenceRefs": ["e1"]},
        {"role": "개발자", "impact": "임의 역할", "basis": BASIS_EXPECTED, "evidenceRefs": []},
        {"role": "QA", "impact": "테스트 추가", "basis": "아무말", "evidenceRefs": ["e1"]},  # basis 허용값 아님 → None
    ],
    "followUpTasks": ["리프레시 토큰 정책 정의"],
    "needsConfirmation": ["변경 이유가 PR 본문에 없음 — PR 작성자에게 확인 필요"],
    "evidence": [
        {"id": "e1", "source": "pr_diff", "location": "AuthService.java", "description": "login 추가"},
        {"id": "e2", "source": "context", "location": "cr-1", "description": "과거 리뷰 지적"},
    ],
}


def _request(diff: str = "@@ -1 +1 @@\n+login()") -> PRAnalysisRequest:
    return PRAnalysisRequest(
        pr_id=1, repo_name="org/contextory", title="t", description="d",
        diff_content=diff, author="dev",
    )


def _run(output=None, diff: str = "@@ -1 +1 @@\n+login()", generate_calls=None):
    def generate(system_prompt, user_prompt):
        if generate_calls is not None:
            generate_calls.append((system_prompt, user_prompt))
        return json.dumps(output if output is not None else LLM_OUTPUT, ensure_ascii=False)

    return analyze_pr_pipeline(
        _request(diff),
        retrieve_fn=lambda **kwargs: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=generate,
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


def test_allowed_roles_filters_invented_roles():
    assert _allowed_roles(["프론트엔드", "개발자", "QA", "프론트엔드"]) == ["프론트엔드", "QA"]
    assert _allowed_roles("문자열") == []
    assert set(_allowed_roles(list(ALLOWED_ROLES))) == set(ALLOWED_ROLES)


# ==========================================
# 응답 매핑
# ==========================================

def test_affected_roles_and_role_impacts_are_restricted():
    response = _run()

    assert response.affected_roles == ["프론트엔드", "QA"]        # "개발자" 제거
    assert [ri.role for ri in response.role_impacts] == ["프론트엔드", "QA"]
    assert response.role_impacts[0].basis == BASIS_EXPECTED
    assert response.role_impacts[1].basis is None                # 허용값 아닌 basis는 버린다
    assert response.role_impacts[0].evidence_refs == ["e1"]


def test_needs_confirmation_items_are_collected():
    response = _run()

    assert response.needs_confirmation is True                   # 동기 API는 bool 유지(하위 호환)
    assert "변경 이유가 PR 본문에 없음 — PR 작성자에게 확인 필요" in response.confirmation_items


def test_record_draft_fields_are_mapped():
    response = _run()

    assert response.purpose == "목적"
    assert response.change_reason == "확인 필요"
    assert response.before == "before" and response.after == "after"
    assert response.related_features == ["로그인"]
    assert response.follow_up_tasks == ["리프레시 토큰 정책 정의"]


def test_string_null_does_not_leak_into_evidence():
    output = {**LLM_OUTPUT, "evidence": [{"id": "null", "source": "null", "location": "None", "description": "설명"}]}

    payload = analyze_pr_for_callback(
        AsyncAnalysisRequest(
            analysisId=1, projectId=1, repositoryId=1, repositoryFullName="org/contextory",
            pullRequest=PullRequestInfo(
                githubPrId=1, prNumber=1, title="t", body="b", headSha="a1",
                sourceBranch="f", targetBranch="d",
                files=[PullRequestFile(filePath="AuthService.java", changeType="MODIFIED", patch="@@ +login()")],
            ),
            language="ko", callbackUrl="https://example.com/cb",
        ),
        retrieve_fn=lambda **kwargs: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=lambda system_prompt, user_prompt: json.dumps(output, ensure_ascii=False),
        translate_fn=lambda title, body: "q",
    )

    body = payload.model_dump(by_alias=True)
    first = body["evidence"][0]
    assert first["id"] == "e1"           # 문자열 "null" → 자동 부여 id
    assert first["location"] is None     # "None" → None
    assert body["needsConfirmation"]     # string[] 계약


def test_callback_projects_new_schema_onto_legacy_fields():
    payload = analyze_pr_for_callback(
        AsyncAnalysisRequest(
            analysisId=1, projectId=1, repositoryId=1, repositoryFullName="org/contextory",
            pullRequest=PullRequestInfo(
                githubPrId=1, prNumber=1, title="t", body="b", headSha="a1",
                sourceBranch="f", targetBranch="d",
                files=[PullRequestFile(filePath="AuthService.java", changeType="MODIFIED", patch="@@ +login()")],
            ),
            language="ko", callbackUrl="https://example.com/cb",
        ),
        retrieve_fn=lambda **kwargs: build_outcome(CHUNKS, sim_threshold=0.5, min_evidence_count=1),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=lambda system_prompt, user_prompt: json.dumps(LLM_OUTPUT, ensure_ascii=False),
        translate_fn=lambda title, body: "q",
    )

    body = payload.model_dump(by_alias=True)
    # changes ← pr_diff 근거 / impacts ← 역할별 영향 / recommendations ← 후속 작업
    assert body["changes"][0]["filePath"] == "AuthService.java"
    assert body["impacts"] == ["프론트엔드: 오류 분기 수정", "QA: 테스트 추가"]
    assert body["recommendations"] == ["리프레시 토큰 정책 정의"]
    assert body["roleImpacts"][0]["basis"] == BASIS_EXPECTED
    assert body["evidence"][1]["chunkId"] == "cr-1"       # location이 chunk를 가리키면 추적 정보 부착
    assert body["evidence"][1]["similarityScore"] == 0.88


def test_evidence_includes_unused_context():
    """LLM이 인용하지 않은 컨텍스트도 '무엇을 보여줬는지' 남긴다."""
    output = {**LLM_OUTPUT, "evidence": [{"id": "e1", "source": "pr_diff", "location": "x.java", "description": "d"}]}

    response = _run(output)

    # 동기 응답의 evidences는 필터 통과 chunk 기반
    assert [e.chunk_id for e in response.evidences] == ["cr-1"]


# ==========================================
# 빈 diff 경로
# ==========================================

def test_empty_diff_skips_llm_and_returns_confirmation_path():
    calls = []
    response = _run(diff="   ", generate_calls=calls)

    assert calls == []                                   # LLM 미호출
    assert response.summary == EMPTY_DIFF_SUMMARY
    assert response.grounding_sufficient is False
    assert response.needs_confirmation is True
    assert any("diff" in item for item in response.confirmation_items)


# ==========================================
# 프롬프트 ↔ 스키마 정합성
# ==========================================

def test_prompt_schema_matches_front_display_items():
    for field in (
        '"summary"', '"purpose"', '"changeReason"', '"before"', '"after"',
        '"relatedFeatures"', '"affectedRoles"', '"roleImpacts"', '"needsConfirmation"', '"evidence"',
    ):
        assert field in OUTPUT_SCHEMA_SECTION


# ==========================================
# 기존 동기 API 계약 유지 (백엔드가 이미 소비 중)
# ==========================================

def test_review_placeholder_values_become_none():
    """구조화 출력 strict 모드는 null 대신 빈 문자열/0을 보내므로 '특정 불가'로 정규화한다."""
    output = {
        **LLM_OUTPUT,
        "riskScore": 55,
        "reviews": [
            {"filePath": "", "lineNumber": 0, "comment": "전체 구조를 다시 확인해 주세요."},
            {"filePath": "AuthService.java", "lineNumber": 24, "comment": "만료 검증 필요"},
            {"filePath": "X.java", "lineNumber": 1, "comment": "  "},   # 빈 코멘트 → 제외
        ],
    }

    response = _run(output)

    assert response.risk_score == 55
    assert len(response.reviews) == 2
    assert response.reviews[0].file_path is None      # "" → None
    assert response.reviews[0].line_number is None    # 0  → None
    assert response.reviews[1].line_number == 24


def test_prompt_schema_keeps_legacy_sync_fields():
    """riskScore/reviews가 스키마에서 빠지면 동기 응답이 조용히 0/[]로 비게 된다(회귀 방지)."""
    assert '"riskScore"' in OUTPUT_SCHEMA_SECTION
    assert '"reviews"' in OUTPUT_SCHEMA_SECTION
    assert "0~100 정수" in OUTPUT_SCHEMA_SECTION

    from services.prompt_builder import RecordDraftOutput

    assert {"riskScore", "reviews"} <= set(RecordDraftOutput.model_fields)
