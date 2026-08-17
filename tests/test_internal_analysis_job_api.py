"""
GET /internal/v1/analyses/{jobId} 응답 계약 테스트.

job_store가 PostgreSQL 기반으로 바뀐 뒤에도 Spring Boot가 파싱하는
필드명(camelCase)/상태 문자열/404 계약이 그대로 유지되는지 확인한다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.config import settings
from core.db import SessionLocal, engine
from routers import internal_analysis
from services.job_store import (
    AIAnalysisJob,
    STATUS_COMPLETED,
    STATUS_PROCESSING,
    create_job,
    init_job_store_table,
    update_job_status,
)


def _db_available() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL에 접속할 수 없어 작업 상태 조회 API 테스트를 건너뜁니다."
)

API_KEY = "test-internal-api-key"


@pytest.fixture(scope="module")
def client():
    init_job_store_table()
    app = FastAPI()
    app.include_router(internal_analysis.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fixed_api_key(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", API_KEY)


@pytest.fixture
def job_id():
    jid = f"test-api-{uuid4().hex[:12]}"
    yield jid
    with SessionLocal() as db:
        job = db.get(AIAnalysisJob, jid)
        if job:
            db.delete(job)
            db.commit()


def _get(client, job_id, api_key=API_KEY):
    return client.get(f"/internal/v1/analyses/{job_id}", headers={"X-Internal-Api-Key": api_key})


def _iso_minutes_ago(minutes: int) -> str:
    """PROCESSING 상태 검증은 고정 시각을 쓰면 타임아웃에 걸리므로 상대 시각을 사용한다."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_get_status_returns_processing_job(client, job_id):
    started_at = _iso_minutes_ago(1)
    asyncio.run(create_job(job_id, 1, started_at))

    response = _get(client, job_id)

    assert response.status_code == 200
    assert response.json() == {
        "jobId": job_id,
        "analysisId": 1,
        "status": STATUS_PROCESSING,
        "startedAt": started_at,
        "completedAt": None,
    }


def test_get_status_returns_completed_job(client, job_id):
    completed_at = _iso_minutes_ago(1)
    asyncio.run(create_job(job_id, 7, _iso_minutes_ago(5)))
    asyncio.run(update_job_status(job_id, STATUS_COMPLETED, completed_at))

    body = _get(client, job_id).json()

    assert body["status"] == STATUS_COMPLETED
    assert body["completedAt"] == completed_at


def test_get_status_unknown_job_returns_404(client):
    """존재하지 않는 jobId는 기존과 동일하게 404를 반환한다."""
    response = _get(client, f"missing-{uuid4().hex[:8]}")

    assert response.status_code == 404
    assert response.json()["detail"] == "해당 jobId의 분석 작업을 찾을 수 없습니다."


def test_get_status_requires_internal_api_key(client, job_id):
    response = _get(client, job_id, api_key="wrong-key")

    assert response.status_code == 401


def test_get_status_reports_timed_out_job_as_failed(client, job_id):
    """서버 강제 종료로 PROCESSING에 멈춘 작업은 조회 시 FAILED로 확정 반환된다."""
    stale = (
        datetime.now(timezone.utc) - timedelta(minutes=settings.JOB_TIMEOUT_MINUTES + 5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    asyncio.run(create_job(job_id, 42, stale))

    body = _get(client, job_id).json()

    assert body["status"] == "FAILED"
    assert body["completedAt"] is not None


def test_maintenance_endpoint_reaps_stale_jobs(client, job_id):
    """정리 엔드포인트가 좀비 작업을 FAILED로 정리하고 건수를 camelCase로 반환한다."""
    stale = (
        datetime.now(timezone.utc) - timedelta(minutes=settings.JOB_TIMEOUT_MINUTES + 5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    asyncio.run(create_job(job_id, 43, stale))

    response = client.post(
        "/internal/v1/analyses/maintenance", headers={"X-Internal-Api-Key": API_KEY}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reapedCount"] >= 1
    assert body["purgedCount"] == 0
    assert body["timeoutMinutes"] == settings.JOB_TIMEOUT_MINUTES

    assert _get(client, job_id).json()["status"] == "FAILED"


def test_maintenance_endpoint_requires_internal_api_key(client):
    response = client.post(
        "/internal/v1/analyses/maintenance", headers={"X-Internal-Api-Key": "wrong-key"}
    )

    assert response.status_code == 401
