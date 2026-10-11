"""image_worker.py -- claims "image_generation" tasks and either (mock mode)
returns the character's existing hero.png almost instantly, or (real mode)
actually calls character-forge-v2's forge2 CLI and uploads the render.

Mock mode lives HERE, not inside forge2 itself -- forge2 is a proven,
published, "really generates images" tool with its own tests; baking a fake
path into its core generate() would muddy what it's for. This worker is new,
task-queue-specific glue, so it's the right place for a fast-path that lets
the rest of the queue (submit/claim/complete/artifact-retrieval) get tested
in milliseconds instead of a real 15-40 min render.

Task payload contract for task_type="image_generation":
    {"character_id": str, "task": str, "seed": int,
     "profile": "full" | "lightning"}
Result on success:
    {"image_artifact_id": str}

Note there's no Popen+webhook-callback dance here, unlike the old
linkedin-content-agent <-> character-forge-v2 integration -- the worker's
own claim loop already IS the background thing, so it can just call forge2
synchronously (claim -> run -> complete -> repeat).

Usage:
    TASK_QUEUE_URL=http://127.0.0.1:8000 MOCK_MODE=true python image_worker.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients"))
from python_client import TaskQueueClient

TASK_QUEUE_URL = os.environ.get("TASK_QUEUE_URL", "http://127.0.0.1:8000")
WORKER_NAME = os.environ.get("WORKER_NAME", socket.gethostname())
WORKER_ID = os.environ.get("WORKER_ID", f"image-worker-{WORKER_NAME}")
WORKER_SESSION_ID = str(uuid.uuid4())
WORKER_VERSION = "0.2.0"
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "60"))

# Sibling-directory convention, same as linkedin-content-agent's config.py.
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
CHARACTER_FORGE_V2_PATH = Path(
    os.environ.get("CHARACTER_FORGE_V2_PATH", str(_WORKSPACE_ROOT / "character-forge-v2")))
COMFYUI_ENV_PYTHON = os.environ.get(
    "COMFYUI_ENV_PYTHON", str(_WORKSPACE_ROOT / "comfyui-env" / "bin" / "python"))


def _run_mock(character_id: str) -> bytes:
    hero_path = CHARACTER_FORGE_V2_PATH / "workspace" / character_id / "hero.png"
    if not hero_path.exists():
        raise FileNotFoundError(f"mock mode: no hero.png for {character_id} at {hero_path}")
    return hero_path.read_bytes()


def _build_real_command(character_id: str, task: str, seed: int,
                        profile: str) -> list[str]:
    if profile not in ("full", "lightning"):
        raise ValueError(f"unsupported image profile: {profile!r}")
    out_name = f"queue_{uuid.uuid4().hex[:8]}.png"
    return [COMFYUI_ENV_PYTHON, "-m", "forge2.cli", "generate", character_id, task,
            "--seed", str(seed), "--profile", profile, "--out", out_name]


class WorkerHeartbeat:
    """Keeps registration, availability, progress, and the task lease alive.

    Rendering blocks the main worker loop for a long time, so heartbeats run
    on their own daemon thread. Progress changes wake the thread immediately;
    otherwise it reports at the configured one-minute interval.
    """

    def __init__(self, client: TaskQueueClient):
        self.client = client
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._state = {
            "state": "idle",
            "current_task_id": None,
            "claim_token": None,
            "progress_stage": None,
            "progress_message": None,
        }

    def register(self) -> None:
        self.client.register_worker(
            WORKER_ID,
            WORKER_NAME,
            WORKER_SESSION_ID,
            capabilities={
                "task_types": ["image_generation"],
                "profiles": ["full", "lightning"],
                "mock_mode": MOCK_MODE,
            },
            version=WORKER_VERSION,
        )

    def update(self, *, state: str, task: dict | None = None,
               stage: str | None = None, message: str | None = None) -> None:
        with self._lock:
            self._state = {
                "state": state,
                "current_task_id": task["id"] if task else None,
                "claim_token": task["claim_token"] if task else None,
                "progress_stage": stage,
                "progress_message": message,
            }
        self._wake.set()

    def _send(self) -> None:
        with self._lock:
            snapshot = dict(self._state)
        self.client.heartbeat(WORKER_ID, WORKER_SESSION_ID, **snapshot)

    def _loop(self) -> None:
        while True:
            try:
                self._send()
            except Exception as exc:
                # A temporary hosted-queue outage should not kill a render.
                print(f"worker heartbeat failed: {exc}")
            self._wake.wait(HEARTBEAT_SECONDS)
            self._wake.clear()

    def start(self) -> None:
        self.register()
        threading.Thread(target=self._loop, name="worker-heartbeat", daemon=True).start()


def _run_real(character_id: str, task: str, seed: int, profile: str) -> bytes:
    cmd = _build_real_command(character_id, task, seed, profile)
    # Match the queue request's four-hour lease. A full 40-step render under
    # memory pressure has exceeded two hours, so the old two-hour subprocess
    # timeout could kill valid work while its queue claim was still healthy.
    subprocess.run(cmd, cwd=CHARACTER_FORGE_V2_PATH, check=True, timeout=14_400)
    out_name = cmd[-1]
    out_path = CHARACTER_FORGE_V2_PATH / "workspace" / character_id / "generated" / out_name
    return out_path.read_bytes()


def handle_task(client: TaskQueueClient, task: dict,
                heartbeat: WorkerHeartbeat | None = None) -> dict:
    payload = task["payload"]
    character_id = payload["character_id"]

    if MOCK_MODE:
        if heartbeat:
            heartbeat.update(state="busy", task=task, stage="rendering",
                             message="Producing mock image")
        image_bytes = _run_mock(character_id)
    else:
        if heartbeat:
            heartbeat.update(state="busy", task=task, stage="rendering",
                             message="Character Forge and ComfyUI are generating the image")
        image_bytes = _run_real(
            character_id,
            payload["task"],
            payload.get("seed", 0),
            payload.get("profile", "full"),
        )

    if heartbeat:
        heartbeat.update(state="busy", task=task, stage="uploading",
                         message="Uploading generated image")
    artifact = client.upload_artifact(image_bytes, "image/png", filename=f"{task['id']}.png")
    return {"image_artifact_id": artifact["id"]}


def main() -> None:
    client = TaskQueueClient(TASK_QUEUE_URL)
    heartbeat = WorkerHeartbeat(client)
    heartbeat.start()
    print(f"image_worker starting -- worker_id={WORKER_ID} mock={MOCK_MODE} "
         f"queue={TASK_QUEUE_URL}")
    while True:
        task = client.claim_next(["image_generation"], WORKER_ID)
        if task is None:
            time.sleep(POLL_SECONDS)
            continue

        print(f"claimed {task['id']} (mock={MOCK_MODE})")
        heartbeat.update(state="busy", task=task, stage="preparing",
                         message="Preparing image generation")

        # Two distinct failure modes, found live, that must NOT crash the
        # worker: (1) the actual work (forge2 render) fails -- report it via
        # fail_task; (2) reporting a result (success OR failure) itself gets
        # rejected with a 409 because this task's lease expired mid-render
        # and someone else already reclaimed/completed it -- nothing to do
        # but log and move on, the work this worker just did is simply
        # discarded. Before this fix, case (2) inside the exception handler
        # for case (1) raised its own uncaught exception and killed the
        # whole worker process.
        try:
            result = handle_task(client, task, heartbeat)
        except Exception as e:
            print(f"work failed for {task['id']}: {e}")
            try:
                client.fail_task(task["id"], task["claim_token"], str(e))
            except httpx.HTTPStatusError as report_err:
                print(f"could not report failure for {task['id']} (claim likely "
                     f"expired/reclaimed): {report_err}")
            heartbeat.update(state="idle")
            continue

        try:
            client.complete_task(task["id"], task["claim_token"], result)
            print(f"completed {task['id']} -> {result}")
        except httpx.HTTPStatusError as e:
            print(f"could not report completion for {task['id']} (claim likely "
                 f"expired/reclaimed -- the work finished but too late, someone "
                 f"else already took over): {e}")
        heartbeat.update(state="idle")


if __name__ == "__main__":
    main()
