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
    {"character_id": str, "task": str, "seed": int}
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
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients"))
from python_client import TaskQueueClient

TASK_QUEUE_URL = os.environ.get("TASK_QUEUE_URL", "http://127.0.0.1:8000")
WORKER_ID = os.environ.get("WORKER_ID", f"image-worker-{uuid.uuid4().hex[:8]}")
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "3"))

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


def _run_real(character_id: str, task: str, seed: int) -> bytes:
    out_name = f"queue_{uuid.uuid4().hex[:8]}.png"
    cmd = [COMFYUI_ENV_PYTHON, "-m", "forge2.cli", "generate", character_id, task,
          "--seed", str(seed), "--out", out_name]
    # 7200s, matching forge2's own comfy.timeout_s ceiling (configs/default.yaml)
    # -- found live: a real render on a loaded Mac took ~2 hours, well past the
    # 3600s this used to be set to, so the worker gave up before forge2 itself
    # would have.
    subprocess.run(cmd, cwd=CHARACTER_FORGE_V2_PATH, check=True, timeout=7200)
    out_path = CHARACTER_FORGE_V2_PATH / "workspace" / character_id / "generated" / out_name
    return out_path.read_bytes()


def handle_task(client: TaskQueueClient, task: dict) -> dict:
    payload = task["payload"]
    character_id = payload["character_id"]

    if MOCK_MODE:
        image_bytes = _run_mock(character_id)
    else:
        image_bytes = _run_real(character_id, payload["task"], payload.get("seed", 0))

    artifact = client.upload_artifact(image_bytes, "image/png", filename=f"{task['id']}.png")
    return {"image_artifact_id": artifact["id"]}


def main() -> None:
    client = TaskQueueClient(TASK_QUEUE_URL)
    print(f"image_worker starting -- worker_id={WORKER_ID} mock={MOCK_MODE} "
         f"queue={TASK_QUEUE_URL}")
    while True:
        task = client.claim_next(["image_generation"], WORKER_ID)
        if task is None:
            time.sleep(POLL_SECONDS)
            continue

        print(f"claimed {task['id']} (mock={MOCK_MODE})")

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
            result = handle_task(client, task)
        except Exception as e:
            print(f"work failed for {task['id']}: {e}")
            try:
                client.fail_task(task["id"], task["claim_token"], str(e))
            except httpx.HTTPStatusError as report_err:
                print(f"could not report failure for {task['id']} (claim likely "
                     f"expired/reclaimed): {report_err}")
            continue

        try:
            client.complete_task(task["id"], task["claim_token"], result)
            print(f"completed {task['id']} -> {result}")
        except httpx.HTTPStatusError as e:
            print(f"could not report completion for {task['id']} (claim likely "
                 f"expired/reclaimed -- the work finished but too late, someone "
                 f"else already took over): {e}")


if __name__ == "__main__":
    main()
