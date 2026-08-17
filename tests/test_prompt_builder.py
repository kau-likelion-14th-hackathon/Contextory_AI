"""Grounded Prompt 단위 테스트 — 규칙 반영과 '필터 통과 Context만 포함' 검증"""

from services.prompt_builder import PRInput, ProjectInfo, build_grounded_prompt

KEPT = [
    {"chunk_id": "cr-1", "source": "code_review_vectors", "similarity_score": 0.81, "text": "JWT 필터 순서를 확인하세요."},
]
REMOVED_TEXT = "이 내용은 필터에서 제거되어 프롬프트에 절대 들어가면 안 된다"


def _prompt():
    return build_grounded_prompt(
        pr=PRInput(
            title="JWT 로그인 추가",
            body="Spring Security 필터 추가",
            changed_files=["src/main/java/auth/AuthService.java"],
            diff="@@ -21,7 +21,18 @@\n+ public TokenResponse login()",
            commits=["a123bc4"],
            issues=["#15"],
        ),
        filtered_contexts=KEPT,
        project=ProjectInfo(name="Contextory", one_liner="PR 기록 서비스", team_roles=["Backend", "Frontend"]),
    )


def test_prompt_contains_all_required_sections():
    prompt = _prompt()

    for section in ("[역할]", "[프로젝트 정보]", "[현재 PR", "[검색된 기존 프로젝트 컨텍스트", "[분석 규칙]", "[출력 스키마"):
        assert section in prompt


def test_prompt_contains_every_grounding_rule():
    prompt = _prompt()

    assert "needsConfirmation" in prompt                     # 규칙 3
    assert "구분해 서술" in prompt                            # 규칙 4
    assert "충돌" in prompt                                   # 규칙 5
    assert "JSON 객체 하나만 출력" in prompt                    # 규칙 6
    assert "evidence" in prompt                              # 규칙 7
    assert "번역하거나 고쳐 쓰지 않고" in prompt                 # 규칙 8
    assert "임의로 만들어내지 않는다" in prompt                   # 규칙 9 (일반 역할 억지 생성 금지)


def test_prompt_lists_all_output_schema_fields():
    prompt = _prompt()

    for field in (
        "summary", "purpose", "changeReason", "before", "after", "relatedFeatures",
        "affectedRoles", "roleImpacts", "followUpTasks", "needsConfirmation", "evidence",
    ):
        assert field in prompt


def test_only_filtered_contexts_appear():
    prompt = build_grounded_prompt(pr=PRInput(title="t", diff="d"), filtered_contexts=KEPT)

    assert "cr-1" in prompt
    assert REMOVED_TEXT not in prompt


def test_no_context_case_is_explicit():
    prompt = build_grounded_prompt(pr=PRInput(title="t", diff="d"), filtered_contexts=[])

    assert "필터를 통과한 컨텍스트 없음" in prompt


def test_language_switches_to_english():
    prompt = build_grounded_prompt(
        pr=PRInput(title="t", diff="d"),
        filtered_contexts=[],
        project=ProjectInfo(name="C", language="en"),
    )

    assert "서술 언어는 English 로 작성한다" in prompt
