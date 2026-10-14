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
    TASK_QUEUE_URL=https://ai-bharatiya-web.vercel.app/queue WORKER_API_KEY=... \
        MOCK_MODE=true python image_worker.py
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "clients"))
sys.path.insert(0, str(_REPO_ROOT))  # `python workers/image_worker.py` -> import workers.*
from python_client import TaskQueueClient

from workers import memory
from workers.comfyui import ComfyUISupervisor

# This worker runs as a standalone script, so the FastAPI application's
# pydantic-settings loader is never imported. Load the repository-local .env
# explicitly while preserving any values supplied by the launching shell.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TASK_QUEUE_URL = os.environ.get("TASK_QUEUE_URL", "http://127.0.0.1:8000")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY")
WORKER_NAME = os.environ.get("WORKER_NAME", socket.gethostname())
WORKER_ID = os.environ.get("WORKER_ID", f"image-worker-{WORKER_NAME}")
WORKER_SESSION_ID = str(uuid.uuid4())
WORKER_VERSION = "0.2.0"
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))
# Mock mode only: pretend rendering takes this long, so progress/lost-worker
# UI can be exercised without ComfyUI.
MOCK_DELAY_SECONDS = float(os.environ.get("MOCK_DELAY_SECONDS", "0"))
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "60"))

# Sibling-directory convention, same as linkedin-content-agent's config.py.
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
CHARACTER_FORGE_V2_PATH = Path(
    os.environ.get("CHARACTER_FORGE_V2_PATH", str(_WORKSPACE_ROOT / "character-forge-v2")))
COMFYUI_ENV_PYTHON = os.environ.get(
    "COMFYUI_ENV_PYTHON", str(_WORKSPACE_ROOT / "comfyui-env" / "bin" / "python"))

# ComfyUI supervision (real mode only). The worker starts ComfyUI on demand,
# stops it after COMFYUI_IDLE_MINUTES without work, and restarts it after a
# blank render. A ComfyUI already running when needed (user-started) is used
# but never stopped. COMFYUI_MANAGE=false disables starting it entirely.
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
COMFYUI_MANAGE = os.environ.get("COMFYUI_MANAGE", "true").lower() == "true"
COMFYUI_IDLE_MINUTES = float(os.environ.get("COMFYUI_IDLE_MINUTES", "10"))
COMFYUI_START_TIMEOUT_SECONDS = float(os.environ.get("COMFYUI_START_TIMEOUT_SECONDS", "300"))
COMFYUI_START_SCRIPT = os.environ.get(
    "COMFYUI_START_SCRIPT", str(CHARACTER_FORGE_V2_PATH / "scripts" / "start_comfyui.sh"))
COMFYUI_LOG = os.environ.get("COMFYUI_LOG", str(_REPO_ROOT / "comfyui.log"))


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

    def __init__(self, client: TaskQueueClient,
                 details: Callable[[], dict] | None = None,
                 capabilities: dict | None = None):
        self.client = client
        self.capabilities = capabilities or {
            "task_types": ["image_generation"],
            "profiles": ["full", "lightning"],
            "mock_mode": MOCK_MODE,
        }
        # Machine status (memory, ComfyUI) sent as heartbeat `details`.
        self._details = details
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
            capabilities=self.capabilities,
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
        if self._details is not None:
            try:
                snapshot["details"] = self._details()
            except Exception as exc:
                # Status reporting must never stop the heartbeat itself.
                print(f"worker details unavailable: {exc}")
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


# `forge2 generate` exits with this code when the render came back blank/
# uniform (see character-forge-v2's forge2/quality.py). Kept as a literal --
# the worker talks to forge2 only through its CLI, never by import.
FORGE2_CORRUPT_EXIT_CODE = 3


def _run_forge2(cmd: list[str], timeout: float) -> tuple[int, list[str]]:
    """Run forge2, streaming its stderr (its log) live, and return the exit
    code plus the last stderr lines -- so a failure can carry forge2's own
    explanation without hiding a multi-hour render's progress."""
    tail: deque[str] = deque(maxlen=40)
    proc = subprocess.Popen(cmd, cwd=CHARACTER_FORGE_V2_PATH, stderr=subprocess.PIPE,
                            text=True, errors="replace")

    def pump() -> None:
        for line in proc.stderr:
            sys.stderr.write(line)
            tail.append(line.rstrip("\n"))

    reader = threading.Thread(target=pump, name="forge2-stderr", daemon=True)
    reader.start()
    try:
        returncode = proc.wait(timeout=timeout)
    except BaseException:
        # Timeout, Ctrl-C or SIGTERM: don't leave forge2 running unattended.
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join(timeout=5)
    return returncode, list(tail)


