"""비동기 내부 API 테스트 — 실패 원인 분류와 콜백 전송 (외부 호출 없음)"""

import pytest

import routers.internal_analysis as internal
from models.schemas import AnalysisResultPayload
from services.analysis_service import LLMResponseParseError
from services.retrieval import RetrievalError

REQUEST = {
    "analysisId": 1,
    "projectId": 1,
    "repositoryId": 1,
    "repositoryFullName": "org/contextory",
    "pullRequest": {
        "githubPrId": 1, "prNumber": 18, "title": "JWT 로그인", "body": "본문",
        "headSha": "a1b2c3", "sourceBranch": "feature/login", "targetBranch": "develop",
        "files": [{"filePath": "AuthService.java", "changeType": "MODIFIED", "patch": "@@ +login()",
                   "additions": 10, "deletions": 1}],
    },
    "language": "ko",
    "callbackUrl": "https://example.com/callback",
}


def _make_request():
    from models.schemas import AsyncAnalysisRequest

    return AsyncAnalysisRequest(**REQUEST)


@pytest.fixture
def captured_callback(monkeypatch):
    sent = []
    monkeypatch.setattr(internal, "send_analysis_callback", lambda url, payload: sent.append((url, payload)))
    return sent


def test_successful_job_sends_completed_callback(monkeypatch, captured_callback):
    monkeypatch.setattr(
        internal, "analyze_pr_for_callback",
        lambda request: AnalysisResultPayload(summary="완료", confidence=0.8),
    )

    internal._run_analysis_job(_make_request(), "rag-job-test")

    url, payload = captured_callback[0]
    assert url == "https://example.com/callback"
    assert payload.status == "COMPLETED"
    assert payload.result.summary == "완료"
    assert payload.error_message is None


@pytest.mark.parametrize(
    "error, expected_code",
    [
        (RetrievalError("vector db down"), internal.FAILURE_CODE_RETRIEVAL),
        (LLMResponseParseError("broken json"), internal.FAILURE_CODE_LLM_RESPONSE),
        (RuntimeError("무언가 터짐"), internal.FAILURE_CODE_UNEXPECTED),
    ],
)
def test_failures_are_classified_in_callback(monkeypatch, captured_callback, error, expected_code):
    def failing(request):
        raise error

    monkeypatch.setattr(internal, "analyze_pr_for_callback", failing)

    internal._run_analysis_job(_make_request(), "rag-job-test")

    _, payload = captured_callback[0]
    assert payload.status == "FAILED"
    assert payload.result is None
    assert payload.error_message.startswith(f"[{expected_code}]")


def test_callback_error_message_is_length_limited(monkeypatch, captured_callback):
    def failing(request):
        raise RuntimeError("x" * 1000)

    monkeypatch.setattr(internal, "analyze_pr_for_callback", failing)

    internal._run_analysis_job(_make_request(), "rag-job-test")

    _, payload = captured_callback[0]
    assert len(payload.error_message) <= internal.MAX_CALLBACK_ERROR_LENGTH


def test_classify_failure_mapping():
    assert internal._classify_failure(RetrievalError("x")) == internal.FAILURE_CODE_RETRIEVAL
    assert internal._classify_failure(LLMResponseParseError("x")) == internal.FAILURE_CODE_LLM_RESPONSE
    assert internal._classify_failure(ValueError("x")) == internal.FAILURE_CODE_UNEXPECTED
