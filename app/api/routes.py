from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..auth import AuthUser, public_auth_config, require_admin, require_admin_or_service
from ..db import get_db
from ..models import Artifact, Task, Worker
from ..schemas import (
    TaskCompleteRequest,
    TaskFailRequest,
    TaskSubmitRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
)
from ..services import artifact_service as asvc
from ..services import task_service as tsvc
from ..services import worker_service as wsvc

router = APIRouter()


def _serialize_task(task: Task) -> dict:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "payload": task.payload,
        "lease_minutes": task.lease_minutes,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "claimed_by": task.claimed_by,
        "created_at": task.created_at,
        "claimed_at": task.claimed_at,
        "lease_expires_at": task.lease_expires_at,
        "completed_at": task.completed_at,
        "progress_stage": task.progress_stage,
        "progress_message": task.progress_message,
        "progress_current": task.progress_current,
        "progress_total": task.progress_total,
        "progress_updated_at": task.progress_updated_at,
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


def _serialize_worker(worker: Worker) -> dict:
    return {
        "id": worker.id,
        "name": worker.name,
        "session_id": worker.session_id,
        "capabilities": worker.capabilities,
        "details": worker.details or {},
        "version": worker.version,
        "state": worker.state,
        "current_task_id": worker.current_task_id,
        "registered_at": worker.registered_at,
        "session_started_at": worker.session_started_at,
        "last_heartbeat_at": worker.last_heartbeat_at,
        "online": wsvc.is_online(worker),
    }


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/auth/config", include_in_schema=False)
def auth_config():
    return public_auth_config()


@router.get("/auth/me", include_in_schema=False)
def auth_me(user: AuthUser = Depends(require_admin)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
    }


@router.post("/workers/register")
def register_worker(req: WorkerRegisterRequest, db: Session = Depends(get_db)):
    worker = wsvc.register_worker(
        db,
        worker_id=req.worker_id,
        name=req.name,
        session_id=req.session_id,
        capabilities=req.capabilities,
        version=req.version,
    )
    return _serialize_worker(worker)


@router.post("/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, req: WorkerHeartbeatRequest,
                     db: Session = Depends(get_db)):
    try:
        worker = wsvc.heartbeat(
            db,
            worker_id=worker_id,
            session_id=req.session_id,
            state=req.state,
            current_task_id=req.current_task_id,
            claim_token=req.claim_token,
            progress_stage=req.progress_stage,
            progress_message=req.progress_message,
            progress_current=req.progress_current,
            progress_total=req.progress_total,
            details=req.details,
        )
        return _serialize_worker(worker)
    except wsvc.WorkerServiceError as e:
        raise HTTPException(409, str(e))


@router.get("/workers")
def list_workers(
    db: Session = Depends(get_db),
    _caller: AuthUser = Depends(require_admin_or_service),
):
    return [_serialize_worker(w) for w in wsvc.list_workers(db)]


@router.post("/tasks")
def submit_task(req: TaskSubmitRequest, db: Session = Depends(get_db)):
    task = tsvc.submit_task(db, req.task_type, req.payload, req.lease_minutes, req.retention_hours)
    return _serialize_task(task)


@router.get("/tasks")
def list_tasks(
    status: str | None = None,
    task_type: str | None = None,
    db: Session = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
):
    return [_serialize_task(t) for t in tsvc.list_tasks(db, status, task_type)]


@router.get("/tasks/next")
def claim_next(task_types: str = Query(..., description="comma-separated task_type list"),
              worker_id: str | None = None,
              lease_minutes: int | None = Query(None, gt=0),
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
