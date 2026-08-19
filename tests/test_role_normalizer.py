"""
project_role 정규화 테스트.

백엔드는 project_role 을 자유 입력 VARCHAR(50)으로 받아 trim 만 하고 저장한다
(ProjectMemberService.normalizeProjectRole). 값 정규화는 AI 서버 담당이다.
"""

import pytest

from services.prompt_builder import ALLOWED_ROLES
from services.role_normalizer import (
    AMBIGUOUS, normalize_role, normalize_roles, partition_roles,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # 실제 팀에서 쓰는 3개 역할의 표기 흔들림
        ("프론트엔드", "프론트엔드"),
        ("프론트", "프론트엔드"),
        ("프런트엔드", "프론트엔드"),
        ("FE", "프론트엔드"),
        ("Frontend", "프론트엔드"),
        ("front-end", "프론트엔드"),
        ("프론트엔드 개발자", "프론트엔드"),
        ("백엔드", "백엔드"),
        ("백", "백엔드"),
        ("BE", "백엔드"),
        ("Back End", "백엔드"),
        ("서버", "백엔드"),
        ("백엔드개발", "백엔드"),
        ("Spring", "백엔드"),
        ("AI", "AI"),
        ("ai", "AI"),
        ("인공지능", "AI"),
        ("ML 엔지니어", "AI"),
        ("LLM", "AI"),
        # 나머지 허용 역할
        ("기획자", "기획"),
        ("PO", "기획"),
        ("디자이너", "디자인"),
        ("UX", "디자인"),
        ("QA", "QA"),
        ("테스터", "QA"),
        ("프로젝트 관리자", "프로젝트 관리자"),
        ("Project Manager", "프로젝트 관리자"),
    ],
)
def test_normalize_role_maps_common_spellings(raw, expected):
    assert normalize_role(raw) == expected


def test_allowed_roles_map_to_themselves():
    """허용 역할 7종은 그대로 넣어도 자기 자신으로 정규화되어야 한다."""
    for role in ALLOWED_ROLES:
        assert normalize_role(role) == role


@pytest.mark.parametrize("raw", ["PM", "매니저", "UI", "웹", "풀스택", "개발"])
def test_ambiguous_values_are_not_guessed(raw):
    """
    뜻이 하나로 정해지지 않는 값은 추측해서 붙이지 않는다.
    (틀린 역할에 영향이 배정되면 그 역할 담당자가 잘못된 작업을 하게 된다)
    """
    assert normalize_role(raw) is None


@pytest.mark.parametrize("raw", ["", "   ", None, "개발자", "아무말", "총무"])
def test_unknown_values_return_none(raw):
    assert normalize_role(raw) is None


def test_ambiguous_table_never_overlaps_alias_table():
    """애매 목록에 있는 값이 실수로 매핑되어 있으면 안 된다."""
    assert all(normalize_role(value) is None for value in AMBIGUOUS)


def test_partition_reports_unresolved_values():
    resolved, unresolved = partition_roles(["프론트", "BE", "PM", "총무", "AI"])

    assert resolved == ["프론트엔드", "백엔드", "AI"]
    assert unresolved == ["PM", "총무"]


def test_normalize_roles_dedupes_and_keeps_order():
    assert normalize_roles(["FE", "프론트엔드", "front", "백엔드"]) == ["프론트엔드", "백엔드"]


def test_normalize_roles_ignores_non_list_input():
    assert normalize_roles("프론트엔드") == []
    assert normalize_roles(None) == []
    assert normalize_roles(42) == []
