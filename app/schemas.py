from __future__ import annotations

from pydantic import BaseModel


class TaskSubmitRequest(BaseModel):
    task_type: str
    payload: dict
    lease_minutes: int | None = None
    retention_hours: int | None = None


class TaskCompleteRequest(BaseModel):
    claim_token: str
    result: dict


class TaskFailRequest(BaseModel):
    claim_token: str
    error: str
