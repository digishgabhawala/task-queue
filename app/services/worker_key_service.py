"""Self-service worker keys: users create, list and revoke their own keys.

The raw key (``tqw_`` + 32 random URL-safe bytes) is returned once by
``create_key`` and never stored or logged -- only its sha256 hex (for lookup)
and its first 8 characters (for display) reach the database.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import WorkerKey

KEY_PREFIX = "tqw_"
MAX_ACTIVE_KEYS = 10


class WorkerKeyError(Exception):
    pass


class WorkerKeyLimit(WorkerKeyError):
    pass


class WorkerKeyNotFound(WorkerKeyError):
    pass


@dataclass(frozen=True)
class CreatedKey:
    key: WorkerKey
    raw_key: str

    def __repr__(self) -> str:  # never let the raw key reach a log line
        return f"CreatedKey(key_id={self.key.id!r}, raw_key='***')"


@dataclass(frozen=True)
class KeyOwner:
    key_id: str
    user_id: str
    name: str


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(db: Session, *, user_id: str, name: str) -> CreatedKey:
    # A safety cap, not race-proof: two concurrent creates may both pass.
    active = db.scalar(select(func.count()).select_from(WorkerKey).where(
        WorkerKey.user_id == user_id, WorkerKey.revoked_at.is_(None)))
    if active >= MAX_ACTIVE_KEYS:
        raise WorkerKeyLimit(f"At most {MAX_ACTIVE_KEYS} active worker keys; revoke one first")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    key = WorkerKey(user_id=user_id, name=name, key_hash=hash_key(raw), key_prefix=raw[:8])
    db.add(key)
    db.commit()
    db.refresh(key)
    return CreatedKey(key=key, raw_key=raw)


def list_keys(db: Session, *, user_id: str) -> list[WorkerKey]:
    return list(db.scalars(select(WorkerKey).where(WorkerKey.user_id == user_id)
                           .order_by(WorkerKey.created_at.desc())))


def revoke_key(db: Session, *, user_id: str, key_id: str) -> WorkerKey:
    key = db.get(WorkerKey, str(key_id))
    if key is None or key.user_id != user_id:  # another user's key looks unknown
        raise WorkerKeyNotFound("Worker key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(key)
    return key


_MATCH = text(
    "select k.id::text as key_id, k.user_id::text as user_id, k.name from worker_keys k "
    "join platform.profiles p on p.user_id = k.user_id "
    "where k.key_hash = :h and k.revoked_at is null and p.status = 'approved'")
_TOUCH = text("update worker_keys set last_used_at = now() where id = cast(:id as uuid) and "
              "(last_used_at is null or last_used_at < now() - interval '1 minute')")


def find_active_key(db: Session, raw_key: str) -> KeyOwner | None:
    """The owner of an active key whose owner's profile is approved, else None.

    The lookup is by sha256 hex through a unique index, so the comparison
    happens on the hash and never on the raw key."""
    row = db.execute(_MATCH, {"h": hash_key(raw_key)}).first()
    if row is None:
        db.rollback()
        return None
    db.execute(_TOUCH, {"id": row.key_id})
    db.commit()
    return KeyOwner(key_id=row.key_id, user_id=row.user_id, name=row.name)
