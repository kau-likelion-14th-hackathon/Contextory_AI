"""RAG 파이프라인 오케스트레이션 테스트 — DB·LLM은 전부 주입(mock)한다."""

import json

import pytest

from models.schemas import (
    AsyncAnalysisRequest, PRAnalysisRequest, PullRequestFile, PullRequestInfo,
)
from services.analysis_service import (
    INSUFFICIENT_GROUNDING_SUMMARY, LLMResponseParseError, analyze_pr_for_callback,
    analyze_pr_pipeline, parse_llm_json, run_pipeline,
)
from services.context_filter import filter_contexts
from services.prompt_builder import PRInput
from services.retrieval import RetrievalError, build_outcome

LLM_OUTPUT = {
    "summary": "JWT 기반 인증을 도입했다.",
    "purpose": "모바일 클라이언트 인증 유지",
    "changeReason": "세션 인증이 모바일에서 끊김",
    "before": "세션 인증",
    "after": "JWT 인증",
    "relatedFeatures": ["로그인"],
    "affectedRoles": ["Backend"],
    "roleImpacts": [{"role": "Backend", "impact": "토큰 검증 필터 추가", "evidenceIds": ["cr-1"]}],
    "followUpTasks": ["리프레시 토큰 만료 정책 정의"],
    "needsConfirmation": False,
    "confirmationItems": [],
    "evidence": [{"chunkId": "cr-1", "filePath": None, "diffLocation": "@@ -21,7 +21,18 @@", "description": "필터 등록"}],
    "risks": ["토큰 탈취"],
    "recommendations": ["만료 시간 축소 검토"],
    "riskScore": 30,
    "reviews": [{"file_path": "AuthService.java", "line_number": 21, "comment": "필터 순서 확인"}],
    "changes": [{"filePath": "AuthService.java", "description": "login 추가"}],
}

CHUNKS = [
    {"chunk_id": "cr-1", "id": "1", "source_type": "code_review", "source": "kb",
     "text": "JWT 필터", "similarity_score": 0.88, "review_comment": "JWT 필터"},
    {"chunk_id": "cr-2", "id": "2", "source_type": "code_review", "source": "kb",
     "text": "무관", "similarity_score": 0.10, "review_comment": "무관"},
]


def _fake_retrieve(chunks):
    def retrieve(**kwargs):
        return build_outcome(chunks, sim_threshold=0.5, min_evidence_count=1)

    return retrieve


def _fake_generate(payload=None, raw=None):
    calls = []

    def generate(system_instruction, prompt):
        calls.append(prompt)
        return raw if raw is not None else json.dumps(payload or LLM_OUTPUT, ensure_ascii=False)

    generate.calls = calls
    return generate


def _request():
    return PRAnalysisRequest(
        pr_id=101, repo_name="org/contextory", title="JWT 로그인 추가",
        description="Spring Security 필터 추가", diff_content="@@ -21,7 +21,18 @@\n+login()", author="dev",
    )


def _async_request():
    return AsyncAnalysisRequest(
        analysisId=1, projectId=1, repositoryId=1, repositoryFullName="org/contextory",
        pullRequest=PullRequestInfo(
            githubPrId=1, prNumber=18, title="JWT 로그인 추가", body="본문", headSha="a1b2c3",
            sourceBranch="feature/login", targetBranch="develop",
            files=[PullRequestFile(filePath="AuthService.java", changeType="MODIFIED", patch="@@ +login()", additions=10, deletions=1)],
        ),
        language="ko",
        callbackUrl="https://example.com/callback",
    )


def test_pipeline_tracks_every_stage():
    generate = _fake_generate()

    ctx = run_pipeline(
        pr=PRInput(title="t", body="b", diff="d"),
        repo_name="org/contextory",
        retrieve_fn=_fake_retrieve(CHUNKS),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=generate,
        translate_fn=lambda title, body: "translated",
    )

    assert ctx.retrieval.retrieved_count == 2
    assert [c["chunk_id"] for c in ctx.kept] == ["cr-1"]
    assert ctx.filter_ratio == 0.5
    assert ctx.prompt and "cr-1" in ctx.prompt
    assert ctx.llm_output["summary"] == LLM_OUTPUT["summary"]
    assert ctx.confidence.score > 0
    log = ctx.to_log_dict()
    assert log["kept_count"] == 1 and log["removed_count"] == 1 and log["top1_preserved"] is True


