"""
비동기 분석 작업 테스트 — 실패 원인 분류와 콜백 전송 (외부 호출·DB 접근 없음)

_run_analysis_job은 async 함수이고 job_store 갱신도 async(PostgreSQL)이므로,
develop의 테스트 관례대로 asyncio.run으로 실행하고 DB 접근 함수는 monkeypatch로 대체한다.
"""

import asyncio

import pytest

import routers.internal_analysis as internal
from models.schemas import AnalysisResultPayload, AsyncAnalysisRequest
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


def _make_request() -> AsyncAnalysisRequest:
    return AsyncAnalysisRequest(**REQUEST)


@pytest.fixture
def captured(monkeypatch):
    """콜백 전송과 job_store 갱신을 가로채 실제 네트워크·DB를 쓰지 않게 한다."""
    sent = []
    job_updates = []

    def fake_send(url, payload):
        sent.append((url, payload))

    async def fake_update(job_id, status, completed_at, error_message=None):
        job_updates.append((job_id, status, completed_at, error_message))

    monkeypatch.setattr(internal, "send_analysis_callback", fake_send)
    monkeypatch.setattr(internal, "update_job_status", fake_update)
    return {"sent": sent, "job_updates": job_updates}


def _run_job(job_id: str = "rag-job-test") -> None:
    asyncio.run(internal._run_analysis_job(_make_request(), job_id))


def test_successful_job_sends_completed_callback(monkeypatch, captured):
    monkeypatch.setattr(
        internal, "analyze_pr_for_callback",
        lambda request: AnalysisResultPayload(summary="완료", confidence=0.8),
    )

    _run_job()

    url, payload = captured["sent"][0]
    assert url == "https://example.com/callback"
    assert payload.status == "COMPLETED"
    assert payload.result.summary == "완료"
    assert payload.error_message is None
    # job_store에도 최종 상태가 기록된다
    assert captured["job_updates"][0][1] == "COMPLETED"


@pytest.mark.parametrize(
    "error, expected_code",
    [
        (RetrievalError("vector db down"), internal.FAILURE_CODE_RETRIEVAL),
        (LLMResponseParseError("broken json"), internal.FAILURE_CODE_LLM_RESPONSE),
        (RuntimeError("무언가 터짐"), internal.FAILURE_CODE_UNEXPECTED),
    ],
)
def test_failures_are_classified_in_callback(monkeypatch, captured, error, expected_code):
    def failing(request):
        raise error

    monkeypatch.setattr(internal, "analyze_pr_for_callback", failing)

    _run_job()

    _, payload = captured["sent"][0]
    assert payload.status == "FAILED"
    assert payload.result is None
    assert payload.error_message.startswith(f"[{expected_code}]")
    assert captured["job_updates"][0][1] == "FAILED"


def test_callback_error_message_is_length_limited(monkeypatch, captured):
    def failing(request):
        raise RuntimeError("x" * 1000)

    monkeypatch.setattr(internal, "analyze_pr_for_callback", failing)

    _run_job()

    _, payload = captured["sent"][0]
    assert len(payload.error_message) <= internal.MAX_CALLBACK_ERROR_LENGTH


def test_job_status_failure_does_not_block_callback(monkeypatch, captured):
    """job_store 갱신이 실패해도 콜백은 전송된다 (develop의 독립 시도 정책)."""
    async def failing_update(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(internal, "update_job_status", failing_update)
    monkeypatch.setattr(
        internal, "analyze_pr_for_callback",
        lambda request: AnalysisResultPayload(summary="완료"),
    )

    _run_job()

    assert captured["sent"][0][1].status == "COMPLETED"


def test_classify_failure_mapping():
    assert internal._classify_failure(RetrievalError("x")) == internal.FAILURE_CODE_RETRIEVAL
    assert internal._classify_failure(LLMResponseParseError("x")) == internal.FAILURE_CODE_LLM_RESPONSE
    assert internal._classify_failure(ValueError("x")) == internal.FAILURE_CODE_UNEXPECTED
