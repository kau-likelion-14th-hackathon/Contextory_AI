"""
비동기 분석 작업(Job) 상태 저장소.

기존에는 프로세스 단일 In-Memory 딕셔너리(threading.Lock 보호)를 사용했으나,
Uvicorn 멀티 워커(--workers > 1) 환경에서는 워커 간 메모리가 공유되지 않아
작업을 생성한 워커와 다른 워커가 GET 요청을 받으면 404가 발생하고,
서버 재기동 시 진행/완료된 작업 메타데이터가 모두 유실되는 문제가 있었다.

이를 해결하기 위해 이미 연결되어 있는 PostgreSQL(core.db)에 `ai_analysis_jobs`
테이블로 영속화한다. 저장소 전체가 동기(SQLAlchemy `Session` + psycopg2) 커넥션
풀만 사용하고 있어(비동기 드라이버/엔진 미도입) 새 DB 드라이버를 추가하지 않기
위해, 기존 `core.db.SessionLocal`(동기 세션)을 그대로 재사용하되 실제 쿼리는
스레드풀에서 실행해 이벤트 루프를 막지 않는 `async def` 인터페이스로 감쌌다.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Index, String, Text, func
from starlette.concurrency import run_in_threadpool

from core.config import settings
from core.db import Base, SessionLocal, engine

# 기존 코드(callback_service.now_iso, routers/internal_analysis.py)에서 쓰던 상태 문자열을 그대로 유지한다.
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
_TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED}

# 타임아웃으로 정리된 좀비 작업에 기록할 사유. Spring Boot 응답에는 노출되지 않고 DB에만 남는다.
TIMEOUT_ERROR_MESSAGE = "분석 작업이 제한 시간 내에 완료되지 않아 타임아웃 처리되었습니다."

# callback_service.now_iso()와 동일한 포맷("2026-08-02T11:30:05Z").
# started_at/completed_at은 Spring Boot와의 응답 계약상 항상 이 포맷의 문자열이어야 한다.
_DT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class AIAnalysisJob(Base):
    """비동기 PR 분석 작업 상태 영속화 테이블."""

    __tablename__ = "ai_analysis_jobs"

    job_id = Column(String(64), primary_key=True)
    analysis_id = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # Spring Boot가 analysis_id로 역조회할 때 사용.
        Index("ix_ai_analysis_jobs_analysis_id", "analysis_id"),
        # 좀비 작업 정리(status=PROCESSING AND started_at < cutoff) 스캔용.
        Index("ix_ai_analysis_jobs_status_started_at", "status", "started_at"),
    )


def init_job_store_table() -> None:
    """앱 기동 시 1회 호출: ai_analysis_jobs 테이블과 인덱스가 없으면 생성한다.

    저장소에 Alembic 등 마이그레이션 도구가 없어(scripts/index_to_pg.py의
    init_db()와 동일한 패턴으로) SQLAlchemy metadata.create_all을 사용한다.
    tables=[...]로 범위를 이 테이블 하나로 한정해 다른 pgvector 테이블에는
    영향을 주지 않는다.

    create_all은 테이블이 이미 있으면 인덱스 생성까지 통째로 건너뛴다. 인덱스가
    추가되기 전에 만들어진 기존 배포본에도 인덱스가 적용되도록 별도로 checkfirst
    생성을 한 번 더 수행한다.
    """
    Base.metadata.create_all(bind=engine, tables=[AIAnalysisJob.__table__])
    with engine.connect() as conn:
        for index in AIAnalysisJob.__table__.indexes:
            index.create(bind=conn, checkfirst=True)
        conn.commit()


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, _DT_FORMAT).replace(tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """DB에서 읽은 시각을 UTC aware datetime으로 정규화한다 (naive면 UTC로 간주)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).strftime(_DT_FORMAT)


def _job_to_dict(job: AIAnalysisJob) -> dict:
    # 기존 In-Memory 구현의 반환 구조(analysis_id/status/started_at/completed_at)를 그대로 유지한다.
    return {
        "analysis_id": job.analysis_id,
        "status": job.status,
        "started_at": _format_iso(job.started_at),
        "completed_at": _format_iso(job.completed_at),
    }


def _create_job_sync(job_id: str, analysis_id: int, started_at: str) -> None:
    with SessionLocal() as db:
        db.merge(
            AIAnalysisJob(
                job_id=job_id,
                analysis_id=analysis_id,
                status=STATUS_PROCESSING,
                started_at=_parse_iso(started_at),
                completed_at=None,
                error_message=None,
            )
        )
        db.commit()


