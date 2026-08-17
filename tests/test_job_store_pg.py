"""
services/job_store.py PostgreSQL 영속화 테스트.

실제 PostgreSQL에 연결해 ai_analysis_jobs 테이블 CRUD와 상태 전환 규칙을 검증한다.
(DB에 접속할 수 없는 환경에서는 모듈 전체를 skip 한다.)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from core.config import settings
from core.db import SessionLocal, engine
from services.job_store import (
    AIAnalysisJob,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    TIMEOUT_ERROR_MESSAGE,
    create_job,
    get_job,
    init_job_store_table,
    purge_old_jobs,
    reap_stale_jobs,
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
    not _db_available(), reason="PostgreSQL에 접속할 수 없어 job_store 영속화 테스트를 건너뜁니다."
)


@pytest.fixture(scope="module", autouse=True)
def _setup_table():
    init_job_store_table()


def _iso_minutes_ago(minutes: int) -> str:
    """지금으로부터 N분 전 시각을 저장소 포맷 문자열로 반환한다.

    PROCESSING 상태를 검증하는 테스트는 고정 시각을 쓰면 시간이 지날수록
    JOB_TIMEOUT_MINUTES를 넘겨 좀비로 판정되므로, 항상 상대 시각을 사용한다.
    """
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def job_id():
    """테스트마다 고유 job_id를 발급하고, 종료 시 해당 행만 정리한다."""
    jid = f"test-job-{uuid4().hex[:12]}"
    yield jid
    with SessionLocal() as db:
        job = db.get(AIAnalysisJob, jid)
        if job:
            db.delete(job)
            db.commit()


def test_create_job_persists_processing_status(job_id):
    """create_job은 PROCESSING 상태로 DB에 저장되고 get_job으로 조회된다."""
    started_at = _iso_minutes_ago(1)
    asyncio.run(create_job(job_id, 101, started_at))

    job = asyncio.run(get_job(job_id))
    assert job == {
        "analysis_id": 101,
        "status": STATUS_PROCESSING,
        "started_at": started_at,
        "completed_at": None,
    }


def test_get_job_returns_none_for_unknown_id():
    """존재하지 않는 jobId는 None을 반환한다 (라우터의 404 계약 근거)."""
    assert asyncio.run(get_job(f"missing-{uuid4().hex[:8]}")) is None


def test_update_job_status_to_completed(job_id):
    """PROCESSING -> COMPLETED 전환이 반영되고 completed_at이 기록된다."""
    started_at, completed_at = _iso_minutes_ago(5), _iso_minutes_ago(1)
    asyncio.run(create_job(job_id, 202, started_at))
    asyncio.run(update_job_status(job_id, STATUS_COMPLETED, completed_at))

    job = asyncio.run(get_job(job_id))
    assert job["status"] == STATUS_COMPLETED
    assert job["completed_at"] == completed_at
    assert job["started_at"] == started_at


def test_update_job_status_to_failed_stores_error_message(job_id):
    """FAILED 전환 시 error_message가 DB에 저장된다 (응답 계약에는 노출하지 않음)."""
    asyncio.run(create_job(job_id, 303, _iso_minutes_ago(5)))
    asyncio.run(
        update_job_status(job_id, STATUS_FAILED, _iso_minutes_ago(1), "Vector DB 검색 실패")
    )

    job = asyncio.run(get_job(job_id))
    assert job["status"] == STATUS_FAILED

    with SessionLocal() as db:
        assert db.get(AIAnalysisJob, job_id).error_message == "Vector DB 검색 실패"


@pytest.mark.parametrize("terminal_status", [STATUS_COMPLETED, STATUS_FAILED])
def test_terminal_status_cannot_revert_to_processing(job_id, terminal_status):
    """COMPLETED/FAILED로 종료된 작업은 PROCESSING으로 되돌아가지 않는다."""
    completed_at = _iso_minutes_ago(2)
    asyncio.run(create_job(job_id, 404, _iso_minutes_ago(5)))
    asyncio.run(update_job_status(job_id, terminal_status, completed_at))

    asyncio.run(update_job_status(job_id, STATUS_PROCESSING, _iso_minutes_ago(1)))

    job = asyncio.run(get_job(job_id))
    assert job["status"] == terminal_status
    assert job["completed_at"] == completed_at


def test_update_unknown_job_is_noop():
    """존재하지 않는 jobId 갱신은 예외 없이 무시된다 (기존 In-Memory 동작과 동일)."""
    asyncio.run(update_job_status(f"missing-{uuid4().hex[:8]}", STATUS_COMPLETED, "2026-08-16T10:00:00Z"))


def test_job_survives_new_session_and_process_state(job_id):
    """영속성: 저장소 모듈 캐시가 아닌 DB에서 조회되는지 새 세션으로 직접 확인한다."""
    asyncio.run(create_job(job_id, 505, "2026-08-16T11:00:00Z"))
    asyncio.run(update_job_status(job_id, STATUS_COMPLETED, "2026-08-16T11:02:00Z"))

    with SessionLocal() as db:
        row = db.get(AIAnalysisJob, job_id)
        assert row is not None
        assert row.analysis_id == 505
        assert row.status == STATUS_COMPLETED
        assert row.created_at is not None
        assert row.updated_at is not None


def test_timeout_failed_job_can_still_be_overwritten_by_real_completion(job_id):
    """타임아웃으로 FAILED 처리된 작업이 뒤늦게 완료되면 COMPLETED가 최종 결과로 남는다."""
    asyncio.run(create_job(job_id, 606, "2026-08-16T10:00:00Z"))
    asyncio.run(update_job_status(job_id, STATUS_FAILED, "2026-08-16T10:30:00Z", TIMEOUT_ERROR_MESSAGE))

    asyncio.run(update_job_status(job_id, STATUS_COMPLETED, "2026-08-16T10:31:00Z"))

    job = asyncio.run(get_job(job_id))
    assert job["status"] == STATUS_COMPLETED
    assert job["completed_at"] == "2026-08-16T10:31:00Z"


# ==========================================
# 좀비 작업(reaper) / 보존 기간(retention)
# ==========================================

def test_reap_marks_timed_out_processing_job_as_failed(job_id):
    """타임아웃을 넘긴 PROCESSING 작업은 reap 시 FAILED로 정리된다."""
    asyncio.run(create_job(job_id, 707, _iso_minutes_ago(settings.JOB_TIMEOUT_MINUTES + 10)))

    reaped = asyncio.run(reap_stale_jobs())
    assert reaped >= 1

    job = asyncio.run(get_job(job_id))
    assert job["status"] == STATUS_FAILED
    assert job["completed_at"] is not None

    with SessionLocal() as db:
        assert db.get(AIAnalysisJob, job_id).error_message == TIMEOUT_ERROR_MESSAGE


def test_reap_leaves_recent_processing_job_untouched(job_id):
    """아직 타임아웃 전인 PROCESSING 작업은 reap 대상이 아니다."""
    asyncio.run(create_job(job_id, 808, _iso_minutes_ago(1)))

    asyncio.run(reap_stale_jobs())

    assert asyncio.run(get_job(job_id))["status"] == STATUS_PROCESSING


def test_reap_is_idempotent(job_id):
    """이미 정리된 작업은 재호출 시 중복 집계되지 않는다."""
    asyncio.run(create_job(job_id, 909, _iso_minutes_ago(settings.JOB_TIMEOUT_MINUTES + 10)))

    asyncio.run(reap_stale_jobs())
    completed_at_first = asyncio.run(get_job(job_id))["completed_at"]

    asyncio.run(reap_stale_jobs())
    job = asyncio.run(get_job(job_id))

    assert job["status"] == STATUS_FAILED
    assert job["completed_at"] == completed_at_first


def test_get_job_lazily_fails_timed_out_job(job_id):
    """정리 스케줄러가 돌기 전이라도 조회 시점에 좀비 작업이 FAILED로 확정된다."""
    asyncio.run(create_job(job_id, 1010, _iso_minutes_ago(settings.JOB_TIMEOUT_MINUTES + 5)))

    job = asyncio.run(get_job(job_id))
    assert job["status"] == STATUS_FAILED

    # 판정 결과가 응답에만 반영되는 것이 아니라 DB에도 확정 저장되어야 한다.
    with SessionLocal() as db:
        assert db.get(AIAnalysisJob, job_id).status == STATUS_FAILED


def test_timeout_detection_disabled_when_setting_is_zero(job_id, monkeypatch):
    """JOB_TIMEOUT_MINUTES=0이면 좀비 판정을 하지 않는다."""
    asyncio.run(create_job(job_id, 1111, _iso_minutes_ago(600)))
    monkeypatch.setattr(settings, "JOB_TIMEOUT_MINUTES", 0)

    assert asyncio.run(reap_stale_jobs()) == 0
    assert asyncio.run(get_job(job_id))["status"] == STATUS_PROCESSING


def test_purge_is_disabled_by_default(job_id, monkeypatch):
    """JOB_RETENTION_DAYS 기본값(0)에서는 어떤 행도 삭제하지 않는다."""
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 0)
    asyncio.run(create_job(job_id, 1212, "2020-01-01T00:00:00Z"))
    asyncio.run(update_job_status(job_id, STATUS_COMPLETED, "2020-01-01T00:01:00Z"))

    assert asyncio.run(purge_old_jobs()) == 0
    assert asyncio.run(get_job(job_id)) is not None


def test_purge_deletes_only_old_terminal_jobs(job_id, monkeypatch):
    """보존 기간이 지난 종료 작업만 삭제하고, 진행 중 작업은 남긴다."""
    monkeypatch.setattr(settings, "JOB_RETENTION_DAYS", 7)

    old_done = f"{job_id}-old"
    recent_done = f"{job_id}-recent"
    still_running = f"{job_id}-running"
    try:
        asyncio.run(create_job(old_done, 1, "2020-01-01T00:00:00Z"))
        asyncio.run(update_job_status(old_done, STATUS_COMPLETED, "2020-01-01T00:01:00Z"))

        asyncio.run(create_job(recent_done, 2, _iso_minutes_ago(10)))
        asyncio.run(update_job_status(recent_done, STATUS_COMPLETED, _iso_minutes_ago(5)))

        asyncio.run(create_job(still_running, 3, _iso_minutes_ago(1)))

        purged = asyncio.run(purge_old_jobs())

        assert purged == 1
        assert asyncio.run(get_job(old_done)) is None
        assert asyncio.run(get_job(recent_done)) is not None
        assert asyncio.run(get_job(still_running)) is not None
    finally:
        with SessionLocal() as db:
            for jid in (old_done, recent_done, still_running):
                row = db.get(AIAnalysisJob, jid)
                if row:
                    db.delete(row)
            db.commit()
