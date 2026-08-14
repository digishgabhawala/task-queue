"""python_client.py -- thin, dependency-light (just httpx) client for both
producers (submit a task, poll for its result) and workers (claim the next
task, complete/fail it). Same "direct httpx, no generated SDK" convention
used for Ollama calls elsewhere in this workspace.

Usage (producer):
    from python_client import TaskQueueClient
    client = TaskQueueClient("http://127.0.0.1:8000")
    task = client.submit_task("image_generation", {"character_id": "gtee_dev", ...})
    result = client.wait_for_task(task["id"])  # polls until done/failed

Usage (worker):
    while True:
        task = client.claim_next(["image_generation"], worker_id="my-laptop")
        if task is None:
            time.sleep(5)
            continue
        try:
            result = do_the_work(task["payload"])
            client.complete_task(task["id"], task["claim_token"], result)
        except Exception as e:
            client.fail_task(task["id"], task["claim_token"], str(e))
"""
from __future__ import annotations

import time

import httpx


class TaskQueueClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def submit_task(self, task_type: str, payload: dict,
                    lease_minutes: int | None = None,
                    retention_hours: int | None = None) -> dict:
        resp = httpx.post(f"{self.base_url}/api/tasks", json={
            "task_type": task_type, "payload": payload,
            "lease_minutes": lease_minutes, "retention_hours": retention_hours,
        }, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_task(self, task_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/tasks/{task_id}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def wait_for_task(self, task_id: str, poll_seconds: float = 3.0,
                      timeout_seconds: float | None = None) -> dict:
        """Blocks until the task reaches done/failed. Raises TimeoutError if
        timeout_seconds is given and exceeded."""
        start = time.time()
        while True:
            task = self.get_task(task_id)
            if task["status"] in ("done", "failed"):
                return task
            if timeout_seconds is not None and time.time() - start > timeout_seconds:
                raise TimeoutError(f"task {task_id} still {task['status']} after {timeout_seconds}s")
            time.sleep(poll_seconds)

    def claim_next(self, task_types: list[str], worker_id: str,
                   lease_minutes: int | None = None) -> dict | None:
        # httpx serializes a None-valued param as an empty string ("?x="),
        # which FastAPI then fails to parse as `int` -- found live. Omit
        # entirely rather than pass None, so FastAPI's own default applies.
        params = {"task_types": ",".join(task_types), "worker_id": worker_id}
        if lease_minutes is not None:
            params["lease_minutes"] = lease_minutes
        resp = httpx.get(f"{self.base_url}/api/tasks/next", params=params, timeout=self.timeout)
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def complete_task(self, task_id: str, claim_token: str, result: dict) -> dict:
        resp = httpx.post(f"{self.base_url}/api/tasks/{task_id}/complete", json={
            "claim_token": claim_token, "result": result,
        }, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fail_task(self, task_id: str, claim_token: str, error: str) -> dict:
        resp = httpx.post(f"{self.base_url}/api/tasks/{task_id}/fail", json={
            "claim_token": claim_token, "error": error,
        }, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def upload_artifact(self, content: bytes, content_type: str, filename: str = "file") -> dict:
        resp = httpx.post(f"{self.base_url}/api/artifacts",
                          files={"file": (filename, content, content_type)},
                          timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_artifact(self, artifact_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/artifacts/{artifact_id}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
