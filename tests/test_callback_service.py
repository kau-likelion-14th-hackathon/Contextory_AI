"""콜백 전송 테스트 — 재시도/백오프/실패 전파 (실제 네트워크 호출 없음)"""

import pytest

import services.callback_service as callback_service
from core.config import settings
from models.schemas import AnalysisCallbackPayload, AnalysisResultPayload

CALLBACK_URL = "https://backend.example.com/api/v1/internal/ai/analyses/1/callback"


def _payload(status: str = "COMPLETED") -> AnalysisCallbackPayload:
    return AnalysisCallbackPayload(
        job_id="rag-job-test",
        status=status,
        model_name="gpt-4o",
        result=AnalysisResultPayload(summary="요약") if status == "COMPLETED" else None,
        error_message=None if status == "COMPLETED" else "[ANALYSIS_FAILED] 실패",
        completed_at="2026-08-17T00:00:00Z",
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """지수 백오프 대기(1s, 2s)를 실제로 기다리지 않도록 대체하고 호출 간격을 기록한다."""
    waits = []
    monkeypatch.setattr(callback_service.time, "sleep", lambda seconds: waits.append(seconds))
    return waits


def test_callback_sends_payload_with_internal_api_key(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(200)

    monkeypatch.setattr(callback_service.httpx, "post", fake_post)
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "test-key")

    callback_service.send_analysis_callback(CALLBACK_URL, _payload())

    assert len(calls) == 1
    assert calls[0]["url"] == CALLBACK_URL
    assert calls[0]["headers"]["X-Internal-Api-Key"] == "test-key"
    assert calls[0]["timeout"] == callback_service.CALLBACK_TIMEOUT_SECONDS


def test_callback_body_is_camel_case():
    """Backend 계약은 camelCase이므로 by_alias 직렬화가 유지되어야 한다."""
    body = _payload().model_dump(by_alias=True)

    assert "jobId" in body and "completedAt" in body and "modelName" in body
    assert "job_id" not in body


def test_callback_retries_then_succeeds(monkeypatch, _no_sleep):
    attempts = {"count": 0}

    def flaky_post(url, json=None, headers=None, timeout=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("connection reset")
        return _FakeResponse(200)

    monkeypatch.setattr(callback_service.httpx, "post", flaky_post)

    callback_service.send_analysis_callback(CALLBACK_URL, _payload())

    assert attempts["count"] == 3
    assert _no_sleep == [1, 2]          # 지수 백오프 1s, 2s


def test_callback_raises_after_max_attempts(monkeypatch, _no_sleep):
    """모두 실패하면 예외를 삼키지 않고 전파한다(호출부가 로그로 남길 수 있게)."""
    attempts = {"count": 0}

    def always_fail(url, json=None, headers=None, timeout=None):
        attempts["count"] += 1
        raise RuntimeError("connection refused")

    monkeypatch.setattr(callback_service.httpx, "post", always_fail)

    with pytest.raises(RuntimeError, match="connection refused"):
        callback_service.send_analysis_callback(CALLBACK_URL, _payload())

    assert attempts["count"] == callback_service.CALLBACK_MAX_ATTEMPTS
    assert len(_no_sleep) == callback_service.CALLBACK_MAX_ATTEMPTS - 1


def test_http_error_status_is_retried(monkeypatch, _no_sleep):
    monkeypatch.setattr(callback_service.httpx, "post", lambda *a, **k: _FakeResponse(500))

    with pytest.raises(RuntimeError):
        callback_service.send_analysis_callback(CALLBACK_URL, _payload("FAILED"))

    assert len(_no_sleep) == callback_service.CALLBACK_MAX_ATTEMPTS - 1


def test_now_iso_format():
    stamp = callback_service.now_iso()

    assert stamp.endswith("Z") and len(stamp) == 20 and "T" in stamp
