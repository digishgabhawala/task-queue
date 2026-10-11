from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskSubmitRequest(BaseModel):
    task_type: str
    payload: dict
    lease_minutes: int | None = Field(default=None, gt=0)
    retention_hours: int | None = Field(default=None, gt=0)


class TaskCompleteRequest(BaseModel):
    claim_token: str
    result: dict


class TaskFailRequest(BaseModel):
    claim_token: str
    error: str


class WorkerRegisterRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    capabilities: dict = Field(default_factory=dict)
    version: str | None = Field(default=None, max_length=100)


class WorkerHeartbeatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    state: Literal["idle", "busy"]
    current_task_id: str | None = None
    claim_token: str | None = None
    progress_stage: str | None = Field(default=None, max_length=100)
    progress_message: str | None = Field(default=None, max_length=500)
    progress_current: int | None = Field(default=None, ge=0)
    progress_total: int | None = Field(default=None, gt=0)
