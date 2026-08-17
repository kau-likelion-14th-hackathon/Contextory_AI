"""데이터셋 모듈 테스트 — GroundTruth 정규화 / JSONL 로더 / 외부 데이터 어댑터 / silver 생성"""

import json

import pytest

from eval.datasets.codereview_adapters import adapt, adapt_code_review_gh, extract_keywords
from eval.datasets.ground_truth import GroundTruthCase, as_case
from eval.datasets.loader import DEFAULT_GROUND_TRUTH_PATH, load_cases, save_cases
from eval.datasets.silver_builder import build_silver_case, build_silver_dataset

RAW_RECORDS = [
    {
        "repo_name": "contextory", "dataset": "github_2023", "lang": "python", "pr_id": 42,
        "owner": "kau", "reviewer": "dev",
        "diff_hunk": "@@ -1,3 +1,9 @@\n+def login():", "code_review_comment": "AuthService.login 에서 토큰 만료를 검증해야 합니다.",
    },
    {  # 단답형 → 제외되어야 함
        "repo_name": "contextory", "pr_id": 43, "diff_hunk": "@@ -1 +1 @@", "code_review_comment": "done",
    },
    {  # diff 없음 → 제외되어야 함
        "repo_name": "contextory", "pr_id": 44, "diff_hunk": "", "code_review_comment": "이 부분은 리팩터링이 필요합니다.",
    },
]


def test_sample_ground_truth_file_loads():
    cases = load_cases(DEFAULT_GROUND_TRUTH_PATH)

    assert len(cases) >= 3
    assert all(case.gold_chunks for case in cases)
    assert all(case.query for case in cases)


def test_as_case_normalizes_dict_and_aliases():
    case = as_case({"question": "쿼리", "gold_ids": ["g1"], "id": "c1"})

    assert isinstance(case, GroundTruthCase)
    assert case.case_id == "c1"
    assert case.query == "쿼리"
    assert case.gold_chunks == ["g1"]
    assert case.has_ground_truth is True


def test_as_case_rejects_missing_query():
    with pytest.raises(ValueError):
        as_case({"case_id": "x"})

    with pytest.raises(TypeError):
        as_case("문자열은 케이스가 아니다")


def test_loader_round_trip(tmp_path):
    path = tmp_path / "cases.jsonl"
    cases = [GroundTruthCase(case_id="c1", query="q1", gold_chunks=["g1"], reference_keywords=["kw"])]

    assert save_cases(cases, path) == 1
    loaded = load_cases(path)

    assert loaded[0].case_id == "c1"
    assert loaded[0].reference_keywords == ["kw"]


def test_loader_reports_broken_line_with_position(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"case_id": "ok", "query": "q"}\n{깨진 줄}\n', encoding="utf-8")

    with pytest.raises(ValueError) as e:
        load_cases(path)

    assert ":2" in str(e.value)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_cases("/tmp/definitely-not-here.jsonl")


def test_code_review_adapter_filters_noise_and_builds_cases():
    cases = adapt_code_review_gh(RAW_RECORDS)

    assert len(cases) == 1
    case = cases[0]
    assert case.query.startswith("@@")
    assert case.gold_chunks == ["cr-0"]
    assert case.reference_answer.startswith("AuthService.login")
    assert "AuthService.login" in case.reference_keywords
    assert case.metadata["source"] == "code_review_gh"


def test_adapter_accepts_custom_chunk_id_mapping():
    cases = adapt_code_review_gh(RAW_RECORDS, chunk_id_fn=lambda record, idx: f"db-{record['pr_id']}")

    assert cases[0].gold_chunks == ["db-42"]


def test_adapt_rejects_unknown_source():
    with pytest.raises(ValueError):
        adapt("unknown_dataset", RAW_RECORDS)


def test_extract_keywords_skips_stopwords():
    keywords = extract_keywords("Please use the AuthService.login method with JwtProvider")

    assert "AuthService.login" in keywords
    assert "JwtProvider" in keywords
    assert "please" not in [k.lower() for k in keywords]


def test_silver_case_uses_strong_matches_and_always_keeps_top1():
    retrieved = [
        {"chunk_id": "a", "similarity_score": 0.91},
        {"chunk_id": "b", "similarity_score": 0.80},
        {"chunk_id": "c", "similarity_score": 0.20},
    ]

    case = build_silver_case("s1", "질의", retrieved, silver_threshold=0.85)

    assert case.gold_chunks == ["a"]
    assert case.metadata["label_type"] == "silver"

    # 전부 threshold 미달이어도 Top-1은 남는다 (런타임 Top-1 보존 규칙과 동일 가정)
    weak = build_silver_case("s2", "질의", retrieved, silver_threshold=0.99)
    assert weak.gold_chunks == ["a"]


def test_silver_dataset_uses_injected_retriever():
    def retrieve_fn(query):
        return [{"chunk_id": "x", "similarity_score": 0.9}]

    cases = build_silver_dataset([{"case_id": "s1", "query": "질의"}], retrieve_fn=retrieve_fn)

    assert cases[0].gold_chunks == ["x"]
    assert cases[0].metadata["retrieved_count"] == 1


def test_ground_truth_case_serialization():
    case = GroundTruthCase(case_id="c", query="q", gold_chunks=["g"], reference_answer="ref")

    payload = json.loads(json.dumps(case.to_dict(), ensure_ascii=False))

    assert payload["gold_chunks"] == ["g"]
    assert case.as_reference()["reference_answer"] == "ref"
