from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Task, Worker

HEARTBEAT_INTERVAL_SECONDS = 60
OFFLINE_AFTER_SECONDS = 120
ACTIVE_LEASE_MINUTES = 5


class WorkerServiceError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_worker(db: Session, *, worker_id: str, name: str, session_id: str,
                    capabilities: dict, version: str | None) -> Worker:
    now = _now()
    worker = db.get(Worker, worker_id)
    if worker is None:
        worker = Worker(id=worker_id, registered_at=now)
        db.add(worker)
    worker.name = name
    worker.session_id = session_id
    worker.capabilities = capabilities
    worker.version = version
    worker.state = "idle"
    worker.current_task_id = None
    worker.session_started_at = now
    worker.last_heartbeat_at = now
    db.commit()
    db.refresh(worker)
    return worker


def heartbeat(db: Session, *, worker_id: str, session_id: str, state: str,
              current_task_id: str | None, claim_token: str | None,
              progress_stage: str | None, progress_message: str | None,
              progress_current: int | None, progress_total: int | None) -> Worker:
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise WorkerServiceError(f"worker {worker_id} is not registered")
    if worker.session_id != session_id:
        raise WorkerServiceError(
            f"worker {worker_id} session was replaced by a newer process")
    if state == "busy" and current_task_id is None:
        raise WorkerServiceError("a busy worker must report its current task")
    if state == "idle" and current_task_id is not None:
        raise WorkerServiceError("an idle worker cannot report a current task")
    if (progress_current is not None and progress_total is not None
            and progress_current > progress_total):
        raise WorkerServiceError("progress_current cannot exceed progress_total")

    now = _now()
    worker.state = state
    worker.current_task_id = current_task_id
    worker.last_heartbeat_at = now

    if current_task_id is not None:
        task = db.get(Task, current_task_id)
        if task is None or task.status != "claimed":
            raise WorkerServiceError(f"task {current_task_id} is not actively claimed")
        if task.claimed_by != worker_id or task.claim_token != claim_token:
            raise WorkerServiceError("heartbeat does not own the current task claim")
        task.progress_stage = progress_stage
        task.progress_message = progress_message
        task.progress_current = progress_current
        task.progress_total = progress_total
        task.progress_updated_at = now
        # Once a modern worker starts heartbeating, replace the producer's
        # generous initial lease with a short rolling failure-detection lease.
        task.lease_expires_at = now + timedelta(minutes=ACTIVE_LEASE_MINUTES)

    db.commit()
    db.refresh(worker)
    return worker


def list_workers(db: Session) -> list[Worker]:
    return db.query(Worker).order_by(Worker.last_heartbeat_at.desc()).all()


def is_online(worker: Worker, now: datetime | None = None) -> bool:
    now = now or _now()
    last_seen = worker.last_heartbeat_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return now - last_seen <= timedelta(seconds=OFFLINE_AFTER_SECONDS)
