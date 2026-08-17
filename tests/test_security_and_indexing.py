"""내부 API 인증(core/security) 및 인덱싱 라우터 테스트 (OpenAI·DB 호출 없음)"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import llamaindex.pipeline as pipeline_module
import routers.indexing as indexing_router
from core.config import settings
from core.security import verify_internal_api_key
from main import app

client = TestClient(app)
API_KEY = "test-internal-api-key"
AUTH_HEADERS = {"X-Internal-Api-Key": API_KEY}

INDEX_BODY = {
    "repo_name": "org/contextory",
    "branch": "main",
    "commit_sha": "abc123",
    "files": [{"file_path": "src/auth/AuthService.java", "chunk_idx": 0, "content": "class AuthService {}"}],
    "deleted_files": ["src/auth/OldService.java"],
}


@pytest.fixture(autouse=True)
def _fixed_api_key(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", API_KEY)


# ==========================================
# core/security
# ==========================================

def test_verify_accepts_matching_key():
    assert verify_internal_api_key(API_KEY) is None


@pytest.mark.parametrize("supplied", [None, "", "wrong-key", API_KEY + "x"])
def test_verify_rejects_wrong_key(supplied):
    with pytest.raises(HTTPException) as e:
        verify_internal_api_key(supplied)

    assert e.value.status_code == 401


def test_verify_rejects_everything_when_server_key_is_unset(monkeypatch):
    """서버에 키가 설정되지 않았으면 어떤 요청도 통과시키지 않는다(빈 키 우회 방지)."""
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "")

    with pytest.raises(HTTPException):
        verify_internal_api_key("")
    with pytest.raises(HTTPException):
        verify_internal_api_key("anything")


# ==========================================
# routers/indexing
# ==========================================

def test_index_endpoint_requires_api_key():
    assert client.post("/api/v1/repos/index", json=INDEX_BODY).status_code == 401


def test_index_endpoint_returns_counts(monkeypatch):
    captured = {}

    def fake_index(repo_name, files, deleted_files):
        captured.update({"repo_name": repo_name, "files": files, "deleted_files": deleted_files})
        return len(files), len(deleted_files or [])

    monkeypatch.setattr(indexing_router, "index_repository_files", fake_index)

    response = client.post("/api/v1/repos/index", json=INDEX_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["indexed_files_count"] == 1
    assert body["deleted_files_count"] == 1
    assert captured["repo_name"] == "org/contextory"
    assert captured["files"][0].file_path == "src/auth/AuthService.java"


def test_index_endpoint_reports_failure(monkeypatch):
    def failing_index(**kwargs):
        raise RuntimeError("pgvector down")

    monkeypatch.setattr(indexing_router, "index_repository_files", failing_index)

    response = client.post("/api/v1/repos/index", json=INDEX_BODY, headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert "pgvector down" in response.json()["detail"]


# ==========================================
# routers/health (readiness)
# ==========================================

def test_readiness_health_requires_api_key():
    assert client.get("/api/v1/health").status_code == 401


def test_readiness_health_reports_database_state():
    response = client.get("/api/v1/health", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # DB가 없는 환경에서도 200을 주되 상태 문자열로 구분한다.
    assert body["database"] == "connected" or body["database"].startswith("disconnected")


# ==========================================
# llamaindex/pipeline — 임베딩 모델 설정 단일 지점
# ==========================================

def test_embed_model_follows_settings(monkeypatch):
    """모델명을 하드코딩하지 않고 settings.EMBEDDING_MODEL을 따른다."""
    captured = {}

    class FakeEmbedding:
        def __init__(self, model, api_key):
            captured.update({"model": model, "api_key": api_key})

    monkeypatch.setattr(pipeline_module, "OpenAIEmbedding", FakeEmbedding)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    pipeline_module.get_embed_model()

    assert captured == {"model": "text-embedding-3-large", "api_key": "sk-test"}
