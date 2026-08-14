import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, String, Text
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
    # Row is eligible for deletion by the pg_cron cleanup job once past this
    # (see supabase/migrations) -- set at submission time so even a task
    # that's never claimed still gets swept eventually.
    expires_at = Column(DateTime(timezone=True), nullable=False)


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
