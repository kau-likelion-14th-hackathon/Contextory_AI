"""라우터 → analysis_service 연결 테스트 (외부 호출 없이 서비스 함수만 monkeypatch)"""

import pytest
from fastapi.testclient import TestClient

import routers.analysis as analysis_router
from core.config import settings
from main import app
from models.schemas import PRAnalysisResponse
from services.analysis_service import LLMResponseParseError
from services.retrieval import RetrievalError

client = TestClient(app)

# /api/v1 라우터는 core/security.verify_internal_api_key 로 내부 API 키를 요구한다.
API_KEY = "test-internal-api-key"
AUTH_HEADERS = {"X-Internal-Api-Key": API_KEY}


@pytest.fixture(autouse=True)
def _fixed_api_key(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", API_KEY)


REQUEST_BODY = {
    "pr_id": 101,
    "repo_name": "org/contextory",
    "title": "JWT 로그인 추가",
    "description": "Spring Security 필터 추가",
    "diff_content": "@@ -21,7 +21,18 @@\n+login()",
    "author": "dev",
}


def test_health_endpoint():
    """liveness 체크는 인증 없이 200 (DB도 보지 않는다)"""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analyze_pr_requires_internal_api_key():
    response = client.post("/api/v1/analyze/pr", json=REQUEST_BODY, headers={"X-Internal-Api-Key": "wrong-key"})

    assert response.status_code == 401


def test_analyze_pr_rejects_missing_api_key():
    response = client.post("/api/v1/analyze/pr", json=REQUEST_BODY)

    assert response.status_code == 401


def test_analyze_pr_returns_pipeline_result(monkeypatch):
    def fake_pipeline(request):
        return PRAnalysisResponse(
            pr_id=request.pr_id, summary="요약", risk_score=10,
            confidence=0.82, needs_confirmation=False, filter_ratio=0.4, purpose="목적",
        )

    monkeypatch.setattr(analysis_router, "analyze_pr_pipeline", fake_pipeline)

    response = client.post("/api/v1/analyze/pr", json=REQUEST_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "요약"
    assert body["confidence"] == 0.82
    assert body["purpose"] == "목적"


@pytest.mark.parametrize(
    "error, expected_status, expected_detail",
    [
        (RetrievalError("vector db down"), 502, "Vector DB 검색 실패"),
        (LLMResponseParseError("broken json"), 502, "LLM 응답 파싱 실패"),
        (RuntimeError("unexpected"), 500, "RAG Pipeline Error"),
    ],
)
def test_external_failures_are_distinguished(monkeypatch, error, expected_status, expected_detail):
    def failing_pipeline(request):
        raise error

    monkeypatch.setattr(analysis_router, "analyze_pr_pipeline", failing_pipeline)

    response = client.post("/api/v1/analyze/pr", json=REQUEST_BODY, headers=AUTH_HEADERS)

    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]
