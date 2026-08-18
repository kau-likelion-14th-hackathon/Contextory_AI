"""Grounded Prompt 단위 테스트 — 명세 규칙 반영과 '필터 통과 Context만 포함' 검증"""

from services.prompt_builder import (
    ALLOWED_ROLES, BASIS_CONFIRMED, BASIS_EXPECTED, NO_CONTEXT_TEXT, PRInput, ProjectInfo,
    RecordDraftOutput, UNKNOWN_VALUE, build_grounded_prompt, build_system_prompt, build_user_prompt,
)

KEPT = [
    {"chunk_id": "cr-1", "source": "code_review_vectors", "similarity_score": 0.81, "text": "JWT 필터 순서를 확인하세요."},
]
REMOVED_TEXT = "이 내용은 필터에서 제거되어 프롬프트에 절대 들어가면 안 된다"

PR = PRInput(
    title="JWT 로그인 추가",
    body="Spring Security 필터 추가",
    changed_files=["src/main/java/auth/AuthService.java"],
    diff="@@ -21,7 +21,18 @@\n+ public TokenResponse login()",
    commits=["a123bc4"],
    issues=["#15"],
)
PROJECT = ProjectInfo(
    name="Contextory", description="PR 기록 서비스", purpose="프로젝트 기록 축적",
    features=["PR 분석"], roles=["프론트엔드", "백엔드"], language="ko",
)


def _user_prompt():
    return build_user_prompt(pr=PR, filtered_contexts=KEPT, project=PROJECT)


# ==========================================
# 시스템 프롬프트 (12개 규칙)
# ==========================================

def test_system_prompt_contains_every_rule_section():
    prompt = build_system_prompt(PROJECT)

    for section in (
        "[자료 사용 규칙]", "[모르는 것 처리 규칙]", "[사실과 예상 구분 규칙]",
        "[역할별 영향 규칙]", "[근거 연결 규칙]", "[표기 규칙]", "[출력 규칙]",
    ):
        assert section in prompt


def test_system_prompt_states_grounding_rules():
    prompt = build_system_prompt(PROJECT)

    assert "diff에 없는 변경을 만들어내지 않고" in prompt          # 규칙 3
    assert UNKNOWN_VALUE in prompt                              # 규칙 4/12
    assert "needsConfirmation" in prompt                        # 규칙 4/5
    assert "충돌" in prompt                                      # 규칙 5
    assert BASIS_CONFIRMED in prompt and BASIS_EXPECTED in prompt  # 규칙 6
    assert "모든 역할에 억지로 영향을 만들지 않는다" in prompt        # 규칙 7
    assert "evidence 배열의 항목과 연결" in prompt                 # 규칙 8
    assert "번역하거나" in prompt                                 # 규칙 9
    assert "JSON 외의 텍스트를 붙이지 않는다" in prompt             # 규칙 11


def test_system_prompt_language_switches():
    assert "한국어로 작성한다" in build_system_prompt(ProjectInfo(language="ko"))
    assert "English로 작성한다" in build_system_prompt(ProjectInfo(language="en"))


# ==========================================
# 유저 프롬프트
# ==========================================

def test_user_prompt_contains_all_sections():
    prompt = _user_prompt()

    for section in ("[프로젝트 정보", "[현재 PR", "[검색된 기존 프로젝트 컨텍스트", "[출력 스키마"):
        assert section in prompt


def test_user_prompt_includes_pr_materials():
    prompt = _user_prompt()

    assert "JWT 로그인 추가" in prompt
    assert "src/main/java/auth/AuthService.java" in prompt
    assert "@@ -21,7 +21,18 @@" in prompt
    assert "a123bc4" in prompt
    assert "#15" in prompt


def test_user_prompt_lists_all_output_schema_fields():
    prompt = _user_prompt()

    for field in (
        "summary", "purpose", "changeReason", "before", "after", "relatedFeatures",
        "affectedRoles", "roleImpacts", "needsConfirmation", "evidence", "basis", "evidenceRefs",
    ):
        assert field in prompt


def test_user_prompt_restricts_allowed_roles():
    prompt = _user_prompt()

    assert "affectedRoles 허용 값" in prompt
    for role in ALLOWED_ROLES:
        assert role in prompt


def test_context_section_format_and_filtering():
    prompt = _user_prompt()

    assert "[cr-1] (출처: code_review_vectors, similarity: 0.8100)" in prompt
    assert REMOVED_TEXT not in prompt


def test_no_context_case_is_explicit():
    prompt = build_user_prompt(pr=PR, filtered_contexts=[], project=PROJECT)

    assert NO_CONTEXT_TEXT in prompt


def test_grounded_prompt_combines_system_and_user():
    combined = build_grounded_prompt(pr=PR, filtered_contexts=KEPT, project=PROJECT)

    assert "당신은 Contextory의 PR 분석가다" in combined
    assert "[현재 PR" in combined


# ==========================================
# 구조화 출력 스키마 (프롬프트와 1:1)
# ==========================================

def test_record_draft_output_covers_front_display_items():
    fields = set(RecordDraftOutput.model_fields)

    # 프론트 표시 10개 항목 + followUpTasks
    assert {
        "summary", "purpose", "changeReason", "before", "after", "relatedFeatures",
        "affectedRoles", "roleImpacts", "needsConfirmation", "evidence", "followUpTasks",
    } <= fields
    # 기존 동기 API(POST /api/v1/analyze/pr)가 소비 중인 필드도 함께 생성한다
    assert {"riskScore", "reviews"} <= fields


def test_record_draft_output_parses_spec_example():
    draft = RecordDraftOutput.model_validate({
        "summary": "요약",
        "purpose": UNKNOWN_VALUE,
        "changeReason": UNKNOWN_VALUE,
        "before": "before",
        "after": "after",
        "relatedFeatures": ["로그인"],
        "affectedRoles": ["프론트엔드"],
        "roleImpacts": [
            {"role": "프론트엔드", "impact": "오류 분기 수정", "basis": BASIS_EXPECTED, "evidenceRefs": ["e1"]}
        ],
        "followUpTasks": [],
        "needsConfirmation": ["변경 이유가 PR 본문에 없음 — PR 작성자에게 확인 필요"],
        "evidence": [
            {"id": "e1", "source": "pr_diff", "location": "LoginResponse.java", "description": "errorCode 추가"}
        ],
        "riskScore": 40,
        "reviews": [
            {"filePath": "LoginResponse.java", "lineNumber": 12, "comment": "errorCode enum 정의 위치를 확인해 주세요."}
        ],
    })

    assert draft.roleImpacts[0].basis == BASIS_EXPECTED
    assert draft.evidence[0].id == "e1"
    assert draft.needsConfirmation
    assert draft.riskScore == 40
    assert draft.reviews[0].lineNumber == 12
