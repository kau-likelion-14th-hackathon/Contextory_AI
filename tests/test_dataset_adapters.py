"""utils/adapter.py 데이터셋 전처리 테스트 (외부 데이터·네트워크 없이 DataFrame으로 검증)"""

import pandas as pd

from utils.adapter import (
    _generate_diff, adapt_code_review_gh, adapt_codereviewer, adapt_contextual_code_review,
)


def test_code_review_gh_filters_short_comments_and_long_diffs():
    df = pd.DataFrame(
        {
            "code_review_comment": [
                "토큰 만료 검증이 필요합니다.",   # 유지 (15자 이상)
                "done",                          # 제거 (단답형)
                "이 부분은 리팩터링이 필요합니다.",  # 제거 (diff가 너무 김)
            ],
            "diff_hunk": ["@@ -1 +1 @@\n+a", "@@ -1 +1 @@\n+b", "x" * 4001],
        }
    )

    adapted = adapt_code_review_gh(df)

    assert len(adapted) == 1
    assert adapted.iloc[0]["review_comment"].startswith("토큰 만료")
    assert adapted.iloc[0]["pr_diff"] == adapted.iloc[0]["source_code"]
    assert bool(adapted.iloc[0]["has_issue"]) is True
    assert list(adapted.columns) == ["source_code", "pr_diff", "review_comment", "has_issue"]


def test_contextual_code_review_generates_diff():
    df = pd.DataFrame(
        {
            "comment": ["예외 처리를 추가해 주세요.", "fixed"],
            "method_body": ["def f():\n    return 1\n", "def g():\n    pass\n"],
            "method_body_after": ["def f():\n    return 2\n", "def g():\n    pass\n"],
        }
    )

    adapted = adapt_contextual_code_review(df)

    assert len(adapted) == 1
    assert "-    return 1" in adapted.iloc[0]["pr_diff"]
    assert "+    return 2" in adapted.iloc[0]["pr_diff"]


def test_codereviewer_maps_label_to_has_issue():
    df = pd.DataFrame(
        {
            "msg": ["Is this used?", "ok"],          # 6자 이상만 유지 → "ok"(2자) 제거
            "oldf": ["int a;", "int b;"],
            "patch": ["@@ -1 +1 @@\n-int a;", "@@ -1 +1 @@"],
            "y": [1, 0],
        }
    )

    adapted = adapt_codereviewer(df)

    assert len(adapted) == 1
    assert bool(adapted.iloc[0]["has_issue"]) is True


def test_generate_diff_handles_empty_input():
    assert _generate_diff("", "after") == ""
    assert _generate_diff("before", "") == ""
    assert "+after" in _generate_diff("before\n", "after\n")


def test_all_adapters_share_the_same_output_columns():
    """세 어댑터 모두 code_review_vectors 스키마에 맞는 동일한 컬럼을 낸다."""
    expected = ["source_code", "pr_diff", "review_comment", "has_issue"]

    gh = adapt_code_review_gh(
        pd.DataFrame({"code_review_comment": ["충분히 긴 리뷰 코멘트입니다."], "diff_hunk": ["@@ -1 +1 @@"]})
    )
    ctx = adapt_contextual_code_review(
        pd.DataFrame({
            "comment": ["충분히 긴 리뷰 코멘트입니다."],
            "method_body": ["a\n"], "method_body_after": ["b\n"],
        })
    )
    reviewer = adapt_codereviewer(
        pd.DataFrame({"msg": ["needs fix"], "oldf": ["a"], "patch": ["@@"], "y": [1]})
    )

    for adapted in (gh, ctx, reviewer):
        assert list(adapted.columns) == expected
