"""local_worker.py -- the one worker per machine.

Each laptop (or Colab session) runs exactly one of these. It advertises the
task types this machine can do -- WORKER_TASK_TYPES, or auto-detected: text
if Ollama answers, image if character-forge-v2/ComfyUI are installed -- and
works ONE task at a time, so a render and an LLM never fight over unified
memory. A waiting text task is claimed before a waiting image task (someone
is usually watching a draft spinner); a running render always finishes
first. Memory is handed over explicitly: the worker's own Ollama model is
unloaded before a render, and its own ComfyUI stopped before text work.

Usage:
    TASK_QUEUE_URL=https://... python workers/local_worker.py
    (python workers/image_worker.py still works -- it delegates here)
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # script use -> workers.*
from workers import image_worker, memory
from workers.ollama import OllamaManager
from workers.text_worker import handle_text_task

TEXT, IMAGE = "text_generation", "image_generation"
PRIORITY = (TEXT, IMAGE)  # claim order when both are waiting

WORKER_TASK_TYPES = os.environ.get("WORKER_TASK_TYPES", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_IDLE_MINUTES = float(os.environ.get("OLLAMA_IDLE_MINUTES", "5"))
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "900"))


def detect_task_types(configured: str, ollama_available: Callable[[], bool],
                      image_available: Callable[[], bool]) -> list[str]:
    if configured.strip():
        types = [t.strip() for t in configured.split(",") if t.strip()]
        unknown = [t for t in types if t not in PRIORITY]
        if unknown:
            raise ValueError(f"unknown WORKER_TASK_TYPES entries: {', '.join(unknown)}")
        return claim_order(types)
    detected = []
    if ollama_available():
        detected.append(TEXT)
    if image_available():
        detected.append(IMAGE)
    return detected


def claim_order(task_types: list[str]) -> list[str]:
    return [t for t in PRIORITY if t in task_types]


def image_available() -> bool:
    if image_worker.MOCK_MODE:
        return True
    return (image_worker.CHARACTER_FORGE_V2_PATH.exists()
            and Path(image_worker.COMFYUI_START_SCRIPT).exists())


def claim_next_task(client, worker_id: str, order: list[str]) -> dict | None:
    for task_type in order:  # one type per call, so the priority is explicit
        task = client.claim_next([task_type], worker_id)
        if task is not None:
            return task
    return None


def run_task(client, task: dict, heartbeat, supervisor, ollama) -> dict:
    task_type = task.get("task_type")
    if task_type == TEXT:
        return handle_text_task(task, heartbeat, ollama, supervisor)
    if task_type == IMAGE:
        if ollama is not None:
            ollama.unload()  # only if this worker loaded it -- renders need the memory
        return image_worker.handle_task(client, task, heartbeat, supervisor)
    raise ValueError(f"unsupported task type {task_type!r}")


def worker_details(supervisor, ollama) -> dict:
    details = image_worker.worker_details(supervisor)
    if ollama is not None:
        details["ollama"] = ollama.details()
        if "memory" not in details:
            mem = memory.memory_details()
            if mem is not None:
                details["memory"] = mem
    return details


def main() -> None:
    client = image_worker.TaskQueueClient(image_worker.TASK_QUEUE_URL)
    ollama_probe = OllamaManager(OLLAMA_URL, OLLAMA_MODEL)
    task_types = detect_task_types(WORKER_TASK_TYPES, ollama_probe.available, image_available)
    if not task_types:
        sys.exit("local_worker: this machine can do no task types (Ollama not reachable at "
                 f"{OLLAMA_URL}, character-forge-v2 not found); set WORKER_TASK_TYPES")

    supervisor = image_worker.build_supervisor() if IMAGE in task_types else None
    ollama = (OllamaManager(OLLAMA_URL, OLLAMA_MODEL, idle_seconds=OLLAMA_IDLE_MINUTES * 60,
                            timeout=OLLAMA_TIMEOUT_SECONDS)
              if TEXT in task_types else None)

    # Never leave behind what this worker started/loaded: stop its ComfyUI and
    # unload its model on Ctrl-C, `kill`, or a closed terminal (SIGHUP).
    if supervisor is not None:
        atexit.register(supervisor.stop)
    if ollama is not None:
        atexit.register(ollama.unload)
    for sig in image_worker.SHUTDOWN_SIGNALS:
        signal.signal(sig, lambda signum, frame: sys.exit(0))

    capabilities = {"task_types": task_types}
    if IMAGE in task_types:
        capabilities |= {"profiles": ["full", "lightning"], "mock_mode": image_worker.MOCK_MODE}
    if TEXT in task_types:
        capabilities |= {"text_providers": ["ollama"], "text_model": OLLAMA_MODEL}
    heartbeat = image_worker.WorkerHeartbeat(
        client, details=lambda: worker_details(supervisor, ollama), capabilities=capabilities)
    heartbeat.start()
    order = claim_order(task_types)
    print(f"local_worker starting -- worker_id={image_worker.WORKER_ID} "
          f"task_types={order} mock_images={image_worker.MOCK_MODE} "
          f"queue={image_worker.TASK_QUEUE_URL}")

    while True:
        task = claim_next_task(client, image_worker.WORKER_ID, order)
        if task is None:
            if supervisor is not None:
                supervisor.maybe_stop_idle()
            if ollama is not None:
                ollama.maybe_unload_idle()
            time.sleep(image_worker.POLL_SECONDS)
            continue

        print(f"claimed {task['task_type']} {task['id']}")
        heartbeat.update(state="busy", task=task, stage="preparing",
                         message=f"Preparing {task['task_type'].replace('_', ' ')}")

        # Two failure modes that must NOT crash the worker (found live): the
        # work itself fails -> report via fail_task; or reporting is rejected
        # (409) because the lease expired and the task was reclaimed -> log.
        try:
            result = run_task(client, task, heartbeat, supervisor, ollama)
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
            print(f"completed {task['task_type']} {task['id']}")
        except httpx.HTTPStatusError as e:
            print(f"could not report completion for {task['id']} (claim likely "
                  f"expired/reclaimed): {e}")
        heartbeat.update(state="idle")


if __name__ == "__main__":
    main()
