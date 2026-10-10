from __future__ import annotations

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
