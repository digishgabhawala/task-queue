import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    """A generic unit of work. task_type/payload/result are deliberately
    opaque to this table -- the queue doesn't know or care what they mean,
    only the producer and worker that agree on a given task_type's shape
    need to. See TESTING.md / README.md for the claim/lease/retry model."""
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_type = Column(String, nullable=False, index=True)
    payload = Column(JSONB, nullable=False)
    # Producer-selected claim duration. Workers normally honor this value;
    # claim_next() retains an explicit override for worker-specific cases.
    lease_minutes = Column(Integer, nullable=False)

    # queued -> claimed -> done | failed
    status = Column(String, nullable=False, default="queued", index=True)

    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)

    claimed_by = Column(String, nullable=True)
    claim_token = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    progress_stage = Column(String, nullable=True)
    progress_message = Column(Text, nullable=True)
    progress_current = Column(Integer, nullable=True)
    progress_total = Column(Integer, nullable=True)
    progress_updated_at = Column(DateTime(timezone=True), nullable=True)
    # Row is eligible for deletion by the pg_cron cleanup job once past this
    # (see supabase/migrations) -- set at submission time so even a task
    # that's never claimed still gets swept eventually.
    expires_at = Column(DateTime(timezone=True), nullable=False)


class Worker(Base):
    """A registered worker installation and its most recent live session.

    Online/offline is derived from last_heartbeat_at at read time. Keeping the
    row when a worker disappears gives the admin UI useful history without a
    scheduler that continually flips stored status flags.
    """
    __tablename__ = "workers"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    session_id = Column(String, nullable=False)
    capabilities = Column(JSONB, nullable=False, default=dict)
    version = Column(String, nullable=True)
    state = Column(String, nullable=False, default="idle")
    current_task_id = Column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    registered_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    session_started_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_heartbeat_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)


class Artifact(Base):
    """Binary result storage (images, eventually other media) -- metadata
    here, bytes live in Supabase Storage under storage_path. Referenced from
    a Task's `result` JSON by id (e.g. {"image_artifact_id": "..."}), never
    embedded directly -- keeps Task rows small and the queue's result shape
    uniform (always JSON) regardless of task type."""
    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    content_type = Column(String, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    storage_path = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
