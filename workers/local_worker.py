"""local_worker.py -- the one worker per machine.

Each laptop (or Colab session) runs exactly one of these. WHAT it works on is
the operator's decision, set in WORKER_TASK_TYPES (e.g.
"text_generation,image_generation") -- not guessed from the machine. It works
ONE task at a time and takes the next available task of any configured type
(the queue hands out the oldest; no priority between types for now), so a
render and an LLM never fight over unified memory. Memory is handed over
explicitly: the worker's own Ollama model is unloaded before a render, and
its own ComfyUI stopped before text work.

Usage:
    WORKER_TASK_TYPES=text_generation,image_generation \
        TASK_QUEUE_URL=https://... python workers/local_worker.py
    (python workers/image_worker.py still works: images only by default)
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # script use -> workers.*
from workers import image_worker, memory
from workers.ollama import OllamaManager
from workers.text_worker import handle_text_task

TEXT, IMAGE = "text_generation", "image_generation"
SUPPORTED = (TEXT, IMAGE)  # task types this worker has handlers for

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_IDLE_MINUTES = float(os.environ.get("OLLAMA_IDLE_MINUTES", "5"))
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "900"))
# After unloading its own model before a render, how long to wait for macOS
# to actually hand the memory back before the memory check runs.
MEMORY_SETTLE_SECONDS = float(os.environ.get("MEMORY_SETTLE_SECONDS", "60"))


def configured_task_types(configured: str) -> list[str]:
    types = [t.strip() for t in configured.split(",") if t.strip()]
    if not types:
        raise ValueError("set WORKER_TASK_TYPES to the task types this worker should run, "
                         f"e.g. {','.join(SUPPORTED)}")
    unknown = [t for t in types if t not in SUPPORTED]
    if unknown:
        raise ValueError(f"unknown WORKER_TASK_TYPES entries: {', '.join(unknown)} "
                         f"(supported: {', '.join(SUPPORTED)})")
    return list(dict.fromkeys(types))


def image_available() -> bool:
    if image_worker.MOCK_MODE:
        return True
    return (image_worker.CHARACTER_FORGE_V2_PATH.exists()
            and Path(image_worker.COMFYUI_START_SCRIPT).exists())


@dataclass(frozen=True)
class ClaimAttempt:
    task: dict | None
    reachable: bool


def claim_next_task(client, worker_id: str, task_types: list[str]) -> ClaimAttempt:
    # One claim across every configured type: the queue returns the oldest
    # waiting task, whatever its type. A network blip or slow hosted queue
    # (found live: a ReadTimeout killed the worker) just means "try again".
    try:
        return ClaimAttempt(client.claim_next(task_types, worker_id), reachable=True)
    except httpx.HTTPError as e:
        print(f"claim failed, retrying next poll: {e!r}")
        return ClaimAttempt(None, reachable=False)


def next_poll_delay(attempt: ClaimAttempt, last_task_done_at: float | None = None,
                    now: float | None = None) -> float:
    """Back off only after the queue positively reports no matching work, and
    only once the cool-off after the last finished task has passed.

    Completed work and temporary network failures retain the short interval.
    An HTTP 204 empty result also keeps it for FAST_POLL_COOLOFF_SECONDS after
    a task finished (the caller's next step usually arrives within seconds),
    then switches to the low-cost idle interval.
    """
    if not (attempt.reachable and attempt.task is None):
        return image_worker.POLL_SECONDS
    now = time.monotonic() if now is None else now
    if (last_task_done_at is not None
            and now - last_task_done_at < image_worker.FAST_POLL_COOLOFF_SECONDS):
        return image_worker.POLL_SECONDS
    return image_worker.IDLE_POLL_SECONDS


def report_result(client, task: dict, result: dict) -> None:
    try:
        client.complete_task(task["id"], task["claim_token"], result)
        print(f"completed {task['task_type']} {task['id']}")
    except httpx.HTTPError as e:
        # 409 = lease expired and someone else took over; network errors =
        # the queue is unreachable. Either way the worker keeps going; an
        # unreported task is reclaimed once its lease expires.
        print(f"could not report completion for {task['id']}: {e!r}")


def report_failure(client, task: dict, error: Exception) -> None:
    try:
        client.fail_task(task["id"], task["claim_token"], str(error))
    except httpx.HTTPError as e:
        print(f"could not report failure for {task['id']}: {e!r}")


def wait_for_memory_release(profile: str, timeout: float, poll: float = 2.0) -> None:
    """Freed memory (e.g. an unloaded 15 GB model) shows up in the OS a few
    seconds later -- found live: measuring right after the unload refused a
    render with '1.1 GB free' that minutes later read 12 GB. Wait (bounded)
    until the profile's requirement is met; the real gate decides after."""
    start = time.monotonic()
    while memory.memory_shortfall(profile) is not None:
        if time.monotonic() - start >= timeout:
            return
        time.sleep(poll)


def run_task(client, task: dict, heartbeat, supervisor, ollama) -> dict:
    task_type = task.get("task_type")
    if task_type == TEXT:
        return handle_text_task(task, heartbeat, ollama, supervisor)
    if task_type == IMAGE:
        # Only a model this worker loaded is unloaded -- renders need the memory.
        if ollama is not None and ollama.unload():
            if heartbeat:
                heartbeat.update(state="busy", task=task, stage="freeing_memory",
                                 message="Waiting for the text model's memory to be released")
            wait_for_memory_release(task["payload"].get("profile", "full"), MEMORY_SETTLE_SECONDS)
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
    client = image_worker.TaskQueueClient(
        image_worker.TASK_QUEUE_URL,
        worker_api_key=image_worker.WORKER_API_KEY,
    )
    try:
        task_types = configured_task_types(os.environ.get("WORKER_TASK_TYPES", ""))
    except ValueError as e:
        sys.exit(f"local_worker: {e}")
    # The operator decides; just warn if this machine looks unable to do it.
    if TEXT in task_types and not OllamaManager(OLLAMA_URL, OLLAMA_MODEL).available():
        print(f"WARNING: text_generation configured but Ollama is not answering at {OLLAMA_URL}")
    if IMAGE in task_types and not image_available():
        print("WARNING: image_generation configured but character-forge-v2/ComfyUI launcher "
              "not found")

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
    print(f"local_worker starting -- worker_id={image_worker.WORKER_ID} "
          f"task_types={task_types} mock_images={image_worker.MOCK_MODE} "
          f"queue={image_worker.TASK_QUEUE_URL}")

    last_task_done_at: float | None = None  # time.monotonic(), for the cool-off
    while True:
        attempt = claim_next_task(client, image_worker.WORKER_ID, task_types)
        task = attempt.task
        if task is None:
            if supervisor is not None:
                supervisor.maybe_stop_idle()
            if ollama is not None:
                ollama.maybe_unload_idle()
            time.sleep(next_poll_delay(attempt, last_task_done_at))
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
            report_failure(client, task, e)
        else:
            report_result(client, task, result)
        heartbeat.update(state="idle")
        last_task_done_at = time.monotonic()
        time.sleep(next_poll_delay(attempt, last_task_done_at))


if __name__ == "__main__":
    main()
