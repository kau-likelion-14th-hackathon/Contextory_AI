"""
Celery task(workers.tasks.run_analysis_job) 테스트 — 실패 원인 분류와 콜백 전송
(외부 호출·DB 접근 없음).

run_analysis_job은 이제 동기 함수(Celery worker는 별도 프로세스라 이벤트 루프가 없음)이므로
그냥 직접 호출하고, DB 접근/콜백 전송 함수는 monkeypatch로 대체한다.
"""

import pytest

import workers.tasks as tasks
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


@pytest.fixture
def captured(monkeypatch):
    """콜백 전송과 job_store 갱신을 가로채 실제 네트워크·DB를 쓰지 않게 한다."""
    sent = []
    job_updates = []

    def fake_send(url, payload):
        sent.append((url, payload))

    def fake_update(job_id, status, completed_at, error_message=None):
        job_updates.append((job_id, status, completed_at, error_message))

    monkeypatch.setattr(tasks, "send_analysis_callback", fake_send)
    monkeypatch.setattr(tasks, "update_job_status_sync", fake_update)
    return {"sent": sent, "job_updates": job_updates}


def _run_job(job_id: str = "rag-job-test") -> None:
    tasks.run_analysis_job(REQUEST, job_id)


def test_successful_job_sends_completed_callback(monkeypatch, captured):
    monkeypatch.setattr(
        tasks, "analyze_pr_for_callback",
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
        (RetrievalError("vector db down"), tasks.FAILURE_CODE_RETRIEVAL),
        (LLMResponseParseError("broken json"), tasks.FAILURE_CODE_LLM_RESPONSE),
        (RuntimeError("무언가 터짐"), tasks.FAILURE_CODE_UNEXPECTED),
    ],
)
def test_failures_are_classified_in_callback(monkeypatch, captured, error, expected_code):
    def failing(request):
        raise error

    monkeypatch.setattr(tasks, "analyze_pr_for_callback", failing)

    _run_job()

    _, payload = captured["sent"][0]
    assert payload.status == "FAILED"
    assert payload.result is None
    assert payload.error_message.startswith(f"[{expected_code}]")
    assert captured["job_updates"][0][1] == "FAILED"


def test_callback_error_message_is_length_limited(monkeypatch, captured):
    def failing(request):
        raise RuntimeError("x" * 1000)

    monkeypatch.setattr(tasks, "analyze_pr_for_callback", failing)

    _run_job()

    _, payload = captured["sent"][0]
    assert len(payload.error_message) <= tasks.MAX_CALLBACK_ERROR_LENGTH


def test_job_status_failure_does_not_block_callback(monkeypatch, captured):
    """job_store 갱신이 실패해도 콜백은 전송된다 (독립 시도 정책)."""
    def failing_update(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tasks, "update_job_status_sync", failing_update)
    monkeypatch.setattr(
        tasks, "analyze_pr_for_callback",
        lambda request: AnalysisResultPayload(summary="완료"),
    )

    _run_job()

    assert captured["sent"][0][1].status == "COMPLETED"


def test_classify_failure_mapping():
    assert tasks._classify_failure(RetrievalError("x")) == tasks.FAILURE_CODE_RETRIEVAL
    assert tasks._classify_failure(LLMResponseParseError("x")) == tasks.FAILURE_CODE_LLM_RESPONSE
    assert tasks._classify_failure(ValueError("x")) == tasks.FAILURE_CODE_UNEXPECTED
