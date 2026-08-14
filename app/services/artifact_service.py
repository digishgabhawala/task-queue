from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .. import storage
from ..config import settings
from ..models import Artifact


class ArtifactServiceError(Exception):
    """Raised when an artifact can't be found."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upload_artifact(db: Session, content: bytes, content_type: str) -> Artifact:
    artifact_id = str(uuid.uuid4())
    storage_path = storage.object_path(artifact_id, content_type)
    storage.upload(storage_path, content, content_type)

    artifact = Artifact(
        id=artifact_id,
        content_type=content_type,
        size_bytes=len(content),
        storage_path=storage_path,
        expires_at=_now() + timedelta(hours=settings.artifact_retention_hours),
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def get_artifact(db: Session, artifact_id: str) -> Artifact:
    try:
        uuid.UUID(artifact_id)
    except ValueError:
        raise ArtifactServiceError(f"artifact {artifact_id} not found") from None
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise ArtifactServiceError(f"artifact {artifact_id} not found")
    return artifact


def get_artifact_bytes(db: Session, artifact_id: str) -> tuple[bytes, str]:
    artifact = get_artifact(db, artifact_id)
    return storage.download(artifact.storage_path), artifact.content_type
