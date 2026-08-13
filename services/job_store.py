import threading
from typing import Dict, Optional

# 단일 프로세스 In-Memory 작업 상태 저장소.
# BackgroundTasks 기반 구현의 한계: 프로세스 재시작 또는 멀티 워커(uvicorn --workers > 1) 구성에서는
# 워커 간 상태가 공유되지 않아 GET 상태 조회가 유실될 수 있다. Celery 등 외부 큐 도입 전까지의 임시 저장소.
_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def create_job(job_id: str, analysis_id: int, started_at: str) -> None:
    with _lock:
        _jobs[job_id] = {
            "analysis_id": analysis_id,
            "status": "PROCESSING",
            "started_at": started_at,
            "completed_at": None,
        }


def update_job_status(job_id: str, status: str, completed_at: str) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status
            _jobs[job_id]["completed_at"] = completed_at


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
