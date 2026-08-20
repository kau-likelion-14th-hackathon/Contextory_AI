"""Runner 테스트 — 가짜 callback만으로 전체 평가가 돌아가는지 검증 (LLM·DB 없음)"""

from eval.datasets.ground_truth import GroundTruthCase
from eval.fakes import (
    SAMPLE_CORPUS, make_failing_retriever, make_fake_confidence_fn, make_keyword_retriever,
    make_template_generator, make_threshold_filter,
)
from eval.judge import OfflineKeywordJudge
from eval.runner import MODE_FILTER_OFF, MODE_FILTER_ON, run_comparison, run_evaluation

CASES = [
    GroundTruthCase(
        case_id="pr-101",
        query="JWT 로그인 도입 PR: AuthService 재발급, SecurityConfig 토큰 필터 등록",
        gold_chunks=["cr-101", "repo-auth01"],
        reference_keywords=["JWT", "AuthService"],
    ),
    GroundTruthCase(
        case_id="pr-102",
        query="프로젝트 목록 조회 N+1 쿼리 제거: ProjectRepository fetch join 적용",
        gold_chunks=["cr-201"],
        reference_keywords=["N+1", "fetch join"],
    ),
]


def _retrieve():
    return make_keyword_retriever(SAMPLE_CORPUS, top_k=5)


def test_runner_works_with_only_fake_callbacks():
    run = run_evaluation(
        cases=CASES,
        retrieve_fn=_retrieve(),
        filter_fn=make_threshold_filter(0.5),
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        confidence_fn=make_fake_confidence_fn(),
        k=5,
    )

    assert run.mode == MODE_FILTER_ON
    assert len(run.cases) == 2
    assert run.aggregate["retrieval"]["recall_at_k"] > 0
    assert run.aggregate["generation"]["groundedness"] > 0
    assert run.aggregate["confidence"] is not None


def test_filter_off_keeps_everything():
    run = run_evaluation(cases=CASES, retrieve_fn=_retrieve(), filter_fn=None, k=5)

    assert run.mode == MODE_FILTER_OFF
    for case in run.cases:
        assert case.filtered_ids == case.retrieved_ids
        assert case.removed_ids == []
        assert case.filter_ratio == 0.0


def test_comparison_shows_filter_effect():
    comparison = run_comparison(
        cases=CASES,
        retrieve_fn=_retrieve(),
        filter_fn=make_threshold_filter(0.5),
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        confidence_fn=make_fake_confidence_fn(),
        k=5,
    )

    off = comparison[MODE_FILTER_OFF].aggregate
    on = comparison[MODE_FILTER_ON].aggregate

    assert off["filtering"]["filter_ratio"] == 0.0
    assert on["filtering"]["filter_ratio"] > 0.0
    # 필터가 노이즈를 걷어내 최종 컨텍스트 정밀도가 오른다
    assert on["context"]["precision_at_k"] > off["context"]["precision_at_k"]
    # gold는 지워지지 않는다
    assert on["filtering"]["false_deletion"] == 0.0
    assert on["filtering"]["recall_delta_at_k"] == 0.0


def test_aggressive_filter_is_detected_as_information_loss():
    """gold를 지우는 강한 임계값에서 false_deletion>0, recall_delta<0 이 잡힌다."""
    comparison = run_comparison(
        cases=CASES,
        retrieve_fn=_retrieve(),
        filter_fn=make_threshold_filter(0.95),
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        confidence_fn=make_fake_confidence_fn(),
        k=5,
    )
    on = comparison[MODE_FILTER_ON].aggregate

    assert on["filtering"]["false_deletion"] > 0.0
    assert on["filtering"]["recall_delta_at_k"] < 0.0
    assert on["filtering"]["gold_retained"] < 1.0


def test_filter_stage_error_when_all_gold_is_deleted():
    """Top-1 보존을 끈 필터가 gold를 전부 지우면 오류 단계가 Filter로 분류된다."""
    run = run_evaluation(
        cases=CASES,
        retrieve_fn=_retrieve(),
        filter_fn=lambda chunks: [],   # Top-1 보존을 지키지 않는 필터를 흉내낸다
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        k=5,
    )

    assert run.aggregate["error_stages"].get("Filter", 0) == len(CASES)
    assert run.aggregate["filtering"]["gold_retained"] == 0.0


def test_reference_free_mode_skips_ground_truth_metrics():
    cases = [{"case_id": "no-gt", "query": "콜백 재시도 추가 PR"}]

    run = run_evaluation(
        cases=cases,
        retrieve_fn=_retrieve(),
        filter_fn=make_threshold_filter(0.5),
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        k=5,
    )

    assert run.aggregate["ground_truth_case_count"] == 0
    assert run.aggregate["filtering"] == {}
    assert run.aggregate["generation"]["groundedness"] > 0


def test_case_level_failure_is_recorded_not_swallowed():
    run = run_evaluation(cases=CASES, retrieve_fn=make_failing_retriever(), k=5)

    assert run.aggregate["error_case_count"] == 2
    assert all(case.error for case in run.cases)
    assert run.aggregate["error_stages"]["Execution"] == 2


def test_filter_fn_may_return_kept_and_removed_tuple():
    def filter_fn(chunks):
        kept = [c for c in chunks if c["similarity_score"] >= 0.5]
        removed = [c for c in chunks if c["similarity_score"] < 0.5]
        return kept, removed

    run = run_evaluation(cases=CASES, retrieve_fn=_retrieve(), filter_fn=filter_fn, k=5)

    assert all(case.removed_ids for case in run.cases)


def test_generate_fn_may_return_confidence_dict():
    def generate_fn(query, contexts):
        return {"answer": "답변", "confidence": 0.42}

    run = run_evaluation(cases=CASES, retrieve_fn=_retrieve(), generate_fn=generate_fn, k=5)

    assert run.aggregate["confidence"] == 0.42
