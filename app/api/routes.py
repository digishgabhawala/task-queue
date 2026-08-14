from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models import Artifact, Task
from ..schemas import TaskCompleteRequest, TaskFailRequest, TaskSubmitRequest
from ..services import artifact_service as asvc
from ..services import task_service as tsvc

router = APIRouter(prefix="/api")


def _serialize_task(task: Task) -> dict:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "payload": task.payload,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "claimed_by": task.claimed_by,
        "created_at": task.created_at,
        "claimed_at": task.claimed_at,
        "lease_expires_at": task.lease_expires_at,
        "completed_at": task.completed_at,
        "expires_at": task.expires_at,
    }


def _serialize_artifact(artifact: Artifact) -> dict:
    return {
        "id": artifact.id,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "url": storage.public_url(artifact.storage_path),
        "created_at": artifact.created_at,
        "expires_at": artifact.expires_at,
    }


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/tasks")
def submit_task(req: TaskSubmitRequest, db: Session = Depends(get_db)):
    task = tsvc.submit_task(db, req.task_type, req.payload, req.lease_minutes, req.retention_hours)
    return _serialize_task(task)


@router.get("/tasks")
def list_tasks(status: str | None = None, task_type: str | None = None, db: Session = Depends(get_db)):
    return [_serialize_task(t) for t in tsvc.list_tasks(db, status, task_type)]


@router.get("/tasks/next")
def claim_next(task_types: str = Query(..., description="comma-separated task_type list"),
              worker_id: str | None = None, lease_minutes: int | None = None,
              db: Session = Depends(get_db)):
    claimed = tsvc.claim_next(db, task_types.split(","), worker_id or f"worker-{uuid.uuid4().hex[:8]}",
                              lease_minutes)
    if claimed is None:
        return Response(status_code=204)
    return claimed


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    try:
        return _serialize_task(tsvc.get_task(db, task_id))
    except tsvc.TaskServiceError as e:
        raise HTTPException(404, str(e))


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, req: TaskCompleteRequest, db: Session = Depends(get_db)):
    try:
        return _serialize_task(tsvc.complete_task(db, task_id, req.claim_token, req.result))
    except tsvc.TaskServiceError as e:
        raise HTTPException(409, str(e))


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: str, req: TaskFailRequest, db: Session = Depends(get_db)):
    try:
        return _serialize_task(tsvc.fail_task(db, task_id, req.claim_token, req.error))
    except tsvc.TaskServiceError as e:
        raise HTTPException(409, str(e))


@router.post("/artifacts")
async def upload_artifact(file: UploadFile, db: Session = Depends(get_db)):
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    artifact = asvc.upload_artifact(db, content, content_type)
    return _serialize_artifact(artifact)


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    try:
        return _serialize_artifact(asvc.get_artifact(db, artifact_id))
    except asvc.ArtifactServiceError as e:
        raise HTTPException(404, str(e))