def _update_job_status_sync(
    job_id: str, status: str, completed_at: str, error_message: Optional[str]
) -> None:
    with SessionLocal() as db:
        # 좀비 정리(reaper)와 백그라운드 태스크의 정상 완료가 동시에 같은 행을 갱신할 수 있으므로
        # 행 잠금으로 read-modify-write 구간을 직렬화한다.
        job = db.get(AIAnalysisJob, job_id, with_for_update=True)
        if job is None:
            return
        if job.status in _TERMINAL_STATUSES and status not in _TERMINAL_STATUSES:
            # COMPLETED/FAILED로 이미 종료된 작업은 PROCESSING 등으로 되돌리지 않는다.
            # 단 종료 상태 간 전환은 허용한다 — 타임아웃으로 FAILED 처리된 작업이
            # 뒤늦게 실제 완료되면 COMPLETED가 최종 결과로 남아야 한다.
            return
        job.status = status
        job.completed_at = _parse_iso(completed_at) if completed_at else None
        job.error_message = error_message
        db.commit()


def _timeout_cutoff() -> Optional[datetime]:
    """PROCESSING 작업을 좀비로 판정할 기준 시각. 설정이 0 이하면 판정하지 않는다."""
    if settings.JOB_TIMEOUT_MINUTES <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(minutes=settings.JOB_TIMEOUT_MINUTES)


def _get_job_sync(job_id: str) -> Optional[dict]:
    with SessionLocal() as db:
        job = db.get(AIAnalysisJob, job_id)
        if job is None:
            return None

        # Lazy 판정: 서버가 분석 도중 강제 종료되면 해당 행이 영원히 PROCESSING으로 남는다.
        # 정리 스케줄러가 아직 돌지 않았더라도 조회 시점에 즉시 FAILED로 확정해 반환한다.
        cutoff = _timeout_cutoff()
        if cutoff is not None and job.status == STATUS_PROCESSING and _as_utc(job.started_at) < cutoff:
            job = db.get(AIAnalysisJob, job_id, with_for_update=True)
            if job.status == STATUS_PROCESSING:  # 잠금 대기 중 정상 완료됐을 수 있으므로 재확인
                job.status = STATUS_FAILED
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = TIMEOUT_ERROR_MESSAGE
                db.commit()

        return _job_to_dict(job)


def _reap_stale_jobs_sync() -> int:
    """타임아웃을 넘긴 PROCESSING 작업을 일괄 FAILED 처리하고 정리된 건수를 반환한다."""
    cutoff = _timeout_cutoff()
    if cutoff is None:
        return 0

    with SessionLocal() as db:
        # 단일 UPDATE ... WHERE 로 처리해 멀티 워커/스케줄러가 동시에 호출해도
        # 행 단위로 직렬화되며, 이미 정리된 행은 조건에서 빠져 중복 처리되지 않는다.
        result = db.query(AIAnalysisJob).filter(
            AIAnalysisJob.status == STATUS_PROCESSING,
            AIAnalysisJob.started_at < cutoff,
        ).update(
            {
                AIAnalysisJob.status: STATUS_FAILED,
                AIAnalysisJob.completed_at: datetime.now(timezone.utc),
                AIAnalysisJob.error_message: TIMEOUT_ERROR_MESSAGE,
                AIAnalysisJob.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
        db.commit()
        return result


def _purge_old_jobs_sync() -> int:
    """보존 기간이 지난 종료 작업 행을 삭제하고 삭제 건수를 반환한다.

    JOB_RETENTION_DAYS 기본값은 0(비활성)이다. 데이터 삭제는 되돌릴 수 없으므로
    운영에서 명시적으로 값을 설정한 경우에만 동작한다.
    """
    if settings.JOB_RETENTION_DAYS <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.JOB_RETENTION_DAYS)
    with SessionLocal() as db:
        result = db.query(AIAnalysisJob).filter(
            AIAnalysisJob.status.in_(_TERMINAL_STATUSES),
            AIAnalysisJob.completed_at.isnot(None),
            AIAnalysisJob.completed_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()
        return result


async def create_job(job_id: str, analysis_id: int, started_at: str) -> None:
    await run_in_threadpool(_create_job_sync, job_id, analysis_id, started_at)


async def update_job_status(
    job_id: str,
    status: str,
    completed_at: str,
    error_message: Optional[str] = None,
) -> None:
    await run_in_threadpool(_update_job_status_sync, job_id, status, completed_at, error_message)


async def get_job(job_id: str) -> Optional[dict]:
    return await run_in_threadpool(_get_job_sync, job_id)


async def reap_stale_jobs() -> int:
    """타임아웃을 넘긴 PROCESSING 작업을 FAILED로 정리하고 건수를 반환한다."""
    return await run_in_threadpool(_reap_stale_jobs_sync)


async def purge_old_jobs() -> int:
    """보존 기간이 지난 종료 작업 행을 삭제하고 건수를 반환한다 (기본 비활성)."""
    return await run_in_threadpool(_purge_old_jobs_sync)
