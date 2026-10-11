"""task_service.py -- the queue's core: submit/claim/complete/fail.

Deliberately opaque to task_type/payload/result -- this module has no idea
what any given task means, only how to move it through
queued -> claimed -> done|failed safely under concurrent workers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Task


class TaskServiceError(Exception):
    """Raised for invalid state transitions or a stale claim_token."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit_task(db: Session, task_type: str, payload: dict,
                lease_minutes: int | None = None,
                retention_hours: int | None = None) -> Task:
    resolved_lease_minutes = (
        lease_minutes if lease_minutes is not None else settings.default_lease_minutes
    )
    resolved_retention_hours = (
        retention_hours if retention_hours is not None else settings.default_retention_hours
    )
    if resolved_lease_minutes <= 0:
        raise TaskServiceError("lease_minutes must be greater than zero")
    if resolved_retention_hours <= 0:
        raise TaskServiceError("retention_hours must be greater than zero")

    task = Task(
        id=str(uuid.uuid4()),
        task_type=task_type,
        payload=payload,
        lease_minutes=resolved_lease_minutes,
        status="queued",
        expires_at=_now() + timedelta(hours=resolved_retention_hours),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: str) -> Task:
    try:
        uuid.UUID(task_id)
    except ValueError:
        # Not a validly-shaped id at all -- same outcome as not found, but
        # without letting a malformed id reach Postgres as a raw ::UUID
        # cast, which raises a DataError instead of just finding no rows.
        raise TaskServiceError(f"task {task_id} not found") from None
    task = db.get(Task, task_id)
    if task is None:
        raise TaskServiceError(f"task {task_id} not found")
    return task


def list_tasks(db: Session, status: str | None = None, task_type: str | None = None) -> list[Task]:
    q = db.query(Task)
    if status:
        q = q.filter(Task.status == status)
    if task_type:
        q = q.filter(Task.task_type == task_type)
    return q.order_by(Task.created_at.desc()).limit(200).all()


# Found live in the Colab pipeline this queue replaces: a worker can die
# mid-task (OOM, interrupted, crashed) and leave a task claimed forever with
# nobody ever finishing it. Reclaim runs as a lazy sweep right before every
# claim attempt -- no background scheduler needed, matches the same
# "nobody cares until someone else actually wants the work" reasoning as
# image_service.py's existing is_stalled() check.
_RECLAIM_SQL = text("""
    UPDATE tasks
    SET status = 'queued', claimed_by = NULL, claim_token = NULL,
        claimed_at = NULL, lease_expires_at = NULL,
        progress_stage = NULL, progress_message = NULL,
        progress_current = NULL, progress_total = NULL,
        progress_updated_at = NULL
    WHERE status = 'claimed'
      AND task_type = ANY(:task_types)
      AND lease_expires_at < :now
""")

# FOR UPDATE SKIP LOCKED is the standard Postgres job-queue idiom -- two
# workers polling at the same instant simply cannot claim the same row,
# by construction, no application-level locking needed.
_CLAIM_SQL = text("""
    WITH candidate AS (
        SELECT id, lease_minutes FROM tasks
        WHERE status = 'queued' AND task_type = ANY(:task_types)
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE tasks AS task
    SET status = 'claimed', claimed_by = :worker_id, claim_token = :claim_token,
        claimed_at = :now,
        progress_stage = 'claimed', progress_message = 'Worker claimed task',
        progress_current = NULL, progress_total = NULL,
        progress_updated_at = :now,
        lease_expires_at = :now + make_interval(
            mins => COALESCE(CAST(:lease_minutes AS integer), candidate.lease_minutes)
        )
    FROM candidate
    WHERE task.id = candidate.id
    RETURNING task.id, task.task_type, task.payload, task.lease_minutes,
              task.status, task.result, task.error, task.claimed_by,
              task.claim_token, task.created_at, task.claimed_at,
              task.lease_expires_at, task.completed_at,
              task.progress_stage, task.progress_message,
              task.progress_current, task.progress_total,
              task.progress_updated_at, task.expires_at
""")


def claim_next(db: Session, task_types: list[str], worker_id: str,
               lease_minutes: int | None = None) -> dict | None:
    """Returns a dict (not an ORM Task -- this is a raw claimed snapshot,
    including claim_token which the worker must present to complete/fail)
    or None if nothing queued matches task_types."""
    if lease_minutes is not None and lease_minutes <= 0:
        raise TaskServiceError("lease_minutes must be greater than zero")

    now = _now()
    db.execute(_RECLAIM_SQL, {"task_types": task_types, "now": now})

    claim_token = str(uuid.uuid4())
    row = db.execute(_CLAIM_SQL, {
        "worker_id": worker_id,
        "claim_token": claim_token,
        "now": now,
        "lease_minutes": lease_minutes,
        "task_types": task_types,
    }).mappings().first()
    db.commit()
    if row is None:
        return None
    # Raw SQL (unlike ORM access) doesn't go through Task.id's as_uuid=False
    # coercion -- psycopg hands back a real uuid.UUID here. Normalize to str
    # so every caller (JSON serialization, string comparisons against
    # ORM-loaded Task.id) sees the same type regardless of which path
    # produced the id.
    result = dict(row)
    result["id"] = str(result["id"])
    return result


def complete_task(db: Session, task_id: str, claim_token: str, result: dict) -> Task:
    task = get_task(db, task_id)
    if task.status != "claimed":
        raise TaskServiceError(f"task {task_id} is not claimed (status={task.status})")
    if task.claim_token != claim_token:
        raise TaskServiceError(f"task {task_id} claim_token mismatch -- stale claim, "
                               "this task was reclaimed by someone else")
    task.status = "done"
    task.result = result
    task.completed_at = _now()
    task.progress_stage = "complete"
    task.progress_message = "Task completed"
    task.progress_updated_at = task.completed_at
    db.commit()
    db.refresh(task)
    return task


def fail_task(db: Session, task_id: str, claim_token: str, error: str) -> Task:
    task = get_task(db, task_id)
    if task.status != "claimed":
        raise TaskServiceError(f"task {task_id} is not claimed (status={task.status})")
    if task.claim_token != claim_token:
        raise TaskServiceError(f"task {task_id} claim_token mismatch -- stale claim, "
                               "this task was reclaimed by someone else")
    task.status = "failed"
    task.error = error
    task.completed_at = _now()
    task.progress_stage = "failed"
    task.progress_message = error[:500]
    task.progress_updated_at = task.completed_at
    db.commit()
    db.refresh(task)
    return task