def test_insufficient_grounding_skips_llm_call():
    """검색 결과가 전부 threshold 미달이면 LLM을 호출하지 않고 '근거 부족' 경로로 처리한다."""
    weak = [{"chunk_id": "cr-9", "id": "9", "similarity_score": 0.2, "text": "무관"}]
    generate = _fake_generate()

    ctx = run_pipeline(
        pr=PRInput(title="t", diff="d"),
        repo_name="org/contextory",
        retrieve_fn=_fake_retrieve(weak),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5, filter_mode="on"),
        generate_fn=generate,
        translate_fn=lambda title, body: "q",
    )

    assert ctx.grounding_sufficient is False
    assert generate.calls == []                       # LLM 미호출
    assert ctx.prompt is None
    assert ctx.llm_output["summary"] == INSUFFICIENT_GROUNDING_SUMMARY
    assert ctx.confidence.needs_confirmation is True


def test_empty_retrieval_is_insufficient_grounding():
    ctx = run_pipeline(
        pr=PRInput(title="t", diff="d"),
        repo_name="org/contextory",
        retrieve_fn=_fake_retrieve([]),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
        generate_fn=_fake_generate(),
        translate_fn=lambda title, body: "q",
    )

    assert ctx.grounding_sufficient is False
    assert ctx.kept == []
    assert ctx.confidence.score == 0.0


@pytest.mark.parametrize("raw", ["not json at all", "", None, "[1, 2, 3]"])
def test_llm_parse_failure_is_not_swallowed(raw):
    """깨진 JSON / 빈 응답 / None / JSON 배열 전부 실패로 처리되어야 한다."""
    with pytest.raises(LLMResponseParseError):
        run_pipeline(
            pr=PRInput(title="t", diff="d"),
            repo_name="org/contextory",
            retrieve_fn=_fake_retrieve(CHUNKS),
            filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
            generate_fn=lambda system_instruction, prompt: raw,
            translate_fn=lambda title, body: "q",
        )


def test_parse_llm_json_accepts_valid_object():
    assert parse_llm_json('{"summary": "ok"}') == {"summary": "ok"}


def test_db_error_propagates_as_retrieval_error():
    def failing_retrieve(**kwargs):
        raise RetrievalError("vector db down")

    with pytest.raises(RetrievalError):
        run_pipeline(
            pr=PRInput(title="t", diff="d"),
            repo_name="org/contextory",
            retrieve_fn=failing_retrieve,
            generate_fn=_fake_generate(),
            translate_fn=lambda title, body: "q",
        )


def test_sync_response_keeps_backward_compatible_fields():
    response = analyze_pr_pipeline(
        _request(),
        retrieve_fn=_fake_retrieve(CHUNKS),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
        generate_fn=_fake_generate(),
        translate_fn=lambda title, body: "q",
    )

    # 기존 계약 필드
    assert response.pr_id == 101
    assert response.summary == LLM_OUTPUT["summary"]
    assert response.risk_score == 30
    assert response.reviews[0].comment == "필터 순서 확인"
    assert response.evidences[0].chunk_id == "cr-1"
    assert response.filter_ratio == 0.5
    # 추가된 기록 초안 필드
    assert response.purpose == LLM_OUTPUT["purpose"]
    assert response.role_impacts[0].role == "Backend"
    assert response.grounding_sufficient is True


def test_sync_response_on_insufficient_grounding():
    response = analyze_pr_pipeline(
        _request(),
        retrieve_fn=_fake_retrieve([{"chunk_id": "x", "id": "x", "similarity_score": 0.1}]),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
        generate_fn=_fake_generate(),
        translate_fn=lambda title, body: "q",
    )

    assert response.grounding_sufficient is False
    assert response.needs_confirmation is True
    assert response.summary == INSUFFICIENT_GROUNDING_SUMMARY
    assert response.confirmation_items


def test_callback_payload_serializes_camel_case():
    payload = analyze_pr_for_callback(
        _async_request(),
        retrieve_fn=_fake_retrieve(CHUNKS),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
        generate_fn=_fake_generate(),
        translate_fn=lambda title, body: "q",
    )

    body = payload.model_dump(by_alias=True)
    assert body["summary"] == LLM_OUTPUT["summary"]
    assert body["changes"][0]["filePath"] == "AuthService.java"
    assert body["changeReason"] == LLM_OUTPUT["changeReason"]
    assert body["evidence"][0]["chunkId"] == "cr-1"
    assert body["roleImpacts"][0]["role"] == "Backend"
    assert body["confidence"] > 0


def test_prompt_excludes_removed_chunks():
    generate = _fake_generate()

    run_pipeline(
        pr=PRInput(title="t", diff="d"),
        repo_name="org/contextory",
        retrieve_fn=_fake_retrieve(CHUNKS),
        filter_fn=lambda chunks: filter_contexts(chunks, sim_threshold=0.5),
        generate_fn=generate,
        translate_fn=lambda title, body: "q",
    )

    prompt = generate.calls[0]
    assert "cr-1" in prompt
    assert "cr-2" not in prompt
