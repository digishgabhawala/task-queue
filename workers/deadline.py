from __future__ import annotations

from datetime import datetime, timezone


def raise_if_expired(payload: dict) -> None:
    deadline_at = payload.get("deadline_at")
    if not deadline_at:
        return
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > deadline:
        raise RuntimeError(
            f"expired: deadline {deadline_at} passed before a worker was free")
