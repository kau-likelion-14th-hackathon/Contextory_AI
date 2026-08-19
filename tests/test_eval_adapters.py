"""
scripts 조립 계층 테스트 — services(런타임) 로직을 eval(평가)에 주입했을 때 실제로 맞물리는지 검증.

eval/ 자체는 services/ 를 import하지 않으므로, 둘을 연결하는 이 계층이 깨지면
"실제 필터·Confidence로 평가한다"는 리포트의 전제가 무너진다.
"""

from core.config import settings
from eval.datasets.ground_truth import GroundTruthCase
from eval.fakes import SAMPLE_CORPUS, make_keyword_retriever, make_template_generator
from eval.judge import OfflineKeywordJudge
from eval.runner import MODE_FILTER_OFF, MODE_FILTER_ON, run_comparison
from scripts.eval_adapters import make_runtime_filter_fn, runtime_confidence_fn
from services import analysis_service

CASES = [
    GroundTruthCase(
        case_id="pr-101",
        query="JWT 로그인 도입 PR: AuthService 재발급, SecurityConfig 토큰 필터 등록",
        gold_chunks=["cr-101", "repo-auth01"],
        reference_keywords=["JWT", "AuthService"],
    ),
]


def test_runtime_filter_adapter_returns_kept_and_removed():
    chunks = [
        {"chunk_id": "a", "similarity_score": 0.9},
        {"chunk_id": "b", "similarity_score": 0.2},
    ]

    kept, removed = make_runtime_filter_fn(0.5)(chunks)

    assert [c["chunk_id"] for c in kept] == ["a"]
    assert [c["chunk_id"] for c in removed] == ["b"]


def test_runtime_filter_adapter_preserves_top1():
    chunks = [{"chunk_id": "a", "similarity_score": 0.1}, {"chunk_id": "b", "similarity_score": 0.05}]

    kept, _ = make_runtime_filter_fn(0.9)(chunks)

    assert [c["chunk_id"] for c in kept] == ["a"]


def test_runtime_confidence_adapter_returns_score_in_range():
    score = runtime_confidence_fn([{"similarity_score": 0.9}, {"similarity_score": 0.8}], 0.2)

    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    assert runtime_confidence_fn([], 0.0) == 0.0


def test_comparison_runs_end_to_end_with_runtime_logic():
    """평가 러너 + 실제 필터/Confidence 조합이 리포트 입력까지 만들어낸다."""
    comparison = run_comparison(
        cases=CASES,
        retrieve_fn=make_keyword_retriever(SAMPLE_CORPUS, top_k=5),
        filter_fn=make_runtime_filter_fn(settings.SIM_THRESHOLD),
        generate_fn=make_template_generator(),
        judge=OfflineKeywordJudge(),
        confidence_fn=runtime_confidence_fn,
        k=5,
    )

    off = comparison[MODE_FILTER_OFF].aggregate
    on = comparison[MODE_FILTER_ON].aggregate

    assert on["filtering"]["filter_ratio"] > off["filtering"]["filter_ratio"]
    assert on["filtering"]["false_deletion"] == 0.0          # 실제 필터는 gold를 지우지 않는다
    assert on["context"]["precision_at_k"] > off["context"]["precision_at_k"]
    assert on["confidence"] is not None


def test_default_filter_selection_follows_filter_mode(monkeypatch):
    """FILTER_MODE=llm 이면 LLM Agent 필터가, 그 외에는 임계값 필터가 선택된다."""
    called = {}

    def fake_llm_filter(chunks, query_text):
        called["query_text"] = query_text
        return "llm-outcome"

    monkeypatch.setattr(analysis_service, "filter_contexts_with_llm", lambda chunks, query_text: fake_llm_filter(chunks, query_text))
    monkeypatch.setattr(settings, "FILTER_MODE", "llm")

    assert analysis_service._make_default_filter("질의")([]) == "llm-outcome"
    assert called["query_text"] == "질의"

    monkeypatch.setattr(settings, "FILTER_MODE", "on")
    outcome = analysis_service._make_default_filter("질의")([{"chunk_id": "a", "similarity_score": 0.9}])
    assert outcome.mode == "on"