def _run_real(character_id: str, task: str, seed: int, profile: str) -> bytes:
    cmd = _build_real_command(character_id, task, seed, profile)
    # Match the queue request's four-hour lease. A full 40-step render under
    # memory pressure has exceeded two hours, so the old two-hour subprocess
    # timeout could kill valid work while its queue claim was still healthy.
    returncode, tail = _run_forge2(cmd, timeout=14_400)
    if returncode == FORGE2_CORRUPT_EXIT_CODE:
        # The producer recognises this prefix; retrying is its decision.
        # Prefer forge2's own line (measured spread + saved filename).
        detail = next((line.strip() for line in reversed(tail)
                       if line.strip().startswith("corrupt_output:")), None)
        raise RuntimeError(detail or (
            "corrupt_output: ComfyUI returned a blank/uniform image "
            f"(forge2 exit {FORGE2_CORRUPT_EXIT_CODE})"))
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd, stderr="\n".join(tail))
    out_name = cmd[-1]
    out_path = CHARACTER_FORGE_V2_PATH / "workspace" / character_id / "generated" / out_name
    return out_path.read_bytes()


def _render_real(task: dict, payload: dict, heartbeat: WorkerHeartbeat | None,
                 supervisor: ComfyUISupervisor | None) -> bytes:
    profile = payload.get("profile", "full")
    # The user picked the profile in the producer app; never downgrade it.
    # Refuse instead, so they can choose Lightning themselves.
    shortfall = memory.memory_shortfall(profile)
    if shortfall and supervisor is not None and supervisor.managed:
        # Our own idle ComfyUI may be what holds the memory (models stay
        # loaded after a render). The worker may free it -- a user-started
        # ComfyUI is never touched -- then measure again.
        print("memory short with worker-owned ComfyUI loaded -- stopping it and re-checking")
        supervisor.stop()
        shortfall = memory.memory_shortfall(profile)
    if shortfall:
        raise RuntimeError(shortfall)

    if supervisor:
        supervisor.mark_busy()
    try:
        if supervisor:
            if heartbeat:
                heartbeat.update(state="busy", task=task, stage="starting_comfyui",
                                 message="Making sure ComfyUI is running")
            supervisor.ensure_running()
        if heartbeat:
            heartbeat.update(state="busy", task=task, stage="rendering",
                             message="Character Forge and ComfyUI are generating the image")
        try:
            return _run_real(payload["character_id"], payload["task"],
                             payload.get("seed", 0), profile)
        except RuntimeError as e:
            if supervisor and str(e).startswith("corrupt_output:"):
                # Long ComfyUI uptime is a known cause of blank output: start
                # fresh before the next task (report the failure first; no
                # retry here -- the user decides).
                supervisor.request_restart()
            raise
    finally:
        if supervisor:
            supervisor.mark_idle()


def handle_task(client: TaskQueueClient, task: dict,
                heartbeat: WorkerHeartbeat | None = None,
                supervisor: ComfyUISupervisor | None = None) -> dict:
    payload = task["payload"]
    character_id = payload["character_id"]

    if MOCK_MODE:
        if heartbeat:
            heartbeat.update(state="busy", task=task, stage="rendering",
                             message="Producing mock image")
        if MOCK_DELAY_SECONDS:
            time.sleep(MOCK_DELAY_SECONDS)
        image_bytes = _run_mock(character_id)
    else:
        image_bytes = _render_real(task, payload, heartbeat, supervisor)

    if heartbeat:
        heartbeat.update(state="busy", task=task, stage="uploading",
                         message="Uploading generated image")
    artifact = client.upload_artifact(image_bytes, "image/png", filename=f"{task['id']}.png")
    return {"image_artifact_id": artifact["id"]}


# Signals that should shut the worker down cleanly (running atexit, which
# stops a worker-started ComfyUI). SIGHUP = the terminal tab was closed.
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def build_supervisor() -> ComfyUISupervisor | None:
    if MOCK_MODE:
        return None
    return ComfyUISupervisor(
        COMFYUI_URL, ["/bin/sh", COMFYUI_START_SCRIPT], Path(COMFYUI_LOG),
        manage=COMFYUI_MANAGE, idle_seconds=COMFYUI_IDLE_MINUTES * 60,
        start_timeout=COMFYUI_START_TIMEOUT_SECONDS,
        pid_path=Path(COMFYUI_LOG).with_suffix(".pid"))


def worker_details(supervisor: ComfyUISupervisor | None) -> dict:
    details = {}
    if supervisor is not None:
        details["comfyui"] = supervisor.details()
        mem = memory.memory_details()
        if mem is not None:
            details["memory"] = mem
    return details


def main() -> None:
    # Kept so `python workers/image_worker.py` (README, Colab notebook) still
    # works: the per-machine loop now lives in local_worker.py. Started this
    # way, a worker does images unless WORKER_TASK_TYPES says otherwise.
    os.environ.setdefault("WORKER_TASK_TYPES", "image_generation")
    from workers import local_worker
    local_worker.main()


if __name__ == "__main__":
    main()
