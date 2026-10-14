"""python_client.py -- thin, dependency-light (just httpx) client for both
producers (submit a task, poll for its result) and workers (claim the next
task, complete/fail it). Same "direct httpx, no generated SDK" convention
used for Ollama calls elsewhere in this workspace.

Usage (producer):
    from python_client import TaskQueueClient
    client = TaskQueueClient(
        "https://ai-bharatiya-web.vercel.app/queue",
        producer_api_key=os.environ["TASK_QUEUE_API_KEY"],
    )
    task = client.submit_task("image_generation", {"character_id": "gtee_dev", ...})
    result = client.wait_for_task(task["id"])  # polls until done/failed

Usage (worker):
    client = TaskQueueClient(
        "https://ai-bharatiya-web.vercel.app/queue",
        worker_api_key=os.environ["WORKER_API_KEY"],
    )
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
    def __init__(self, base_url: str, timeout: float = 30.0,
                 producer_api_key: str | None = None,
                 worker_api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_url = self._api_url(self.base_url)
        self.timeout = timeout
        self.producer_api_key = producer_api_key
        self.worker_api_key = worker_api_key

    @staticmethod
    def _api_url(base_url: str) -> str:
        """Resolve a service origin, API base, or canonical suite URL.

        Production configuration uses the public ``.../queue`` URL. Its API
        is mounted beside it at ``.../api/queue`` rather than underneath it.
        Direct service origins and explicit API bases remain supported for
        local development and backwards compatibility.
        """
        normalized = base_url.rstrip("/")
        if normalized.endswith(("/api", "/api/queue")):
            return normalized
        if normalized.endswith("/queue"):
            return f"{normalized.removesuffix('/queue')}/api/queue"
        return f"{normalized}/api"

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        return {"X-API-Key": api_key} if api_key else {}

    @property
    def producer_headers(self) -> dict[str, str]:
        return self._headers(self.producer_api_key)

    @property
    def worker_headers(self) -> dict[str, str]:
        return self._headers(self.worker_api_key)

    def submit_task(self, task_type: str, payload: dict,
                    lease_minutes: int | None = None,
                    retention_hours: int | None = None) -> dict:
        resp = httpx.post(f"{self.api_url}/tasks", json={
            "task_type": task_type, "payload": payload,
            "lease_minutes": lease_minutes, "retention_hours": retention_hours,
        }, headers=self.producer_headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_task(self, task_id: str) -> dict:
        resp = httpx.get(
            f"{self.api_url}/tasks/{task_id}",
            headers=self.producer_headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def register_worker(self, worker_id: str, name: str, session_id: str,
                        capabilities: dict, version: str | None = None) -> dict:
        resp = httpx.post(f"{self.api_url}/workers/register", json={
            "worker_id": worker_id,
            "name": name,
            "session_id": session_id,
            "capabilities": capabilities,
            "version": version,
        }, headers=self.worker_headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def heartbeat(self, worker_id: str, session_id: str, state: str,
                  current_task_id: str | None = None,
                  claim_token: str | None = None,
                  progress_stage: str | None = None,
                  progress_message: str | None = None,
                  progress_current: int | None = None,
                  progress_total: int | None = None,
                  details: dict | None = None) -> dict:
        resp = httpx.post(
            f"{self.api_url}/workers/{worker_id}/heartbeat",
            json={
                "session_id": session_id,
                "state": state,
                "current_task_id": current_task_id,
                "claim_token": claim_token,
                "progress_stage": progress_stage,
                "progress_message": progress_message,
                "progress_current": progress_current,
                "progress_total": progress_total,
                "details": details,
            },
            headers=self.worker_headers,
            timeout=self.timeout,
        )
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
        resp = httpx.get(
            f"{self.api_url}/tasks/next",
            params=params,
            headers=self.worker_headers,
            timeout=self.timeout,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def complete_task(self, task_id: str, claim_token: str, result: dict) -> dict:
        resp = httpx.post(f"{self.api_url}/tasks/{task_id}/complete", json={
            "claim_token": claim_token, "result": result,
        }, headers=self.worker_headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fail_task(self, task_id: str, claim_token: str, error: str) -> dict:
        resp = httpx.post(f"{self.api_url}/tasks/{task_id}/fail", json={
            "claim_token": claim_token, "error": error,
        }, headers=self.worker_headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def upload_artifact(self, content: bytes, content_type: str, filename: str = "file") -> dict:
        resp = httpx.post(f"{self.api_url}/artifacts",
                          files={"file": (filename, content, content_type)},
                          headers=self.worker_headers,
                          timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_artifact(self, artifact_id: str) -> dict:
        resp = httpx.get(
            f"{self.api_url}/artifacts/{artifact_id}",
            headers=self.producer_headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
