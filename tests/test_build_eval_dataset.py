"""fromdb 데이터셋 빌더 테스트 — DB 없이 행→케이스 변환 로직만 검증"""

from scripts.build_eval_dataset import build_cases_from_rows
from services.retrieval import make_repo_chunk_id, make_review_chunk_id

ROWS = [
    {
        "id": 12345, "orig_idx": 7, "dataset_source": "github_2023",
        "source_code": "def login(): ...",
        "pr_diff": "@@ -1,3 +1,9 @@\n+def login():",
        "review_comment": "AuthService.login 에서 토큰 만료를 검증해야 합니다.",
    },
    {  # pr_diff 없음 → 제외
        "id": 2, "pr_diff": "", "source_code": "", "review_comment": "충분히 긴 리뷰 코멘트입니다.",
    },
    {  # 코멘트 없이 diff만 있어도 self-retrieval 케이스로는 유효
        "id": 3, "pr_diff": "@@ -5 +5 @@\n-old\n+new", "review_comment": None,
    },
]


def test_gold_chunk_id_matches_runtime_rule():
    """gold id를 런타임과 같은 함수로 만들어야 --live 평가에서 맞물린다."""
    cases = build_cases_from_rows(ROWS)

    assert cases[0].gold_chunks == [make_review_chunk_id(12345)] == ["cr-12345"]


def test_rows_without_diff_are_skipped():
    cases = build_cases_from_rows(ROWS)

    assert [case.metadata["db_id"] for case in cases] == [12345, 3]


def test_case_fields_are_populated():
    case = build_cases_from_rows(ROWS)[0]

    assert case.case_id == "db-12345"
    assert case.query.startswith("@@")
    assert case.reference_answer.startswith("AuthService.login")
    assert "AuthService.login" in case.reference_keywords
    assert case.metadata["label_type"] == "self_retrieval"
    assert case.metadata["orig_idx"] == 7
    assert case.metadata["dataset_source"] == "github_2023"


def test_row_without_comment_has_no_reference():
    case = build_cases_from_rows(ROWS)[1]

    assert case.reference_answer is None
    assert case.reference_keywords == []
    assert case.gold_chunks == ["cr-3"]


def test_empty_rows_produce_no_cases():
    assert build_cases_from_rows([]) == []


def test_chunk_id_helpers_are_single_source_of_truth():
    assert make_review_chunk_id(9) == "cr-9"
    assert make_repo_chunk_id("abcdefghijkl") == "repo-abcdefgh"
